"""
PostgreSQL connection and session lifecycle.

Uses SQLAlchemy 2.x with the psycopg 3 driver. A connection pool is configured so
the backend and each worker host can hold multiple concurrent sessions safely.

Switching from SQLite to Postgres is what makes the multi-host topology work:
every worker machine connects to this central database over TCP rather than
trying to open a local file.
"""
from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from backend.config import get_settings

settings = get_settings()

engine = create_engine(
    settings.sqlalchemy_url,
    pool_pre_ping=True,   # validate connections before use (survives restarts)
    pool_size=10,         # base pool per process
    max_overflow=20,      # burst capacity under load
    future=True,
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    """Declarative base class for all ORM models."""


def init_db() -> None:
    """
    Create all tables if they do not yet exist (idempotent).

    Args:
        None.

    Returns:
        None.

    Note:
        This is dev convenience, not a migration tool. It will not alter or drop
        existing columns. Running it from multiple replicas concurrently on first
        boot is racy — gate behind a single migration step in production.
    """
    from backend import models  # noqa: F401 - register models on the metadata

    Base.metadata.create_all(bind=engine)


def get_db() -> Iterator[Session]:
    """
    FastAPI dependency that yields a database session and always closes it.

    Args:
        None.

    Yields:
        Session: An active SQLAlchemy session, closed when the request ends.
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
