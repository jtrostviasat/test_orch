"""Tests for shared.schemas.TestBundleRequest validation + round-trip."""
from __future__ import annotations

import pytest

from shared.schemas import TestBundleRequest, TestStatus


def _valid(**overrides):
    base = dict(
        test_id="t-1",
        user_id=7,
        runner_type="pytest",
        framework_image="img:1",
    )
    base.update(overrides)
    return base


def test_round_trip_preserves_fields():
    req = TestBundleRequest(**_valid(required_hw_tags=["A"], requires_emulation=True))
    again = TestBundleRequest.from_dict(req.to_dict())
    assert again == req


def test_rejects_empty_test_id():
    with pytest.raises(ValueError):
        TestBundleRequest(**_valid(test_id=""))


def test_rejects_non_positive_user_id():
    with pytest.raises(ValueError):
        TestBundleRequest(**_valid(user_id=0))


def test_rejects_bad_tag_list():
    with pytest.raises(TypeError):
        TestBundleRequest(**_valid(required_hw_tags=["A", 5]))


def test_from_dict_requires_keys():
    with pytest.raises(ValueError, match="missing required keys"):
        TestBundleRequest.from_dict({"test_id": "t", "user_id": 1})


def test_status_enum_values():
    assert TestStatus.PASSED.value == "PASSED"
    assert set(TestStatus) >= {
        TestStatus.PENDING,
        TestStatus.RUNNING,
        TestStatus.PASSED,
        TestStatus.FAILED,
    }