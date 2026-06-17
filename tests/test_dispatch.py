"""
Tests for scheduler.dispatch — the authoritative placement path.

Unlike select_host (pure, covered in test_scheduler), dispatch mutates state: it
reserves a container slot, enqueues the Celery task to the chosen host's queue,
and rolls the reservation back if the enqueue fails. These use a real in-memory
SQLite session and a stubbed Celery client so nothing hits the broker.
"""
from __future__ import annotations

import pytest

from backend import celery_client as celery_client_module
from backend.models import WorkerHost
from backend.services.scheduler import dispatch
from shared.schemas import TestBundleRequest


def _bundle(**overrides) -> TestBundleRequest:
    base = dict(test_id="t-1", user_id=1, runner_type="pytest", framework_image="img:1")
    base.update(overrides)
    return TestBundleRequest(**base)


@pytest.fixture
def captured_send(monkeypatch):
    """Replace the backend Celery client's send_task with a recording stub."""
    calls = []

    def fake_send_task(name, args=None, queue=None, **kwargs):
        calls.append({"name": name, "args": args, "queue": queue})

    monkeypatch.setattr(celery_client_module.celery_client, "send_task", fake_send_task)
    return calls


def test_dispatch_reserves_and_sends_to_lightest(db_session, captured_send):
    db_session.add_all([
        WorkerHost(host_id="h1", is_online=True, active_containers=3,
                   cpu_utilization=10, max_containers=10),
        WorkerHost(host_id="h2", is_online=True, active_containers=0,
                   cpu_utilization=50, max_containers=10),
    ])
    db_session.commit()

    # Scores: h1 = 3*20+10 = 70, h2 = 0*20+50 = 50 → h2 is lightest.
    chosen = dispatch(db_session, _bundle())

    assert chosen == "h2"
    assert len(captured_send) == 1
    assert captured_send[0]["name"] == "worker.run_test_bundle"
    assert captured_send[0]["queue"] == "queue_h2"
    # The slot was optimistically reserved on the chosen host.
    assert db_session.query(WorkerHost).filter_by(host_id="h2").one().active_containers == 1


def test_dispatch_no_survivors_returns_none(db_session, captured_send):
    db_session.add(
        WorkerHost(host_id="full", is_online=True, active_containers=10, max_containers=10)
    )
    db_session.commit()

    assert dispatch(db_session, _bundle()) is None
    assert captured_send == []  # nothing enqueued when no host qualifies


def test_dispatch_rolls_back_reservation_on_send_failure(db_session, monkeypatch):
    db_session.add(
        WorkerHost(host_id="h1", is_online=True, active_containers=2, max_containers=10)
    )
    db_session.commit()

    def boom(*args, **kwargs):
        raise RuntimeError("broker down")

    monkeypatch.setattr(celery_client_module.celery_client, "send_task", boom)

    with pytest.raises(RuntimeError):
        dispatch(db_session, _bundle())

    # The optimistic +1 must be undone so capacity isn't leaked.
    assert db_session.query(WorkerHost).filter_by(host_id="h1").one().active_containers == 2


def test_dispatch_rejects_bad_bundle_type(db_session):
    with pytest.raises(TypeError):
        dispatch(db_session, {"not": "a bundle"})
