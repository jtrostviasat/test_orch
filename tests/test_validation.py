"""Tests for the shared.validation boundary guards."""
from __future__ import annotations

import pytest

from shared.validation import (
    require,
    require_keys,
    require_mapping,
    require_non_empty_str,
    require_non_negative_number,
    require_positive_int,
)


def test_require_passes_and_fails():
    require(True, "ok")  # no raise
    with pytest.raises(ValueError, match="boom"):
        require(False, "boom")


@pytest.mark.parametrize("bad", ["", "   ", "\n"])
def test_require_non_empty_str_rejects_blank(bad):
    with pytest.raises(ValueError):
        require_non_empty_str(bad, "field")


def test_require_non_empty_str_rejects_non_str():
    with pytest.raises(TypeError):
        require_non_empty_str(123, "field")


def test_require_non_empty_str_returns_value():
    assert require_non_empty_str("hi", "field") == "hi"


@pytest.mark.parametrize("bad", [0, -1, -100])
def test_require_positive_int_rejects_non_positive(bad):
    with pytest.raises(ValueError):
        require_positive_int(bad, "n")


def test_require_positive_int_rejects_bool():
    # bool is an int subclass; must be rejected explicitly.
    with pytest.raises(TypeError):
        require_positive_int(True, "n")


def test_require_non_negative_number_coerces_float():
    assert require_non_negative_number(3, "x") == 3.0
    assert isinstance(require_non_negative_number(3, "x"), float)


def test_require_non_negative_number_rejects_negative():
    with pytest.raises(ValueError):
        require_non_negative_number(-0.1, "x")


def test_require_mapping_and_keys():
    require_mapping({"a": 1}, "m")
    with pytest.raises(TypeError):
        require_mapping([], "m")
    require_keys({"a": 1, "b": 2}, ["a", "b"], "m")
    with pytest.raises(ValueError, match="missing required keys: c"):
        require_keys({"a": 1}, ["a", "c"], "m")