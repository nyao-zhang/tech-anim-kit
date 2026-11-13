# -*- coding: utf-8 -*-
"""UE Import Guard（P3.2）回归测试。

覆盖面
------
* manifest 合法性、只在 unreal 被发现、9 条规则注册与 dcc / plugin_id 约束；
* 40 个 golden fixtures 经 runner 零误报（不含 UE 字段时整组跳过）；
* 完整 UE 资产数据全部通过；metadata.ue 与 options.ue_assets 双来源；
* 9 条规则各自的命中 / 不命中反例；
* validate_import：一致通过、命名差异阻断、缺字段跳过、空输入健壮、
  bind_matrix 默认忽略且可配置为比对；
* Web 面板片段只用主题类名、无硬编码颜色与外链；Qt 无环境返回 None；
* Report 三种渲染、metadata(rules_count/validate/api_notes)。

全部离线：只用 NullAdapter 与 fixtures JSON，不接触真实 UE / 网络。
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

TOOL_ID = "ue_import_guard"
_RULE = TOOL_ID + "."
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_FIXTURES = os.path.join(_ROOT, "fixtures")
_PLUGIN_DIR = os.path.join(_ROOT, "tools", TOOL_ID)
_BLOCKING = ("error", "blocker")
_IDENTITY = [1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0,
             0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0]
_SKELETON_BONES = ["root", "pelvis", "spine_01", "neck", "head"]
_TARGET = "/Game/Characters/Default/SK_Default"

RULE_IDS = (
    _RULE + "skeleton_compatible",
    _RULE + "required_bone_chain",
    _RULE + "bone_naming",
    _RULE + "root_motion",
    _RULE + "anim_compression",
    _RULE + "curve_loss",
    _RULE + "material_slots",
    _RULE + "lod_allow_deformer",
    _RULE + "sidecar_mismatch",
)


# --------------------------------------------------------------------------- #
# 构造工具
# --------------------------------------------------------------------------- #
def _fixture(folder, name):
    with open(os.path.join(_FIXTURES, folder, name), "r", encoding="utf-8") as fh:
        return json.load(fh)


def _all_fixtures(folder):
    path = os.path.join(_FIXTURES, folder)
    return [_fixture(folder, name) for name in sorted(os.listdir(path))
            if name.endswith(".json")]


def _joint(name, parent=None, depth=0):
    return {"parent": parent, "matrix": list(_IDENTITY), "depth": depth,
            "scale": [1.0, 1.0, 1.0], "rotate": [0.0, 0.0, 0.0],
            "namespace": ""}


def _snapshot(joints=None):
    return {
        "dcc": "unreal", "version": "5.8", "unit": "cm", "up_axis": "Y",
        "joints": joints if joints is not None else {
            "root": _joint("root"), "pelvis": _joint("pelvis", "root", 1)},
        "frame_range": [1.0, 120.0],
        "skin": {"max_influences_found": 0, "weight_sum_deviation": 0.0,
                 "deformers": []},
        "metadata": {"seed": 7, "source": "tak ue test"},
    }


def _assets():
    """一份完全干净的 UE 资产数据（9 条规则都不应命中）。"""
    return {
        "skeleton": {"bones": list(_SKELETON_BONES),
                     "compatible_with": [_TARGET], "root_bone": "root"},
        "anim_sequences": [{
            "name": "/Game/Anim/AS_Run",
            "skeleton": _TARGET,
            "compression": "Default",
            "root_motion": False,
            "curve_count": 2,
            "curves": ["run_phase", "lean"],
        }],
        "skeletal_meshes": [{
            "name": "/Game/Characters/SKM_Body",
            "material_slots": 2,
            "lods": [{"allow_deformer": False}, {"allow_deformer": False}],
        }],
        "import": {"sidecar": {"snapshot": _snapshot()}},
    }


def _run(options=None, snapshot=None, spec=None, rule_ids=None):
    return runner.run_tool(TOOL_ID,
                           snapshot if snapshot is not None else _snapshot(),
                           dcc="unreal", adapter=NullAdapter(), spec=spec,
                           options=dict(options or {}), rule_ids=rule_ids)


def _run_assets(assets, snapshot=None, spec=None, rule_ids=None):
    return _run({"ue_assets": assets}, snapshot=snapshot, spec=spec,
                rule_ids=rule_ids)


def _ids(result):
    return [item.rule_id for item in result.report.findings]


def _errors(result):
    return [item for item in result.report.findings
            if item.severity.value in _BLOCKING]


def _load(submodule):
    return registry.load_plugin_module(runner.find_plugin(TOOL_ID), submodule)


@pytest.fixture(scope="module", autouse=True)
def _registered():
    """确保本插件规则已注册（runner 的模块缓存跨测试复用）。"""
    runner.load_tool_modules(runner.find_plugin(TOOL_ID))
    yield


# --------------------------------------------------------------------------- #
# manifest / 发现 / 注册
# --------------------------------------------------------------------------- #
def test_manifest_is_valid_and_complete():
    with open(os.path.join(_PLUGIN_DIR, "manifest.json"), "r", encoding="utf-8") as fh:
        data = json.load(fh)
    assert registry.validate_manifest(data) == []
    assert data["id"] == TOOL_ID
    assert data["entry"] == TOOL_ID
    assert data["dcc"] == ["unreal"]
    assert data["min_version"] == {"unreal": "5.8"}
    assert data["ui"] == "both"
    assert data["permissions"] == ["scene.read", "import.validate"]
    assert data["name"] and data["description"]


def test_plugin_discovered_only_for_unreal():
    assert TOOL_ID in [item["id"] for item in runner.list_tools(dcc="unreal")]
    for dcc in ("maya", "max", "mobu"):
        assert TOOL_ID not in [item["id"] for item in runner.list_tools(dcc=dcc)]


def test_nine_rules_registered_with_explicit_plugin_id():
    catalog = {item["id"]: item for item in list_rules("unreal")
               if item["plugin_id"] == TOOL_ID}
    assert set(catalog) == set(RULE_IDS)
    assert len(RULE_IDS) >= 8
    for rule_id, item in catalog.items():
        assert item["dcc"] == ["unreal"], rule_id
        assert item["description"], rule_id
        assert item["severity"] in {"blocker", "error", "warning", "info"}


def test_rules_do_not_leak_into_other_dcc():
    for dcc in ("maya", "max", "mobu"):
        assert not [item for item in list_rules(dcc)
                    if item["plugin_id"] == TOOL_ID]


def test_info_and_data_contract():
    core = _load("core")
    info = core.info()
    assert info["id"] == TOOL_ID
    assert info["dcc"] == ["unreal"]
    assert "import.validate" in info["capabilities"]
    contract = core.describe_data_contract()
    assert contract["sources"] == ["snapshot.metadata.ue", "options.ue_assets"]
    assert "字段缺失" in contract["missing_policy"]
    for key in ("skeleton.bones", "anim_sequences[].curves",
                "skeletal_meshes[].material_slots",
                "skeletal_meshes[].lods[].allow_deformer", "import.sidecar"):
        assert key in contract["fields"], key


# --------------------------------------------------------------------------- #
# golden fixtures：不含 UE 字段时必须零误报
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("name", ["good_001.json", "good_002.json",
                                  "bad_001.json", "bad_002.json"])
def test_golden_fixtures_pass_through_runner(name):
    folder = "good" if name.startswith("good") else "bad"
    result = _run(snapshot=_fixture(folder, name)["snapshot"])
    assert result.passed is True
    assert result.report.findings == []
    assert result.report.metadata["ue_assets_present"] is False
    assert result.report.metadata["validate"]["compared"] is False


def test_all_forty_golden_fixtures_produce_no_finding():
    for folder in ("good", "bad"):
        for fixture in _all_fixtures(folder):
            result = _run(snapshot=fixture["snapshot"])
            assert result.report.findings == [], fixture["id"]
            assert result.passed is True, fixture["id"]


# --------------------------------------------------------------------------- #
# 干净数据
# --------------------------------------------------------------------------- #
def test_clean_ue_assets_pass_all_rules():
    result = _run_assets(_assets())
    assert result.report.findings == []
    assert result.passed is True
    assert result.report.metadata["rules_count"] == len(RULE_IDS)


def test_ue_assets_can_come_from_snapshot_metadata():
    snapshot = _snapshot()
    snapshot["metadata"]["ue"] = _assets()
    result = _run(snapshot=snapshot)
    assert result.report.findings == []
    assert result.report.metadata["ue_assets_present"] is True


def test_options_ue_assets_take_priority_over_metadata():
    snapshot = _snapshot()
    broken = _assets()
    broken["skeleton"]["compatible_with"] = ["/Game/Other/SK_Other"]
    snapshot["metadata"]["ue"] = broken
    result = _run({"ue_assets": _assets()}, snapshot=snapshot)
    assert result.report.findings == []
    assert result.passed is True


def test_rules_never_mutate_snapshot_or_assets():
    assets = _assets()
    assets["skeleton"]["compatible_with"] = ["/Game/Other/SK_Other"]
    assets["anim_sequences"][0]["curve_count"] = 2
    assets["anim_sequences"][0]["curves"] = []
    snapshot = _snapshot()
    snapshot["metadata"]["ue"] = assets
    before_snapshot = copy.deepcopy(snapshot)
    before_assets = copy.deepcopy(assets)
    _run(snapshot=snapshot)
    assert snapshot == before_snapshot
    assert assets == before_assets


# --------------------------------------------------------------------------- #
# 规则 1：Skeleton 兼容性
# --------------------------------------------------------------------------- #
def test_skeleton_compatible_hit_and_miss():
    assets = _assets()
    assets["skeleton"]["compatible_with"] = ["/Game/Other/SK_Other"]
    hit = _run_assets(assets)
    assert _ids(hit) == [_RULE + "skeleton_compatible"]
    assert hit.report.findings[0].severity.value == "error"
    assert hit.passed is False
    assert _is_clean(_run_assets(_assets()))


def test_skeleton_compatible_empty_list_is_warning():
    assets = _assets()
    assets["skeleton"]["compatible_with"] = []
    result = _run_assets(assets)
    assert _ids(result) == [_RULE + "skeleton_compatible"]
    assert result.report.findings[0].severity.value == "warning"


def test_skeleton_compatible_empty_bones_is_error():
    assets = _assets()
    assets["skeleton"]["bones"] = []
    result = _run_assets(assets)
    assert _ids(result) == [_RULE + "skeleton_compatible"]
    assert result.passed is False


# --------------------------------------------------------------------------- #
# 规则 2：必需骨骼链
# --------------------------------------------------------------------------- #
def test_required_bone_chain_hit_and_miss():
    assets = _assets()
    assets["skeleton"]["bones"] = ["root", "pelvis", "spine_01", "head"]
    hit = _run_assets(assets)
    assert _ids(hit) == [_RULE + "required_bone_chain"]
    assert hit.report.findings[0].node == "neck"
    assert hit.passed is False
    assert _is_clean(_run_assets(_assets()))


def test_required_bone_chain_root_bone_not_in_bones():
    assets = _assets()
    assets["skeleton"]["root_bone"] = "armature_root"
    result = _run_assets(assets)
    assert _RULE + "required_bone_chain" in _ids(result)


def test_required_bone_chain_skips_non_humanoid():
    assets = _assets()
    assets["skeleton"]["bones"] = ["root", "prop_a"]
    result = _run_assets(assets)
    assert _RULE + "required_bone_chain" not in _ids(result)


# --------------------------------------------------------------------------- #
# 规则 3：骨骼命名一致性
# --------------------------------------------------------------------------- #
def test_bone_naming_hit_and_miss():
    assets = _assets()
    assets["skeleton"]["bones"] = list(_SKELETON_BONES) + ["rig:extra"]
    hit = _run_assets(assets)
    assert _ids(hit) == [_RULE + "bone_naming"]
    assert hit.report.findings[0].severity.value == "error"
    assert _is_clean(_run_assets(_assets()))


def test_bone_naming_case_collision_is_warning():
    assets = _assets()
    assets["skeleton"]["bones"] = list(_SKELETON_BONES) + ["Root"]
    result = _run_assets(assets)
    assert _RULE + "bone_naming" in _ids(result)
    assert result.passed is True  # WARNING 不阻断


# --------------------------------------------------------------------------- #
# 规则 4：根运动
# --------------------------------------------------------------------------- #
def test_root_motion_hit_and_miss():
    assets = _assets()
    assets["skeleton"].pop("root_bone")
    assets["anim_sequences"][0]["root_motion"] = True
    hit = _run_assets(assets)
    assert _ids(hit) == [_RULE + "root_motion"]
    assert hit.passed is False
    assert _is_clean(_run_assets(_assets()))


def test_root_motion_non_bool_is_warning():
    assets = _assets()
    assets["anim_sequences"][0]["root_motion"] = "true"
    result = _run_assets(assets)
    assert _ids(result) == [_RULE + "root_motion"]
    assert result.report.findings[0].severity.value == "warning"


# --------------------------------------------------------------------------- #
# 规则 5：压缩设置
# --------------------------------------------------------------------------- #
def test_anim_compression_hit_and_miss():
    assets = _assets()
    assets["anim_sequences"][0]["compression"] = "AnimCompress_Bitwise"
    hit = _run_assets(assets)
    assert _ids(hit) == [_RULE + "anim_compression"]
    assert hit.report.findings[0].severity.value == "warning"
    assert hit.passed is True
    assert _is_clean(_run_assets(_assets()))


def test_anim_compression_empty_is_error():
    assets = _assets()
    assets["anim_sequences"][0]["compression"] = ""
    result = _run_assets(assets)
    assert _ids(result) == [_RULE + "anim_compression"]
    assert result.passed is False


def test_anim_compression_without_spec_expectation_skips():
    assets = _assets()
    assets["anim_sequences"][0]["compression"] = "Whatever"
    spec = copy.deepcopy(load_spec())
    spec["ue"].pop("animation_compression")
    assert _RULE + "anim_compression" not in _ids(_run_assets(assets, spec=spec))


# --------------------------------------------------------------------------- #
# 规则 6：曲线丢失
# --------------------------------------------------------------------------- #
def test_curve_loss_hit_and_miss():
    assets = _assets()
    assets["anim_sequences"][0]["curves"] = []
    hit = _run_assets(assets)
    assert _ids(hit) == [_RULE + "curve_loss"]
    assert hit.passed is False
    assert _is_clean(_run_assets(_assets()))


def test_curve_loss_partial_is_warning():
    assets = _assets()
    assets["anim_sequences"][0]["curves"] = ["run_phase"]
    result = _run_assets(assets)
    assert _ids(result) == [_RULE + "curve_loss"]
    assert result.report.findings[0].severity.value == "warning"


def test_curve_loss_missing_curves_field_skips():
    assets = _assets()
    assets["anim_sequences"][0].pop("curves")
    assert _RULE + "curve_loss" not in _ids(_run_assets(assets))


# --------------------------------------------------------------------------- #
# 规则 7：材质槽
# --------------------------------------------------------------------------- #
def test_material_slots_hit_and_miss():
    assets = _assets()
    assets["skeletal_meshes"][0]["material_slots"] = 0
    hit = _run_assets(assets)
    assert _ids(hit) == [_RULE + "material_slots"]
    assert hit.passed is False
    assert _is_clean(_run_assets(_assets()))


def test_material_slots_empty_entry_is_warning():
    assets = _assets()
    assets["skeletal_meshes"][0]["material_slots"] = ["M_Body", ""]
    result = _run_assets(assets)
    assert _ids(result) == [_RULE + "material_slots"]
    assert result.report.findings[0].severity.value == "warning"


def test_material_slots_missing_field_skips():
    assets = _assets()
    assets["skeletal_meshes"][0].pop("material_slots")
    assert _RULE + "material_slots" not in _ids(_run_assets(assets))


# --------------------------------------------------------------------------- #
# 规则 8：LOD allow_deformer
# --------------------------------------------------------------------------- #
def test_lod_allow_deformer_hit_and_miss():
    assets = _assets()
    assets["skeletal_meshes"][0]["lods"][0]["allow_deformer"] = True
    hit = _run_assets(assets)
    assert _ids(hit) == [_RULE + "lod_allow_deformer"]
    assert hit.report.findings[0].severity.value == "warning"
    assert hit.passed is True
    assert _is_clean(_run_assets(_assets()))


def test_lod_allow_deformer_non_bool_is_warning():
    assets = _assets()
    assets["skeletal_meshes"][0]["lods"][1]["allow_deformer"] = 1
    result = _run_assets(assets)
    assert _ids(result) == [_RULE + "lod_allow_deformer"]
    assert result.report.findings[0].severity.value == "warning"


def test_lod_allow_deformer_respects_spec_allow():
    assets = _assets()
    assets["skeletal_meshes"][0]["lods"][0]["allow_deformer"] = True
    spec = copy.deepcopy(load_spec())
    spec["ue"]["allow_mesh_deformer"] = True
    assert _RULE + "lod_allow_deformer" not in _ids(_run_assets(assets, spec=spec))


# --------------------------------------------------------------------------- #
# 规则 9：sidecar 不匹配
# --------------------------------------------------------------------------- #
def test_sidecar_mismatch_hit_and_miss():
    assets = _assets()
    sidecar = copy.deepcopy(_snapshot())
    sidecar["joints"]["ghost"] = _joint("ghost", "root", 1)
    assets["import"]["sidecar"] = {"snapshot": sidecar}
    hit = _run_assets(assets, rule_ids=_RULE + "sidecar_mismatch")
    assert set(_ids(hit)) == {_RULE + "sidecar_mismatch"}
    fields = {item.data.get("field") for item in hit.report.findings}
    assert fields & {"joint_added", "joint_renamed", "joint_count"}, fields
    assert hit.passed is False
    assert _is_clean(_run_assets(_assets()))


def test_sidecar_mismatch_unit_is_warning():
    assets = _assets()
    sidecar = copy.deepcopy(_snapshot())
    sidecar["unit"] = "m"
    assets["import"]["sidecar"] = {"snapshot": sidecar}
    result = _run_assets(assets, rule_ids=_RULE + "sidecar_mismatch")
    assert _ids(result) == [_RULE + "sidecar_mismatch"]
    assert result.report.findings[0].severity.value == "warning"
    assert result.passed is True


def test_sidecar_mismatch_without_joints_skips():
    assets = _assets()
    assets["import"]["sidecar"] = {"snapshot": {"unit": "cm"}}
    assert _RULE + "sidecar_mismatch" not in _ids(_run_assets(assets))


# --------------------------------------------------------------------------- #
# validate_import
# --------------------------------------------------------------------------- #
def test_validate_import_same_snapshot_passes():
    core = _load("core")
    result = core.validate_import({"snapshot": _snapshot()}, _snapshot(),
                                  load_spec())
    assert set(result) == {"passed", "diffs", "blocking", "reason", "ignored"}
    assert result["passed"] is True
    assert result["diffs"] == [] and result["blocking"] == []
    assert result["ignored"] == []
    assert "通过" in result["reason"]


def test_validate_import_accepts_bare_snapshot_and_object():
    from tak.core.diff import SkeletonSnapshot

    core = _load("core")
    snapshot = _snapshot()
    assert core.validate_import(snapshot, snapshot)["passed"] is True
    assert core.validate_import(SkeletonSnapshot.from_dict(snapshot),
                                snapshot)["passed"] is True


def test_validate_import_rename_is_blocking():
    core = _load("core")
    after = copy.deepcopy(_snapshot())
    after["joints"]["hips"] = after["joints"].pop("pelvis")
    result = core.validate_import(_snapshot(), after)
    assert result["passed"] is False
    fields = {item["field"] for item in result["blocking"]}
    assert "joint_renamed" in fields
    assert result["blocking"][0]["message"]
    assert "未通过" in result["reason"]


def test_validate_import_missing_joint_is_blocking():
    core = _load("core")
    after = copy.deepcopy(_snapshot())
    after["joints"].pop("pelvis")
    result = core.validate_import(_snapshot(), after)
    assert result["passed"] is False
    assert {"joint_missing", "joint_count"} <= {
        item["field"] for item in result["blocking"]}


@pytest.mark.parametrize("sidecar,ue_snapshot", [
    (None, None),
    ({}, {}),
    ({"snapshot": {}}, None),
    ({"unit": "cm"}, _snapshot()),
    (_snapshot(), {"metadata": {"ue": {}}}),
    ([], []),
    ("not a snapshot", 42),
])
def test_validate_import_tolerates_empty_and_bad_input(sidecar, ue_snapshot):
    core = _load("core")
    result = core.validate_import(sidecar, ue_snapshot)
    assert set(result) == {"passed", "diffs", "blocking", "reason", "ignored"}
    assert result["passed"] is True  # 无法比对时绝不误判为失败
    assert result["diffs"] == []
    assert result["reason"]


def test_validate_import_ignores_bind_matrix_by_default():
    core = _load("core")
    after = copy.deepcopy(_snapshot())
    after["joints"]["root"]["matrix"] = [2.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0,
                                         0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0]
    result = core.validate_import(_snapshot(), after, load_spec())
    assert result["passed"] is True
    assert result["diffs"] == []
    assert result["ignored"] == ["bind_matrix"]
    assert "忽略" in result["reason"]


def test_validate_import_can_check_bind_matrix():
    core = _load("core")
    after = copy.deepcopy(_snapshot())
    after["joints"]["root"]["matrix"] = [2.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0,
                                         0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0]
    spec = {"ue": {"import_check_bind_matrices": True}}
    result = core.validate_import(_snapshot(), after, spec)
    assert result["passed"] is False
    assert {item["field"] for item in result["blocking"]} == {"bind_matrix"}


def test_validate_import_honours_ignore_fields_and_warn_fields():
    core = _load("core")
    after = copy.deepcopy(_snapshot())
    after["unit"] = "m"
    after["frame_range"] = [1.0, 60.0]
    result = core.validate_import(_snapshot(), after,
                                  {"ue": {"import_ignore_fields": ["unit"]}})
    assert result["passed"] is True
    fields = {item["field"] for item in result["diffs"]}
    assert "unit" not in fields
    assert "frame_range" in fields
    assert "unit" in result["ignored"]
    assert result["blocking"] == []


def test_validate_import_accepts_export_bridge_sidecar(tmp_path):
    """Export Bridge 写出的 sidecar（{"snapshot": ...}）可直接被本工具消费。"""
    export_core = registry.load_plugin_module(
        runner.find_plugin("export_bridge"), "core")
    path = tmp_path / "rig.tak_snapshot.json"
    export_core.pre_export(_snapshot(), str(path))
    with open(str(path), "r", encoding="utf-8") as fh:
        payload = json.load(fh)
    assert payload["tool"] == "export_bridge"
    assert export_core.ue_import_hook(payload)["target"] == TOOL_ID
    result = _load("core").validate_import(payload, _snapshot())
    assert result["passed"] is True
    assert result["diffs"] == []


def test_run_report_validation_blocking_becomes_blocker_finding():
    after = copy.deepcopy(_snapshot())
    after["joints"]["hips"] = after["joints"].pop("pelvis")
    assets = _assets()
    assets["import"]["sidecar"] = {"snapshot": after}
    result = _run_assets(assets)
    assert result.passed is False
    assert _RULE + "import_blocking_diff" in _ids(result)
    validate = result.report.metadata["validate"]
    assert validate["compared"] is True
    assert validate["blocking_count"] >= 1
    assert validate["passed"] is False


def test_run_report_validation_success_adds_no_finding():
    result = _run_assets(_assets())
    assert result.passed is True
    assert result.report.findings == []
    assert result.report.metadata["validate"]["compared"] is True
    assert result.report.metadata["validate"]["passed"] is True


# --------------------------------------------------------------------------- #
# metadata / api_notes
# --------------------------------------------------------------------------- #
def test_metadata_contains_rules_validate_and_api_notes():
    result = _run_assets(_assets())
    metadata = result.report.metadata
    assert metadata["rules_count"] == len(RULE_IDS)
    assert isinstance(metadata["duration_ms"], float)
    assert set(metadata["validate"]) == {"compared", "passed", "diff_count",
                                         "blocking_count", "ignored", "reason"}
    symbols = {item["symbol"] for item in metadata["api_notes"]}
    assert {"MLDeformerAsset", "NeuralMorphModel", "DeformerGraphAsset",
            "EditorDialogLibrary"} <= symbols
    for note in metadata["api_notes"]:
        assert note["source"] == "validation/ue_adapter_probe_result.json"
        assert note["note"]
    assert "EditorDialog" in [item["note"] for item in metadata["api_notes"]
                              if item["symbol"] == "EditorDialogLibrary"][0]


# --------------------------------------------------------------------------- #
# 渲染
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("fmt", ["json", "md", "html"])
def test_report_renders_three_formats(fmt):
    result = _run_assets(_assets())
    text = result.render(fmt)
    assert isinstance(text, str) and text
    if fmt == "json":
        assert TOOL_ID in text
        assert "api_notes" in result.report.to_json()
    elif fmt == "md":
        assert "UE Import Guard" in text
    else:
        assert "tak-panel" in text


def test_web_panel_fragment_contains_theme_classes():
    assets = _assets()
    after = copy.deepcopy(_snapshot())
    after["joints"]["hips"] = after["joints"].pop("pelvis")
    assets["import"]["sidecar"] = {"snapshot": after}
    ui = _load("ui_web")
    html = ui.render_panel_html(_run_assets(assets))
    for token in ("tak-panel", "tak-header", "tak-meta", "tak-state",
                  "tak-state-fail", "tak-table", "tak-row", "tak-badge",
                  "tak-sev-error", "tak-hint", "API 说明"):
        assert token in html, token
    assert "快照 diff" in html
    assert "joint_renamed" in html
    assert "<html" not in html and "<body" not in html


def test_web_panel_has_no_hardcoded_colour_or_external_link():
    ui = _load("ui_web")
    html = ui.render_panel_html(_run_assets(_assets()))
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b", html), "禁止硬编码十六进制颜色"
    assert "rgb(" not in html and "hsl(" not in html
    assert "http://" not in html and "https://" not in html
    assert "<link" not in html and "<script" not in html


def test_web_panel_pass_state_and_report_html():
    ui = _load("ui_web")
    result = _run_assets(_assets())
    html = ui.render_panel_html(result)
    assert "tak-state-pass" in html
    report_html = ui.render_report_html(result.report)
    assert "tak-panel" in report_html and "tak-table" in report_html
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b", report_html)


def test_web_panel_escapes_html_in_messages():
    assets = _assets()
    assets["skeletal_meshes"][0]["name"] = "/Game/<script>alert(1)</script>"
    assets["skeletal_meshes"][0]["material_slots"] = 0
    ui = _load("ui_web")
    html = ui.render_panel_html(_run_assets(assets))
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


# --------------------------------------------------------------------------- #
# UI
# --------------------------------------------------------------------------- #
def test_ui_show_returns_none_without_qt():
    from tak.ui.qt import HAS_QT

    ui = _load("ui_qt")
    if HAS_QT:
        pytest.skip("本机安装 Qt，无 Qt 降级分支不适用")
    assert ui.show() is None
    assert ui.show(dcc="unreal") is None


def test_ui_module_imports_without_qt():
    ui = _load("ui_qt")
    assert callable(ui.show)
    assert ui.TOOL_ID == TOOL_ID


# --------------------------------------------------------------------------- #
# 内部断言助手（放在文件末尾，避免 pytest 收集）
# --------------------------------------------------------------------------- #
def _is_clean(result):
    """断言「干净数据不命中任何规则」。"""
    return result.report.findings == [] and result.passed is True
