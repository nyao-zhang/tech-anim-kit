# -*- coding: utf-8 -*-
"""批次 3 · P4.3：四端一致性契约与回归脚本的单元测试。

不依赖任何真实 DCC：全部用 tak.core.mocks 注入的假宿主模块，验证
「一份骨架 → 四个适配器 → 同一份判定」的框架侧不变量。
"""
from __future__ import annotations

import copy
import json
import os
import sys

import pytest

from tak.adapters import conformance, snapshot_utils as su
from tak.core.mocks import install_mock, uninstall_mock
from tak.core.mocks.scene import MockScene
from tak.core.runner import adapter_for

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
import validation.four_dcc_regression as regression  # noqa: E402

FIXTURE = os.path.join(ROOT, "fixtures", "good", "good_001.json")


@pytest.fixture
def good_snapshot():
    with open(FIXTURE, "r", encoding="utf-8") as fh:
        return json.load(fh)["snapshot"]


@pytest.fixture
def maya_scene(good_snapshot):
    handle = install_mock("maya", good_snapshot)
    try:
        yield handle
    finally:
        uninstall_mock()


# --------------------------------------------------------------------------- #
# 结构契约
# --------------------------------------------------------------------------- #
def test_good_fixture_conforms(good_snapshot):
    result = conformance.validate_snapshot(good_snapshot)
    assert result["ok"] is True
    assert result["joint_count"] == len(good_snapshot["joints"])
    assert result["contract"] == conformance.CONTRACT_VERSION


def test_missing_fields_are_reported(good_snapshot):
    broken = copy.deepcopy(good_snapshot)
    broken.pop("skin")
    broken["joints"]["root"].pop("matrix")
    result = conformance.validate_snapshot(broken)
    assert result["ok"] is False
    assert any("skin" in item for item in result["errors"])
    assert any("matrix" in item for item in result["errors"])


def test_bad_matrix_and_unknown_parent(good_snapshot):
    broken = copy.deepcopy(good_snapshot)
    broken["joints"]["root"]["matrix"] = [1, 2, 3]
    broken["joints"]["pelvis"]["parent"] = "not_there"
    result = conformance.validate_snapshot(broken)
    assert result["ok"] is False
    assert any("16" in item for item in result["errors"])
    assert any("不存在的关节" in item for item in result["errors"])


def test_invalid_dcc_and_empty_snapshot():
    assert conformance.validate_snapshot({})["ok"] is False
    assert conformance.validate_snapshot(None)["ok"] is False
    assert conformance.validate_snapshot(su.empty_snapshot("max", "tak"))["ok"] is True


# --------------------------------------------------------------------------- #
# 内容比对
# --------------------------------------------------------------------------- #
def test_compare_identical_snapshots(good_snapshot):
    left = su.normalize_snapshot(good_snapshot, "maya", "tak")
    right = su.normalize_snapshot(good_snapshot, "unreal", "tak")
    result = conformance.compare_snapshots(left, right)
    assert result["ok"] is True
    assert result["differences"] == []
    assert "metadata" in result["ignored"]


def test_compare_detects_unit_matrix_and_joint_changes(good_snapshot):
    left = su.normalize_snapshot(good_snapshot, "maya", "tak")
    right = copy.deepcopy(left)
    right["unit"] = "m"
    right["joints"]["root"]["matrix"][12] = 9.0
    right["joints"].pop("head")
    result = conformance.compare_snapshots(left, right)
    assert result["ok"] is False
    paths = {item["path"] for item in result["differences"]}
    assert "unit" in paths
    assert "joints.root.matrix" in paths
    assert any(path.startswith("joints.head") for path in paths)


def test_matrix_tolerance_and_ignore(good_snapshot):
    left = su.normalize_snapshot(good_snapshot, "maya", "tak")
    right = copy.deepcopy(left)
    right["joints"]["root"]["matrix"][12] = 0.00001
    assert conformance.compare_snapshots(left, right)["ok"] is True
    right["joints"]["root"]["matrix"][12] = 5.0
    assert conformance.compare_snapshots(
        left, right, ignore=("joints.root.matrix",))["ok"] is True


# --------------------------------------------------------------------------- #
# 适配器契约
# --------------------------------------------------------------------------- #
def test_check_adapter_with_maya_mock(maya_scene):
    adapter = adapter_for("maya")
    result = conformance.check_adapter(adapter)
    assert result["ok"] is True
    assert result["validation"]["joint_count"] == len(maya_scene.scene.joints)
    assert result["capability"]["missing_required"] == []


def test_capability_matrix_shape(maya_scene):
    matrix = conformance.capability_matrix({"maya": adapter_for("maya")})
    assert matrix["contract"] == conformance.CONTRACT_VERSION
    block = matrix["dccs"]["maya"]
    assert "scene.query" in block["capabilities"]
    assert isinstance(block["conformant"], bool)


def test_check_adapter_rejects_non_adapter():
    result = conformance.check_adapter(object())
    assert result["ok"] is False
    assert result["errors"]


# --------------------------------------------------------------------------- #
# 纯函数工具
# --------------------------------------------------------------------------- #
def test_snapshot_utils_helpers():
    assert su.floats([1, "2", 3], 3, [0, 0, 0]) == [1.0, 2.0, 3.0]
    assert su.floats("bad", 3, [1, 1, 1]) == [1.0, 1.0, 1.0]
    assert su.short_name("|group|ns:joint") == "ns:joint"
    assert su.namespace_of("ns:joint") == "ns"
    assert su.depth_of("c", {"c": "b", "b": "a", "a": None}) == 2
    assert su.safe("fallback", lambda: None) == "fallback"


def test_filter_root_and_write_sidecar(tmp_path, good_snapshot):
    snapshot = su.build_snapshot("max", "tak", {
        "root": {"parent": None, "matrix": su.IDENTITY_MATRIX},
        "pelvis": {"parent": "root", "matrix": su.IDENTITY_MATRIX},
        "head": {"parent": "pelvis", "matrix": su.IDENTITY_MATRIX},
        "other": {"parent": None, "matrix": su.IDENTITY_MATRIX},
    })
    filtered = su.filter_root(snapshot, "pelvis")
    assert set(filtered["joints"]) == {"pelvis", "head"}
    assert filtered["metadata"]["root"] == "pelvis"
    path = str(tmp_path / "sidecar.json")
    assert su.write_sidecar(path, snapshot) == path
    with open(path, "r", encoding="utf-8") as fh:
        assert json.load(fh)["dcc"] == "max"


def test_normalize_snapshot_forces_dcc(good_snapshot):
    normalized = su.normalize_snapshot(good_snapshot, "mobu", "tak")
    assert normalized["dcc"] == "mobu"
    assert conformance.validate_snapshot(normalized)["ok"] is True


# --------------------------------------------------------------------------- #
# 回归脚本（先用已就位的 maya / unreal 两端做端到端 smoke）
# --------------------------------------------------------------------------- #
def test_regression_collect_maya(good_snapshot):
    item = regression.collect("maya", good_snapshot)
    assert item["ok"] is True
    assert item["degraded"] is False
    assert item["detected"] == []
    assert item["adapter_name"] == "maya"


def test_regression_run_one_two_dccs():
    case = regression.run_one(FIXTURE, ("maya", "unreal"))
    assert case["ok"] is True
    assert set(case["dccs"]) == {"maya", "unreal"}
    assert case["dccs"]["maya"]["baseline_diff"]["ok"] is True
    assert case["dccs"]["unreal"]["cross_dcc_diff"]["ok"] is True


def test_regression_plugin_consistency():
    result = regression.plugin_consistency(("maya", "unreal"))
    assert result["ok"] is True
    assert all(item["ok"] for item in result["per_dcc"].values())


def test_regression_quick_summary_is_reproducible():
    first = regression.run(regression.fixture_files(True)[:2], ("maya", "unreal"))
    second = regression.run(regression.fixture_files(True)[:2], ("maya", "unreal"))
    assert first["ok"] is True and second["ok"] is True
    assert first["per_dcc"] == second["per_dcc"]
