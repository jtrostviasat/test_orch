"""
Shared pytest fixtures and lightweight test doubles.

These avoid any real I/O: the fake WorkerHost mimics just the attributes the
scheduler reads, and the fake WebSocket records sent fragments for assertions.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pytest


@dataclass
class FakeHost:
    """Minimal stand-in for backend.models.WorkerHost used by scheduler tests."""

    host_id: str
    is_online: bool = True
    active_containers: int = 0
    max_containers: int = 10
    cpu_utilization: float = 0.0
    hardware_tags: str = ""
    supports_emulation: bool = False

    def tag_set(self) -> set[str]:
        """Parse the comma-separated tags exactly like the real model does."""
        return {t.strip() for t in self.hardware_tags.split(",") if t.strip()}


class FakeWebSocket:
    """Records text frames sent to it so tests can assert on pushed HTML."""

    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send_text(self, text: str) -> None:
        self.sent.append(text)


@pytest.fixture
def make_host():
    """Factory fixture returning configured FakeHost instances."""
    def _make(host_id: str, **kwargs) -> FakeHost:
        return FakeHost(host_id=host_id, **kwargs)
    return _make


@pytest.fixture
def db_session():
    """
    A real SQLAlchemy session backed by an in-memory SQLite database.

    The ORM models are dialect-agnostic, so this exercises the actual queries
    (filters, ``in_``, ``with_for_update`` — a no-op on SQLite) with zero network
    or Postgres dependency. Tables are created fresh per test and torn down after.
    """
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from backend import models  # noqa: F401 - registers tables on Base.metadata
    from backend.database import Base

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    TestingSession = sessionmaker(bind=engine, expire_on_commit=False)
    session = TestingSession()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()