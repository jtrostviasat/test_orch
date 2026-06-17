"""
Tests for the worker translation layer (UI variables -> framework config files).

Covers the Factory (get_runner), both concrete runners' rendered output, and the
input validation added for pytest's timeout. Config is written under a temp dir
so no real /tmp/test_runs is touched.
"""
from __future__ import annotations

import json
import os

import pytest

from worker import translation_layer
from worker.translation_layer import get_runner


@pytest.fixture
def runs_dir(tmp_path, monkeypatch):
    """Point the runner's config root at a per-test temp directory."""
    monkeypatch.setattr(translation_layer.settings, "test_runs_dir", str(tmp_path))
    return tmp_path


def test_pytest_runner_writes_config(runs_dir):
    runner = get_runner("pytest", "test-1", {
        "markers": ["smoke"], "target_dut": "dutA",
        "timeout_seconds": 300, "extra_args": ["-v"],
    })
    out_dir = runner.write_config()

    cfg_path = os.path.join(out_dir, "pytest_config.json")
    assert os.path.isfile(cfg_path)
    with open(cfg_path, encoding="utf-8") as fh:
        cfg = json.load(fh)
    assert cfg["markers"] == ["smoke"]
    assert cfg["target_dut"] == "dutA"
    assert cfg["timeout_seconds"] == 300
    assert cfg["extra_args"] == ["-v"]


def test_pytest_runner_applies_defaults(runs_dir):
    runner = get_runner("pytest", "test-2", {})
    with open(os.path.join(runner.write_config(), "pytest_config.json"), encoding="utf-8") as fh:
        cfg = json.load(fh)
    assert cfg["timeout_seconds"] == 600
    assert cfg["markers"] == []
    assert cfg["target_dut"] == ""


@pytest.mark.parametrize("bad", ["abc", "", "12x", None])
def test_pytest_runner_rejects_non_numeric_timeout(runs_dir, bad):
    runner = get_runner("pytest", "test-3", {"timeout_seconds": bad})
    with pytest.raises(ValueError):
        runner.write_config()


@pytest.mark.parametrize("bad", [0, -5])
def test_pytest_runner_rejects_nonpositive_timeout(runs_dir, bad):
    runner = get_runner("pytest", "test-4", {"timeout_seconds": bad})
    with pytest.raises(ValueError):
        runner.write_config()


def test_robot_runner_writes_args(runs_dir):
    runner = get_runner("robot", "test-5", {
        "target_dut": "dutB", "include_tags": ["tagA", "tagB"],
    })
    out_path = os.path.join(runner.write_config(), "robot.args")
    assert os.path.isfile(out_path)
    with open(out_path, encoding="utf-8") as fh:
        content = fh.read()
    assert "--variable DUT:dutB" in content
    assert "--include tagA" in content
    assert "--include tagB" in content


def test_get_runner_unknown_type_raises():
    with pytest.raises(KeyError):
        get_runner("nonsense", "test-6", {})


def test_get_runner_rejects_empty_runner_type():
    with pytest.raises((ValueError, TypeError)):
        get_runner("", "test-7", {})


def test_runner_rejects_non_mapping_variables():
    with pytest.raises(TypeError):
        get_runner("pytest", "test-8", ["not", "a", "mapping"])
