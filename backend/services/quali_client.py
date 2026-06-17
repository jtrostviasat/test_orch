"""
Quali CloudShell client.

Only the reservation-release call the cleanup/reaper paths need is implemented;
the real Quali API surface is much larger. Network/HTTP errors are surfaced to
the caller (releases are best-effort).
"""
from __future__ import annotations

import requests

from backend.config import get_settings
from shared.validation import require_non_empty_str

settings = get_settings()


class QualiClient:
    """Minimal client for ending Quali CloudShell reservations."""

    def __init__(self) -> None:
        """Capture the API base URL and bearer auth header from settings."""
        self._base = settings.quali_api_url.rstrip("/")
        self._headers = {"Authorization": f"Bearer {settings.quali_api_token}"}

    def release_reservation(self, reservation_id: str) -> bool:
        """
        Immediately release all assets held by a Quali reservation.

        Args:
            reservation_id: The Quali reservation identifier (non-empty).

        Returns:
            bool: ``True`` if the release request succeeded (HTTP 2xx).

        Raises:
            TypeError/ValueError: If ``reservation_id`` is missing/blank.
            requests.HTTPError: If Quali returns a non-2xx response.
        """
        require_non_empty_str(reservation_id, "reservation_id")
        url = f"{self._base}/reservations/{reservation_id}/end"
        resp = requests.post(url, headers=self._headers, timeout=15)
        resp.raise_for_status()
        return True