# -*- coding: utf-8 -*-
"""Export Bridge 插件自带集成测试（可选，不在默认 testpaths 内）。

不 ``import core``（插件目录不在 sys.path），只通过
``tak.core.registry.load_plugin_module`` 取公开函数。可在仓库根执行::

    python -m pytest tools/export_bridge/test_core.py -q
"""
from __future__ import annotations

import copy
import json
import os

from tak.adapters.base import NullAdapter
from tak.core import registry, runner
from tak.core.report import Report

TOOL_ID = "export_bridge"
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_HERE))
_FIXTURES = os.path.join(_ROOT, "fixtures")


def _manifest():
    with open(os.path.join(_HERE, "manifest.json"), "r", encoding="utf-8") as fh:
        return json.load(fh)


def _good():
    with open(os.path.join(_FIXTURES, "good", "good_001.json"),
              "r", encoding="utf-8") as fh:
        return copy.deepcopy(json.load(fh)["snapshot"])


def _core():
    return registry.load_plugin_module(runner.find_plugin(TOOL_ID), "core")


def test_manifest_is_valid():
    data = _manifest()
    assert registry.validate_manifest(data) == []
    assert data["id"] == TOOL_ID
    assert data["permissions"] == ["scene.read", "export.fbx"]


def test_same_snapshot_passes():
    core = _core()
    result = core.verify_pair(_good(), _good())
    assert result["passed"] is True
    assert result["diffs"] == []


def test_unit_change_blocks():
    core = _core()
    before = _good()
    after = copy.deepcopy(before)
    after["unit"] = "m"
    result = core.verify_pair(before, after)
    assert result["passed"] is False
    assert [item["type"] for item in result["blocking"]] == ["unit"]


def test_sidecar_roundtrip(tmp_path):
    core = _core()
    path = tmp_path / "rig.tak_snapshot.json"
    core.pre_export(_good(), str(path))
    assert json.loads(path.read_text(encoding="utf-8"))["tool"] == TOOL_ID
    assert core.post_export(str(path), _good())["passed"] is True


def test_ue_hook_is_delegated():
    hook = _core().ue_import_hook("/tmp/rig.tak_snapshot.json")
    assert hook["status"] == "delegated"
    assert hook["target"] == "ue_import_guard"
    assert hook["implemented"] is False


def test_run_tool_returns_report():
    result = runner.run_tool(TOOL_ID, _good(), dcc="maya", adapter=NullAdapter(),
                             options={"before": _good(), "after": _good()})
    assert isinstance(result.report, Report)
    assert result.passed is True
    assert result.report.metadata["verify"]["passed"] is True


def test_ui_degrades_without_qt():
    ui = registry.load_plugin_module(runner.find_plugin(TOOL_ID), "ui_qt")
    assert ui.show(dcc="maya") is None
