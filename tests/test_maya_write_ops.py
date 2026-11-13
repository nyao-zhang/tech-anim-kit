# -*- coding: utf-8 -*-
"""P4.4：Maya 真实写操作与受限 DSL 的安全链路回归。

批次 2 的已知限制是 MayaAdapter 只有查询、写动作全是 no-op；本批次补齐
rename_node / set_control_color / normalize_weights / set_namespace 后，
spec_enforcer 的四个 DSL 动作必须能真正调用到 adapter，且仍然只走
「白名单 → dry-run → 权限 → 人工确认 → undo → 失败回滚」这条链。
"""
from __future__ import annotations

import pytest

from tak.adapters.base import Capability
from tak.adapters.maya import MayaAdapter, create_adapter
from tak.core.dsl import ACTION_REGISTRY, DSLError
from tak.core.mocks import install_mock, uninstall_mock
from tak.core.registry import discover
from tak.core.runner import find_plugin, load_tool_modules

FIXTURE = "fixtures/good/good_001.json"


@pytest.fixture
def maya_scene():
    handle = install_mock("maya", FIXTURE)
    try:
        yield handle
    finally:
        uninstall_mock()


def _write_log(handle):
    return handle.scene.metadata.get("write_log", [])


def test_adapter_declares_write_capabilities(maya_scene):
    adapter = create_adapter()
    assert isinstance(adapter, MayaAdapter)
    assert adapter.supports(Capability.NODE_WRITE)
    assert adapter.supports(Capability.WEIGHT_WRITE)
    assert "node.write" in adapter.describe()["capabilities"]
    assert "skin.write" in adapter.describe()["capabilities"]


def test_rename_node_records_mock_write(maya_scene):
    result = create_adapter().rename_node("root", "root_ctl")
    assert result["status"] == "done"
    assert result["node"] == "root" and result["name"] == "root_ctl"
    assert _write_log(maya_scene)[-1]["action"] == "rename_node"


def test_set_control_color_clamps_index(maya_scene):
    result = create_adapter().set_control_color("pelvis", 999)
    assert result["status"] == "done"
    assert result["color"] == 31
    assert _write_log(maya_scene)[-1]["color"] == 31


def test_normalize_weights_records_parameters(maya_scene):
    result = create_adapter().normalize_weights(max_influences=4, tolerance=0.001)
    assert result["status"] == "done"
    assert result["mock"] is True
    entry = _write_log(maya_scene)[-1]
    assert entry["action"] == "normalize_weights"
    assert entry["max_influences"] == 4


def test_set_namespace_records_target(maya_scene):
    result = create_adapter().set_namespace("spine_01", "char:")
    assert result["status"] == "done"
    assert result["namespace"] == "char"
    assert _write_log(maya_scene)[-1]["namespace"] == "char"


def test_empty_arguments_are_skipped_not_errors(maya_scene):
    adapter = create_adapter()
    assert adapter.rename_node("", "x")["status"] == "skipped"
    assert adapter.rename_node("root", "")["status"] == "skipped"
    assert len(_write_log(maya_scene)) == 0


def test_writes_do_not_break_snapshot_contract(maya_scene):
    from tak.adapters.conformance import validate_snapshot

    adapter = create_adapter()
    before = adapter.get_skeleton_snapshot()
    for _ in range(3):
        adapter.set_control_color("root", 4)
        adapter.normalize_weights(max_influences=4)
        adapter.set_namespace("pelvis", "rig")
    after = adapter.get_skeleton_snapshot()
    assert validate_snapshot(before)["ok"] is True
    assert validate_snapshot(after)["ok"] is True
    assert set(before["joints"]) == set(after["joints"])


def test_offline_adapter_without_host_is_safe():
    adapter = create_adapter(data={"joints": {}, "dcc": "maya"})
    for call in (lambda: adapter.rename_node("a", "b"),
                 lambda: adapter.set_control_color("a", 3),
                 lambda: adapter.normalize_weights(),
                 lambda: adapter.set_namespace("a", "ns")):
        assert call()["status"] == "skipped"


def _load_actions():
    plugin = find_plugin("spec_enforcer")
    load_tool_modules(plugin)
    return plugin


def test_dsl_dry_run_has_no_side_effects(maya_scene):
    _load_actions()
    adapter = create_adapter()
    plan = ACTION_REGISTRY.build_plan({"actions": [
        {"tool": "spec_enforcer.set_control_color", "params": {"node": "root", "color": 6}}]})
    result = ACTION_REGISTRY.preview(plan)
    assert result[0]["status"] == "pending"
    assert _write_log(maya_scene) == []


def test_dsl_execute_requires_permission_and_confirmation(maya_scene):
    _load_actions()
    adapter = create_adapter()
    plan = ACTION_REGISTRY.build_plan({"actions": [
        {"tool": "spec_enforcer.set_control_color", "params": {"node": "root", "color": 6}}]})
    with pytest.raises(DSLError):
        ACTION_REGISTRY.execute(plan, adapter, granted=[], dry_run=False, confirm=True)
    with pytest.raises(DSLError):
        ACTION_REGISTRY.execute(plan, adapter, granted=["node.write"],
                                dry_run=False, confirm=False)
    assert _write_log(maya_scene) == []


def test_dsl_confirmed_execution_reaches_adapter(maya_scene):
    _load_actions()
    adapter = create_adapter()
    plan = ACTION_REGISTRY.build_plan({"actions": [
        {"tool": "spec_enforcer.set_control_color", "params": {"node": "root", "color": 6}},
        {"tool": "spec_enforcer.normalize_weights", "params": {"max_influences": 4}},
    ]})
    result = ACTION_REGISTRY.execute(plan, adapter, granted=["node.write"],
                                     dry_run=False, confirm=True)
    assert result["committed"] is True
    assert [item["status"] for item in result["changes"]] == ["done", "done"]
    actions = [item["action"] for item in _write_log(maya_scene)]
    assert actions == ["set_control_color", "normalize_weights"]


def test_all_four_dsl_actions_are_reachable(maya_scene):
    _load_actions()
    adapter = create_adapter()
    plan = ACTION_REGISTRY.build_plan({"actions": [
        {"tool": "spec_enforcer.rename_joint",
         "params": {"node": "root", "name": "root_ctl"}},
        {"tool": "spec_enforcer.set_control_color",
         "params": {"node": "pelvis", "color": 12}},
        {"tool": "spec_enforcer.normalize_weights", "params": {}},
        {"tool": "spec_enforcer.set_namespace",
         "params": {"node": "head", "namespace": "rig"}},
    ]})
    result = ACTION_REGISTRY.execute(plan, adapter, granted=["node.write"],
                                     dry_run=False, confirm=True)
    assert result["committed"] is True
    assert len(_write_log(maya_scene)) == 4


def test_plugin_discovery_still_intact():
    ids = {plugin.id for plugin in discover()}
    assert "spec_enforcer" in ids
