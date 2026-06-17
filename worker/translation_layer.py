"""
UI-variable -> framework-config translation.

The user picks high-level variables in the web UI; each runner translates those
into the concrete on-disk config its container image expects. Adding support for
a new framework = adding a new Runner subclass and registering it.
"""
from __future__ import annotations

import abc
import json
import os
from typing import Any, Dict

from backend.config import get_settings
from shared.validation import require_mapping, require_non_empty_str

settings = get_settings()


class Runner(abc.ABC):
    """Abstract base translating UI variables into a container config dir."""

    def __init__(self, test_id: str, variables: Dict[str, Any]) -> None:
        """
        Initialize a runner for one test.

        Args:
            test_id: The test identifier (non-empty); names the config dir.
            variables: UI-supplied variable mapping for this run.

        Raises:
            TypeError/ValueError: If ``test_id`` is invalid or ``variables`` is
                not a mapping.
        """
        require_non_empty_str(test_id, "test_id")
        require_mapping(variables, "variables")
        self.test_id = test_id
        self.variables = variables

    @property
    def config_dir(self) -> str:
        """
        Return (and create) the per-test config directory path.

        Args:
            None.

        Returns:
            str: ``<test_runs_dir>/<test_id>``, created if missing.
        """
        path = os.path.join(settings.test_runs_dir, self.test_id)
        os.makedirs(path, exist_ok=True)
        return path

    @abc.abstractmethod
    def write_config(self) -> str:
        """
        Render this runner's config files into ``config_dir``.

        Args:
            None.

        Returns:
            str: The directory path containing the rendered config (mounted
            read-only into the test container).
        """
        raise NotImplementedError


class PytestRunner(Runner):
    """Runner that renders a JSON config consumed by a pytest-based image."""

    def write_config(self) -> str:
        """
        Write ``pytest_config.json`` derived from the UI variables.

        Args:
            None.

        Returns:
            str: The config directory path.

        Raises:
            ValueError: If timeout_seconds is not a positive integer.
        """
        timeout_str = self.variables.get("timeout_seconds", "600")
        try:
            timeout_seconds = int(timeout_str)
            if timeout_seconds <= 0:
                raise ValueError("timeout_seconds must be positive")
        except (ValueError, TypeError) as e:
            raise ValueError(f"Invalid timeout_seconds: {timeout_str}") from e

        cfg = {
            "markers": self.variables.get("markers", []),
            "target_dut": self.variables.get("target_dut", ""),
            "timeout_seconds": timeout_seconds,
            "extra_args": self.variables.get("extra_args", []),
        }
        out = os.path.join(self.config_dir, "pytest_config.json")
        try:
            with open(out, "w", encoding="utf-8") as fh:
                json.dump(cfg, fh, indent=2)
        except IOError as e:
            raise RuntimeError(f"Failed to write config to {out}: {e}") from e
        return self.config_dir


class RobotRunner(Runner):
    """Runner that renders a Robot Framework argument file."""

    def write_config(self) -> str:
        """
        Write ``robot.args`` (one argument per line) from the UI variables.

        Args:
            None.

        Returns:
            str: The config directory path.

        Raises:
            RuntimeError: If the config file cannot be written.
        """
        lines = [f"--variable DUT:{self.variables.get('target_dut', '')}"]
        for tag in self.variables.get("include_tags", []):
            lines.append(f"--include {tag}")
        out = os.path.join(self.config_dir, "robot.args")
        try:
            with open(out, "w", encoding="utf-8") as fh:
                fh.write("\n".join(lines))
        except IOError as e:
            raise RuntimeError(f"Failed to write config to {out}: {e}") from e
        return self.config_dir


_RUNNERS: dict[str, type[Runner]] = {
    "pytest": PytestRunner,
    "robot": RobotRunner,
}


def get_runner(runner_type: str, test_id: str, variables: Dict[str, Any]) -> Runner:
    """
    Construct the Runner registered for a given runner type.

    Args:
        runner_type: Registered runner key (e.g. ``"pytest"`` or ``"robot"``).
        test_id: The test identifier (non-empty).
        variables: UI-supplied variable mapping.

    Returns:
        Runner: A concrete runner instance ready to ``write_config()``.

    Raises:
        TypeError/ValueError: If ``runner_type``/``test_id`` are invalid.
        KeyError: If ``runner_type`` is not registered.
    """
    require_non_empty_str(runner_type, "runner_type")
    require_non_empty_str(test_id, "test_id")
    if runner_type not in _RUNNERS:
        raise KeyError(f"Unknown runner_type '{runner_type}'. Known: {sorted(_RUNNERS)}")
    return _RUNNERS[runner_type](test_id, variables)