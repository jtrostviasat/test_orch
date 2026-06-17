"""
LDAP authentication: bind engineers against the corporate directory and map
successful logins to persistent, unprivileged sessions in Postgres.

The bind itself IS the authentication — passwords are never stored locally. Only
identity (User) and an opaque session token (UserSession) are persisted.
"""
from __future__ import annotations

import datetime as dt
import secrets

from ldap3 import Connection, Server
from ldap3.core.exceptions import LDAPException
from sqlalchemy.orm import Session

from backend.config import get_settings
from backend.models import User, UserSession
from shared.validation import require_non_empty_str

settings = get_settings()

# How long a web session remains valid before re-authentication is required.
SESSION_TTL = dt.timedelta(hours=12)


class LDAPAuthError(Exception):
    """Raised when an LDAP bind fails or the directory is unreachable."""


def _ldap_bind(username: str, password: str) -> dict:
    """
    Perform an LDAP simple bind and fetch basic profile attributes.

    Args:
        username: The directory username (used to build the bind DN).
        password: The user's password (the bind itself is the authentication).

    Returns:
        dict: ``{"display_name": str, "email": str | None}`` best-effort
        attributes (falls back to the username when attributes are absent).

    Raises:
        LDAPAuthError: If the bind fails (bad credentials) or the directory is
            unreachable.
    """
    user_dn = settings.ldap_user_dn_template.format(username=username)
    server = Server(settings.ldap_host, port=settings.ldap_port, use_ssl=settings.ldap_use_ssl)
    try:
        conn = Connection(server, user=user_dn, password=password, auto_bind=True)
    except LDAPException as exc:
        raise LDAPAuthError(f"LDAP bind failed for {username}: {exc}") from exc

    attrs = {"display_name": username, "email": None}
    try:
        conn.search(
            search_base=settings.ldap_base_dn,
            search_filter=f"(uid={username})",
            attributes=["cn", "mail"],
        )
        if conn.entries:
            entry = conn.entries[0]
            attrs["display_name"] = str(getattr(entry, "cn", username))
            attrs["email"] = str(getattr(entry, "mail", "")) or None
    finally:
        conn.unbind()
    return attrs


def authenticate_ldap(db: Session, username: str, password: str) -> tuple[User, UserSession]:
    """
    Authenticate a user against LDAP and persist their identity + session.

    Args:
        db: Active SQLAlchemy session.
        username: Directory username (non-empty).
        password: User password (non-empty).

    Returns:
        tuple[User, UserSession]: The persisted user and a freshly-minted,
        unexpired session whose token should be set as the session cookie.

    Raises:
        TypeError/ValueError: If ``username``/``password`` are missing/blank.
        LDAPAuthError: If authentication fails or the directory is unreachable.
    """
    require_non_empty_str(username, "username")
    require_non_empty_str(password, "password")

    attrs = _ldap_bind(username, password)
    user = db.query(User).filter_by(username=username).one_or_none()
    if user is None:
        user = User(username=username, display_name=attrs["display_name"], email=attrs["email"])
        db.add(user)
        db.flush()

    session = UserSession(
        token=secrets.token_urlsafe(32),
        user_id=user.id,
        expires_at=dt.datetime.now(dt.timezone.utc) + SESSION_TTL,
    )
    db.add(session)
    db.commit()
    return user, session


def resolve_session(db: Session, token: str | None) -> User | None:
    """
    Resolve a session token to its owning user, if valid and unexpired.

    Args:
        db: Active SQLAlchemy session.
        token: The session token from the cookie, or ``None``.

    Returns:
        User | None: The user if the token is present, found, and not expired;
        otherwise ``None``.
    """
    if not token:
        return None
    sess = db.query(UserSession).filter_by(token=token).one_or_none()
    if sess is None:
        return None
    # Tolerate a naive expiry (some DB backends drop tzinfo) by assuming UTC, so
    # the comparison can never raise on offset-naive vs offset-aware datetimes.
    expires_at = sess.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=dt.timezone.utc)
    if expires_at < dt.datetime.now(dt.timezone.utc):
        return None
    return sess.user