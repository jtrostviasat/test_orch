"""
Rootless Docker control for test containers.

Connects to the user's rootless Docker socket (DOCKER_HOST) and runs each test in
its own container with the rendered config mounted read-only. Log streaming is a
generator so the caller can fan lines out live while the container runs.
"""
from __future__ import annotations

from typing import Iterator

import docker

from backend.config import get_settings
from shared.validation import require_non_empty_str

settings = get_settings()

# Containers this system owns are always named with this prefix so we never
# count or remove unrelated containers sharing the rootless socket.
CONTAINER_PREFIX = "test_"


def _container_name(test_id: str) -> str:
    """
    Build the owned container name for a test.

    Args:
        test_id: The test identifier (non-empty).

    Returns:
        str: ``"test_<test_id>"``.

    Raises:
        TypeError/ValueError: If ``test_id`` is missing/blank.
    """
    require_non_empty_str(test_id, "test_id")
    return f"{CONTAINER_PREFIX}{test_id}"


class DockerEngine:
    """Wrapper over the rootless Docker client for test container lifecycle."""

    def __init__(self) -> None:
        """Create a Docker client bound to the configured rootless socket."""
        self._client = docker.DockerClient(base_url=settings.docker_host)

    def start_test_container(self, image: str, config_dir: str, test_id: str):
        """
        Pull (if needed) and start a detached test container.

        Args:
            image: Fully-qualified framework image reference.
            config_dir: Host directory mounted read-only at ``/config``.
            test_id: The test identifier (names the container).

        Returns:
            docker.models.containers.Container: The started container handle.

        Raises:
            TypeError/ValueError: If ``image``/``config_dir``/``test_id`` invalid.
            docker.errors.APIError: On image pull or container start failure.
        """
        require_non_empty_str(image, "image")
        require_non_empty_str(config_dir, "config_dir")
        name = _container_name(test_id)
        return self._client.containers.run(
            image=image,
            name=name,
            detach=True,
            network_mode="bridge",
            volumes={config_dir: {"bind": "/config", "mode": "ro"}},
            labels={"managed-by": "test_orch", "test-id": test_id},
        )

    @staticmethod
    def stream_logs(container) -> Iterator[str]:
        """
        Yield decoded log lines from a running container as they are produced.

        Args:
            container: A started container handle.

        Yields:
            str: One log line at a time (trailing newline stripped), streamed
            live so callers can fan lines out to InfluxDB and the WebSocket.
        """
        for raw in container.logs(stream=True, follow=True):
            yield raw.decode("utf-8", errors="replace").rstrip("\n")

    def active_test_container_count(self) -> int:
        """
        Count running containers OWNED by this system on the rootless socket.

        Args:
            None.

        Returns:
            int: Number of running containers whose name begins with
            ``CONTAINER_PREFIX``. Filtering by prefix avoids counting unrelated
            containers that may share the rootless daemon.
        """
        running = self._client.containers.list(filters={"status": "running"})
        return sum(1 for c in running if c.name.startswith(CONTAINER_PREFIX))

    def cleanup(self, test_id: str) -> None:
        """
        Force-remove a test's container if it still exists (idempotent).

        Args:
            test_id: The test identifier whose container to remove.

        Returns:
            None. Silently ignores a missing container (already gone).

        Raises:
            docker.errors.APIError: On a removal failure other than 404.
        """
        name = _container_name(test_id)
        try:
            container = self._client.containers.get(name)
            container.remove(force=True)
        except docker.errors.NotFound:
            return