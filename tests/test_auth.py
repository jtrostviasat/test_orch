"""
Tests for session resolution and LDAP-backed login persistence.

resolve_session is exercised directly against an in-memory DB; authenticate_ldap
is tested with the actual LDAP bind monkeypatched out, so no directory is needed.
"""
from __future__ import annotations

import datetime as dt

import pytest

from backend.auth import ldap_auth
from backend.auth.ldap_auth import authenticate_ldap, resolve_session
from backend.models import User, UserSession


def _mint(db, username: str, token: str, *, expires_in: dt.timedelta) -> None:
    user = User(username=username)
    db.add(user)
    db.flush()
    db.add(UserSession(
        token=token,
        user_id=user.id,
        expires_at=dt.datetime.now(dt.timezone.utc) + expires_in,
    ))
    db.commit()


def test_resolve_session_none_token(db_session):
    assert resolve_session(db_session, None) is None


def test_resolve_session_unknown_token(db_session):
    assert resolve_session(db_session, "does-not-exist") is None


def test_resolve_session_valid(db_session):
    _mint(db_session, "alice", "good-token", expires_in=dt.timedelta(hours=1))
    user = resolve_session(db_session, "good-token")
    assert user is not None
    assert user.username == "alice"


def test_resolve_session_expired(db_session):
    _mint(db_session, "bob", "stale-token", expires_in=dt.timedelta(hours=-1))
    assert resolve_session(db_session, "stale-token") is None


def test_authenticate_creates_user_and_session(db_session, monkeypatch):
    monkeypatch.setattr(
        ldap_auth, "_ldap_bind",
        lambda username, password: {"display_name": "Carol C", "email": "carol@x"},
    )

    user, session = authenticate_ldap(db_session, "carol", "pw")

    assert user.username == "carol"
    assert user.display_name == "Carol C"
    assert session.token
    assert session.expires_at > dt.datetime.now(dt.timezone.utc)


def test_authenticate_reuses_existing_user(db_session, monkeypatch):
    monkeypatch.setattr(
        ldap_auth, "_ldap_bind",
        lambda username, password: {"display_name": "Dave", "email": None},
    )

    user1, session1 = authenticate_ldap(db_session, "dave", "pw")
    user2, session2 = authenticate_ldap(db_session, "dave", "pw")

    assert user2.id == user1.id          # same identity row reused
    assert session2.token != session1.token  # but a fresh session each login
    assert db_session.query(User).filter_by(username="dave").count() == 1


def test_authenticate_rejects_empty_username(db_session):
    with pytest.raises((ValueError, TypeError)):
        authenticate_ldap(db_session, "", "pw")
