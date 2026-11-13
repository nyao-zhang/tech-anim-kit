# -*- coding: utf-8 -*-
"""Spec Enforcer 插件自带集成测试（可选，不在默认 testpaths 内）。

刻意只通过 ``tak.core.runner`` / ``tak.core.registry`` 的公开接口验证插件，
不 ``import core`` / ``import rules`` / ``import actions``（插件目录不在
sys.path）。可在仓库根执行::

    python -m pytest tools/spec_enforcer/test_core.py -q
"""
from __future__ import annotations

import copy
import json
import os
import sys

from tak.adapters.base import NullAdapter
from tak.core import registry, runner
from tak.core.report import Report

TOOL_ID = "spec_enforcer"
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_HERE))
_FIXTURES = os.path.join(_ROOT, "fixtures")


def _manifest():
    with open(os.path.join(_HERE, "manifest.json"), "r", encoding="utf-8") as fh:
        return json.load(fh)


def _fixture(folder, name):
    with open(os.path.join(_FIXTURES, folder, name), "r", encoding="utf-8") as fh:
        return json.load(fh)["snapshot"]


def _plugin():
    return runner.find_plugin(TOOL_ID)


def _run(snapshot, options=None):
    return runner.run_tool(TOOL_ID, snapshot, dcc="maya", adapter=NullAdapter(),
                           options=dict(options or {}))


def test_manifest_is_valid():
    data = _manifest()
    assert registry.validate_manifest(data) == []
    assert data["id"] == TOOL_ID
    assert data["permissions"] == ["scene.read", "node.write"]


def test_empty_scene_passes_without_scope():
    result = _run({})
    assert isinstance(result.report, Report)
    assert result.passed is True
    # 未开作用域不等于「规范全部合格」，必须有且仅有一条跳过说明。
    assert [item.rule_id for item in result.report.findings] == [
        TOOL_ID + ".scope.disabled"]
    assert result.report.metadata["scope"]["enabled"] is False


def test_enforced_bad_fixture_is_caught():
    result = _run(_fixture("bad", "bad_001.json"), options={"spec_enforcer": True})
    assert result.passed is False
    assert [item.rule_id for item in result.report.findings] == [
        TOOL_ID + ".naming_pattern"]


def test_plan_fixes_only_plans():
    module = registry.load_plugin_module(_plugin(), "core")
    snapshot = _fixture("bad", "bad_001.json")
    before = copy.deepcopy(snapshot)
    ctx = runner.ToolContext(dcc="maya", snapshot=snapshot, adapter=NullAdapter(),
                             options={"spec_enforcer": True}, tool_id=TOOL_ID)
    plan = module.plan_fixes(ctx)
    assert [item.tool for item in plan.actions] == [TOOL_ID + ".rename_joint"]
    assert snapshot == before
    json.loads(plan.to_json())


def test_actions_load_twice_without_error():
    registry.load_plugin_module(_plugin(), "actions")
    registry.load_plugin_module(_plugin(), "actions")
    assert TOOL_ID + ".rename_joint" in sys.modules[
        "tak_plugin_%s_actions" % TOOL_ID].ACTION_NAMES


def test_ui_degrades_without_qt():
    ui = registry.load_plugin_module(_plugin(), "ui_qt")
    assert ui.show(dcc="maya") is None
