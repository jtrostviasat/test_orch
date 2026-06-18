"""
Integration tests for the HTTP routes (FastAPI TestClient).

The app's SessionLocal is repointed at a shared in-memory SQLite database, the
Celery client's send_task is stubbed, and the client is built WITHOUT the
lifespan context manager so the Postgres-backed startup/maintenance loop never
runs (init_db / run_maintenance_sweep are also no-op'd as a safety net).
"""
from __future__ import annotations

import datetime as dt
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool


@pytest.fixture
def env(monkeypatch):
    from fastapi.testclient import TestClient

    from backend import celery_client as cc
    from backend import database as db_module
    from backend import main as main_module
    from backend import models
    from backend.database import Base

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,  # one shared in-memory connection across threads
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)

    # Routes call SessionLocal() directly; get_db uses database.SessionLocal.
    monkeypatch.setattr(db_module, "SessionLocal", Session)
    monkeypatch.setattr(main_module, "SessionLocal", Session)
    # Safety net in case lifespan ever runs: keep it off Postgres.
    monkeypatch.setattr(main_module, "init_db", lambda: None)
    monkeypatch.setattr(main_module, "run_maintenance_sweep", lambda: None)

    sent = []
    monkeypatch.setattr(
        cc.celery_client, "send_task",
        lambda name, **kw: sent.append({"name": name, **kw}),
    )

    client = TestClient(main_module.app)  # no `with` -> lifespan not triggered
    return SimpleNamespace(client=client, Session=Session, models=models, sent=sent)


def _login(env, username: str = "tester") -> int:
    """Create a user + valid session and attach the cookie. Returns user id."""
    s = env.Session()
    user = env.models.User(username=username)
    s.add(user)
    s.flush()
    uid = user.id
    token = f"tok-{username}"
    s.add(env.models.UserSession(
        token=token, user_id=uid,
        expires_at=dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=1),
    ))
    s.commit()
    s.close()
    # Set as a default header rather than via the cookie jar: httpx/http.cookiejar
    # won't reliably attach a cookie whose domain is the dot-less "testserver".
    env.client.headers["Cookie"] = f"session_token={token}"
    return uid


# --------------------------------------------------------------------------- #
# Auth gating
# --------------------------------------------------------------------------- #
def test_dashboard_redirects_when_anonymous(env):
    resp = env.client.get("/", follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["location"] == "/login"


def test_admin_redirects_when_anonymous(env):
    assert env.client.get("/admin", follow_redirects=False).status_code == 302


def test_dashboard_ok_when_logged_in(env):
    _login(env)
    resp = env.client.get("/")
    assert resp.status_code == 200
    assert "Submit a Test" in resp.text


# --------------------------------------------------------------------------- #
# Test submission (POST /tests -> dispatch)
# --------------------------------------------------------------------------- #
def test_submit_dispatches_to_online_host(env):
    _login(env)
    s = env.Session()
    s.add(env.models.WorkerHost(host_id="wh1", is_online=True,
                                active_containers=0, max_containers=5))
    s.commit()
    s.close()

    resp = env.client.post(
        "/tests",
        data={"runner_type": "pytest", "framework_image": "mvp-test:latest"},
        follow_redirects=False,
    )
    assert resp.status_code == 302

    s = env.Session()
    runs = s.query(env.models.TestExecution).all()
    s.close()
    assert len(runs) == 1
    assert runs[0].status == "DISPATCHED"
    assert runs[0].target_host_id == "wh1"
    assert env.sent and env.sent[0]["name"] == "worker.run_test_bundle"
    assert env.sent[0]["queue"] == "queue_wh1"


def test_submit_fails_when_no_host_available(env):
    _login(env)
    resp = env.client.post(
        "/tests",
        data={"runner_type": "pytest", "framework_image": "img:1"},
        follow_redirects=False,
    )
    assert resp.status_code == 302

    s = env.Session()
    runs = s.query(env.models.TestExecution).all()
    s.close()
    assert len(runs) == 1
    assert runs[0].status == "FAILED"
    assert env.sent == []  # nothing enqueued


# --------------------------------------------------------------------------- #
# Host capability editing (POST /admin/hosts)
# --------------------------------------------------------------------------- #
def test_update_host_sets_capabilities(env):
    _login(env)
    s = env.Session()
    s.add(env.models.WorkerHost(host_id="wh1", is_online=True))
    s.commit()
    s.close()

    resp = env.client.post(
        "/admin/hosts",
        data={
            "host_id": "wh1",
            "hardware_tags": " DUT_A , DUT_B ",
            "supports_emulation": "true",
            "max_containers": "7",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 302

    s = env.Session()
    host = s.query(env.models.WorkerHost).filter_by(host_id="wh1").one()
    s.close()
    assert host.hardware_tags == "DUT_A,DUT_B"  # trimmed + normalized
    assert host.supports_emulation is True
    assert host.max_containers == 7


# --------------------------------------------------------------------------- #
# Log page ownership (GET /tests/{test_id})
# --------------------------------------------------------------------------- #
def test_logs_page_owner_only(env):
    uid = _login(env, "owner")
    s = env.Session()
    s.add(env.models.TestExecution(
        test_id="mine", user_id=uid, runner_type="pytest",
        framework_image="i", status="RUNNING"))
    other = env.models.User(username="other")
    s.add(other)
    s.flush()
    s.add(env.models.TestExecution(
        test_id="theirs", user_id=other.id, runner_type="pytest",
        framework_image="i", status="RUNNING"))
    s.commit()
    s.close()

    assert env.client.get("/tests/mine", follow_redirects=False).status_code == 200
    # Another user's run is not viewable -> redirect to dashboard.
    assert env.client.get("/tests/theirs", follow_redirects=False).status_code == 302


def test_logs_page_unknown_test_redirects(env):
    _login(env)
    assert env.client.get("/tests/nope", follow_redirects=False).status_code == 302
