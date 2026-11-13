# -*- coding: utf-8 -*-
"""统一运行契约的回归测试（批次 2 共享基线）。

刻意用临时外部插件目录，不往 tools/ 里写真插件，
避免污染全局 RULE_REGISTRY。
"""
from __future__ import annotations

import json

import pytest

from tak.core import runner
from tak.core.report import Report
from tak.adapters.base import NullAdapter

_FAKE_MANIFEST = {
    "id": "fake_probe", "name": "假探针", "dcc": ["maya", "unreal"],
    "min_version": {"maya": "2023", "unreal": "5.8"},
    "entry": "fake_probe", "ui": "none", "permissions": ["scene.read"],
}
_FAKE_CORE = '''# -*- coding: utf-8 -*-
from tak.core.report import Report

def info():
    return {"id": "fake_probe"}

def run_report(ctx):
    report = Report(title="假探针", dcc=ctx.dcc)
    report.metadata["joint_count"] = len((ctx.snapshot or {}).get("joints", {}))
    report.metadata["has_adapter"] = ctx.adapter is not None
    return report
'''


def _clear_fake_cache():
    """只清本测试用的假插件缓存，绝不清空真实插件的缓存。

    清空全局缓存会导致插件 rules.py 被二次 exec，@rule 重复注册，
    从而污染后续所有依赖全局规则集的测试。
    """
    for key in [k for k in runner._MODULE_CACHE if k[0] == "fake_probe"]:
        del runner._MODULE_CACHE[key]


@pytest.fixture
def fake_plugin(tmp_path, monkeypatch):
    root = tmp_path / "plugins"
    target = root / "fake_probe"
    target.mkdir(parents=True)
    (target / "manifest.json").write_text(json.dumps(_FAKE_MANIFEST), encoding="utf-8")
    (target / "core.py").write_text(_FAKE_CORE, encoding="utf-8")
    monkeypatch.setenv("TAK_PLUGIN_PATH", str(root))
    _clear_fake_cache()
    yield target
    _clear_fake_cache()


def test_adapter_degrades_without_host():
    adapter = runner.adapter_for("no_such_dcc")
    assert isinstance(adapter, NullAdapter)
    assert "ModuleNotFoundError" in adapter.data["degraded"]


def test_run_tool_with_external_plugin(fake_plugin):
    result = runner.run_tool("fake_probe", {"joints": {"root": {}, "pelvis": {}}},
                             dcc="maya", adapter=NullAdapter())
    assert isinstance(result.report, Report)
    assert result.passed is True
    assert result.report.metadata["joint_count"] == 2
    assert result.report.metadata["has_adapter"] is True
    assert result.tool_id == "fake_probe"


def test_run_tool_caches_modules(fake_plugin):
    first = runner.run_tool("fake_probe", {}, dcc="maya", adapter=NullAdapter())
    second = runner.run_tool("fake_probe", {}, dcc="maya", adapter=NullAdapter())
    assert first.report is not second.report
    assert ("fake_probe", "core") in runner._MODULE_CACHE


def test_run_tool_rejects_wrong_dcc(fake_plugin):
    with pytest.raises(runner.ToolError):
        runner.run_tool("fake_probe", {}, dcc="mobu", adapter=NullAdapter())


def test_missing_plugin_raises(fake_plugin):
    with pytest.raises(runner.ToolNotFound):
        runner.run_tool("not_exist", {}, dcc="maya", adapter=NullAdapter())


def test_list_tools_and_render(fake_plugin):
    tools = runner.list_tools(dcc="maya")
    assert any(item["id"] == "fake_probe" for item in tools)
    result = runner.run_tool("fake_probe", {}, dcc="maya", adapter=NullAdapter())
    assert "fake_probe" in result.render("json")
    assert "假探针" in result.render("md")
    assert "tak-panel" in result.render("html")
    with pytest.raises(ValueError):
        result.render("pdf")


def test_to_dict_preserves_report_metadata(fake_plugin):
    result = runner.run_tool("fake_probe", {}, dcc="maya", adapter=NullAdapter())
    payload = result.to_dict()
    assert payload["metadata"]["joint_count"] == 0
    assert payload["metadata"]["tool_id"] == "fake_probe"


def test_rule_ids_filter(fake_plugin):
    result = runner.run_tool("fake_probe", {}, dcc="maya",
                             adapter=NullAdapter(), rule_ids="nope.rule")
    assert result.report.findings == []


def test_evaluate_uses_builtin_rules():
    report = runner.evaluate({"joints": {"LeftUpArm": {"scale": [-1, 1, 1]}}},
                             dcc="maya", adapter=NullAdapter())
    assert isinstance(report, Report)
    assert "adapter" in report.metadata


def test_clear_cache_does_not_duplicate_plugin_rules():
    """清空模块缓存后重新加载插件，@rule 不得重复注册（曾导致 19 个测试误报）。"""
    from tak.core.registry import discover
    from tak.core.rules.base import RULE_REGISTRY

    plugin = next((p for p in discover() if p.id == "rig_health_guard"), None)
    if plugin is None:
        pytest.skip("rig_health_guard 插件不存在")
    runner.load_tool_modules(plugin)
    before = [item.id for item in RULE_REGISTRY
              if item.plugin_id == "rig_health_guard"]
    runner.clear_cache()
    runner.load_tool_modules(plugin)
    after = [item.id for item in RULE_REGISTRY
             if item.plugin_id == "rig_health_guard"]
    assert after == before
    assert len(after) == len(set(after))


def test_tool_context_helpers():
    ctx = runner.ToolContext(dcc="unreal", snapshot={"joints": {}},
                             spec={"version": "9.9"}, options={"dry": True})
    assert ctx.rule_context().dcc == "unreal"
    assert isinstance(ctx.adapter_or_null, NullAdapter)
    assert ctx.rule_context().options == {"dry": True}
