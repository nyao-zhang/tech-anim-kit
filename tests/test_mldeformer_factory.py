# -*- coding: utf-8 -*-
"""P6.2 ML Deformer 数据工厂：核心采样 / 清单 / 门槛 / 插件 / 双端 UI。

覆盖重点
--------
* 采样在真实 fixture 上非空、类别齐全、角度不越界；
* 确定性硬断言：同一输入两次构建的 version / pose_digest 完全相同，
  改一个角度 digest 必变；
* 每条质量门槛各触发一次；
* 空快照 / 无 joints / 畸形类型不抛异常；
* 插件 core 与 run_report 的 JSON 安全、safety.training_api is False；
* 反向断言：``tak/core/mldeformer`` 与 ``tools/mldeformer_factory`` 里不存在
  任何引擎 import（不碰 unreal / maya / pymxs / pyfbsdk）。
"""
from __future__ import annotations

import ast
import copy
import json
import os
import re
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from tak.core import registry, runner  # noqa: E402
from tak.core.diff import SkeletonSnapshot  # noqa: E402
from tak.core.mldeformer import (  # noqa: E402
    CATEGORY_ORDER, DATASET_CONTRACT, build_manifest, build_plan,
    category_order, iter_pose_keys, joint_angle_range, quality_gates,
    sample_summary, validate_manifest)
from tak.core.mldeformer.dataset import DEFAULT_GATES, gate_codes  # noqa: E402
from tak.core.report import Report  # noqa: E402

TOOL_ID = "mldeformer_factory"
PLUGIN_DIR = os.path.join(ROOT, "tools", TOOL_ID)
CORE_DIR = os.path.join(ROOT, "tak", "core", "mldeformer")
FIXTURE = os.path.join(ROOT, "fixtures", "good", "good_001.json")
FORBIDDEN_ROOTS = ("maya", "pymxs", "pyfbsdk", "unreal")

#: 与采样器一致的启发式上限，测试里独立写死一份，避免「自己验自己」。
HEURISTIC = (("lowerarm", 150.0), ("calf", 150.0), ("upperarm", 180.0),
             ("clavicle", 180.0), ("spine", 45.0), ("pelvis", 45.0),
             ("neck", 60.0), ("head", 60.0), ("hand", 90.0), ("foot", 90.0),
             ("thigh", 120.0))


@pytest.fixture(scope="module")
def snapshot():
    with open(FIXTURE, "r", encoding="utf-8") as fh:
        return json.load(fh)["snapshot"]


@pytest.fixture(scope="module")
def plan(snapshot):
    return build_plan(snapshot)


@pytest.fixture(scope="module")
def manifest(plan):
    return build_manifest(plan)


def _plugin():
    return runner.find_plugin(TOOL_ID)


def _core():
    return registry.load_plugin_module(_plugin(), "core")


def _ui(name):
    return registry.load_plugin_module(_plugin(), name)


def _jsonable(value):
    return json.loads(json.dumps(value, ensure_ascii=False, allow_nan=False))


def _codes(validation):
    return [issue["code"] for issue in validation["issues"]]


def _limit_for(joint):
    token = re.sub(r"[^a-z]", "", joint.lower())
    for key, limit in HEURISTIC:
        if key in token:
            return limit
    return 90.0


# --------------------------------------------------------------------------- #
# build_plan：内容与不变量
# --------------------------------------------------------------------------- #
def test_plan_contract_and_shape(plan, snapshot):
    assert plan.contract == DATASET_CONTRACT == "1.0"
    assert plan.joint_count == len(snapshot["joints"]) == 19
    assert isinstance(plan.poses, list) and plan.poses
    assert plan.seed == 20240101
    for pose in plan.poses:
        assert set(pose) == {"pose_id", "category", "joint", "axis", "angle",
                             "label", "tags"}
        assert isinstance(pose["angle"], float)
        assert isinstance(pose["tags"], list)


def test_plan_covers_required_categories(plan):
    present = set(plan.categories)
    assert {"extreme", "mid", "grid", "rest"} <= present
    assert present <= set(CATEGORY_ORDER)


def test_plan_angles_never_exceed_heuristic(plan):
    for pose in plan.poses:
        limit = _limit_for(pose["joint"])
        assert abs(pose["angle"]) <= limit + 1e-6, pose


def test_plan_sorted_deterministically(plan):
    rank = {name: index for index, name in enumerate(CATEGORY_ORDER)}
    axis_rank = {"*": -1, "x": 0, "y": 1, "z": 2}
    keys = [(rank[pose["category"]], pose["joint"], axis_rank[pose["axis"]],
             pose["angle"]) for pose in plan.poses]
    assert keys == sorted(keys)


def test_plan_deduplicates_pose_keys(plan):
    keys = iter_pose_keys(plan.poses)
    assert len(keys) == len(set(keys))


def test_plan_is_byte_identical_across_runs(snapshot):
    first = build_plan(snapshot)
    second = build_plan(snapshot)
    left = json.dumps(first.to_dict(), ensure_ascii=False, sort_keys=True)
    right = json.dumps(second.to_dict(), ensure_ascii=False, sort_keys=True)
    assert left == right


def test_plan_random_category_is_seeded(snapshot):
    options = {"categories": ["random"], "seed": 7, "max_poses": 2000}
    first = build_plan(snapshot, options)
    second = build_plan(snapshot, options)
    assert [pose["angle"] for pose in first.poses] == \
           [pose["angle"] for pose in second.poses]
    other = build_plan(snapshot, dict(options, seed=8))
    assert [pose["angle"] for pose in first.poses] != \
           [pose["angle"] for pose in other.poses]


def test_plan_include_rest_false(snapshot):
    result = build_plan(snapshot, {"include_rest": False})
    assert "rest" not in result.categories
    assert all(pose["category"] != "rest" for pose in result.poses)


def test_plan_max_poses_truncates(snapshot):
    result = build_plan(snapshot, {"max_poses": 20})
    assert len(result.poses) == 20
    assert any("max_poses=20" in item for item in result.warnings)


def test_plan_max_poses_hard_cap(snapshot):
    result = build_plan(snapshot, {"max_poses": 99999})
    assert len(result.poses) <= 2000
    assert any("硬上限" in item for item in result.warnings)


def test_plan_invalid_max_poses_falls_back(snapshot):
    result = build_plan(snapshot, {"max_poses": "很多"})
    assert len(result.poses) <= 240
    assert any("max_poses" in item for item in result.warnings)


def test_plan_limits_are_hard_bounds(snapshot):
    result = build_plan(snapshot, {
        "limits": {"upperarm_L": {"x": [-10, 10]}},
        "categories": ["extreme", "mid", "grid", "random"],
        "max_poses": 2000,
    })
    angles = [pose["angle"] for pose in result.poses
              if pose["joint"] == "upperarm_L" and pose["axis"] == "x"]
    assert angles
    assert max(abs(angle) for angle in angles) <= 10 + 1e-6


def test_plan_limits_flat_pair_and_scalar(snapshot):
    result = build_plan(snapshot, {
        "limits": {"neck": [-5, 5], "head": 12},
        "categories": ["extreme"],
    })
    neck = [pose["angle"] for pose in result.poses if pose["joint"] == "neck"]
    head = [pose["angle"] for pose in result.poses if pose["joint"] == "head"]
    assert neck and max(abs(angle) for angle in neck) <= 5 + 1e-6
    assert head and max(abs(angle) for angle in head) <= 12 + 1e-6


def test_plan_mirror_appears_when_limits_asymmetric(snapshot):
    result = build_plan(snapshot, {
        "limits": {"upperarm_L": {"x": [-30, 30]},
                   "upperarm_R": {"x": [-90, 90]}},
        "categories": ["extreme", "mirror"],
        "max_poses": 2000,
    })
    assert result.categories.get("mirror", 0) > 0
    for pose in result.poses:
        if pose["category"] != "mirror":
            continue
        assert abs(pose["angle"]) <= 90 + 1e-6
        assert "mirror" in pose["tags"]


def test_plan_symmetric_limits_merge_mirror_into_extreme(plan):
    # 对称骨架的镜像角度与对侧极值完全重合，去重后只留一条并带 mirror 标记。
    assert plan.categories.get("mirror", 0) == 0
    tagged = [pose for pose in plan.poses if "mirror" in pose["tags"]]
    assert tagged
    assert any("mirror" in note for note in plan.notes)


def test_plan_unknown_category_warns(snapshot):
    result = build_plan(snapshot, {"categories": ["extreme", "不存在的类别"]})
    assert any("未知类别" in item for item in result.warnings)
    assert set(result.categories) == {"extreme"}


def test_plan_empty_category_selection_returns_empty(snapshot):
    result = build_plan(snapshot, {"categories": []})
    assert result.poses == []
    assert result.warnings


def test_plan_empty_snapshot_is_safe():
    for bad in (None, {}, {"joints": {}}, {"joints": []}):
        result = build_plan(bad)
        assert result.poses == []
        assert result.contract == DATASET_CONTRACT
        assert result.warnings
        assert _jsonable(result.to_dict()) is not None


def test_plan_malformed_inputs_do_not_raise():
    for bad in ("不是字典", 123, 4.5, [None, 3, {"name": ""}],
                {"joints": "坏数据"}, {"joints": [1, 2, 3]}, object()):
        result = build_plan(bad)
        assert result.poses == []
        assert result.joint_count == 0


def test_plan_accepts_joint_list():
    result = build_plan({"joints": [{"name": "root"}, {"name": "neck"}]})
    assert result.joint_count == 2
    assert result.poses
    assert {pose["joint"] for pose in result.poses} == {"root", "neck"}


def test_plan_accepts_skeleton_snapshot_object(snapshot, plan):
    as_object = SkeletonSnapshot.from_dict(snapshot)
    assert [pose["pose_id"] for pose in build_plan(as_object).poses] == \
           [pose["pose_id"] for pose in plan.poses]


def test_plan_limit_warnings_for_bad_limits(snapshot):
    result = build_plan(snapshot, {"limits": "坏", "max_poses": 10})
    assert any("limits" in item for item in result.warnings)
    bad_axis = build_plan(snapshot, {"limits": {"neck": {"w": [0, 1]}},
                                     "max_poses": 10})
    assert any("未知轴" in item for item in bad_axis.warnings)


# --------------------------------------------------------------------------- #
# build_manifest：内容寻址与确定性
# --------------------------------------------------------------------------- #
def test_manifest_deterministic_ids(plan):
    first = build_manifest(plan)
    second = build_manifest(plan)
    assert first["dataset_id"] == second["dataset_id"]
    assert first["version"] == second["version"]
    assert first["pose_digest"] == second["pose_digest"]
    assert json.dumps(first, ensure_ascii=False, sort_keys=True) == \
           json.dumps(second, ensure_ascii=False, sort_keys=True)


def test_manifest_digest_changes_when_angle_changes(plan):
    base = build_manifest(plan)
    mutated = plan.to_dict()
    mutated["poses"][0]["angle"] = mutated["poses"][0]["angle"] + 1.0
    changed = build_manifest(mutated)
    assert changed["pose_digest"] != base["pose_digest"]
    assert changed["dataset_id"] != base["dataset_id"]


def test_manifest_fields_and_totals(manifest):
    for key in ("contract", "dataset_id", "version", "pose_count", "joint_count",
                "categories", "coverage", "pose_digest", "seed", "gates",
                "generated_by", "notes"):
        assert key in manifest
    assert manifest["generated_by"] == "tak.core.mldeformer"
    assert manifest["contract"] == DATASET_CONTRACT
    assert sum(manifest["coverage"].values()) == manifest["pose_count"]
    assert manifest["joint_count"] == len(manifest["coverage"])
    assert len(manifest["dataset_id"]) == 16
    assert len(manifest["pose_digest"]) == 16
    assert manifest["duplicate_ratio"] == 0.0
    assert "generated_at" not in manifest


def test_manifest_is_json_safe(manifest):
    dumped = json.dumps(manifest, ensure_ascii=False, allow_nan=False)
    assert dumped
    assert _jsonable(manifest) == _jsonable(manifest)


def test_manifest_of_empty_plan_is_safe():
    manifest = build_manifest(None)
    assert manifest["pose_count"] == 0
    assert manifest["coverage"] == {}
    assert manifest["categories"] == {}
    assert len(manifest["dataset_id"]) == 16
    json.dumps(manifest, ensure_ascii=False, allow_nan=False)


def test_manifest_counts_duplicates(snapshot):
    plan_data = build_plan(snapshot, {"categories": ["extreme"]}).to_dict()
    plan_data["poses"] = [dict(plan_data["poses"][0]) for _ in range(30)]
    manifest = build_manifest(plan_data)
    assert manifest["pose_count"] == 30
    assert manifest["duplicates"] == 29
    assert manifest["duplicate_ratio"] == pytest.approx(round(29 / 30.0, 4))


# --------------------------------------------------------------------------- #
# validate_manifest：默认通过
# --------------------------------------------------------------------------- #
def test_validate_default_manifest_passes(manifest):
    result = validate_manifest(manifest)
    assert result["ok"] is True
    assert result["issues"] == []
    assert result["counts"] == {"error": 0, "warning": 0, "info": 0}
    assert result["contract"] == DATASET_CONTRACT
    assert set(result) == {"ok", "contract", "issues", "counts", "gates"}


def test_gate_codes_are_stable():
    codes = gate_codes()
    for code in ("mld.pose_count.too_low", "mld.pose_count.over_budget",
                 "mld.coverage.missing_joint", "mld.coverage.low",
                 "mld.duplicate.too_high", "mld.category.missing",
                 "mld.extreme.missing", "mld.contract.mismatch",
                 "mld.manifest.invalid"):
        assert code in codes


# --------------------------------------------------------------------------- #
# 每条门槛各触发一次
# --------------------------------------------------------------------------- #
def test_gate_contract_mismatch(manifest):
    broken = copy.deepcopy(manifest)
    broken["contract"] = "0.9"
    validation = validate_manifest(broken)
    issues = [item for item in validation["issues"]
              if item["code"] == "mld.contract.mismatch"]
    assert issues and issues[0]["severity"] == "error"
    assert validation["ok"] is False


def test_gate_pose_count_too_low(manifest):
    broken = copy.deepcopy(manifest)
    broken["pose_count"] = 3
    validation = validate_manifest(broken)
    assert "mld.pose_count.too_low" in _codes(validation)


def test_gate_pose_count_over_budget(manifest):
    broken = copy.deepcopy(manifest)
    broken["pose_count"] = 3000
    validation = validate_manifest(broken)
    assert "mld.pose_count.over_budget" in _codes(validation)


def test_gate_coverage_missing_joint(manifest):
    broken = copy.deepcopy(manifest)
    broken["joints"] = sorted(broken["coverage"]) + ["ghost_joint"]
    broken["joint_count"] = len(broken["joints"])
    validation = validate_manifest(broken)
    issues = [item for item in validation["issues"]
              if item["code"] == "mld.coverage.missing_joint"]
    assert issues and issues[0]["data"]["joint"] == "ghost_joint"
    assert issues[0]["severity"] == "error"


def test_gate_coverage_missing_joint_without_joint_list(manifest):
    broken = copy.deepcopy(manifest)
    broken.pop("joints")
    broken["joint_count"] = broken["joint_count"] + 2
    validation = validate_manifest(broken)
    assert "mld.coverage.missing_joint" in _codes(validation)


def test_gate_coverage_low(manifest):
    broken = copy.deepcopy(manifest)
    broken["coverage"] = dict(broken["coverage"])
    broken["coverage"]["neck"] = 1
    validation = validate_manifest(broken)
    issues = [item for item in validation["issues"]
              if item["code"] == "mld.coverage.low"]
    assert issues and issues[0]["severity"] == "warning"
    assert validation["ok"] is True


def test_gate_duplicate_too_high(manifest):
    broken = copy.deepcopy(manifest)
    broken["duplicates"] = 120
    broken["duplicate_ratio"] = 0.5
    validation = validate_manifest(broken)
    assert "mld.duplicate.too_high" in _codes(validation)


def test_gate_category_missing(manifest):
    validation = validate_manifest(manifest, {"require_categories": ["mirror"]})
    issues = [item for item in validation["issues"]
              if item["code"] == "mld.category.missing"]
    assert issues and issues[0]["data"]["category"] == "mirror"
    assert issues[0]["severity"] == "warning"


def test_gate_extreme_missing(manifest):
    broken = copy.deepcopy(manifest)
    broken["categories"] = {name: count for name, count
                            in broken["categories"].items() if name != "extreme"}
    validation = validate_manifest(broken, {"require_categories": []})
    assert "mld.extreme.missing" in _codes(validation)
    assert "mld.category.missing" not in _codes(validation)


def test_validate_malformed_manifest_is_safe():
    for bad in (None, "坏清单", [], 42, object()):
        result = validate_manifest(bad)
        assert result["ok"] is False
        assert result["issues"][0]["code"] == "mld.manifest.invalid"
        assert result["counts"]["error"] == 1
        json.dumps(result, ensure_ascii=False, allow_nan=False)


def test_gate_thresholds_can_be_overridden(manifest):
    strict = validate_manifest(manifest, {"min_total_poses": 5000})
    assert "mld.pose_count.too_low" in _codes(strict)
    loose = validate_manifest(manifest, {"min_poses_per_joint": 0,
                                        "max_duplicate_ratio": 1.0})
    assert loose["ok"] is True


# --------------------------------------------------------------------------- #
# quality_gates / sample_summary
# --------------------------------------------------------------------------- #
def test_quality_gates_defaults_and_merge():
    gates = quality_gates()
    assert gates["min_total_poses"] == DEFAULT_GATES["min_total_poses"] == 24
    assert gates["max_total_poses"] == 2000
    assert gates["min_poses_per_joint"] == 4
    assert gates["max_duplicate_ratio"] == 0.1
    assert gates["require_categories"] == ["extreme", "mid"]
    assert gates["require_contract"] == DATASET_CONTRACT
    merged = quality_gates({"min_total_poses": 5, "require_categories": ["grid"]})
    assert merged["min_total_poses"] == 5
    assert merged["require_categories"] == ["grid"]
    assert merged["min_poses_per_joint"] == 4


def test_quality_gates_ignore_invalid_overrides():
    gates = quality_gates({"min_total_poses": "多", "max_duplicate_ratio": 5,
                           "require_categories": 3})
    assert gates["min_total_poses"] == 24
    assert gates["max_duplicate_ratio"] == 0.1
    assert gates["require_categories"] == ["extreme", "mid"]


def test_sample_summary_shape(plan):
    summary = sample_summary(plan)
    assert summary["pose_count"] == len(plan.poses)
    assert summary["joint_count"] == plan.joint_count
    assert summary["categories"] == plan.categories
    assert 0 < len(summary["top_joints"]) <= 5
    assert summary["angle_range"]["min"] <= summary["angle_range"]["max"]
    assert _jsonable(summary)


def test_sample_summary_empty_plan():
    summary = sample_summary({"poses": [], "joint_count": 0})
    assert summary["pose_count"] == 0
    assert summary["joint_count"] == 0
    assert summary["categories"] == {}
    assert summary["angle_range"] == {"min": 0.0, "max": 0.0}


def test_joint_angle_range_respects_limits():
    low, high = joint_angle_range("upperarm_L", "x", {"upperarm_L": {"x": [-5, 9]}})
    assert (low, high) == (-5.0, 9.0)
    assert joint_angle_range("neck", "x") == (-60.0, 60.0)
    assert category_order() == list(CATEGORY_ORDER)


# --------------------------------------------------------------------------- #
# 插件 core
# --------------------------------------------------------------------------- #
def test_plugin_manifest_contract():
    plugin = _plugin()
    assert plugin.id == TOOL_ID
    assert plugin.ui == "both"
    assert plugin.permissions == ["scene.read"]
    assert set(plugin.dcc) == {"maya", "max", "mobu", "unreal"}
    with open(os.path.join(PLUGIN_DIR, "manifest.json"), encoding="utf-8") as fh:
        payload = json.load(fh)
    assert registry.validate_manifest(payload) == []
    assert "rules" not in payload
    for name in ("core.py", "ui_web.py", "ui_qt.py", "manifest.json"):
        assert os.path.isfile(os.path.join(PLUGIN_DIR, name))


def test_plugin_info_declares_no_training():
    info = _core().info()
    assert info["id"] == TOOL_ID
    assert info["training_api"] is False
    assert info["read_only"] is True
    assert info["permissions"] == ["scene.read"]
    assert info["contract"] == DATASET_CONTRACT
    assert "未暴露" in info["limitation"]
    assert _jsonable(info)


def test_plugin_plan_returns_json_safe_payload(snapshot):
    payload = _core().plan(snapshot)
    assert payload["ok"] is True
    assert payload["plan"]["poses"]
    assert payload["summary"]["pose_count"] == len(payload["plan"]["poses"])
    assert payload["safety"]["training_api"] is False
    assert _jsonable(payload)


def test_plugin_manifest_and_validate(snapshot):
    core = _core()
    payload = core.manifest(snapshot)
    assert payload["ok"] is True
    assert payload["manifest"]["dataset_id"]
    assert payload["validation"]["ok"] is True
    assert _jsonable(payload)
    assert core.validate(payload["manifest"])["ok"] is True
    assert core.validate(None)["ok"] is False


def test_plugin_manifest_honours_gate_overrides(snapshot):
    payload = _core().manifest(snapshot, {"min_total_poses": 100000})
    assert payload["ok"] is False
    assert "mld.pose_count.too_low" in _codes(payload["validation"])


def test_plugin_plan_handles_empty_snapshot():
    payload = _core().plan(None)
    assert payload["ok"] is True
    assert payload["plan"]["poses"] == []
    assert payload["warnings"]
    assert _jsonable(payload)


# --------------------------------------------------------------------------- #
# run_report
# --------------------------------------------------------------------------- #
def _ctx(snapshot, options=None):
    return runner.ToolContext(dcc="maya", snapshot=snapshot or {},
                              spec={"version": "1.0.0"},
                              options=dict(options or {}), tool_id=TOOL_ID,
                              plugin=_plugin())


def test_run_report_on_good_snapshot(snapshot):
    report = _core().run_report(_ctx(snapshot))
    assert isinstance(report, Report)
    assert report.findings
    assert report.findings[-1].rule_id == "mldeformer_factory.summary"
    metadata = report.metadata
    for key in ("plan", "manifest", "issues", "safety", "summary"):
        assert key in metadata
    assert metadata["safety"] == {
        "read_only": True, "scene_write": False, "network": False,
        "training_api": False,
        "note": ("仅产出数据集清单与质量门槛报告；UE 侧 mldeformer.train 能力位为 "
                 "false，本工具不调用任何训练入口"),
    }
    assert metadata["safety"]["training_api"] is False
    assert metadata["plan"]["truncated"] is True
    assert len(metadata["plan"]["poses"]) <= 40
    assert len(json.dumps(report.to_dict(), ensure_ascii=False)) > 0


def test_run_report_emits_error_findings_from_issues():
    report = _core().run_report(_ctx({}))
    codes = [finding.rule_id for finding in report.findings]
    assert "mld.pose_count.too_low" in codes
    assert report.passed is False
    error = next(finding for finding in report.findings
                 if finding.rule_id == "mld.pose_count.too_low")
    assert error.severity.value == "error"
    assert error.fix_hint
    assert error.data["code"] == "mld.pose_count.too_low"


def test_run_report_reads_nested_options(snapshot):
    report = _core().run_report(_ctx(snapshot, {"mldeformer": {"max_poses": 12}}))
    assert report.metadata["plan"]["pose_count"] == 12
    assert len(report.metadata["manifest"]["coverage"]) > 0


def test_run_report_with_skeleton_snapshot_object(snapshot):
    report = _core().run_report(_ctx(SkeletonSnapshot.from_dict(snapshot).to_dict()))
    assert report.metadata["manifest"]["pose_count"] > 0


def test_markdown_summary_sections(snapshot):
    core = _core()
    payload = core.manifest(snapshot)
    text = core.markdown_summary(payload["manifest"], payload["validation"])
    assert text.startswith("## ML Deformer 数据清单")
    assert "### 质量门槛" in text
    assert "### 类别分布" in text
    assert "mldeformer.train" in text
    assert "仅产出数据集清单与质量门槛报告" in text
    assert payload["manifest"]["dataset_id"] in text


def test_markdown_summary_handles_bad_input():
    text = _core().markdown_summary(None, None)
    assert "## ML Deformer 数据清单" in text
    assert "未通过" in text or "通过" in text


# --------------------------------------------------------------------------- #
# UI（Web / Qt）
# --------------------------------------------------------------------------- #
def test_ui_web_escapes_and_has_no_external_links(snapshot):
    ui = _ui("ui_web")
    payload = _core().manifest(snapshot)
    payload["validation"]["issues"] = [{
        "code": "mld.contract.mismatch",
        "severity": "error",
        "message": '<script>alert("x")</script> https://evil.example',
        "data": {},
    }]
    html = ui.render_panel_html(payload)
    assert "tak-panel" in html
    assert "<script" not in html.lower()
    assert "http://" not in html and "https://" not in html
    assert "&lt;script&gt;" in html
    assert "href=" not in html and "src=" not in html
    assert ui.render_report_html(payload) == html


def test_ui_web_handles_report_and_empty_payload():
    ui = _ui("ui_web")
    report = _core().run_report(_ctx({}))
    assert "tak-state-fail" in ui.render_report_html(report)
    assert "tak-panel" in ui.render_panel_html({})


def test_ui_qt_imports_without_qt():
    assert _ui("ui_qt").show() is None


# --------------------------------------------------------------------------- #
# 反向断言：不得出现任何引擎 / 训练调用
# --------------------------------------------------------------------------- #
def _sources(directory):
    found = []
    for dirpath, dirnames, filenames in os.walk(directory):
        dirnames[:] = sorted(name for name in dirnames if name != "__pycache__")
        for name in sorted(filenames):
            if name.endswith(".py"):
                found.append(os.path.join(dirpath, name))
    return found


def _import_roots(source, path):
    tree = ast.parse(source, filename=path, feature_version=(3, 9))
    roots = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.extend(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots.append(node.module.split(".")[0])
    return roots


def test_sources_are_python39_and_engine_free():
    files = _sources(CORE_DIR) + _sources(PLUGIN_DIR)
    assert len(files) >= 6
    for path in files:
        with open(path, "r", encoding="utf-8") as fh:
            source = fh.read()
        roots = _import_roots(source, path)
        assert not (set(roots) & set(FORBIDDEN_ROOTS)), path
        assert "ml_deformer" not in roots, path
        assert re.search(r"(?m)^\s*import\s+unreal\b", source) is None, path


def test_no_training_entrypoint_is_claimed():
    files = _sources(CORE_DIR) + _sources(PLUGIN_DIR)
    banned = re.compile(r"(start_train|begin_training|train_model|"
                        r"MLDeformerAsset\.|NeuralMorphModel\()")
    for path in files:
        with open(path, "r", encoding="utf-8") as fh:
            source = fh.read()
        assert banned.search(source) is None, path
