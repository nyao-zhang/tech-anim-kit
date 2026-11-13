# -*- coding: utf-8 -*-
"""批次 2 端到端回归：五个插件、统一运行契约与完整体检作用域。

P2.2 的插件默认只跑自身专项规则（便于隔离单测），UI / 钩子通过
``options["include_builtin"]=True`` 打开完整体检。本文件守住这条链路，
并验证所有插件在无 DCC 环境下都能被发现和运行。
"""
from __future__ import annotations

import json
import os

import pytest

from tak.adapters.base import NullAdapter
from tak.core import runner
from tak.core.report import Report

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_EXPECTED_PLUGINS = {
    "rig_health_guard", "spec_enforcer", "export_bridge",
    "ue_import_guard", "ik_retarget_guard",
}


def _fixture(kind, name):
    with open(os.path.join(_ROOT, "fixtures", kind, name), encoding="utf-8") as fh:
        return json.load(fh)


def test_all_batch2_plugins_discoverable():
    ids = {plugin["id"] for plugin in runner.list_tools(include_internal=True)}
    assert _EXPECTED_PLUGINS <= ids
    assert "__TEMPLATE_ID__" not in ids


def test_health_guard_default_scope_is_plugin_only():
    data = _fixture("good", "good_001.json")["snapshot"]
    result = runner.run_tool("rig_health_guard", data, dcc="maya",
                             adapter=NullAdapter())
    assert result.report.metadata["scope"] == "plugin_only"
    assert result.report.metadata["rules_count"] == 9


def test_full_health_check_includes_builtin_rules():
    good = _fixture("good", "good_001.json")["snapshot"]
    bad = _fixture("bad", "bad_001.json")["snapshot"]
    options = {"include_builtin": True}
    good_result = runner.run_tool("rig_health_guard", good, dcc="maya",
                                  adapter=NullAdapter(), options=options)
    bad_result = runner.run_tool("rig_health_guard", bad, dcc="maya",
                                 adapter=NullAdapter(), options=options)
    assert good_result.report.metadata["scope"] == "all"
    assert good_result.report.metadata["rules_count"] > 9
    assert any(rule_id.startswith("naming.") for rule_id in
               good_result.report.metadata["rule_ids"])
    assert good_result.passed is True
    assert bad_result.passed is False
    assert any(f.rule_id == "naming.joint_pattern" for f in bad_result.report.findings)


def test_full_check_ai_explanations_survive_serialization():
    """AI 解释层只写 metadata，且必须能穿过 ToolResult.to_dict()。"""
    bad = _fixture("bad", "bad_001.json")["snapshot"]
    result = runner.run_tool("rig_health_guard", bad, dcc="maya",
                             adapter=NullAdapter(),
                             options={"include_builtin": True})
    payload = result.to_dict()
    assert payload["metadata"]["scope"] == "all"
    assert payload["metadata"]["ai_explanations"]
    assert payload["metadata"]["ai_explanations"][0]["explanation"]


@pytest.mark.parametrize("tool_id,dcc", [
    ("rig_health_guard", "maya"),
    ("spec_enforcer", "maya"),
    ("export_bridge", "maya"),
    ("ue_import_guard", "unreal"),
    ("ik_retarget_guard", "unreal"),
])
def test_every_plugin_runs_offline(tool_id, dcc):
    data = _fixture("good", "good_001.json")["snapshot"]
    result = runner.run_tool(tool_id, data, dcc=dcc, adapter=NullAdapter())
    assert isinstance(result.report, Report)
    assert result.tool_id == tool_id
    assert isinstance(result.render("json"), str)


def test_all_plugins_still_discoverable_including_internal():
    """五个规则提供者仍然被发现，只是不再作为用户可见工具。"""
    ids = {plugin["id"] for plugin in runner.list_tools(include_internal=True)}
    assert _EXPECTED_PLUGINS <= ids


def test_web_api_lists_visible_plugins_only():
    """Web 端工具列表与面板一致：内部规则提供者不出现。"""
    from tak.ui.web import server
    status, payload = server.handle_api("GET", "/api/tools")
    assert status == 200
    ids = {item["id"] for item in payload["tools"]}
    assert {"qa_inspector", "asset_pipeline"} <= ids
    assert not (ids & _EXPECTED_PLUGINS)


def test_web_api_evaluate_good_and_bad_fixtures():
    from tak.ui.web import server
    for kind, name, expected in (("good", "good_001.json", True),
                                 ("bad", "bad_001.json", False)):
        status, payload = server.handle_api(
            "POST", "/api/evaluate",
            body={"snapshot": _fixture(kind, name)["snapshot"], "dcc": "maya"})
        assert status == 200
        assert payload["passed"] is expected
