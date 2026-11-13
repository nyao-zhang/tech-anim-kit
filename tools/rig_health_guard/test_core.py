# -*- coding: utf-8 -*-
"""Rig Health Guard 插件自带集成测试（可选，不在默认 testpaths 内）。

刻意只通过 ``tak.core.runner.run_tool`` 与 ``tak.core.registry`` 的公开
接口验证插件，不 ``import core`` / ``import rules``（插件目录不在
sys.path）。可在仓库根执行::

    python -m pytest tools/rig_health_guard/test_core.py -q
"""
from __future__ import annotations

import json
import os

from tak.adapters.base import NullAdapter
from tak.core import registry, runner
from tak.core.report import Report

TOOL_ID = "rig_health_guard"
_HERE = os.path.dirname(os.path.abspath(__file__))


def _manifest():
    with open(os.path.join(_HERE, "manifest.json"), "r", encoding="utf-8") as fh:
        return json.load(fh)


def _snapshot(health=None):
    return {
        "dcc": "maya",
        "version": "2023",
        "joints": {
            "root": {"parent": None, "depth": 0, "scale": [1.0, 1.0, 1.0],
                     "rotate": [0.0, 0.0, 0.0], "namespace": ""},
        },
        "skin": {"max_influences_found": 4, "weight_sum_deviation": 0.0,
                 "deformers": ["skinCluster"]},
        "metadata": {"seed": 7, "health": health if health is not None else {}},
    }


def _run(snapshot):
    return runner.run_tool(TOOL_ID, snapshot, dcc="maya", adapter=NullAdapter())


def test_manifest_is_valid():
    data = _manifest()
    assert registry.validate_manifest(data) == []
    assert data["id"] == TOOL_ID
    assert set(data["dcc"]) == {"maya", "max", "mobu", "unreal"}
    assert data["permissions"] == ["scene.read"]


def test_empty_scene_passes_without_plugin_fields():
    result = _run({})
    assert isinstance(result.report, Report)
    assert result.passed is True
    # 空快照没有 metadata.health：规则一条都不跑，但必须留一条说明，
    # 否则界面会把「没检查」显示成「没问题」。
    assert [item.rule_id for item in result.report.findings] == [
        TOOL_ID + ".datasource.missing"]
    assert result.report.metadata["health_section"]["present"] is False


def test_orphan_rule_hits_via_runner():
    result = _run(_snapshot({"orphan_nodes": ["locator1"]}))
    ids = [item.rule_id for item in result.report.findings]
    assert ids == [TOOL_ID + ".orphan_nodes"]
    assert result.passed is True  # 孤立节点只是 warning


def test_control_color_hit_is_info():
    result = _run(_snapshot({"control_colors": {"ctrl_arm_L": None}}))
    assert [item.severity.value for item in result.report.findings] == ["info"]


def test_ai_explanations_match_error_count():
    snapshot = _snapshot({})
    snapshot["joints"]["root"]["scale"] = [-1.0, 1.0, 1.0]
    snapshot["skin"]["weight_sum_deviation"] = 0.2
    result = _run(snapshot)
    errors = [item for item in result.report.findings if item.severity.value == "error"]
    assert len(errors) == 2
    explanations = result.report.metadata["ai_explanations"]
    assert len(explanations) == len(errors)
    assert all({"rule_id", "node", "explanation", "suggestion", "model"}
               <= set(item) for item in explanations)


def test_hooks_are_pure_and_report_blocking():
    plugin = runner.find_plugin(TOOL_ID)
    hooks = registry.load_plugin_module(plugin, "hooks")
    snapshot = _snapshot({})
    snapshot["joints"]["root"]["scale"] = [-1.0, 1.0, 1.0]
    ctx = runner.ToolContext(dcc="maya", snapshot=snapshot, adapter=NullAdapter(),
                             tool_id=TOOL_ID)
    out = hooks.on_before_save(ctx)
    assert set(out) == {"passed", "report", "block"}
    assert out["passed"] is False and out["block"] is True
    assert hooks.on_before_export(ctx)["block"] is True
    assert isinstance(hooks.hook_status(), dict)


def test_ui_degrades_without_qt():
    plugin = runner.find_plugin(TOOL_ID)
    ui = registry.load_plugin_module(plugin, "ui_qt")
    assert callable(ui.show)
    assert ui.show(dcc="maya") is None
