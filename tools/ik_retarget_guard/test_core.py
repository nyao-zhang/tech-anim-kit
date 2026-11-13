# -*- coding: utf-8 -*-
"""IK Retarget Guard 插件自测；插件目录不加入 sys.path，全部经注册表加载。"""
from __future__ import annotations

import json
import os

from tak.adapters.base import NullAdapter
from tak.core import registry, runner

TOOL_ID = "ik_retarget_guard"
_HERE = os.path.dirname(os.path.abspath(__file__))


def _load(submodule):
    return registry.load_plugin_module(runner.find_plugin(TOOL_ID), submodule)


def test_manifest_and_empty_runner():
    with open(os.path.join(_HERE, "manifest.json"), "r", encoding="utf-8") as handle:
        manifest = json.load(handle)
    assert registry.validate_manifest(manifest) == []
    result = runner.run_tool(TOOL_ID, {}, dcc="unreal", adapter=NullAdapter())
    assert result.passed is True
    assert result.report.findings == []


def test_normalize_and_deterministic_mapping():
    mapping = _load("mapping")
    assert mapping.normalize_bone_name("Bip01 L UpperArm") == "upperarm_l"
    first = mapping.suggest_mapping(["upperarm_L", "hand_L"], ["LeftHand", "LeftUpArm"])
    second = mapping.suggest_mapping(["hand_L", "upperarm_L"], ["LeftUpArm", "LeftHand"])
    assert first == second
    assert len(first["entries"]) == 2


def test_mapping_asset_roundtrip(tmp_path):
    mapping = _load("mapping")
    suggestion = mapping.suggest_mapping(["upperarm_L"], ["LeftUpArm"])
    path = tmp_path / "bones.json"
    assert mapping.write_mapping_asset(suggestion, path) == str(path)
    loaded = mapping.load_mapping_asset(path)
    assert loaded["version"] == "1.0"
    assert loaded["entries"] == suggestion["entries"]


def test_inspect_and_web_panel():
    core = _load("core")
    inspected = core.inspect_retarget({"retargeter": {"root_offset": [1, 0, 0]}})
    assert inspected["passed"] is False
    assert inspected["findings"][0]["rule_id"].endswith("root_pelvis_offset")
    panel = _load("ui_web").render_panel_html(inspected)
    assert "tak-panel" in panel and "tak-table" in panel
