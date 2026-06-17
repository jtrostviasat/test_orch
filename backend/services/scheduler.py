"""
Filter-and-Rank worker selection engine.

Two-phase placement:
  1. FILTER  - drop hosts that are offline, at capacity, lack required hardware
               tags, or can't provide emulation when required.
  2. RANK    - score survivors by current workload (lower is better) and pick
               the lightest-loaded one (ties broken by host_id for determinism).

`dispatch` is the authoritative path: it locks rows FOR UPDATE and reserves a
container slot before sending the Celery task to the chosen host's queue.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.models import WorkerHost
from shared.schemas import TestBundleRequest
from shared.validation import require_non_empty_str


def queue_name_for(host_id: str) -> str:
    """
    Build the dedicated Celery queue name for a host.

    Args:
        host_id: Unique worker host identifier (non-empty).

    Returns:
        str: ``"queue_<host_id>"``.

    Raises:
        TypeError/ValueError: If ``host_id`` is missing or blank.
    """
    require_non_empty_str(host_id, "host_id")
    queue = f"queue_{host_id}"
    assert queue.startswith("queue_"), "queue name must be prefixed 'queue_'"
    return queue


@dataclass(frozen=True)
class RankedHost:
    """A scheduler-selected host paired with its computed workload score."""

    host_id: str
    score: float


def _workload_score(host: WorkerHost) -> float:
    """
    Compute a host's workload score (lower is better).

    Args:
        host: The worker host row to score.

    Returns:
        float: ``active_containers * 20 + cpu_utilization``.

    Raises:
        AssertionError: If the host carries negative counts/utilization (an
            internal invariant; stripped under ``python -O``).
    """
    assert host.active_containers >= 0, "active_containers must be >= 0"
    assert host.cpu_utilization >= 0, "cpu_utilization must be >= 0"
    score = (host.active_containers * 20) + host.cpu_utilization
    assert score >= 0, "workload score must be non-negative"
    return score


def _passes_filter(host: WorkerHost, bundle: TestBundleRequest) -> bool:
    """
    Decide whether a host survives the filtering phase for a bundle.

    Args:
        host: Candidate worker host.
        bundle: The job's requirements (tags, emulation).

    Returns:
        bool: ``True`` if the host is online, has spare container capacity,
        provides emulation when required, and carries all required hardware
        tags; otherwise ``False``.
    """
    if not host.is_online:
        return False
    if host.active_containers >= host.max_containers:
        return False
    if bundle.requires_emulation and not host.supports_emulation:
        return False
    required = set(bundle.required_hw_tags)
    if required and not required.issubset(host.tag_set()):
        return False
    return True


def select_host(db: Session, bundle: TestBundleRequest) -> Optional[RankedHost]:
    """
    Run the read-only filter+rank and return the best host (no side effects).

    Args:
        db: Active SQLAlchemy session.
        bundle: The validated bundle to place.

    Returns:
        Optional[RankedHost]: The lowest-scoring qualified host (ties broken by
        ``host_id``), or ``None`` if no host qualifies.

    Raises:
        TypeError: If ``bundle`` is not a ``TestBundleRequest``.

    Note:
        This performs no locking. Use :func:`dispatch` for authoritative placement.
    """
    if not isinstance(bundle, TestBundleRequest):
        raise TypeError("bundle must be a TestBundleRequest")
    hosts = db.query(WorkerHost).all()
    survivors = [h for h in hosts if _passes_filter(h, bundle)]
    if not survivors:
        return None
    best = min(survivors, key=lambda h: (_workload_score(h), h.host_id))
    result = RankedHost(host_id=best.host_id, score=_workload_score(best))
    assert result.host_id in {h.host_id for h in survivors}, "selected host not among survivors"
    return result


def dispatch(db: Session, bundle: TestBundleRequest) -> Optional[str]:
    """
    Atomically select a host, reserve a slot, and dispatch the Celery task.

    Locks candidate rows ``FOR UPDATE`` so concurrent dispatches serialize on the
    inventory. The Celery ``apply_async`` is attempted AFTER the optimistic slot
    increment is committed; if dispatch raises, the reservation is rolled back so
    the slot is not leaked until the next heartbeat.

    Args:
        db: Active SQLAlchemy session.
        bundle: The validated bundle to place and run.

    Returns:
        Optional[str]: The chosen ``host_id``, or ``None`` if nothing qualified.

    Raises:
        TypeError: If ``bundle`` is not a ``TestBundleRequest``.
        Exception: Re-raises any error from ``apply_async`` after rolling back
            the optimistic reservation.
    """
    if not isinstance(bundle, TestBundleRequest):
        raise TypeError("bundle must be a TestBundleRequest")

    hosts = db.execute(select(WorkerHost).with_for_update()).scalars().all()
    survivors = [h for h in hosts if _passes_filter(h, bundle)]
    if not survivors:
        db.rollback()
        return None

    chosen = min(survivors, key=lambda h: (_workload_score(h), h.host_id))
    target_queue = queue_name_for(chosen.host_id)

    # Enqueue by name via the backend's thin Celery client so we never import the
    # worker package (its Docker/psutil deps aren't in the backend image).
    from backend.celery_client import celery_client

    # Reserve optimistically, then dispatch. If dispatch fails, undo the reserve
    # so we don't leak capacity until the next heartbeat reconciles it.
    chosen.active_containers += 1
    try:
        db.commit()
        celery_client.send_task(
            "worker.run_test_bundle", args=[bundle.to_dict()], queue=target_queue
        )
    except Exception:
        db.rollback()
        fresh = db.get(WorkerHost, chosen.id)
        if fresh is not None and fresh.active_containers > 0:
            fresh.active_containers -= 1
            db.commit()
        raise

    assert chosen.host_id, "dispatched host_id must be truthy"
    return chosen.host_id