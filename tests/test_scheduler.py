"""Tests for the backend.services.scheduler filter-and-rank logic."""
from __future__ import annotations

import pytest

from backend.services import scheduler
from backend.services.scheduler import RankedHost, queue_name_for, select_host
from shared.schemas import TestBundleRequest


class FakeQuery:
    """Mimics db.query(WorkerHost).all() for select_host()."""

    def __init__(self, hosts):
        self._hosts = hosts

    def all(self):
        return self._hosts


class FakeDB:
    """Just enough of a Session for the read-only select_host path."""

    def __init__(self, hosts):
        self._hosts = hosts

    def query(self, _model):
        return FakeQuery(self._hosts)


def _bundle(**overrides):
    base = dict(test_id="t-1", user_id=1, runner_type="pytest", framework_image="img:1")
    base.update(overrides)
    return TestBundleRequest(**base)


def test_queue_name_for():
    assert queue_name_for("worker_host_01") == "queue_worker_host_01"
    with pytest.raises(ValueError):
        queue_name_for("")


def test_select_picks_least_loaded(make_host):
    hosts = [
        make_host("h1", active_containers=3, cpu_utilization=10),
        make_host("h2", active_containers=0, cpu_utilization=50),  # lightest by score
        make_host("h3", active_containers=1, cpu_utilization=90),
    ]
    chosen = select_host(FakeDB(hosts), _bundle())
    assert isinstance(chosen, RankedHost)
    assert chosen.host_id == "h2"


def test_select_filters_offline_and_full(make_host):
    hosts = [
        make_host("off", is_online=False, active_containers=0),
        make_host("full", active_containers=10, max_containers=10),
    ]
    assert select_host(FakeDB(hosts), _bundle()) is None


def test_select_requires_hardware_tags(make_host):
    hosts = [
        make_host("no_tag", hardware_tags="DUT_TYPE_B"),
        make_host("has_tag", hardware_tags="DUT_TYPE_A,DUT_TYPE_B"),
    ]
    chosen = select_host(FakeDB(hosts), _bundle(required_hw_tags=["DUT_TYPE_A"]))
    assert chosen.host_id == "has_tag"


def test_select_requires_emulation(make_host):
    hosts = [
        make_host("no_emu", supports_emulation=False),
        make_host("emu", supports_emulation=True),
    ]
    chosen = select_host(FakeDB(hosts), _bundle(requires_emulation=True))
    assert chosen.host_id == "emu"


def test_tie_broken_deterministically_by_host_id(make_host):
    # Identical scores → lowest host_id wins for stable, reproducible placement.
    hosts = [
        make_host("hb", active_containers=1, cpu_utilization=0),
        make_host("ha", active_containers=1, cpu_utilization=0),
    ]
    chosen = select_host(FakeDB(hosts), _bundle())
    assert chosen.host_id == "ha"


def test_select_rejects_bad_bundle_type():
    with pytest.raises(TypeError):
        select_host(FakeDB([]), {"not": "a bundle"})