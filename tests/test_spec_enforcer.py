# -*- coding: utf-8 -*-
"""Spec Enforcer（P2.3）回归测试。

覆盖面
------
* manifest 合法性、三端发现、Maya 规则与 DSL 动作注册；
* golden fixtures：未显式开启校验时零误报（40 份全过）；
* 显式开启后：20 份 good 全部通过，bad 命中对应规则；
* plan_fixes 只产出计划、不改快照、不执行；preview 为 dry-run diff；
* DSL 执行器语义：dry-run 无副作用、缺权限 / 未确认报错、成功提交、失败整体回滚；
* NullAdapter（宿主无对应方法）时安全 no-op；
* ui_qt.show() 无 Qt 返回 None。

全部离线：只使用 NullAdapter / RecordingAdapter 与 fixtures，不接触真实 DCC。
"""
from __future__ import annotations

import copy
import json
import os
import sys

import pytest

from tak.adapters.base import NullAdapter
from tak.core import registry, runner
from tak.core.dsl import ACTION_REGISTRY, DSLError
from tak.core.rules import RuleContext
from tak.core.rules.engine import list_rules
from tak.core.spec import load_spec

TOOL_ID = "spec_enforcer"
_RULE = TOOL_ID + "."
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_FIXTURES = os.path.join(_ROOT, "fixtures")
_PLUGIN_DIR = os.path.join(_ROOT, "tools", TOOL_ID)

RULE_IDS = (
    _RULE + "naming_pattern",
    _RULE + "required_joints",
    _RULE + "max_depth",
    _RULE + "weight_sum",
    _RULE + "influence_limit",
    _RULE + "control_namespace",
    _RULE + "control_color",
)

ACTION_IDS = (
    _RULE + "rename_joint",
    _RULE + "set_control_color",
    _RULE + "normalize_weights",
    _RULE + "set_namespace",
)

_IDENTITY = [1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0,
             0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0]


# --------------------------------------------------------------------------- #
# 工具函数 / fixture
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


def _clean_snapshot():
    """直接复用 golden good 快照，保证「合规场景」不依赖手写数据。"""
    return copy.deepcopy(_fixture("good", "good_001.json")["snapshot"])


def _enforced(snapshot):
    """给快照打上插件专属作用域字段（模拟 Maya 侧显式开启校验）。"""
    data = copy.deepcopy(snapshot)
    metadata = data.get("metadata")
    metadata = dict(metadata) if isinstance(metadata, dict) else {}
    metadata["spec_enforcer"] = {"enabled": True}
    data["metadata"] = metadata
    return data


def _ctx(snapshot, options=None, spec=None):
    return RuleContext(snapshot=snapshot, spec=spec or load_spec(), dcc="maya",
                       options=dict(options or {}))


def _tool_ctx(snapshot, options=None, spec=None):
    return runner.ToolContext(dcc="maya", snapshot=snapshot,
                             spec=spec or load_spec(), adapter=NullAdapter(),
                             options=dict(options or {}), tool_id=TOOL_ID)


def _run(snapshot, options=None):
    return runner.run_tool(TOOL_ID, snapshot, dcc="maya", adapter=NullAdapter(),
                           options=dict(options or {}))


def _ids(findings):
    return [item.rule_id for item in findings]


class RecordingAdapter(NullAdapter):
    """记录 adapter 交互，用于断言「写场景」确实经过 adapter 且可回滚。"""

    def __init__(self, fail_color=False):
        super().__init__()
        self.events = []
        self.fail_color = fail_color

    def begin_undo(self, label="TAK"):
        self.events.append("begin")
        return "token"

    def commit_undo(self, token):
        self.events.append("commit")

    def rollback(self, token):
        self.events.append("rollback")

    def log(self, message, level="info"):
        self.events.append("log:%s" % level)

    def set_control_color(self, node, color):
        if self.fail_color:
            raise RuntimeError("host rejected")
        self.events.append("color:%s:%s" % (node, color))

    def set_namespace(self, node, namespace):
        self.events.append("ns:%s:%s" % (node, namespace))

    def normalize_weights(self, **kwargs):
        self.events.append("weights:%s" % sorted(kwargs.items()))

    def rename_node(self, node, name):
        self.events.append("rename:%s:%s" % (node, name))


@pytest.fixture(scope="module")
def plugin():
    return runner.find_plugin(TOOL_ID)


@pytest.fixture(scope="module")
def core_module(plugin):
    return registry.load_plugin_module(plugin, "core")


@pytest.fixture(scope="module")
def rules_module(plugin):
    runner.load_tool_modules(plugin)
    return sys.modules["tak_plugin_%s_rules" % TOOL_ID]


@pytest.fixture(scope="module")
def actions_module(plugin):
    runner.load_tool_modules(plugin)
    return sys.modules["tak_plugin_%s_actions" % TOOL_ID]


# --------------------------------------------------------------------------- #
# manifest / 发现
# --------------------------------------------------------------------------- #
def test_manifest_is_valid():
    with open(os.path.join(_PLUGIN_DIR, "manifest.json"), "r", encoding="utf-8") as fh:
        data = json.load(fh)
    assert registry.validate_manifest(data) == []
    assert data["id"] == TOOL_ID
    assert data["dcc"] == ["maya", "max", "mobu"]
    assert data["min_version"] == {"maya": "2023", "max": "2023", "mobu": "2023"}
    assert data["ui"] == "qt"
    assert data["permissions"] == ["scene.read", "node.write"]
    assert data["name"] and data["description"]


def test_plugin_discovered_for_authoring_dcc_only():
    for dcc in ("maya", "max", "mobu"):
        assert TOOL_ID in [item["id"] for item in runner.list_tools(dcc=dcc, include_internal=True)]
    assert TOOL_ID not in [item["id"] for item in runner.list_tools(
        dcc="unreal", include_internal=True)]


def test_seven_maya_rules_registered_with_explicit_plugin_id(rules_module):
    catalog = {item["id"]: item for item in list_rules("maya")
               if item["plugin_id"] == TOOL_ID}
    assert set(catalog) == set(RULE_IDS)
    assert len(RULE_IDS) >= 4
    for rule_id, item in catalog.items():
        assert item["dcc"] == ["maya"], rule_id
        assert item["description"]
        assert item["severity"] in {"info", "warning", "error", "blocker"}
    assert catalog[_RULE + "control_color"]["severity"] == "info"


def test_plugin_rules_do_not_leak_into_other_dcc(rules_module):
    for dcc in ("max", "mobu", "unreal"):
        assert not [item for item in list_rules(dcc)
                    if item["plugin_id"] == TOOL_ID]


def test_info_contract(core_module):
    info = core_module.info()
    assert info["id"] == TOOL_ID
    assert "scene.read" in info["capabilities"]
    assert "node.write" in info["capabilities"]


# --------------------------------------------------------------------------- #
# 作用域：未开启时零误报（关键回归约束）
# --------------------------------------------------------------------------- #
def test_all_forty_fixtures_produce_no_finding_without_scope():
    for folder in ("good", "bad"):
        for fixture in _all_fixtures(folder):
            result = _run(fixture["snapshot"])
            # 未开作用域 = 零误报，但不是零结论：必须有一条说清「本次跳过」。
            # 否则 UI 把「未检查」显示成「全部合格」。
            assert [item.rule_id for item in result.report.findings] == [
                TOOL_ID + ".scope.disabled"], fixture["id"]
            assert result.passed is True, fixture["id"]
            assert result.report.metadata["plan_count"] == 0
            assert result.report.metadata["scope"]["enabled"] is False


def test_all_rules_return_empty_without_scope(rules_module):
    snapshot = _fixture("bad", "bad_001.json")["snapshot"]
    ctx = _ctx(snapshot)
    for rule_id in RULE_IDS:
        fn = getattr(rules_module, rule_id.split(".", 1)[1])
        assert fn(ctx) == [], rule_id


def test_scope_can_be_enabled_by_options(rules_module):
    snapshot = _enforced(_clean_snapshot())
    for rule_id in RULE_IDS:
        fn = getattr(rules_module, rule_id.split(".", 1)[1])
        assert fn(_ctx(snapshot, options={"spec_enforcer": True})) == [], rule_id


# --------------------------------------------------------------------------- #
# 规则逐条验证
# --------------------------------------------------------------------------- #
def test_naming_pattern_hit_and_fix(rules_module):
    snapshot = _enforced(_clean_snapshot())
    snapshot["joints"]["Bip01 Spine"] = {"parent": "root", "depth": 1,
                                        "matrix": list(_IDENTITY),
                                        "scale": [1.0, 1.0, 1.0],
                                        "rotate": [0.0, 0.0, 0.0],
                                        "namespace": ""}
    findings = rules_module.naming_pattern(_ctx(snapshot))
    assert _ids(findings) == [_RULE + "naming_pattern"]
    fix = findings[0].data["fix"]
    assert fix["op"] == "rename_joint"
    assert fix["params"]["node"] == "Bip01 Spine"
    assert findings[0].severity.value == "error"


def test_naming_pattern_invalid_regex_is_not_a_scene_fault(rules_module):
    spec = load_spec()
    spec["naming"]["joint_pattern"] = "("
    snapshot = _enforced(_clean_snapshot())
    assert rules_module.naming_pattern(_ctx(snapshot, spec=spec)) == []


def test_required_joints_hit(rules_module):
    snapshot = _enforced(_clean_snapshot())
    snapshot["joints"].pop("head")
    findings = rules_module.required_joints(_ctx(snapshot))
    assert _ids(findings) == [_RULE + "required_joints"]
    assert findings[0].node == "head"
    assert findings[0].data["fix"]["op"] == "create_joint"


def test_max_depth_hit(rules_module):
    snapshot = _enforced(_clean_snapshot())
    snapshot["joints"]["foot_R"]["depth"] = 99
    findings = rules_module.max_depth(_ctx(snapshot))
    assert _ids(findings) == [_RULE + "max_depth"]
    assert findings[0].node == "foot_R"
    assert findings[0].data["fix"]["params"]["max_depth"] == 12


def test_weight_sum_hit_and_tolerance(rules_module):
    snapshot = _enforced(_clean_snapshot())
    snapshot["skin"]["weight_sum_deviation"] = 0.05
    findings = rules_module.weight_sum(_ctx(snapshot))
    assert _ids(findings) == [_RULE + "weight_sum"]
    assert findings[0].data["fix"] == {"op": "normalize_weights",
                                       "params": {"tolerance": 0.001}}
    snapshot["skin"]["weight_sum_deviation"] = 0.0005
    assert rules_module.weight_sum(_ctx(snapshot)) == []


def test_influence_limit_hit(rules_module):
    snapshot = _enforced(_clean_snapshot())
    snapshot["skin"]["max_influences_found"] = 8
    findings = rules_module.influence_limit(_ctx(snapshot))
    assert _ids(findings) == [_RULE + "influence_limit"]
    fix = findings[0].data["fix"]
    assert fix["op"] == "normalize_weights"
    assert fix["params"]["max_influences"] == 4


def test_control_namespace_and_color(rules_module):
    snapshot = _enforced(_clean_snapshot())
    snapshot["metadata"]["spec_enforcer"]["controls"] = [
        {"name": "ctrl_arm_L", "namespace": ""},
        {"name": "ctrl_arm_R", "namespace": "CTRL", "color": 6},
        {"name": "ctrl_root", "namespace": "CTRL", "color": "none"},
    ]
    ns_findings = rules_module.control_namespace(_ctx(snapshot))
    assert [item.node for item in ns_findings] == ["ctrl_arm_L"]
    assert ns_findings[0].data["fix"]["params"] == {"node": "ctrl_arm_L",
                                                   "namespace": "CTRL"}

    color_findings = rules_module.control_color(_ctx(snapshot))
    assert [item.node for item in color_findings] == ["ctrl_arm_L", "ctrl_root"]
    assert [item.data["fix"]["params"]["color"] for item in color_findings] == [13, 17]


def test_control_rules_skip_without_controls_field(rules_module):
    snapshot = _enforced(_clean_snapshot())
    assert rules_module.control_namespace(_ctx(snapshot)) == []
    assert rules_module.control_color(_ctx(snapshot)) == []


@pytest.mark.parametrize("snapshot", [
    {"joints": None},
    {"joints": []},
    {"joints": {"root": {}}},
    {"joints": {"root": {"depth": "deep", "scale": "big", "namespace": 3}}},
    {"skin": "nope"},
    {"metadata": "bad"},
])
def test_malformed_snapshot_is_survived(rules_module, snapshot):
    ctx = _ctx(dict(snapshot, metadata={"spec_enforcer": {}}))
    for rule_id in RULE_IDS:
        fn = getattr(rules_module, rule_id.split(".", 1)[1])
        assert isinstance(fn(ctx), list), rule_id


# --------------------------------------------------------------------------- #
# runner 集成
# --------------------------------------------------------------------------- #
def test_good_fixtures_pass_when_enforced():
    for fixture in _all_fixtures("good"):
        result = _run(fixture["snapshot"], options={"spec_enforcer": True})
        assert result.passed is True, fixture["id"]
        assert result.report.findings == [], fixture["id"]


@pytest.mark.parametrize("name,rule_id", [
    ("bad_001.json", _RULE + "naming_pattern"),
    ("bad_004.json", _RULE + "required_joints"),
    ("bad_005.json", _RULE + "max_depth"),
    ("bad_008.json", _RULE + "influence_limit"),
    ("bad_009.json", _RULE + "weight_sum"),
])
def test_bad_fixtures_hit_matching_rule(name, rule_id):
    fixture = _fixture("bad", name)
    result = _run(fixture["snapshot"], options={"spec_enforcer": True})
    assert result.passed is False
    assert rule_id in _ids(result.report.findings)


def test_metadata_scope_can_enable_rules():
    fixture = _fixture("bad", "bad_001.json")
    result = _run(_enforced(fixture["snapshot"]))
    assert result.passed is False
    assert result.report.metadata["scope"]["enabled"] is True


def test_run_result_is_json_serializable():
    result = _run(_fixture("bad", "bad_001.json")["snapshot"],
                  options={"spec_enforcer": True})
    payload = result.to_dict()
    json.dumps(payload, ensure_ascii=False)
    assert payload["metadata"]["plan_count"] == 1
    assert payload["metadata"]["fix_plan"]["actions"][0]["tool"] == _RULE + "rename_joint"


# --------------------------------------------------------------------------- #
# 计划生成：只产出，不执行
# --------------------------------------------------------------------------- #
def test_plan_fixes_produces_plan_without_touching_snapshot(core_module):
    snapshot = _fixture("bad", "bad_001.json")["snapshot"]
    before = copy.deepcopy(snapshot)
    ctx = _tool_ctx(snapshot, options={"spec_enforcer": True})
    plan = core_module.plan_fixes(ctx)
    assert [action.tool for action in plan.actions] == [_RULE + "rename_joint"]
    assert snapshot == before, "生成计划不得修改快照"
    json.loads(plan.to_json())
    core_module.plan_fixes(ctx)
    assert snapshot == before


def test_run_report_does_not_write_scene(core_module):
    snapshot = _fixture("bad", "bad_001.json")["snapshot"]
    before = copy.deepcopy(snapshot)
    adapter = RecordingAdapter()
    ctx = runner.ToolContext(dcc="maya", snapshot=snapshot, spec=load_spec(),
                             adapter=adapter, options={"spec_enforcer": True},
                             tool_id=TOOL_ID)
    report = core_module.run_report(ctx)
    assert snapshot == before
    assert adapter.events == []
    assert report.metadata["plan_count"] == len(report.metadata["fix_plan"]["actions"])
    assert report.metadata["dry_run_only"] is True
    json.dumps(report.to_dict(), ensure_ascii=False)


def test_unmapped_fixes_are_recorded_as_manual(core_module):
    snapshot = _fixture("bad", "bad_004.json")["snapshot"]  # 缺必需关节
    ctx = _tool_ctx(snapshot, options={"spec_enforcer": True})
    report = core_module.run_report(ctx)
    assert report.metadata["plan_count"] == 0
    skipped = report.metadata["skipped_fixes"]
    assert [item["op"] for item in skipped] == ["create_joint"]
    assert "人工" in skipped[0]["reason"]


def test_preview_is_dry_run_diff(core_module):
    snapshot = _fixture("bad", "bad_001.json")["snapshot"]
    ctx = _tool_ctx(snapshot, options={"spec_enforcer": True})
    rows = core_module.preview(ctx)
    assert rows and rows[0]["status"] == "pending"
    assert rows[0]["permissions"] == ["node.write"]
    assert rows[0]["tool"] == _RULE + "rename_joint"
    assert core_module.preview(ctx) == rows


# --------------------------------------------------------------------------- #
# DSL 动作与执行器语义
# --------------------------------------------------------------------------- #
def test_actions_registered_with_declarations(actions_module):
    names = set(ACTION_REGISTRY.names)
    assert set(ACTION_IDS) <= names
    for name in ACTION_IDS:
        spec = ACTION_REGISTRY._specs[name]
        assert spec.permissions == ["node.write"]
        assert spec.description
        assert spec.parameters, name


def test_action_module_reload_is_idempotent(plugin):
    registry.load_plugin_module(plugin, "actions")
    registry.load_plugin_module(plugin, "actions")
    assert set(ACTION_IDS) <= set(ACTION_REGISTRY.names)


def test_unregistered_action_is_rejected():
    with pytest.raises(DSLError, match="未注册"):
        ACTION_REGISTRY.build_plan({"actions": [
            {"tool": _RULE + "not_a_real_action", "params": {}}]})


def test_declared_parameter_bounds_are_enforced():
    with pytest.raises(DSLError):
        ACTION_REGISTRY.build_plan({"actions": [
            {"tool": _RULE + "set_control_color",
             "params": {"node": "ctrl_root", "color": 99}}]})
    with pytest.raises(DSLError):
        ACTION_REGISTRY.build_plan({"actions": [
            {"tool": _RULE + "rename_joint", "params": {"node": "a", "extra": 1}}]})


def test_dry_run_has_no_side_effects():
    plan = ACTION_REGISTRY.build_plan({"actions": [
        {"tool": _RULE + "set_control_color",
         "params": {"node": "ctrl_arm_L", "color": 13}}]})
    adapter = RecordingAdapter()
    result = ACTION_REGISTRY.execute(plan, adapter, dry_run=True)
    assert result["dry_run"] is True and result["committed"] is False
    assert result["changes"][0]["status"] == "pending"
    assert adapter.events == []


def test_missing_permission_is_rejected():
    plan = ACTION_REGISTRY.build_plan({"actions": [
        {"tool": _RULE + "set_control_color",
         "params": {"node": "ctrl_arm_L", "color": 13}}]})
    with pytest.raises(DSLError, match="缺少权限"):
        ACTION_REGISTRY.execute(plan, RecordingAdapter(), dry_run=False, confirm=True)


def test_confirm_required_before_writing():
    plan = ACTION_REGISTRY.build_plan({"actions": [
        {"tool": _RULE + "set_control_color",
         "params": {"node": "ctrl_arm_L", "color": 13}}]})
    adapter = RecordingAdapter()
    with pytest.raises(DSLError, match="缺少权限"):
        ACTION_REGISTRY.execute(plan, adapter, granted=[], dry_run=False)
    with pytest.raises(DSLError, match="confirm"):
        ACTION_REGISTRY.execute(plan, adapter, granted=["node.write"], dry_run=False)
    assert adapter.events == []


def test_confirm_and_grant_commit_through_adapter():
    plan = ACTION_REGISTRY.build_plan({"actions": [
        {"tool": _RULE + "set_control_color",
         "params": {"node": "ctrl_arm_L", "color": 13}},
        {"tool": _RULE + "set_namespace",
         "params": {"node": "ctrl_arm_L", "namespace": "CTRL"}}]})
    adapter = RecordingAdapter()
    result = ACTION_REGISTRY.execute(plan, adapter, granted=["node.write"],
                                     dry_run=False, confirm=True)
    assert result["committed"] is True
    assert adapter.events[0] == "begin"
    assert adapter.events[-1] == "commit"
    assert "color:ctrl_arm_L:13" in adapter.events
    assert "ns:ctrl_arm_L:CTRL" in adapter.events
    assert all(item["status"] == "done" for item in result["changes"])


def test_failed_action_rolls_back_whole_plan():
    plan = ACTION_REGISTRY.build_plan({"actions": [
        {"tool": _RULE + "set_namespace",
         "params": {"node": "ctrl_arm_L", "namespace": "CTRL"}},
        {"tool": _RULE + "set_control_color",
         "params": {"node": "ctrl_arm_L", "color": 13}}]})
    adapter = RecordingAdapter(fail_color=True)
    result = ACTION_REGISTRY.execute(plan, adapter, granted=["node.write"],
                                     dry_run=False, confirm=True)
    assert result["committed"] is False
    assert "host rejected" in result["error"]
    assert adapter.events[0] == "begin"
    assert "ns:ctrl_arm_L:CTRL" in adapter.events
    assert "rollback" in adapter.events
    assert "commit" not in adapter.events
    assert adapter.events.index("rollback") > adapter.events.index("ns:ctrl_arm_L:CTRL")
    assert all(item["status"] == "rolled_back" for item in result["changes"])


def test_missing_adapter_method_is_safe_noop():
    plan = ACTION_REGISTRY.build_plan({"actions": [
        {"tool": _RULE + "normalize_weights",
         "params": {"max_influences": 4, "tolerance": 0.001}}]})
    result = ACTION_REGISTRY.execute(plan, NullAdapter(), granted=["node.write"],
                                     dry_run=False, confirm=True)
    assert result["committed"] is True


def test_rename_without_target_name_is_skipped_not_guessed():
    plan = ACTION_REGISTRY.build_plan({"actions": [
        {"tool": _RULE + "rename_joint",
         "params": {"node": "Bip01 Spine", "pattern": "^[a-z_]+$"}}]})
    adapter = RecordingAdapter()
    result = ACTION_REGISTRY.execute(plan, adapter, granted=["node.write"],
                                     dry_run=False, confirm=True)
    assert result["committed"] is True
    assert not [item for item in adapter.events if item.startswith("rename:")]


def test_apply_plan_wrapper_defaults_to_dry_run(core_module):
    snapshot = _fixture("bad", "bad_001.json")["snapshot"]
    plan = core_module.plan_fixes(_tool_ctx(snapshot, options={"spec_enforcer": True}))
    adapter = RecordingAdapter()
    result = core_module.apply_plan(plan, adapter)
    assert result["dry_run"] is True and result["committed"] is False
    assert adapter.events == []
    with pytest.raises(DSLError, match="confirm"):
        core_module.apply_plan(plan, adapter, granted=["node.write"], dry_run=False)


def test_apply_plan_commits_only_with_confirm(core_module):
    plan = ACTION_REGISTRY.build_plan({"actions": [
        {"tool": _RULE + "set_control_color",
         "params": {"node": "ctrl_arm_L", "color": 13}}]})
    adapter = RecordingAdapter()
    result = core_module.apply_plan(plan, adapter, granted=["node.write"],
                                    confirm=True)
    assert result["committed"] is True
    assert "color:ctrl_arm_L:13" in adapter.events


# --------------------------------------------------------------------------- #
# UI
# --------------------------------------------------------------------------- #
def test_ui_degrades_without_qt(plugin):
    ui = registry.load_plugin_module(plugin, "ui_qt")
    assert callable(ui.show)
    assert ui.show(dcc="maya") is None
