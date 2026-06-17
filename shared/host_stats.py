"""Host statistics collection (psutil-based), with input + return validation."""
from __future__ import annotations

import os
import shutil
from dataclasses import asdict, dataclass
from typing import Any, Dict

import psutil

from shared.validation import require_non_empty_str


@dataclass
class HostStats:
    """Validated point-in-time snapshot of a worker host's resource utilization."""

    host_id: str
    cpu_percent: float
    ram_available_mb: float
    ram_total_mb: float
    disk_available_gb: float
    disk_total_gb: float
    active_users: int
    online: bool = True

    def __post_init__(self) -> None:
        """
        Enforce physical invariants on the snapshot after construction.

        Args:
            None (operates on the dataclass fields).

        Returns:
            None.

        Raises:
            AssertionError: If any field violates its expected range (e.g. CPU
                outside 0-100, available > total, negative counts). Note these
                are ``assert``s and are stripped under ``python -O``.
        """
        assert isinstance(self.host_id, str) and self.host_id, "host_id must be non-empty str"
        assert 0.0 <= self.cpu_percent <= 100.0, f"cpu_percent out of range: {self.cpu_percent}"
        assert self.ram_available_mb >= 0, "ram_available_mb must be >= 0"
        assert self.ram_total_mb >= 0, "ram_total_mb must be >= 0"
        assert self.ram_available_mb <= self.ram_total_mb + 1e-6, "avail RAM > total RAM"
        assert self.disk_available_gb >= 0, "disk_available_gb must be >= 0"
        assert self.disk_total_gb >= 0, "disk_total_gb must be >= 0"
        assert self.disk_available_gb <= self.disk_total_gb + 1e-6, "avail disk > total disk"
        assert self.active_users >= 0, "active_users must be >= 0"

    def to_dict(self) -> Dict[str, Any]:
        """
        Serialize the snapshot to a plain dictionary.

        Args:
            None.

        Returns:
            Dict[str, Any]: All fields as a JSON-serializable mapping.
        """
        return asdict(self)


def collect_host_stats(host_id: str, disk_path: str = "/") -> HostStats:
    """
    Gather a validated live snapshot of this host's resources via psutil.

    Args:
        host_id: Unique identifier of the host being measured (non-empty).
        disk_path: Filesystem path whose free/total space is measured. Defaults
            to ``"/"``; callers typically pass the test-runs directory.

    Returns:
        HostStats: A fully-validated snapshot. CPU is clamped into ``[0, 100]``
        to tolerate transient psutil readings. ``active_users`` falls back to
        ``0`` when the host's utmp is unreadable (common inside containers).

    Raises:
        TypeError: If ``host_id``/``disk_path`` are not strings.
        ValueError: If ``host_id``/``disk_path`` are empty.
        FileNotFoundError: If ``disk_path`` does not exist (from ``disk_usage``).
    """
    require_non_empty_str(host_id, "host_id")
    require_non_empty_str(disk_path, "disk_path")

    cpu_percent = float(psutil.cpu_percent(interval=None))
    cpu_percent = max(0.0, min(100.0, cpu_percent))  # clamp transient over/under-shoot

    vm = psutil.virtual_memory()
    ram_available_mb = vm.available / (1024 * 1024)
    ram_total_mb = vm.total / (1024 * 1024)

    # Create disk_path if it doesn't exist so metrics can be collected
    if not os.path.exists(disk_path):
        try:
            os.makedirs(disk_path, exist_ok=True)
        except OSError:
            disk_path = "/"

    usage = shutil.disk_usage(disk_path)
    disk_available_gb = usage.free / (1024 ** 3)
    disk_total_gb = usage.total / (1024 ** 3)

    try:
        active_users = len({u.name for u in psutil.users()})
    except Exception:  # noqa: BLE001 - empty/absent utmp in containers
        active_users = 0

    return HostStats(
        host_id=host_id,
        cpu_percent=round(cpu_percent, 1),
        ram_available_mb=round(ram_available_mb, 1),
        ram_total_mb=round(ram_total_mb, 1),
        disk_available_gb=round(disk_available_gb, 2),
        disk_total_gb=round(disk_total_gb, 2),
        active_users=active_users,
        online=True,
    )
