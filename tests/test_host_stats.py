"""Tests for shared.host_stats invariants and the psutil-backed collector."""
from __future__ import annotations

import pytest

from shared import host_stats as hs
from shared.host_stats import HostStats, collect_host_stats


def test_hoststats_rejects_cpu_out_of_range():
    with pytest.raises(AssertionError):
        HostStats(
            host_id="h", cpu_percent=150.0, ram_available_mb=1, ram_total_mb=2,
            disk_available_gb=1, disk_total_gb=2, active_users=0,
        )


def test_hoststats_rejects_avail_gt_total():
    with pytest.raises(AssertionError):
        HostStats(
            host_id="h", cpu_percent=10.0, ram_available_mb=5, ram_total_mb=2,
            disk_available_gb=1, disk_total_gb=2, active_users=0,
        )


def test_collect_clamps_cpu_and_validates(monkeypatch):
    # Force psutil to report an out-of-range CPU; collector must clamp to 100.
    class _VM:
        available = 1024 * 1024 * 100
        total = 1024 * 1024 * 200

    monkeypatch.setattr(hs.psutil, "cpu_percent", lambda interval=None: 999.0)
    monkeypatch.setattr(hs.psutil, "virtual_memory", lambda: _VM())
    monkeypatch.setattr(hs.psutil, "users", lambda: [])
    monkeypatch.setattr(
        hs.shutil, "disk_usage",
        lambda p: type("U", (), {"free": 5 * 1024**3, "total": 10 * 1024**3})(),
    )

    stats = collect_host_stats("worker_x", disk_path="/")
    assert stats.cpu_percent == 100.0          # clamped
    assert stats.host_id == "worker_x"
    assert stats.active_users == 0
    assert stats.ram_total_mb >= stats.ram_available_mb


def test_collect_rejects_blank_host_id():
    with pytest.raises(ValueError):
        collect_host_stats("", disk_path="/")