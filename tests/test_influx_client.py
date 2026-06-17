"""
Tests for InfluxLogClient.write_line input validation.

The validation guards run before any network write, so these assert the client
rejects malformed log points without ever contacting InfluxDB. Constructing the
client is offline (the SDK connects lazily on first write).
"""
from __future__ import annotations

import pytest

from backend.services.influx_client import InfluxLogClient


@pytest.fixture
def client():
    return InfluxLogClient()


def test_write_line_rejects_empty_test_id(client):
    with pytest.raises((ValueError, TypeError)):
        client.write_line("", 0, "hello")


def test_write_line_rejects_non_str_test_id(client):
    with pytest.raises(TypeError):
        client.write_line(123, 0, "hello")


def test_write_line_rejects_negative_line_no(client):
    with pytest.raises(ValueError):
        client.write_line("t-1", -1, "hello")


def test_write_line_rejects_bool_line_no(client):
    # bool is an int subclass but must not be accepted as a line number.
    with pytest.raises(ValueError):
        client.write_line("t-1", True, "hello")


def test_write_line_rejects_non_str_content(client):
    with pytest.raises(TypeError):
        client.write_line("t-1", 0, 123)
