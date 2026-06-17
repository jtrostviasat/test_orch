"""
Tests for the central resiliency sweep (backend.services.maintenance).

These cover the spec §D guarantees that the worker cannot provide for itself:
marking dead hosts offline on stale heartbeats and failing the runs they orphan
(releasing the associated Quali reservations). They use a real in-memory SQLite
session and a fake QualiClient so no network is touched.
"""
from __future__ import annotations

import datetime as dt

from backend.models import TestExecution, WorkerHost
from backend.services import maintenance
from backend.services.maintenance import fail_orphaned_runs, sweep_stale_hosts
from shared.schemas import TestStatus

# Fixed "now" (aware UTC) plus a naive UTC base for heartbeat timestamps, since
# SQLite stores DateTime without tz and reads them back naive.
NOW = dt.datetime(2026, 1, 1, 12, 0, 0, tzinfo=dt.timezone.utc)
NAIVE = NOW.replace(tzinfo=None)


def test_sweep_marks_stale_host_offline(db_session):
    db_session.add_all([
        WorkerHost(host_id="fresh", is_online=True,
                   last_heartbeat=NAIVE - dt.timedelta(seconds=10)),
        WorkerHost(host_id="stale", is_online=True,
                   last_heartbeat=NAIVE - dt.timedelta(seconds=200)),
    ])
    db_session.commit()

    affected = sweep_stale_hosts(db_session, NOW, max_age_seconds=90)

    assert affected == ["stale"]
    assert db_session.query(WorkerHost).filter_by(host_id="stale").one().is_online is False
    assert db_session.query(WorkerHost).filter_by(host_id="fresh").one().is_online is True


def test_sweep_marks_never_heartbeated_online_host_offline(db_session):
    db_session.add(WorkerHost(host_id="zombie", is_online=True, last_heartbeat=None))
    db_session.commit()

    assert sweep_stale_hosts(db_session, NOW, 90) == ["zombie"]


def test_sweep_ignores_already_offline_hosts(db_session):
    db_session.add(WorkerHost(host_id="down", is_online=False, last_heartbeat=None))
    db_session.commit()

    assert sweep_stale_hosts(db_session, NOW, 90) == []


def test_fail_orphaned_runs_fails_active_and_releases(db_session, monkeypatch):
    released = []

    class FakeQuali:
        def release_reservation(self, reservation_id):
            released.append(reservation_id)
            return True

    monkeypatch.setattr(maintenance, "QualiClient", FakeQuali)

    db_session.add_all([
        TestExecution(test_id="t-run", user_id=1, target_host_id="dead",
                      runner_type="pytest", framework_image="img:1",
                      status=TestStatus.RUNNING.value, quali_reservation_id="resv-1"),
        TestExecution(test_id="t-disp", user_id=1, target_host_id="dead",
                      runner_type="pytest", framework_image="img:1",
                      status=TestStatus.DISPATCHED.value),
        TestExecution(test_id="t-pass", user_id=1, target_host_id="dead",
                      runner_type="pytest", framework_image="img:1",
                      status=TestStatus.PASSED.value),
    ])
    db_session.commit()

    failed = fail_orphaned_runs(db_session, ["dead"])

    assert failed == 2
    statuses = {
        e.test_id: e.status for e in db_session.query(TestExecution).all()
    }
    assert statuses["t-run"] == TestStatus.FAILED.value
    assert statuses["t-disp"] == TestStatus.FAILED.value
    assert statuses["t-pass"] == TestStatus.PASSED.value  # terminal state untouched
    assert released == ["resv-1"]  # only the run holding a reservation


def test_fail_orphaned_runs_survives_release_error(db_session, monkeypatch):
    class BoomQuali:
        def release_reservation(self, reservation_id):
            raise RuntimeError("quali unreachable")

    monkeypatch.setattr(maintenance, "QualiClient", BoomQuali)

    db_session.add(
        TestExecution(test_id="t1", user_id=1, target_host_id="dead",
                      runner_type="pytest", framework_image="img:1",
                      status=TestStatus.RUNNING.value, quali_reservation_id="r1")
    )
    db_session.commit()

    # A failing release must not prevent the run being marked FAILED.
    failed = fail_orphaned_runs(db_session, ["dead"])

    assert failed == 1
    assert db_session.query(TestExecution).filter_by(test_id="t1").one().status == (
        TestStatus.FAILED.value
    )


def test_fail_orphaned_runs_noop_on_empty(db_session):
    assert fail_orphaned_runs(db_session, []) == 0


def test_fail_orphaned_runs_ignores_other_hosts(db_session):
    db_session.add(
        TestExecution(test_id="alive", user_id=1, target_host_id="healthy",
                      runner_type="pytest", framework_image="img:1",
                      status=TestStatus.RUNNING.value)
    )
    db_session.commit()

    # "healthy" is not in the dead-host list, so its run is left alone.
    assert fail_orphaned_runs(db_session, ["dead"]) == 0
    assert db_session.query(TestExecution).filter_by(test_id="alive").one().status == (
        TestStatus.RUNNING.value
    )
