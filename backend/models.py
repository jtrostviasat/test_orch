"""
SQLAlchemy models for the central PostgreSQL state database.

Concerns:
  * User            - LDAP-authenticated engineers (audit identity).
  * UserSession     - persistent unprivileged web sessions.
  * WorkerHost      - distributed host inventory + live load metrics.
  * TestExecution   - per-run state machine (audit trail + artifact URL).
  * LogLine         - lightweight relational log index (full history -> InfluxDB).

These models are dialect-agnostic; they ran on SQLite and run unchanged on
PostgreSQL.
"""
from __future__ import annotations

import datetime as dt
from typing import Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.database import Base


def _utcnow() -> dt.datetime:
    """
    Return the current time as a timezone-aware UTC datetime.

    Args:
        None.

    Returns:
        datetime.datetime: ``datetime.now`` in UTC with tzinfo set.
    """
    return dt.datetime.now(dt.timezone.utc)


class User(Base):
    """An LDAP-authenticated engineer; the audit identity for executions."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    display_name: Mapped[Optional[str]] = mapped_column(String(256))
    email: Mapped[Optional[str]] = mapped_column(String(256))
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    sessions: Mapped[list["UserSession"]] = relationship(back_populates="user")
    executions: Mapped[list["TestExecution"]] = relationship(back_populates="user")


class UserSession(Base):
    """Persistent unprivileged session token mapped to an LDAP identity."""

    __tablename__ = "user_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    token: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    expires_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))

    user: Mapped["User"] = relationship(back_populates="sessions")


class WorkerHost(Base):
    """
    Inventory + live telemetry for one distributed worker machine.

    ``hardware_tags`` is stored as a comma-separated string for MVP simplicity
    (e.g. "DUT_TYPE_A,DUT_TYPE_B"). The scheduler reads these.
    """

    __tablename__ = "worker_hosts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    host_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)  # -> queue name
    is_online: Mapped[bool] = mapped_column(Boolean, default=False)
    active_containers: Mapped[int] = mapped_column(Integer, default=0)
    max_containers: Mapped[int] = mapped_column(Integer, default=10)
    cpu_utilization: Mapped[float] = mapped_column(Float, default=0.0)  # 0-100
    hardware_tags: Mapped[str] = mapped_column(String(512), default="")
    supports_emulation: Mapped[bool] = mapped_column(Boolean, default=False)
    last_heartbeat: Mapped[Optional[dt.datetime]] = mapped_column(DateTime(timezone=True))

    def tag_set(self) -> set[str]:
        """
        Parse the comma-separated ``hardware_tags`` column into a set.

        Args:
            None (reads ``self.hardware_tags``).

        Returns:
            set[str]: Individual, stripped, non-empty hardware tag strings.
        """
        return {t.strip() for t in self.hardware_tags.split(",") if t.strip()}


class TestExecution(Base):
    """One test run. Carries the full audit trail and the final artifact URL."""

    __tablename__ = "test_executions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    test_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    target_host_id: Mapped[Optional[str]] = mapped_column(String(64), index=True)
    runner_type: Mapped[str] = mapped_column(String(64))
    framework_image: Mapped[str] = mapped_column(String(256))
    status: Mapped[str] = mapped_column(String(32), default="PENDING", index=True)
    quali_reservation_id: Mapped[Optional[str]] = mapped_column(String(128))
    artifact_url: Mapped[Optional[str]] = mapped_column(String(1024))
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )

    user: Mapped["User"] = relationship(back_populates="executions")


class LogLine(Base):
    """
    Relational index of log lines (timestamp + pointer).

    Full log text lives in InfluxDB; this table makes the user's history/search
    page fast without scanning the time-series store.
    """

    __tablename__ = "log_lines"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    test_id: Mapped[str] = mapped_column(String(64), index=True)
    line_no: Mapped[int] = mapped_column(Integer)
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
