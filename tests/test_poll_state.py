"""
Tests for the admin poll sentinel latch (_PollState) in backend.main.

These exercise the concurrency-safety guarantees directly: the completion
sentinel must latch exactly once even if the grace timer and a final straggler
reply race each other.
"""
from __future__ import annotations

import asyncio

import pytest

from backend.main import _PollState
from tests.conftest import FakeWebSocket


@pytest.mark.asyncio
async def test_on_reply_emits_final_when_all_respond():
    ws = FakeWebSocket()
    state = _PollState(ws, total=2)
    await state.on_reply()
    await state.on_reply()           # second reply completes the set
    assert len(ws.sent) == 1         # exactly one (final) sentinel
    assert "All 2" in ws.sent[0]


@pytest.mark.asyncio
async def test_timer_partial_then_no_double_emit_on_late_reply():
    ws = FakeWebSocket()
    state = _PollState(ws, total=2)
    await state.on_reply()                       # 1 of 2
    await state.emit_sentinel(final=False)       # grace timer fires → partial
    assert len(ws.sent) == 1
    assert "1 of 2" in ws.sent[0]
    # A late straggler arrives AFTER the partial sentinel was already latched.
    await state.on_reply()
    assert len(ws.sent) == 1                      # latched: no second emit


@pytest.mark.asyncio
async def test_concurrent_final_and_timer_latch_once():
    ws = FakeWebSocket()
    state = _PollState(ws, total=1)
    # Fire the grace timer and the completing reply concurrently.
    await asyncio.gather(state.emit_sentinel(final=False), state.on_reply())
    assert len(ws.sent) == 1                      # exactly one sentinel total