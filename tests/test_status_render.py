"""Tests for the pure HTML-fragment renderers used by the admin poll."""
from __future__ import annotations

import json

from backend.services.status_render import render_sentinel, render_status_row


def test_render_status_row_happy_path():
    body = json.dumps({
        "host_id": "worker_1", "cpu_percent": 12.34,
        "ram_available_mb": 2048.0, "ram_total_mb": 8192.0,
        "disk_available_gb": 50.5, "disk_total_gb": 100.0, "active_users": 3,
    })
    html = render_status_row(body)
    assert 'id="host-status-results"' in html
    assert 'hx-swap-oob="beforeend"' in html
    assert "worker_1" in html
    assert "12.3" in html        # cpu formatted to 1 decimal
    assert "3" in html


def test_render_status_row_escapes_host_id():
    body = json.dumps({"host_id": "<script>", "cpu_percent": 0,
                       "ram_available_mb": 0, "ram_total_mb": 0,
                       "disk_available_gb": 0, "disk_total_gb": 0, "active_users": 0})
    html = render_status_row(body)
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


def test_render_status_row_invalid_json_returns_empty():
    assert render_status_row("not json{") == ""


def test_render_sentinel_all_responded():
    html = render_sentinel(received=3, total=3)
    assert "sentinel-ok" in html
    assert "All 3" in html
    assert 'id="poll-sentinel"' in html


def test_render_sentinel_partial():
    html = render_sentinel(received=1, total=3)
    assert "sentinel-warn" in html
    assert "1 of 3" in html
    assert "2 did not respond" in html


def test_render_sentinel_no_hosts():
    html = render_sentinel(received=0, total=0)
    assert "sentinel-empty" in html
    assert "No worker hosts" in html


def test_render_sentinel_defensive_when_received_exceeds_total():
    # Should never happen, but must not render a negative "missing" count.
    html = render_sentinel(received=5, total=3)
    assert "sentinel-ok" in html