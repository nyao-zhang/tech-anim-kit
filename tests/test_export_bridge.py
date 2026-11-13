# -*- coding: utf-8 -*-
"""Export Bridge（P2.3）回归测试。

覆盖面
------
* manifest 合法性、四端发现、2 条 Maya 导出规则的注册与 dcc 约束；
* ``verify_pair``：同快照零差异；单位 / 轴向 / 关节增删 / 重命名 / 绑定矩阵 /
  父级 / 帧范围各产生对应偏差类型并默认阻断；容差与 spec 策略可覆盖；
* 空 / None / 畸形快照不抛异常；
* ``pre_export`` + ``post_export`` 往返（tmp_path），sidecar 为严格 JSON；
* ``ue_import_hook`` 只返回 delegated 占位；
* ``run_tool`` 集成（before/after、sidecar、缺数据跳过、sidecar 不可读）；
* ``ui_qt.show()`` 无 Qt 返回 None。

全部离线：不接触真实 DCC，也不需要 Maya / UE。
"""
from __future__ import annotations

import copy
import json
import os
import sys

import pytest

from tak.adapters.base import NullAdapter
from tak.core import registry, runner
from tak.core.diff import SkeletonSnapshot
from tak.core.rules import RuleContext
from tak.core.rules.engine import list_rules
from tak.core.spec import load_spec

TOOL_ID = "export_bridge"
_RULE = TOOL_ID + "."
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_FIXTURES = os.path.join(_ROOT, "fixtures")
_PLUGIN_DIR = os.path.join(_ROOT, "tools", TOOL_ID)

RULE_IDS = (
    _RULE + "unit_axis_consistency",
    _RULE + "frame_range_consistency",
)
_BLOCKING_TYPES = {"unit", "up_axis", "joint_count", "joint_missing",
                   "joint_added", "joint_renamed", "bind_matrix", "parent",
                   "frame_range"}


# --------------------------------------------------------------------------- #
# 工具函数 / fixture
# --------------------------------------------------------------------------- #
def _fixture(folder, name):
    with open(os.path.join(_FIXTURES, folder, name), "r", encoding="utf-8") as fh:
        return json.load(fh)


def _good():
    return copy.deepcopy(_fixture("good", "good_001.json")["snapshot"])


def _types(result):
    return [item["type"] for item in result["diffs"]]


def _run(snapshot=None, options=None):
    return runner.run_tool(TOOL_ID, snapshot, dcc="maya", adapter=NullAdapter(),
                           options=dict(options or {}))


@pytest.fixture(scope="module")
def plugin():
    return runner.find_plugin(TOOL_ID)


@pytest.fixture(scope="module")
def core_module(plugin):
    runner.load_tool_modules(plugin)
    return sys.modules["tak_plugin_%s_core" % TOOL_ID]


@pytest.fixture(scope="module")
def rules_module(plugin):
    runner.load_tool_modules(plugin)
    return sys.modules["tak_plugin_%s_rules" % TOOL_ID]


def _ctx(snapshot, spec=None):
    return RuleContext(snapshot=snapshot, spec=spec or load_spec(), dcc="maya",
                       options={})


# --------------------------------------------------------------------------- #
# manifest / 发现 / 规则注册
# --------------------------------------------------------------------------- #
def test_manifest_is_valid():
    with open(os.path.join(_PLUGIN_DIR, "manifest.json"), "r", encoding="utf-8") as fh:
        data = json.load(fh)
    assert registry.validate_manifest(data) == []
    assert data["id"] == TOOL_ID
    assert data["dcc"] == ["maya", "max", "mobu", "unreal"]
    assert data["min_version"] == {"maya": "2023", "max": "2023",
                                   "mobu": "2023", "unreal": "5.8"}
    assert data["ui"] == "qt"
    assert data["permissions"] == ["scene.read", "export.fbx"]


def test_plugin_discovered_for_all_four_dcc():
    for dcc in ("maya", "max", "mobu", "unreal"):
        assert TOOL_ID in [item["id"] for item in runner.list_tools(dcc=dcc, include_internal=True)]


def test_two_maya_export_rules_registered(rules_module):
    catalog = {item["id"]: item for item in list_rules("maya")
               if item["plugin_id"] == TOOL_ID}
    assert set(catalog) == set(RULE_IDS)
    assert len(RULE_IDS) >= 2
    for rule_id, item in catalog.items():
        assert item["dcc"] == ["maya"], rule_id
        assert item["severity"] == "error"
        assert item["description"]


def test_plugin_rules_do_not_leak_into_other_dcc(rules_module):
    for dcc in ("max", "mobu", "unreal"):
        assert not [item for item in list_rules(dcc)
                    if item["plugin_id"] == TOOL_ID]


def test_info_contract(core_module):
    info = core_module.info()
    assert info["id"] == TOOL_ID
    assert "export.fbx" in info["capabilities"]


# --------------------------------------------------------------------------- #
# verify_pair：一致与各类偏差
# --------------------------------------------------------------------------- #
def test_identical_snapshots_pass(core_module):
    snapshot = _good()
    result = core_module.verify_pair(snapshot, copy.deepcopy(snapshot))
    assert result["passed"] is True
    assert result["diffs"] == []
    assert result["blocking"] == []
    assert "一致" in result["reason"]
    json.dumps(result, ensure_ascii=False)


@pytest.mark.parametrize("mutate,expected", [
    (lambda s: s.update({"unit": "m"}), {"unit"}),
    (lambda s: s.update({"up_axis": "Z"}), {"up_axis"}),
    (lambda s: s["joints"].pop("foot_R"), {"joint_count", "joint_missing"}),
    (lambda s: s["joints"].update({"extra_joint": {"parent": "root", "depth": 1}}),
     {"joint_count", "joint_added"}),
    (lambda s: s["joints"]["root"].update({
        "matrix": [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 12.5, 0, 0, 1]}),
     {"bind_matrix"}),
    (lambda s: s["joints"]["foot_L"].update({"parent": "root"}), {"parent"}),
    (lambda s: s.update({"frame_range": [1, 60]}), {"frame_range"}),
])
def test_each_deviation_type_is_classified_and_blocking(core_module, mutate, expected):
    before = _good()
    after = copy.deepcopy(before)
    mutate(after)
    result = core_module.verify_pair(before, after)
    assert set(_types(result)) == expected
    assert set(_types(result)) <= _BLOCKING_TYPES
    assert result["passed"] is False
    assert expected == {item["type"] for item in result["blocking"]}
    assert all(item["blocking"] for item in result["blocking"])
    assert all(item["message"] for item in result["diffs"])


def test_renamed_joint_is_detected(core_module):
    before = _good()
    after = copy.deepcopy(before)
    after["joints"]["hand_Lx"] = after["joints"].pop("hand_L")
    result = core_module.verify_pair(before, after)
    assert _types(result) == ["joint_renamed"]
    assert result["diffs"][0]["before"] == "hand_L"
    assert result["diffs"][0]["after"] == "hand_Lx"
    assert result["passed"] is False


def test_matrix_tolerance_controls_bind_matrix_diff(core_module):
    before = _good()
    after = copy.deepcopy(before)
    after["joints"]["root"]["matrix"] = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0,
                                         0.00001, 0, 0, 1]
    assert core_module.verify_pair(before, after)["diffs"] == []
    strict = core_module.verify_pair(before, after, matrix_tolerance=0.0)
    assert _types(strict) == ["bind_matrix"]


def test_spec_can_ignore_or_soften_fields(core_module):
    spec = load_spec()
    spec["export"]["fbx"]["verify"] = {"ignore": ["frame_range"]}
    before = _good()
    after = copy.deepcopy(before)
    after["frame_range"] = [1, 60]
    result = core_module.verify_pair(before, after, spec)
    assert result["passed"] is True
    assert result["diffs"] == []
    assert result["ignored_fields"] == ["frame_range"]

    spec["export"]["fbx"]["verify"] = {"blocking": ["unit"]}
    after = copy.deepcopy(before)
    after["up_axis"] = "Z"
    softened = core_module.verify_pair(before, after, spec)
    assert softened["passed"] is True
    assert softened["diffs"][0]["blocking"] is False
    assert softened["diffs"][0]["type"] == "up_axis"


def test_spec_matrix_tolerance_is_used_when_parameter_is_none(core_module):
    spec = load_spec()
    spec["export"]["fbx"]["verify"] = {"matrix_tolerance": 0.0}
    before = _good()
    after = copy.deepcopy(before)
    after["joints"]["root"]["matrix"] = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0,
                                         0.00001, 0, 0, 1]
    result = core_module.verify_pair(before, after, spec, matrix_tolerance=None)
    assert _types(result) == ["bind_matrix"]


def test_empty_and_none_snapshots_are_robust(core_module):
    assert core_module.verify_pair(None, None)["passed"] is True
    assert core_module.verify_pair({}, None)["passed"] is True
    populated = core_module.verify_pair(None, _good())
    assert populated["passed"] is False
    assert "joint_added" in _types(populated)
    with pytest.raises(ValueError):
        core_module.verify_pair(_good(), _good(), matrix_tolerance=-1.0)


def test_verify_accepts_snapshot_objects(core_module):
    snapshot = SkeletonSnapshot.from_dict(_good())
    assert core_module.verify_pair(snapshot, snapshot)["passed"] is True
    after = SkeletonSnapshot.from_dict(_good())
    after.unit = "m"
    assert _types(core_module.verify_pair(snapshot, after)) == ["unit"]


# --------------------------------------------------------------------------- #
# sidecar 往返
# --------------------------------------------------------------------------- #
def test_pre_export_writes_strict_json_sidecar(core_module, tmp_path):
    target = tmp_path / "nested" / "rig.tak_snapshot.json"
    assert not target.parent.exists()
    snapshot = _good()
    result = core_module.pre_export(snapshot, str(target))
    assert result["written"] is True
    assert result["path"] == str(target)
    assert target.is_file()
    with open(str(target), "r", encoding="utf-8") as fh:
        payload = json.load(fh)
    assert payload["tool"] == TOOL_ID
    assert payload["sidecar_version"] == 1
    assert set(payload["snapshot"]) >= {"joints", "skin", "frame_range", "unit"}
    assert payload["snapshot"] == core_module.normalize_snapshot(snapshot)


def test_pre_export_handles_none_snapshot(core_module, tmp_path):
    target = tmp_path / "empty.tak_snapshot.json"
    result = core_module.pre_export(None, str(target))
    assert result["before"]["joints"] == {}
    assert json.loads(target.read_text(encoding="utf-8"))["snapshot"]["unit"] == "cm"


def test_pre_post_export_roundtrip_passes(core_module, tmp_path):
    snapshot = _good()
    sidecar = core_module.pre_export(snapshot, str(tmp_path / "rig.tak_snapshot.json"))
    result = core_module.post_export(sidecar["path"], _good())
    assert result["readable"] is True
    assert result["passed"] is True
    assert result["sidecar"] == sidecar["path"]
    assert [item["rule_id"] for item in result["findings"]] == [_RULE + "pair_match"]
    assert result["report"]["passed"] is True
    assert result["report"]["counts"]["info"] == 1
    json.dumps(result, ensure_ascii=False)


def test_post_export_mismatch_blocks(core_module, tmp_path):
    sidecar = core_module.pre_export(_good(), str(tmp_path / "rig.tak_snapshot.json"))
    after = _good()
    after["unit"] = "m"
    result = core_module.post_export(sidecar["path"], after)
    assert result["passed"] is False
    assert result["report"]["counts"]["blocker"] == 1
    finding = result["findings"][0]
    assert finding["rule_id"] == _RULE + "blocking_diff"
    assert finding["severity"] == "blocker"
    assert finding["data"]["field"] == "unit"


@pytest.mark.parametrize("content", [None, "{not json", "[]"])
def test_post_export_unreadable_sidecar(core_module, tmp_path, content):
    path = tmp_path / "broken.tak_snapshot.json"
    if content is not None:
        path.write_text(content, encoding="utf-8")
    result = core_module.post_export(str(path), _good())
    assert result["readable"] is False
    assert result["passed"] is False
    assert result["findings"][0]["rule_id"] == _RULE + "sidecar_unreadable"
    assert result["findings"][0]["severity"] == "error"
    assert "sidecar" in result["reason"]


# --------------------------------------------------------------------------- #
# UE 交接占位与 sidecar 约定
# --------------------------------------------------------------------------- #
def test_ue_import_hook_is_delegated_only(core_module):
    hook = core_module.ue_import_hook("/tmp/rig.tak_snapshot.json")
    assert hook["status"] == "delegated"
    assert hook["target"] == "ue_import_guard"
    assert hook["implemented"] is False
    assert hook["sidecar"] == {"path": "/tmp/rig.tak_snapshot.json"}
    assert "P3.2" in hook["note"]
    json.dumps(hook, ensure_ascii=False)


def test_sidecar_contract_is_documented(core_module):
    contract = core_module.sidecar_contract()
    assert contract["tool"] == TOOL_ID
    assert contract["suffix"].endswith(".json")
    assert contract["producer"].startswith("pre_export(")
    assert contract["consumer"].startswith("post_export(")
    assert "ue_import_guard" in contract["ue_handoff"]


# --------------------------------------------------------------------------- #
# run_tool 集成
# --------------------------------------------------------------------------- #
def test_run_tool_with_before_after_options():
    before = _good()
    after = copy.deepcopy(before)
    after["up_axis"] = "Z"
    result = _run(before, options={"before": before, "after": after})
    assert result.passed is False
    assert [item.severity.value for item in result.report.findings] == ["blocker"]
    verify = result.report.metadata["verify"]
    assert verify["passed"] is False
    assert _types(verify) == ["up_axis"]
    assert result.report.metadata["ue_import"]["status"] == "delegated"
    assert result.report.metadata["sidecar"]["tool"] == TOOL_ID
    json.dumps(result.to_dict(), ensure_ascii=False)


def test_run_tool_with_sidecar_option(tmp_path):
    sidecar = _plugin_pre_export(tmp_path)
    result = _run(_good(), options={"sidecar": sidecar["path"]})
    assert result.passed is True
    assert result.report.metadata["verify"]["readable"] is True


def test_run_tool_with_file_paths(tmp_path):
    before_path = tmp_path / "before.json"
    after_path = tmp_path / "after.json"
    before_path.write_text(json.dumps(_good()), encoding="utf-8")
    after = _good()
    after["frame_range"] = [1, 60]
    after_path.write_text(json.dumps(after), encoding="utf-8")
    result = _run(None, options={"before_path": str(before_path),
                                 "after_path": str(after_path)})
    assert result.passed is False
    assert _types(result.report.metadata["verify"]) == ["frame_range"]


def test_run_tool_without_pairs_is_informational():
    result = _run(_good())
    assert result.passed is True
    assert [item.rule_id for item in result.report.findings] == [_RULE + "verify_skipped"]
    assert result.report.metadata["verify"]["skipped"] is True


def test_run_tool_unreadable_sidecar_blocks(tmp_path):
    result = _run(_good(), options={"sidecar": str(tmp_path / "missing.json")})
    assert result.passed is False
    assert [item.rule_id for item in result.report.findings] == [
        _RULE + "sidecar_unreadable"]


def _plugin_pre_export(tmp_path):
    module = registry.load_plugin_module(runner.find_plugin(TOOL_ID), "core")
    return module.pre_export(_good(), str(tmp_path / "rig.tak_snapshot.json"))


# --------------------------------------------------------------------------- #
# 规则逐条验证
# --------------------------------------------------------------------------- #
def test_unit_axis_rule_hits_and_skips_missing_fields(rules_module):
    snapshot = _good()
    snapshot["unit"] = "m"
    snapshot["up_axis"] = "Z"
    findings = rules_module.unit_axis_consistency(_ctx(snapshot))
    assert [item.data["field"] for item in findings] == ["unit", "up_axis"]
    assert all(item.severity.value == "error" for item in findings)
    assert findings[0].data["fix"]["op"] == "set_scene_unit"

    assert rules_module.unit_axis_consistency(_ctx({"joints": {}})) == []
    spec = load_spec()
    spec["export"]["fbx"].pop("unit")
    spec["export"]["fbx"].pop("up_axis")
    assert rules_module.unit_axis_consistency(_ctx(snapshot, spec)) == []


def test_frame_range_rule_hits_and_skips_missing_fields(rules_module):
    snapshot = _good()
    snapshot["frame_range"] = [1, 60]
    findings = rules_module.frame_range_consistency(_ctx(snapshot))
    assert len(findings) == 1
    assert findings[0].data["fix"]["params"]["frame_range"] == [1, 120]

    assert rules_module.frame_range_consistency(_ctx({"joints": {}})) == []
    snapshot["frame_range"] = [1]
    assert rules_module.frame_range_consistency(_ctx(snapshot)) == []


# --------------------------------------------------------------------------- #
# UI
# --------------------------------------------------------------------------- #
def test_ui_degrades_without_qt(plugin):
    ui = registry.load_plugin_module(plugin, "ui_qt")
    assert callable(ui.show)
    assert ui.show(dcc="maya") is None
