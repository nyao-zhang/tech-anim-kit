# -*- coding: utf-8 -*-
"""Rig Health Guard（P2.2）回归测试。

覆盖面
------
* manifest 合法性、四端发现、9 条 Maya 规则的注册与 dcc 约束；
* 20 好 + 20 坏 golden fixtures：不含插件专属字段时零误报、passed=True；
* metadata.health 缺失 / 类型错误时整组跳过；
* 每条专属规则在合成数据下的命中与不命中；
* AI 解释层：离线降级不抛异常、条数与 ERROR 一致、不修改 findings；
* hooks 三个纯函数、无 Maya 时 install 返回 False；
* ui_qt.show() 无 Qt 返回 None；JSON/MD/HTML 三种渲染。

全部离线，只使用 NullAdapter 与 fixtures JSON，不接触真实 DCC。
"""
from __future__ import annotations

import copy
import importlib.util
import json
import os

import pytest

from tak.adapters.base import NullAdapter
from tak.core import registry, runner
from tak.core.rules import Severity
from tak.core.rules.base import Finding
from tak.core.spec import load_spec

TOOL_ID = "rig_health_guard"
_RULE = TOOL_ID + "."
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_FIXTURES = os.path.join(_ROOT, "fixtures")
_PLUGIN_DIR = os.path.join(_ROOT, "tools", TOOL_ID)
_BLOCKING = ("error", "blocker")
_IDENTITY = [1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0,
             0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0]

RULE_IDS = (
    _RULE + "orphan_nodes",
    _RULE + "negative_scale",
    _RULE + "unfrozen_transform",
    _RULE + "namespace_pollution",
    _RULE + "duplicate_shape",
    _RULE + "skin_influence_over_limit",
    _RULE + "skin_weight_sum",
    _RULE + "legacy_reference",
    _RULE + "control_uncolored",
)


# --------------------------------------------------------------------------- #
# 工具函数
# --------------------------------------------------------------------------- #
def _fixture(folder, name):
    with open(os.path.join(_FIXTURES, folder, name), "r", encoding="utf-8") as fh:
        return json.load(fh)


def _all_fixtures(folder):
    path = os.path.join(_FIXTURES, folder)
    items = []
    for name in sorted(os.listdir(path)):
        if name.endswith(".json"):
            items.append(_fixture(folder, name))
    return items


def _base_joint(**overrides):
    data = {"parent": None, "depth": 0, "matrix": list(_IDENTITY),
            "scale": [1.0, 1.0, 1.0], "rotate": [0.0, 0.0, 0.0],
            "namespace": ""}
    data.update(overrides)
    return data


def _snapshot(health=None, joints=None, skin=None, metadata=None):
    """合成快照：默认干净，health 默认 {}（字段存在但为空）。"""
    payload = {
        "dcc": "maya",
        "version": "2023",
        "unit": "cm",
        "up_axis": "Y",
        "joints": joints if joints is not None else {"root": _base_joint()},
        "frame_range": [1.0, 120.0],
        "skin": skin if skin is not None else {
            "max_influences_found": 4,
            "weight_sum_deviation": 0.0,
            "deformers": ["skinCluster"],
        },
        "metadata": metadata if metadata is not None else {
            "seed": 1, "health": {} if health is None else health},
    }
    return payload


def _run(snapshot, **kwargs):
    return runner.run_tool(TOOL_ID, snapshot, dcc=kwargs.pop("dcc", "maya"),
                           adapter=kwargs.pop("adapter", NullAdapter()), **kwargs)


def _ids(result):
    return [item.rule_id for item in result.report.findings]


def _errors(result):
    return [item for item in result.report.findings
            if item.severity.value in _BLOCKING]


def _plugin():
    return runner.find_plugin(TOOL_ID)


def _load(submodule):
    return registry.load_plugin_module(_plugin(), submodule)


@pytest.fixture()
def core_module():
    return _load("core")


@pytest.fixture()
def hooks_module():
    return _load("hooks")


# --------------------------------------------------------------------------- #
# manifest / 发现 / 规则注册
# --------------------------------------------------------------------------- #
def test_manifest_is_valid_and_complete():
    with open(os.path.join(_PLUGIN_DIR, "manifest.json"), "r", encoding="utf-8") as fh:
        data = json.load(fh)
    assert registry.validate_manifest(data) == []
    assert data["id"] == TOOL_ID
    assert data["entry"] == TOOL_ID
    assert data["ui"] == "qt"
    assert data["permissions"] == ["scene.read"]
    assert data["name"] and data["description"]


def test_manifest_declares_four_dcc_and_min_versions():
    with open(os.path.join(_PLUGIN_DIR, "manifest.json"), "r", encoding="utf-8") as fh:
        data = json.load(fh)
    assert data["dcc"] == ["maya", "max", "mobu", "unreal"]
    assert data["min_version"] == {"maya": "2023", "max": "2023",
                                   "mobu": "2023", "unreal": "5.8"}


def test_plugin_discovered_for_all_supported_dcc():
    for dcc in ("maya", "max", "mobu", "unreal"):
        assert TOOL_ID in [item["id"] for item in runner.list_tools(dcc=dcc)]


def test_nine_maya_rules_registered_with_explicit_plugin_id():
    from tak.core.rules.engine import list_rules

    runner.load_tool_modules(_plugin())
    catalog = {item["id"]: item for item in list_rules("maya")
               if item["plugin_id"] == TOOL_ID}
    assert set(catalog) == set(RULE_IDS)
    assert len(RULE_IDS) >= 6
    for rule_id, item in catalog.items():
        assert item["dcc"] == ["maya"], rule_id
        assert item["description"]
        assert item["severity"] in {s.value for s in Severity}


def test_plugin_rules_do_not_leak_into_other_dcc():
    from tak.core.rules.engine import list_rules

    runner.load_tool_modules(_plugin())
    for dcc in ("max", "mobu", "unreal"):
        assert not [item for item in list_rules(dcc) if item["plugin_id"] == TOOL_ID]


def test_info_contract(core_module):
    info = core_module.info()
    assert info["id"] == TOOL_ID
    assert "scene.read" in info["capabilities"]


# --------------------------------------------------------------------------- #
# golden fixtures：无插件专属字段时必须零误报
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("name", ["good_001.json", "good_002.json",
                                  "bad_001.json", "bad_002.json"])
def test_golden_fixtures_pass_through_runner(name):
    folder = "good" if name.startswith("good") else "bad"
    result = _run(_fixture(folder, name)["snapshot"])
    assert result.passed is True
    assert result.report.findings == []
    assert result.report.metadata["health_section"]["present"] is False


def test_all_forty_golden_fixtures_produce_no_blocking_finding():
    for folder in ("good", "bad"):
        for fixture in _all_fixtures(folder):
            result = _run(fixture["snapshot"])
            assert _errors(result) == [], fixture["id"]
            assert result.passed is True, fixture["id"]


def test_missing_health_section_is_not_a_false_positive():
    snapshot = _snapshot()
    snapshot["joints"]["root"]["scale"] = [-1.0, 1.0, 1.0]
    snapshot["joints"]["root"]["namespace"] = "rig"
    snapshot["skin"]["max_influences_found"] = 12
    snapshot["skin"]["weight_sum_deviation"] = 0.5
    snapshot["metadata"] = {"seed": 3}  # 没有 health
    result = _run(snapshot)
    assert result.report.findings == []
    assert result.passed is True


@pytest.mark.parametrize("metadata", [
    {"health": []},
    {"health": "broken"},
    {"health": None},
    {"health": 42},
    {},
])
def test_non_dict_health_skips_whole_group(metadata):
    snapshot = _snapshot()
    snapshot["metadata"] = metadata
    snapshot["joints"]["root"]["scale"] = [-1.0, 1.0, 1.0]
    result = _run(snapshot)
    assert result.report.findings == []
    assert result.report.metadata["health_section"]["present"] is False


# --------------------------------------------------------------------------- #
# 单条规则：命中与不命中
# --------------------------------------------------------------------------- #
def test_orphan_nodes_hit_and_miss():
    hit = _run(_snapshot({"orphan_nodes": ["locator1", {"node": "groupId"}]}))
    assert _ids(hit) == [_RULE + "orphan_nodes"] * 2
    assert {item.node for item in hit.report.findings} == {"locator1", "groupId"}
    assert hit.report.findings[0].data["fix"]["op"] == "delete_node"
    miss = _run(_snapshot({}))
    assert _RULE + "orphan_nodes" not in _ids(miss)


def test_negative_scale_hit_and_miss():
    joints = {"root": _base_joint(), "arm_L": _base_joint(scale=[-1.0, 1.0, 1.0])}
    hit = _run(_snapshot({}, joints=joints))
    assert _RULE + "negative_scale" in _ids(hit)
    assert hit.passed is False  # ERROR 级
    miss = _run(_snapshot({}, joints={"root": _base_joint()}))
    assert _RULE + "negative_scale" not in _ids(miss)


def test_negative_scale_accepts_adapter_reported_nodes():
    hit = _run(_snapshot({"negative_scale": ["grp_mirror"]}))
    assert [item.node for item in hit.report.findings] == ["grp_mirror"]


def test_unfrozen_transform_hit_from_joints_and_miss():
    joints = {"root": _base_joint(rotate=[0.0, 45.0, 0.0])}
    hit = _run(_snapshot({}, joints=joints))
    assert _RULE + "unfrozen_transform" in _ids(hit)
    miss = _run(_snapshot({}, joints={"root": _base_joint()}))
    assert _RULE + "unfrozen_transform" not in _ids(miss)


def test_unfrozen_transform_explicit_field_is_authoritative():
    hit = _run(_snapshot({"unfrozen_transforms": ["spine_01"]}))
    assert [item.node for item in hit.report.findings] == ["spine_01"]
    # 显式给出空列表时，即使 joints 有脏变换也以适配器上报为准
    clean = _run(_snapshot({"unfrozen_transforms": []},
                           joints={"root": _base_joint(scale=[2.0, 1.0, 1.0])}))
    assert _RULE + "unfrozen_transform" not in _ids(clean)


def test_namespace_pollution_hit_and_miss():
    joints = {"root": _base_joint(namespace="rig")}
    hit = _run(_snapshot({}, joints=joints))
    assert _RULE + "namespace_pollution" in _ids(hit)
    colon = _run(_snapshot({}, joints={"rig:root": _base_joint()}))
    assert _RULE + "namespace_pollution" in _ids(colon)
    miss = _run(_snapshot({}, joints={"root": _base_joint()}))
    assert _RULE + "namespace_pollution" not in _ids(miss)


def test_duplicate_shape_hit_and_miss():
    hit = _run(_snapshot({"duplicate_shapes": [
        {"shape": "pCubeShape1", "nodes": ["pCube1", "pCube2"]}]}))
    assert _ids(hit) == [_RULE + "duplicate_shape"]
    assert hit.report.findings[0].data["nodes"] == ["pCube1", "pCube2"]
    miss = _run(_snapshot({"duplicate_shapes": []}))
    assert _RULE + "duplicate_shape" not in _ids(miss)


def test_skin_influence_over_limit_hit_and_miss():
    skin = {"max_influences_found": 9, "weight_sum_deviation": 0.0,
            "deformers": ["skinCluster"]}
    hit = _run(_snapshot({}, skin=skin))
    assert _RULE + "skin_influence_over_limit" in _ids(hit)
    assert hit.report.findings[0].data["max_influences"] == 4
    miss = _run(_snapshot({}))
    assert _RULE + "skin_influence_over_limit" not in _ids(miss)


def test_skin_influence_respects_custom_spec_and_health_override():
    skin = {"max_influences_found": 6, "weight_sum_deviation": 0.0,
            "deformers": []}
    spec = copy.deepcopy(load_spec())
    spec["skin"]["max_influences"] = 8
    assert _RULE + "skin_influence_over_limit" not in _ids(
        _run(_snapshot({}, skin=skin), spec=spec))
    assert _RULE + "skin_influence_over_limit" in _ids(
        _run(_snapshot({"max_influences": 4}, skin=skin), spec=spec))


def test_skin_weight_sum_hit_and_miss():
    skin = {"max_influences_found": 4, "weight_sum_deviation": 0.05,
            "deformers": ["skinCluster"]}
    hit = _run(_snapshot({}, skin=skin))
    assert _RULE + "skin_weight_sum" in _ids(hit)
    miss = _run(_snapshot({}))
    assert _RULE + "skin_weight_sum" not in _ids(miss)


def test_legacy_reference_hit_and_miss():
    hit = _run(_snapshot({"references": [
        {"name": "oldRig", "loaded": False}]}))
    assert _ids(hit) == [_RULE + "legacy_reference"]
    assert hit.report.findings[0].data["fix"]["op"] == "remove_reference"
    miss = _run(_snapshot({"references": []}))
    assert _RULE + "legacy_reference" not in _ids(miss)


def test_control_uncolored_hit_and_miss():
    hit = _run(_snapshot({"control_colors": {
        "ctrl_arm_L": None, "ctrl_arm_R": [0.0, 0.0, 1.0]}}))
    assert _ids(hit) == [_RULE + "control_uncolored"]
    assert hit.report.findings[0].severity is Severity.INFO
    list_hit = _run(_snapshot({"control_colors": ["ctrl_leg_L"]}))
    assert [item.node for item in list_hit.report.findings] == ["ctrl_leg_L"]
    miss = _run(_snapshot({"control_colors": {"ctrl_arm_L": [1.0, 0.0, 0.0]}}))
    assert _RULE + "control_uncolored" not in _ids(miss)


def test_clean_health_reports_nothing_for_every_rule():
    result = _run(_snapshot({"orphan_nodes": [], "duplicate_shapes": [],
                             "references": [], "control_colors": {},
                             "unfrozen_transforms": []}))
    assert result.report.findings == []
    assert result.passed is True


def test_rule_ids_filter_isolates_one_rule():
    snapshot = _snapshot({"orphan_nodes": ["locator1"],
                          "references": [{"name": "oldRig", "loaded": True}]})
    result = _run(snapshot, rule_ids=_RULE + "orphan_nodes")
    assert _ids(result) == [_RULE + "orphan_nodes"]


def test_rules_never_mutate_snapshot():
    snapshot = _snapshot({"orphan_nodes": ["locator1"],
                          "duplicate_shapes": [{"shape": "s", "nodes": ["a", "b"]}],
                          "references": [{"name": "ref", "loaded": False}],
                          "control_colors": {"ctrl": None}})
    snapshot["joints"]["root"]["scale"] = [-1.0, 1.0, 1.0]
    before = copy.deepcopy(snapshot)
    _run(snapshot)
    assert snapshot == before


# --------------------------------------------------------------------------- #
# AI 解释层
# --------------------------------------------------------------------------- #
def test_ai_explanations_count_matches_error_count():
    snapshot = _snapshot({})
    snapshot["joints"]["root"]["scale"] = [-1.0, 1.0, 1.0]
    snapshot["skin"]["weight_sum_deviation"] = 0.3
    result = _run(snapshot)
    assert len(_errors(result)) == 2
    explanations = result.report.metadata["ai_explanations"]
    assert len(explanations) == 2
    for item in explanations:
        assert set(["rule_id", "node", "explanation", "suggestion", "model"]) <= set(item)
        assert item["explanation"]
        assert "dry-run" in item["suggestion"]


def test_ai_layer_does_not_change_findings_or_verdict():
    snapshot = _snapshot({})
    snapshot["joints"]["root"]["scale"] = [-1.0, 1.0, 1.0]
    first = _run(snapshot)
    second = _run(copy.deepcopy(snapshot))
    assert _ids(first) == _ids(second)
    assert first.passed is second.passed is False
    messages = [item.message for item in first.report.findings]
    for explanation in first.report.metadata["ai_explanations"]:
        assert explanation["explanation"] not in messages
        assert explanation["rule_id"] in _ids(first)


def test_ai_layer_is_empty_when_no_blocking_findings():
    result = _run(_snapshot({"orphan_nodes": ["locator1"]}))
    assert result.passed is True
    assert result.report.metadata["ai_explanations"] == []
    assert result.report.metadata["ai"]["degraded"]


def test_ai_layer_uses_local_template_offline():
    snapshot = _snapshot({})
    snapshot["skin"]["max_influences_found"] = 10
    result = _run(snapshot)
    meta = result.report.metadata["ai"]
    assert meta["offline"] is True
    assert meta["model"] in ("mock", "offline", "local-template")
    assert result.report.metadata["ai_explanations"][0]["model"] == "local-template"


def test_ai_layer_degrades_when_provider_raises(monkeypatch, core_module):
    import tak.core.ai as ai_module

    def boom(**_kwargs):
        raise RuntimeError("no network")

    monkeypatch.setattr(ai_module, "get_provider", boom)
    snapshot = _snapshot({})
    snapshot["joints"]["root"]["scale"] = [-1.0, 1.0, 1.0]
    result = _run(snapshot)
    assert len(result.report.metadata["ai_explanations"]) == 1
    assert "no network" in result.report.metadata["ai"]["degraded"]
    assert result.passed is False


def test_ai_layer_covers_blocker_findings(core_module):
    finding = Finding(rule_id=_RULE + "negative_scale", severity=Severity.BLOCKER,
                      message="blocker 测试", node="arm_L",
                      fix_hint="归正缩放", data={"fix": {"op": "reset_scale"}})
    explanations, meta = core_module._build_explanations([finding])
    assert len(explanations) == 1
    assert explanations[0]["severity"] == "blocker"
    assert explanations[0]["node"] == "arm_L"
    assert "reset_scale" in explanations[0]["suggestion"]
    assert meta["offline"] is True


# --------------------------------------------------------------------------- #
# metadata / 渲染
# --------------------------------------------------------------------------- #
def test_metadata_contains_rules_duration_ai_and_history():
    result = _run(_snapshot({}))
    metadata = result.report.metadata
    assert metadata["rules_count"] == len(RULE_IDS)
    assert isinstance(metadata["duration_ms"], float)
    assert metadata["duration_ms"] >= 0.0
    assert isinstance(metadata["ai_explanations"], list)
    assert metadata["history"]["linked"] is False
    assert metadata["history"]["tickets"] == []
    assert metadata["history"]["note"]
    assert metadata["health_section"]["present"] is True


@pytest.mark.parametrize("fmt", ["json", "md", "html"])
def test_report_renders_three_formats(fmt):
    snapshot = _snapshot({"orphan_nodes": ["locator1"]})
    result = _run(snapshot)
    text = result.render(fmt)
    assert isinstance(text, str) and text
    if fmt == "json":
        assert TOOL_ID in text
        # ToolResult 的 JSON 汇总用 runner.metadata 覆盖报告 metadata，
        # 插件扩展字段以 Report 自身的 JSON 为准。
        assert "ai_explanations" in result.report.to_json()
    elif fmt == "md":
        assert "Rig Health Guard" in text
        assert _RULE + "orphan_nodes" in text
    else:
        assert "tak-panel" in text


# --------------------------------------------------------------------------- #
# hooks
# --------------------------------------------------------------------------- #
def test_hooks_envelope_passes_clean_scene(hooks_module):
    ctx = runner.ToolContext(dcc="maya", snapshot=_snapshot({}),
                             adapter=NullAdapter(), tool_id=TOOL_ID)
    for fn in (hooks_module.on_before_save, hooks_module.on_before_export):
        out = fn(ctx)
        assert set(out) == {"passed", "report", "block"}
        assert out["passed"] is True
        assert out["block"] is False
        assert isinstance(out["report"], dict)
        assert out["report"]["findings"] == []


def test_hooks_block_on_error(hooks_module):
    snapshot = _snapshot({})
    snapshot["joints"]["root"]["scale"] = [-1.0, 1.0, 1.0]
    ctx = runner.ToolContext(dcc="maya", snapshot=snapshot, adapter=NullAdapter(),
                             tool_id=TOOL_ID)
    out = hooks_module.on_before_save(ctx)
    assert out["passed"] is False
    assert out["block"] is True
    assert out["report"]["hook"] == "before_save"
    assert out["report"]["passed"] is False


def test_hooks_warning_does_not_block(hooks_module):
    ctx = runner.ToolContext(dcc="maya",
                             snapshot=_snapshot({"orphan_nodes": ["locator1"]}),
                             adapter=NullAdapter(), tool_id=TOOL_ID)
    out = hooks_module.on_before_export(ctx)
    assert out["passed"] is True
    assert out["block"] is False


def test_hooks_never_block_on_broken_context(hooks_module):
    out = hooks_module.on_before_save({"dcc": "blender"})
    assert out["passed"] is True
    assert out["block"] is False
    assert "error" in out["report"]


@pytest.mark.parametrize("fn_name", ["on_before_save", "on_before_export",
                                     "hook_status", "install_hooks",
                                     "uninstall_hooks"])
def test_hooks_public_api_exists(hooks_module, fn_name):
    assert callable(getattr(hooks_module, fn_name))


def test_install_hooks_without_maya_returns_false(hooks_module):
    if _maya_available():
        pytest.skip("本机安装 Maya，无 Maya 降级分支不适用")
    assert hooks_module.install_hooks() is False
    status = hooks_module.hook_status()
    assert status["installed"] is False
    assert "Maya" in status["reason"]
    assert status["only_checks"] is True


def test_uninstall_hooks_without_maya_is_safe(hooks_module):
    if _maya_available():
        pytest.skip("本机安装 Maya，无 Maya 降级分支不适用")
    assert isinstance(hooks_module.uninstall_hooks(), bool)
    assert hooks_module.hook_status()["installed"] is False


def _maya_available():
    try:
        return importlib.util.find_spec("maya") is not None
    except Exception:
        return False


# --------------------------------------------------------------------------- #
# UI
# --------------------------------------------------------------------------- #
def test_ui_show_returns_none_without_qt():
    from tak.ui.qt import HAS_QT

    ui = _load("ui_qt")
    if HAS_QT:
        pytest.skip("本机安装 Qt，无 Qt 降级分支不适用")
    assert ui.show() is None
    assert ui.show(dcc="maya") is None


def test_ui_module_imports_without_qt():
    ui = _load("ui_qt")
    assert callable(ui.show)
    assert ui.TOOL_ID == TOOL_ID
