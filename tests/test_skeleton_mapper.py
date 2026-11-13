# -*- coding: utf-8 -*-
"""P5.2 Skeleton Mapper 离线回归测试。

覆盖命名归一化（UE / Mixamo / Biped）、语义词典、确定性候选、校验、AI 文本
容错解析、离线可复现、AI 只补空位、冲突不自动裁决、mapping asset v1.0 基线
消费与往返、插件发现、Report / UI 渲染与主题约束，以及 Python 3.9 语法和
tak/core DCC 隔离静态检查。全部使用纯数据与 NullAdapter，不接触真实 DCC 或网络。
"""
from __future__ import annotations

import ast
import json
import os
import subprocess
import sys

import pytest

from tak.adapters.base import NullAdapter
from tak.core import registry, runner
from tak.core.mapping import (
    SEMANTIC_GROUPS,
    confidence_of,
    deterministic_pairs,
    load_mapping,
    map_skeleton,
    merge_candidates,
    normalize_bone,
    parse_ai_candidates,
    semantic_of,
    validate_mapping,
    write_mapping,
)
from tak.core.mapping import semantics
from tak.core.report import Report
from tak.core.rules import Severity

TOOL_ID = "skeleton_mapper"
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load(submodule):
    return registry.load_plugin_module(runner.find_plugin(TOOL_ID), submodule)


def _ik_mapping():
    return registry.load_plugin_module(
        runner.find_plugin("ik_retarget_guard"), "mapping")


class _FakeProvider:
    """返回固定 JSON 的假 provider，用于验证 AI 候选只补空位。"""

    model = "fake-json"

    def __init__(self, entries):
        self._entries = entries

    def chat(self, messages, **kwargs):
        return json.dumps({"entries": self._entries}, ensure_ascii=False)


def _ctx(**kwargs):
    return runner.ToolContext(dcc=kwargs.pop("dcc", "maya"), **kwargs)


# --------------------------------------------------------------------------- #
# 1. 命名归一化（UE / Mixamo / 3ds Max Biped）
# --------------------------------------------------------------------------- #
def test_normalize_bone_ue_mixamo_biped_converge():
    names = ["upperarm_L", "LeftUpArm", "Bip01 L UpperArm",
             "mixamorig:LeftUpArm"]
    canonical = {normalize_bone(name) for name in names}
    assert canonical == {"upperarm_left"}


def test_normalize_bone_prefixes_and_side():
    assert normalize_bone("VAL:root") == "root"
    assert normalize_bone("Bip01 Pelvis") == "pelvis"
    assert normalize_bone("mixamorig:Hips") == "pelvis"
    assert normalize_bone("upperarm_R") == "upperarm_right"
    assert normalize_bone("RightUpLeg") == "thigh_right"
    assert normalize_bone("Armature|spine_01") == "spine_01"


def test_normalize_bone_invalid_input():
    assert normalize_bone(None) == ""
    assert normalize_bone("") == ""
    assert normalize_bone("   ") == ""
    assert normalize_bone(123) == ""


# --------------------------------------------------------------------------- #
# 2. 语义词典与评分
# --------------------------------------------------------------------------- #
def test_semantic_groups_cover_required():
    required = {"root", "pelvis", "spine", "chest", "neck", "head",
                "clavicle", "arm", "forearm", "hand", "leg", "calf",
                "foot", "toe", "finger"}
    assert required <= set(SEMANTIC_GROUPS)
    assert all(isinstance(group, dict) and group
               for group in SEMANTIC_GROUPS.values())


def test_semantic_of_common_names():
    assert semantic_of("mixamorig:Hips") == "pelvis"
    assert semantic_of("Bip01 L UpperArm") == "arm"
    assert semantic_of("LeftForeArm") == "forearm"
    assert semantic_of("VAL:root") == "root"
    assert semantic_of("index_01_L") == "finger"
    assert semantic_of("totally_unknown_bone") == ""


def test_confidence_of_is_deterministic_in_range():
    assert confidence_of("root", "root") == 1.0
    assert confidence_of("upperarm_L", "LeftUpArm") == 0.95
    assert confidence_of("spine_01", "spine_02") == 0.55
    assert confidence_of("root", "pelvis") == 0.0
    assert confidence_of("ghost", "root") == 0.0
    for left, right in [("root", "root"), ("root", "pelvis")]:
        assert 0.0 <= confidence_of(left, right) <= 1.0


# --------------------------------------------------------------------------- #
# 3. 确定性候选
# --------------------------------------------------------------------------- #
def test_deterministic_pairs_covers_root_pelvis():
    pairs = deterministic_pairs(["root", "pelvis", "head"],
                               ["root", "pelvis", "head"])
    by_pair = {(item["source"], item["target"]): item for item in pairs}
    assert by_pair[("root", "root")]["semantic"] == "root"
    assert by_pair[("pelvis", "pelvis")]["semantic"] == "pelvis"
    assert all(item["status"] == "auto" for item in pairs)
    assert all(item["origin"] == "dictionary" for item in pairs)
    assert all("semantic" in item and "confidence" in item for item in pairs)


def test_deterministic_pairs_reproducible_regardless_of_input_order():
    first = deterministic_pairs(["root", "upperarm_L", "pelvis"],
                               ["root", "LeftUpArm", "pelvis"])
    second = deterministic_pairs({"pelvis", "root", "upperarm_L"},
                                 ("LeftUpArm", "root", "pelvis"))
    assert first == second


# --------------------------------------------------------------------------- #
# 4. 确定性校验
# --------------------------------------------------------------------------- #
def test_validate_mapping_detects_missing_bones():
    entries = [{"source": "ghost", "target": "root", "confidence": 0.9}]
    result = validate_mapping(entries, ["root"], ["root"])
    assert result["ok"] is False
    assert any("不存在" in item for item in result["errors"])
    assert result["valid_entries"] == []


def test_validate_mapping_detects_duplicate_target():
    entries = [
        {"source": "upperarm_L", "target": "upperarm_l",
         "confidence": 0.9, "semantic": "arm"},
        {"source": "LeftUpArm", "target": "upperarm_l",
         "confidence": 0.9, "semantic": "arm"},
    ]
    result = validate_mapping(entries, ["upperarm_L", "LeftUpArm"],
                              ["upperarm_l"])
    assert any(item["type"] == "duplicate_target" for item in result["conflicts"])
    assert all(item["target"] != "upperarm_l" for item in result["valid_entries"])


def test_validate_mapping_detects_invalid_confidence():
    entries = [
        {"source": "root", "target": "root", "confidence": 1.5},
        {"source": "pelvis", "target": "pelvis", "confidence": "high"},
        {"source": "head", "target": "head", "confidence": float("nan")},
    ]
    bones = ["root", "pelvis", "head"]
    result = validate_mapping(entries, bones, bones)
    assert result["ok"] is False
    assert len(result["errors"]) >= 3
    assert result["stats"]["errors"] >= 3


def test_validate_mapping_warns_on_required_chain_gap():
    result = validate_mapping([], ["root", "pelvis"], ["root", "pelvis"])
    assert any("必填链" in item for item in result["warnings"])
    assert result["stats"]["covered_semantics"] == []


# --------------------------------------------------------------------------- #
# 5. AI 文本容错解析
# --------------------------------------------------------------------------- #
def test_parse_ai_candidates_three_formats():
    assert len(parse_ai_candidates(
        '```json\n[{"source":"a","target":"b"}]\n```')) == 1
    assert len(parse_ai_candidates(
        '[{"source":"a","target":"b"}]')) == 1
    assert len(parse_ai_candidates(
        '{"entries":[{"source":"a","target":"b"}]}')) == 1


def test_parse_ai_candidates_invalid_returns_empty_with_error():
    assert parse_ai_candidates("totally not json") == []
    assert parse_ai_candidates(None) == []
    assert parse_ai_candidates("") == []
    assert semantics.LAST_PARSE_ERROR


def test_parse_ai_candidates_never_raises():
    for value in [None, 123, [], {}, "```", "[]", "{}", "[1,2,3]",
                  "[mock] 已收到 2 条消息", object()]:
        assert isinstance(parse_ai_candidates(value), list)


# --------------------------------------------------------------------------- #
# 6. map_skeleton：可复现 / AI / 冲突 / 基线
# --------------------------------------------------------------------------- #
def test_map_skeleton_offline_is_reproducible():
    source = ["root", "pelvis", "upperarm_L", "lowerarm_L", "hand_L"]
    target = ["root", "pelvis", "LeftUpArm", "LeftForeArm", "LeftHand"]
    first = map_skeleton(source, target)
    second = map_skeleton(list(reversed(source)), tuple(target))
    assert first == second
    assert first["version"] == "1.0"
    assert first["metadata"]["consumers"] == [
        "P5.2 Skeleton Mapper", "UE IK Retargeter"]
    assert "handoff" in first["metadata"]


def test_map_skeleton_offline_ai_degrades_gracefully():
    result = map_skeleton(["root", "pelvis", "upperarm_L"],
                          ["root", "pelvis", "LeftUpArm"],
                          options={"use_ai": True})
    assert result["metadata"]["ai"]["degraded"] is True
    assert any("降级" in item for item in result["warnings"])
    assert len(result["entries"]) == 3
    assert result["metadata"]["offline"] is True


def test_ai_candidates_do_not_override_dictionary():
    provider = _FakeProvider([
        {"source": "upperarm_L", "target": "WRONG_TARGET", "confidence": 0.99},
    ])
    result = map_skeleton(
        ["root", "pelvis", "upperarm_L"],
        ["root", "pelvis", "LeftUpArm", "WRONG_TARGET"],
        options={"use_ai": True}, provider=provider)
    assert any(item["source"] == "upperarm_L"
               and item["target"] == "LeftUpArm" for item in result["entries"])
    assert not any(item["source"] == "upperarm_L"
                   and item["target"] == "WRONG_TARGET"
                   for item in result["entries"])
    assert any(item["type"] == "ai_overlaps_dictionary"
               for item in result["conflicts"])


def test_ai_candidates_only_fill_uncovered_slots():
    provider = _FakeProvider([
        {"source": "twist_L", "target": "twist_helper_L", "confidence": 0.8},
    ])
    result = map_skeleton(
        ["root", "upperarm_L", "twist_L"],
        ["root", "LeftUpArm", "twist_helper_L"],
        options={"use_ai": True}, provider=provider)
    added = [item for item in result["entries"]
             if item["source"] == "twist_L" and item["target"] == "twist_helper_L"]
    assert added and added[0]["origin"] == "ai"
    assert added[0]["status"] == "needs_review"
    assert result["metadata"]["ai"]["candidates"] == 1


def test_merge_candidates_marks_source_and_keeps_conflicts():
    dictionary = deterministic_pairs(["root", "upperarm_L"],
                                     ["root", "LeftUpArm"])
    ai = [{"source": "upperarm_L", "target": "WRONG", "confidence": 0.9}]
    merged = merge_candidates(dictionary, ai,
                              ["root", "upperarm_L"],
                              ["root", "LeftUpArm", "WRONG"])
    assert all("origin" in item for item in merged["entries"])
    assert all(item["origin"] == "dictionary" for item in merged["entries"])
    assert any(item["type"] == "ai_overlaps_dictionary"
               for item in merged["conflicts"])


def test_conflicts_are_never_auto_resolved():
    result = map_skeleton(["upperarm_L", "LeftUpArm"], ["upperarm_l"])
    assert result["conflicts"]
    assert any("人工确认" in item["message"] for item in result["conflicts"])
    assert not any(item["target"] == "upperarm_l" for item in result["entries"])
    assert {item["bone"] for item in result["unresolved"]} >= {
        "upperarm_L", "LeftUpArm"}


def test_mapping_asset_dict_is_consumed_as_baseline():
    asset = {
        "version": "1.0", "generated_by": "ik_retarget_guard",
        "entries": [{"source": "upperarm_L", "target": "CUSTOM",
                     "confidence": 0.7, "method": "asset"}],
        "conflicts": [], "metadata": {},
    }
    result = map_skeleton(["root", "upperarm_L"],
                          ["root", "CUSTOM", "LeftUpArm"],
                          mapping_asset=asset)
    baseline = [item for item in result["entries"] if item["source"] == "upperarm_L"]
    assert baseline and baseline[0]["target"] == "CUSTOM"
    assert baseline[0]["origin"] == "asset"
    assert not any(item["source"] == "upperarm_L"
                   and item["target"] == "LeftUpArm" for item in result["entries"])


def test_mapping_asset_file_is_consumed_as_baseline(tmp_path):
    asset = {
        "version": "1.0", "generated_by": "ik_retarget_guard",
        "entries": [{"source": "upperarm_L", "target": "CUSTOM",
                     "confidence": 0.7, "method": "asset"}],
        "conflicts": [], "metadata": {},
    }
    path = tmp_path / "baseline.json"
    path.write_text(json.dumps(asset, ensure_ascii=False), encoding="utf-8")
    result = map_skeleton(["root", "upperarm_L"],
                          ["root", "CUSTOM", "LeftUpArm"],
                          mapping_asset=str(path))
    assert any(item["source"] == "upperarm_L" and item["target"] == "CUSTOM"
               and item["origin"] == "asset" for item in result["entries"])


# --------------------------------------------------------------------------- #
# 7. 映射资产 v1.0 往返
# --------------------------------------------------------------------------- #
def test_write_mapping_roundtrip_with_ik_retarget_guard(tmp_path):
    result = map_skeleton(
        ["root", "pelvis", "upperarm_L", "lowerarm_L"],
        ["root", "pelvis", "LeftUpArm", "LeftForeArm"])
    path = tmp_path / "mapping.json"
    assert write_mapping(result, str(path)) == str(path)
    asset = _ik_mapping().load_mapping_asset(str(path))
    assert asset["version"] == "1.0"
    assert asset["generated_by"] == "ik_retarget_guard"
    expected = [
        {"source": item["source"], "target": item["target"],
         "confidence": item["confidence"], "method": item["method"]}
        for item in result["entries"]]
    assert asset["entries"] == expected
    assert asset["metadata"]["consumers"] == [
        "P5.2 Skeleton Mapper", "UE IK Retargeter"]
    assert "entry_details" in asset["metadata"]
    assert load_mapping(str(path)) == asset


def test_write_mapping_is_byte_reproducible(tmp_path):
    result = map_skeleton(["root", "pelvis", "upperarm_L"],
                          ["root", "pelvis", "LeftUpArm"])
    first = tmp_path / "a.json"
    second = tmp_path / "b.json"
    write_mapping(result, str(first))
    write_mapping(result, str(second))
    assert first.read_bytes() == second.read_bytes()


# --------------------------------------------------------------------------- #
# 8. manifest / 插件发现
# --------------------------------------------------------------------------- #
def test_manifest_is_discoverable():
    plugins = registry.discover()
    matched = [item for item in plugins if item.id == TOOL_ID]
    assert matched
    plugin = matched[0]
    assert plugin.name == "Skeleton Mapper 骨骼语义映射"
    assert plugin.dcc == ["maya", "max", "mobu", "unreal"]
    assert plugin.ui in ("web", "both")
    assert plugin.permissions == ["scene.read"]
    assert "人工确认" in plugin.description


def test_new_tool_list_includes_skeleton_mapper():
    process = subprocess.run(
        [sys.executable, os.path.join(_ROOT, "tools", "new_tool.py"), "--list"],
        cwd=_ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    assert process.returncode == 0
    assert b"skeleton_mapper" in process.stdout


# --------------------------------------------------------------------------- #
# 9. 插件 core：run_report / plan
# --------------------------------------------------------------------------- #
def test_run_report_returns_report_with_mapping_metadata():
    core = _load("core")
    ctx = _ctx(options={
        "source_bones": ["root", "pelvis", "upperarm_L"],
        "target_bones": ["root", "pelvis", "LeftUpArm"],
    })
    report = core.run_report(ctx)
    assert isinstance(report, Report)
    assert "mapping" in report.metadata
    assert "stats" in report.metadata
    assert "citations" in report.metadata
    assert report.metadata["mapping"]["entries"]


def test_run_report_translates_conflicts_and_unresolved_to_findings():
    core = _load("core")
    ctx = _ctx(options={
        "source_bones": ["upperarm_L", "LeftUpArm"],
        "target_bones": ["upperarm_l"],
    })
    report = core.run_report(ctx)
    errors = [item for item in report.findings if item.severity == Severity.ERROR]
    warnings = [item for item in report.findings
                if item.severity == Severity.WARNING]
    assert any(item.rule_id == TOOL_ID + ".conflict" for item in errors)
    assert any(item.rule_id == TOOL_ID + ".unresolved" for item in warnings)


def test_run_tool_end_to_end_uses_snapshot_and_spec(tmp_path):
    snapshot = {"joints": {"root": {}, "pelvis": {}, "head": {}}}
    spec = {"version": "1.0.0",
            "skeleton": {"required_joints": ["root", "pelvis", "head"]}}
    result = runner.run_tool(TOOL_ID, snapshot, dcc="maya",
                             adapter=NullAdapter(), spec=spec)
    assert isinstance(result.report, Report)
    assert result.report.metadata["mapping"]["entries"]
    assert not list(tmp_path.iterdir())


def test_plan_only_produces_candidates_without_writing(tmp_path):
    core = _load("core")
    ctx = _ctx(options={
        "source_bones": ["root", "pelvis"],
        "target_bones": ["root", "pelvis"],
    })
    before = sorted(item.name for item in tmp_path.iterdir())
    plan = core.plan(ctx)
    assert isinstance(plan, dict) and "entries" in plan
    assert sorted(item.name for item in tmp_path.iterdir()) == before


# --------------------------------------------------------------------------- #
# 10. Web 面板
# --------------------------------------------------------------------------- #
def test_ui_html_escapes_all_dynamic_text():
    ui = _load("ui_web")
    evil = map_skeleton(["root"], ["root"])
    evil["entries"] = [{
        "source": "<script>alert(1)</script>", "target": "A&B\"Q",
        "semantic": "arm", "confidence": 0.9, "origin": "dictionary",
        "method": "normalized", "status": "auto",
    }]
    evil["conflicts"] = []
    evil["unresolved"] = []
    html = ui.render_panel_html(evil)
    assert "<script>" not in html
    assert "&lt;script&gt;" in html
    assert "&amp;" in html
    assert "&quot;" in html


def test_ui_html_has_no_external_links_or_inline_styles():
    ui = _load("ui_web")
    html = ui.render_panel_html(map_skeleton(
        ["root", "pelvis", "upperarm_L"],
        ["root", "pelvis", "LeftUpArm"])).lower()
    for forbidden in ("http://", "https://", "<link", "<script", "cdn",
                      "style=", "<style", "#fff", "rgb("):
        assert forbidden not in html
    assert '<table' in html


def test_ui_render_from_report_and_tool_result():
    ui = _load("ui_web")
    core = _load("core")
    ctx = _ctx(options={
        "source_bones": ["root", "upperarm_L"],
        "target_bones": ["root", "LeftUpArm"],
    })
    report = core.run_report(ctx)
    from_report = ui.render_report_html(report)
    from_dict = ui.render_panel_html(report.to_dict())
    assert "tak-table" in from_report
    assert from_report == from_dict
    assert ui.render_report_html(map_skeleton(["root"], ["root"]))


# --------------------------------------------------------------------------- #
# 11. Python 3.9 语法与 tak/core DCC 隔离
# --------------------------------------------------------------------------- #
def test_new_sources_parse_as_py39_and_core_has_no_dcc_imports():
    files = [
        os.path.join(_ROOT, "tak", "core", "mapping", "__init__.py"),
        os.path.join(_ROOT, "tak", "core", "mapping", "semantics.py"),
        os.path.join(_ROOT, "tools", TOOL_ID, "core.py"),
        os.path.join(_ROOT, "tools", TOOL_ID, "ui_web.py"),
    ]
    forbidden = {"maya", "pymxs", "pyfbsdk", "unreal"}
    for path in files:
        with open(path, "r", encoding="utf-8") as handle:
            source = handle.read()
        tree = ast.parse(source, filename=path, feature_version=(3, 9))
        if os.path.join("tak", "core") in path:
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    roots = [alias.name.split(".")[0] for alias in node.names]
                elif isinstance(node, ast.ImportFrom) \
                        and node.level == 0 and node.module:
                    roots = [node.module.split(".")[0]]
                else:
                    continue
                assert not (set(roots) & forbidden), \
                    "%s 出现 DCC import: %s" % (path, roots)
