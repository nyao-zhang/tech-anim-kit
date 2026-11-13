# -*- coding: utf-8 -*-
"""IK Retarget Guard（P3.3）离线回归测试。

覆盖 manifest / 六条规则 / 40 个 golden fixtures 零误报、跨命名体系候选映射、
冲突检测、映射资产往返、inspect 健壮性、Web 主题约束与三种 Report 渲染。
全部使用 NullAdapter 和纯 JSON，不接触真实 Unreal 或网络。
"""
from __future__ import annotations

import copy
import json
import os
import re

import pytest

from tak.adapters.base import NullAdapter
from tak.core import registry, runner
from tak.core.rules.engine import list_rules
from tak.core.spec import load_spec

TOOL_ID = "ik_retarget_guard"
_RULE = TOOL_ID + "."
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_PLUGIN = os.path.join(_ROOT, "tools", TOOL_ID)
_FIXTURES = os.path.join(_ROOT, "fixtures")
RULE_IDS = (
    _RULE + "chain_mapping_completeness",
    _RULE + "bone_naming_mismatch",
    _RULE + "root_pelvis_offset",
    _RULE + "pose_deviation",
    _RULE + "required_chains",
    _RULE + "mapping_conflicts",
)


def _load(submodule):
    return registry.load_plugin_module(runner.find_plugin(TOOL_ID), submodule)


def _data():
    bones = [
        "root", "pelvis", "spine_01", "neck", "head",
        "clavicle_L", "upperarm_L", "lowerarm_L", "hand_L",
        "clavicle_R", "upperarm_R", "lowerarm_R", "hand_R",
        "thigh_L", "calf_L", "foot_L", "thigh_R", "calf_R", "foot_R",
    ]
    chains = [
        {"name": "Spine", "bones": ["root", "pelvis", "spine_01", "neck", "head"]},
        {"name": "LeftArm", "bones": ["clavicle_L", "upperarm_L", "lowerarm_L", "hand_L"]},
        {"name": "RightArm", "bones": ["clavicle_R", "upperarm_R", "lowerarm_R", "hand_R"]},
        {"name": "LeftLeg", "bones": ["thigh_L", "calf_L", "foot_L"]},
        {"name": "RightLeg", "bones": ["thigh_R", "calf_R", "foot_R"]},
    ]
    return {
        "ik_rig": {"chains": chains, "retarget_root": "root", "goal": {}},
        "source_bones": list(bones), "target_bones": list(bones),
        "retargeter": {
            "chain_mapping": {item["name"]: item["name"] for item in chains},
            "root_offset": [0, 0, 0], "pelvis_offset": [0, 0, 0],
        },
        "pose_deviation": {"max": 0.001,
                           "per_bone": {"hand_L": 0.001, "hand_R": 0.001}},
    }


def _run(data=None, snapshot=None, spec=None, rule_ids=None):
    return runner.run_tool(
        TOOL_ID, snapshot if snapshot is not None else {},
        dcc="unreal", adapter=NullAdapter(), spec=spec,
        options={"retarget": data} if data is not None else {}, rule_ids=rule_ids)


def _ids(result):
    return [item.rule_id for item in result.report.findings]


def _fixtures(folder):
    path = os.path.join(_FIXTURES, folder)
    result = []
    for name in sorted(os.listdir(path)):
        if not name.endswith(".json"):
            continue
        with open(os.path.join(path, name), "r", encoding="utf-8") as handle:
            result.append(json.load(handle))
    return result


@pytest.fixture(scope="module", autouse=True)
def _registered():
    runner.load_tool_modules(runner.find_plugin(TOOL_ID))
    yield


# ---------------------------------------------------------------------------
# manifest / 注册 / 数据契约
# ---------------------------------------------------------------------------
def test_manifest_is_valid_and_complete():
    with open(os.path.join(_PLUGIN, "manifest.json"), "r", encoding="utf-8") as handle:
        manifest = json.load(handle)
    assert registry.validate_manifest(manifest) == []
    assert manifest["id"] == TOOL_ID and manifest["entry"] == TOOL_ID
    assert manifest["dcc"] == ["unreal"]
    assert manifest["min_version"] == {"unreal": "5.8"}
    assert manifest["ui"] == "both" and manifest["permissions"] == ["scene.read"]
    assert manifest["name"] and manifest["description"]


def test_six_rules_registered_only_for_unreal():
    catalog = {item["id"]: item for item in list_rules("unreal")
               if item["plugin_id"] == TOOL_ID}
    assert set(catalog) == set(RULE_IDS)
    assert len(catalog) == 6
    assert all(item["dcc"] == ["unreal"] for item in catalog.values())
    for dcc in ("maya", "max", "mobu"):
        assert not [item for item in list_rules(dcc) if item["plugin_id"] == TOOL_ID]


def test_info_data_contract_and_handoff():
    core = _load("core")
    assert core.info()["rule_count"] == 6
    contract = core.describe_data_contract()
    assert contract["sources"] == ["snapshot.metadata.retarget", "options.retarget"]
    for field in ("ik_rig.chains", "source_bones", "target_bones",
                  "retargeter.chain_mapping", "retargeter.root_offset",
                  "retargeter.pelvis_offset", "pose_deviation.max",
                  "pose_deviation.per_bone"):
        assert field in contract["fields"]
    result = _run(_data())
    handoff = result.report.metadata["handoff"]
    assert handoff["consumer"] == "P5.2 Skeleton Mapper"
    assert handoff["asset_version"] == "1.0"
    assert "UE IK Retargeter" in handoff["targets"]


# ---------------------------------------------------------------------------
# golden fixtures 与完整干净数据
# ---------------------------------------------------------------------------
def test_all_forty_golden_fixtures_produce_no_finding():
    fixtures = _fixtures("good") + _fixtures("bad")
    assert len(fixtures) == 40
    for fixture in fixtures:
        result = _run(snapshot=fixture["snapshot"])
        assert result.passed is True, fixture["id"]
        assert result.report.findings == [], fixture["id"]
        assert result.report.metadata["retarget_present"] is False


def test_complete_chain_mapping_and_clean_data_pass():
    result = _run(_data())
    assert result.passed is True and result.report.findings == []
    assert result.report.metadata["rules_count"] == 6
    assert result.report.metadata["mapping_summary"]["entries"] == 19
    assert all(item["status"] == "mapped"
               for item in result.report.metadata["chain_mapping"])


def test_retarget_can_come_from_snapshot_metadata():
    result = _run(snapshot={"metadata": {"retarget": _data()}})
    assert result.passed is True and result.report.findings == []
    assert result.report.metadata["retarget_present"] is True


def test_options_take_priority_over_snapshot_metadata():
    broken = _data()
    broken["retargeter"]["root_offset"] = [1, 0, 0]
    snapshot = {"metadata": {"retarget": broken}}
    result = _run(_data(), snapshot=snapshot)
    assert result.passed is True and result.report.findings == []


def test_rules_do_not_mutate_input():
    data = _data()
    data["retargeter"]["root_offset"] = [1, 0, 0]
    before = copy.deepcopy(data)
    _run(data)
    assert data == before


# ---------------------------------------------------------------------------
# 六条规则分别命中
# ---------------------------------------------------------------------------
def test_missing_chain_mapping_is_blocking():
    data = _data()
    del data["retargeter"]["chain_mapping"]["LeftArm"]
    result = _run(data)
    assert _RULE + "chain_mapping_completeness" in _ids(result)
    assert result.passed is False
    finding = next(item for item in result.report.findings
                   if item.rule_id == _RULE + "chain_mapping_completeness")
    assert finding.data["issue"] == "missing_mapping"
    assert finding.data["fix"]["op"] == "set_chain_mapping"


def test_unknown_chain_mapping_is_blocking():
    data = _data()
    data["retargeter"]["chain_mapping"]["GhostChain"] = "GhostChain"
    result = _run(data)
    assert _RULE + "chain_mapping_completeness" in _ids(result)
    assert any(item.data.get("issue") == "unknown_source_chain"
               for item in result.report.findings)


def test_bone_naming_mismatch_without_candidate_is_blocking():
    data = _data()
    data["target_bones"][data["target_bones"].index("upperarm_L")] = "mystery_bone"
    result = _run(data)
    assert _RULE + "bone_naming_mismatch" in _ids(result)
    assert result.passed is False
    assert any(item.node == "mystery_bone" for item in result.report.findings)


def test_bone_naming_mismatch_offers_dictionary_candidate():
    data = _data()
    data["target_bones"][data["target_bones"].index("upperarm_L")] = "LeftUpArm"
    result = _run(data)
    findings = [item for item in result.report.findings
                if item.rule_id == _RULE + "bone_naming_mismatch"]
    assert findings and result.passed is True
    assert findings[0].severity.value == "warning"
    assert findings[0].data["candidate"]["target"] == "LeftUpArm"
    assert findings[0].data["fix"]["op"] == "set_bone_mapping"


def test_root_offset_over_default_threshold():
    data = _data()
    data["retargeter"]["root_offset"] = [0.02, 0, 0]
    result = _run(data)
    assert _ids(result) == [_RULE + "root_pelvis_offset"]
    finding = result.report.findings[0]
    assert finding.data["field"] == "root_offset"
    assert finding.data["tolerance"] == 0.01


def test_pelvis_offset_threshold_can_come_from_spec():
    data = _data()
    data["retargeter"]["pelvis_offset"] = [0.02, 0, 0]
    spec = copy.deepcopy(load_spec())
    spec["ue"]["retarget_pelvis_offset_tolerance"] = 0.03
    assert _RULE + "root_pelvis_offset" not in _ids(_run(data, spec=spec))
    data["thresholds"] = {"pelvis_offset": 0.01}
    assert _RULE + "root_pelvis_offset" in _ids(_run(data, spec=spec))


def test_global_pose_deviation_over_threshold():
    data = _data()
    data["pose_deviation"]["max"] = 0.03
    result = _run(data)
    assert _ids(result) == [_RULE + "pose_deviation"]
    assert result.report.findings[0].data["scope"] == "global"


def test_per_bone_pose_deviation_and_custom_threshold():
    data = _data()
    data["pose_deviation"]["per_bone"]["hand_L"] = 0.03
    result = _run(data)
    assert _ids(result) == [_RULE + "pose_deviation"]
    assert result.report.findings[0].node == "hand_L"
    data["thresholds"] = {"pose_per_bone": {"hand_L": 0.04}}
    assert _RULE + "pose_deviation" not in _ids(_run(data))


def test_missing_required_chain():
    data = _data()
    data["ik_rig"]["chains"] = [item for item in data["ik_rig"]["chains"]
                                if item["name"] != "LeftLeg"]
    result = _run(data)
    assert _RULE + "required_chains" in _ids(result)
    assert any(item.node == "LeftLeg" for item in result.report.findings)


def test_required_chains_are_configurable():
    data = _data()
    data["required_chains"] = ["Spine"]
    data["ik_rig"]["chains"] = [item for item in data["ik_rig"]["chains"]
                                if item["name"] == "Spine"]
    data["retargeter"]["chain_mapping"] = {"Spine": "Spine"}
    assert _RULE + "required_chains" not in _ids(_run(data))


def test_shared_target_chain_is_conflict():
    data = _data()
    data["retargeter"]["chain_mapping"]["RightArm"] = "LeftArm"
    result = _run(data)
    assert _ids(result) == [_RULE + "mapping_conflicts"]
    assert result.report.findings[0].data["conflict_type"] == "shared_target"


def test_duplicate_source_chain_list_is_conflict():
    data = _data()
    data["retargeter"]["chain_mapping"] = [
        {"source": "Spine", "target": "Spine"},
        {"source": "Spine", "target": "OtherSpine"},
    ]
    result = _run(data)
    assert _RULE + "mapping_conflicts" in _ids(result)
    assert any(item.data.get("conflict_type") == "duplicate_source"
               for item in result.report.findings)


# ---------------------------------------------------------------------------
# 归一化 / suggest_mapping / mapping asset
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("name,expected", [
    ("upperarm_L", "upperarm_l"),
    ("upperarm_l", "upperarm_l"),
    ("LeftUpArm", "upperarm_l"),
    ("Bip01 L UpperArm", "upperarm_l"),
    ("mixamorig:LeftArm", "upperarm_l"),
    ("L_UpperArm", "upperarm_l"),
    ("UPPERARM_L", "upperarm_l"),
    ("Bip01 R Forearm", "lowerarm_r"),
    ("mixamorig:RightForeArm", "lowerarm_r"),
    ("Hips", "pelvis"),
    ("Bip01 Pelvis", "pelvis"),
    ("LeftUpLeg", "thigh_l"),
    ("LeftLeg", "calf_l"),
    ("LeftFoot", "foot_l"),
    ("Spine", "spine_01"),
    ("Spine1", "spine_02"),
    ("spine_03", "spine_03"),
    ("", ""),
    (None, ""),
])
def test_normalize_bone_name_across_naming_systems(name, expected):
    assert _load("mapping").normalize_bone_name(name) == expected


def test_suggest_mapping_is_deterministic_and_cross_naming():
    mapping = _load("mapping")
    sources = ["upperarm_L", "lowerarm_L", "hand_L", "thigh_R", "calf_R", "foot_R"]
    targets = ["RightFoot", "LeftUpArm", "RightUpLeg", "LeftHand", "RightLeg", "LeftLowerArm"]
    first = mapping.suggest_mapping(sources, targets)
    second = mapping.suggest_mapping(list(reversed(sources)), list(reversed(targets)))
    assert first == second
    assert len(first["entries"]) == 6
    assert first["unmapped_source"] == [] and first["unmapped_target"] == []
    assert {item["method"] for item in first["entries"]} == {"dictionary"}


def test_suggest_mapping_detects_one_source_many_targets():
    result = _load("mapping").suggest_mapping(
        ["upperarm_L"], ["LeftUpArm", "upperarm_l"])
    assert result["entries"] == []
    assert result["conflicts"][0]["type"] == "one_source_many_targets"
    assert result["unmapped_source"] == ["upperarm_L"]
    assert set(result["unmapped_target"]) == {"LeftUpArm", "upperarm_l"}


def test_suggest_mapping_detects_many_sources_one_target():
    result = _load("mapping").suggest_mapping(
        ["upperarm_L", "LeftUpArm"], ["Bip01 L UpperArm"])
    assert result["entries"] == []
    assert result["conflicts"][0]["type"] == "many_sources_one_target"
    assert set(result["unmapped_source"]) == {"upperarm_L", "LeftUpArm"}


def test_suggest_mapping_lists_unmapped_bones():
    result = _load("mapping").suggest_mapping(["root", "custom_source"], ["root", "custom_target"])
    assert result["entries"][0]["method"] == "exact"
    assert result["unmapped_source"] == ["custom_source"]
    assert result["unmapped_target"] == ["custom_target"]


def test_mapping_conflicts_rule_consumes_suggestions():
    data = _data()
    data["target_bones"].extend(["LeftUpArm", "upperarm_l"])
    result = _run(data)
    assert _RULE + "mapping_conflicts" in _ids(result)
    assert result.report.metadata["mapping"]["conflicts"]


def test_mapping_asset_write_read_roundtrip_is_deterministic(tmp_path):
    mapping = _load("mapping")
    suggestion = mapping.suggest_mapping(["upperarm_L", "hand_L"], ["LeftHand", "LeftUpArm"])
    first = tmp_path / "mapping.json"
    second = tmp_path / "nested" / "mapping.json"
    assert mapping.write_mapping_asset(suggestion, first) == str(first)
    mapping.write_mapping_asset(suggestion, second)
    assert first.read_bytes() == second.read_bytes()
    loaded = mapping.load_mapping_asset(first)
    assert set(loaded) == {"version", "generated_by", "entries", "conflicts", "metadata"}
    assert loaded["version"] == "1.0" and loaded["generated_by"] == TOOL_ID
    assert loaded["entries"] == suggestion["entries"]
    assert loaded["conflicts"] == []
    assert "P5.2 Skeleton Mapper" in loaded["metadata"]["consumers"]
    assert json.loads(first.read_text(encoding="utf-8")) == loaded


# ---------------------------------------------------------------------------
# inspect 健壮性 / 渲染
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("data", [None, {}, [], "bad", {"ik_rig": {}},
                                  {"retargeter": {}}, {"pose_deviation": {}}])
def test_inspect_retarget_empty_or_missing_fields_is_robust(data):
    result = _load("core").inspect_retarget(data)
    assert set(result) == {"passed", "findings", "diffs", "mapping", "summary"}
    assert result["passed"] is True and result["findings"] == []
    assert result["diffs"] == []
    assert result["mapping"] == {"entries": [], "unmapped_source": [],
                                 "unmapped_target": [], "conflicts": []}
    json.dumps(result, ensure_ascii=False, allow_nan=False)


def test_inspect_combines_rules_mapping_diffs_and_summary():
    data = _data()
    data["target_bones"][data["target_bones"].index("upperarm_L")] = "LeftUpArm"
    result = _load("core").inspect_retarget(data, load_spec())
    assert result["passed"] is True
    assert result["findings"] and result["diffs"]
    assert result["mapping"]["entries"]
    assert result["summary"]["mapped"] == 19
    json.dumps(result, ensure_ascii=False, allow_nan=False)


@pytest.mark.parametrize("fmt", ["json", "md", "html"])
def test_report_renders_three_formats(fmt):
    result = _run(_data())
    rendered = result.render(fmt)
    assert isinstance(rendered, str) and rendered
    if fmt == "json":
        payload = json.loads(rendered)
        assert payload["metadata"]["handoff"]["consumer"] == "P5.2 Skeleton Mapper"
    elif fmt == "md":
        assert "IK Retarget Guard" in rendered
    else:
        assert "tak-panel" in rendered


def test_web_panel_has_theme_classes_mapping_conflicts_and_no_external_assets():
    data = _data()
    data["target_bones"].extend(["LeftUpArm", "upperarm_l"])
    panel = _load("ui_web").render_panel_html(_run(data))
    for token in ("tak-panel", "tak-header", "tak-meta", "tak-state", "tak-table",
                  "tak-row", "tak-badge", "tak-sev-error", "tak-hint",
                  "IK 链映射", "置信度", "映射冲突", "规则结果", "P5.2"):
        assert token in panel, token
    assert "<html" not in panel and "<body" not in panel
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b", panel)
    assert "rgb(" not in panel and "hsl(" not in panel
    assert "http://" not in panel and "https://" not in panel
    assert "<link" not in panel and "<script" not in panel


def test_web_panel_escapes_dynamic_bone_names():
    data = _data()
    data["source_bones"].append("<script>alert(1)</script>")
    panel = _load("ui_web").render_panel_html(_run(data))
    assert "<script>" not in panel
    assert "&lt;script&gt;" in panel


def test_web_panel_accepts_report_and_inspect_dict():
    ui = _load("ui_web")
    result = _run(_data())
    assert "tak-state-pass" in ui.render_panel_html(result.report)
    inspected = _load("core").inspect_retarget(_data())
    assert "tak-panel" in ui.render_panel_html(inspected)
