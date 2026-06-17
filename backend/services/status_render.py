"""
Pure rendering helpers for admin host-status replies and the poll sentinel.

These functions return HTML fragment strings and are intentionally free of I/O
and side effects so they remain trivially unit-testable. Dynamic values are
HTML-escaped to prevent injection in the admin table.
"""
from __future__ import annotations

import json


def _escape(text: str) -> str:
    """
    HTML-escape the minimal set of characters for safe fragment embedding.

    Args:
        text: Raw text to escape.

    Returns:
        str: ``text`` with ``&``, ``<``, and ``>`` replaced by entities.
    """
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def render_status_row(body: str) -> str:
    """
    Render one host-status reply as an out-of-band table-row append fragment.

    Args:
        body: JSON string of a HostStats reply.

    Returns:
        str: An HTMX OOB ``<tbody>`` fragment appending one ``<tr>`` to
        ``#host-status-results``; an empty string if ``body`` is not valid JSON.
    """
    try:
        s = json.loads(body)
    except json.JSONDecodeError:
        return ""
    return (
        '<tbody id="host-status-results" hx-swap-oob="beforeend">'
        "<tr>"
        f"<td>{_escape(str(s.get('host_id', '?')))}</td>"
        f"<td>{s.get('cpu_percent', 0):.1f}</td>"
        f"<td>{s.get('ram_available_mb', 0):.1f} / {s.get('ram_total_mb', 0):.0f}</td>"
        f"<td>{s.get('disk_available_gb', 0):.2f} / {s.get('disk_total_gb', 0):.0f}</td>"
        f"<td>{s.get('active_users', 0)}</td>"
        "</tr></tbody>"
    )


def render_sentinel(received: int, total: int) -> str:
    """
    Render the soft "polling complete (N of M)" status line.

    Args:
        received: Number of host replies received so far.
        total: Number of hosts the poll was dispatched to.

    Returns:
        str: An HTMX OOB ``innerHTML`` fragment for ``#poll-sentinel`` whose
        text/color reflects complete (all responded), partial (some missing),
        or empty (no hosts) states. Defensive against ``received > total``.
    """
    if total == 0:
        msg, css = "No worker hosts registered to poll.", "sentinel-empty"
    elif received >= total:
        msg, css = f"All {total} runner(s) responded.", "sentinel-ok"
    else:
        missing = total - received
        msg = (
            f"Polling complete - {received} of {total} runner(s) responded "
            f"({missing} did not respond in time)."
        )
        css = "sentinel-warn"
    return (
        f'<div id="poll-sentinel" hx-swap-oob="innerHTML">'
        f'<span class="{css}">{msg}</span></div>'
    )