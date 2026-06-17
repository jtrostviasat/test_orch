"""
InfluxDB 2.x client for streaming test log lines into the time-series bucket and
reading them back for the history view.

Writes are synchronous for MVP simplicity; under very chatty logs consider
batched/async writes (see worker README).
"""
from __future__ import annotations

from influxdb_client import InfluxDBClient, Point, WritePrecision
from influxdb_client.client.write_api import SYNCHRONOUS

from backend.config import get_settings
from shared.validation import require_non_empty_str, require_positive_int

settings = get_settings()

MEASUREMENT = "test_log"


class InfluxLogClient:
    """Thin wrapper around the InfluxDB client for log line write/read."""

    def __init__(self) -> None:
        """Open the InfluxDB client and the synchronous write/query APIs."""
        self._client = InfluxDBClient(
            url=settings.influx_url, token=settings.influx_token, org=settings.influx_org
        )
        self._write_api = self._client.write_api(write_options=SYNCHRONOUS)
        self._query_api = self._client.query_api()

    def write_line(self, test_id: str, line_no: int, content: str) -> None:
        """
        Persist a single container log line to the time-series bucket.

        Args:
            test_id: Owning test identifier (non-empty); becomes an Influx tag.
            line_no: Zero-based line index (int >= 0); stored as a field.
            content: The raw log line text.

        Returns:
            None.

        Raises:
            TypeError: If ``test_id``/``content`` are wrong types.
            ValueError: If ``test_id`` is empty or ``line_no`` < 0.
        """
        require_non_empty_str(test_id, "test_id")
        if isinstance(line_no, bool) or not isinstance(line_no, int) or line_no < 0:
            raise ValueError(f"line_no must be an int >= 0, got {line_no!r}")
        if not isinstance(content, str):
            raise TypeError(f"content must be a str, got {type(content).__name__}")
        point = (
            Point(MEASUREMENT)
            .tag("test_id", test_id)
            .field("line_no", line_no)
            .field("content", content)
        )
        self._write_api.write(
            bucket=settings.influx_bucket, record=point, write_precision=WritePrecision.NS
        )

    def read_lines(self, test_id: str, limit: int = 1000) -> list[str]:
        """
        Fetch historical log lines for a test, ordered by line number.

        Args:
            test_id: Owning test identifier (non-empty).
            limit: Maximum number of lines to return (int > 0).

        Returns:
            list[str]: Log line contents, sorted by the stored ``line_no`` field
            (pivoted) to preserve exact emission order even when multiple lines
            share a timestamp.

        Raises:
            TypeError/ValueError: If ``test_id`` is invalid or ``limit`` <= 0.
        """
        require_non_empty_str(test_id, "test_id")
        require_positive_int(limit, "limit")
        flux = f'''
        from(bucket: "{settings.influx_bucket}")
          |> range(start: -30d)
          |> filter(fn: (r) => r._measurement == "{MEASUREMENT}")
          |> filter(fn: (r) => r.test_id == "{test_id}")
          |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
          |> sort(columns: ["line_no"])
          |> limit(n: {limit})
        '''
        tables = self._query_api.query(flux)
        return [r.values.get("content", "") for table in tables for r in table.records]

    def close(self) -> None:
        """
        Close the underlying InfluxDB client and its APIs.

        Args:
            None.

        Returns:
            None.
        """
        self._client.close()