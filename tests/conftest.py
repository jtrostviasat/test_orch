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