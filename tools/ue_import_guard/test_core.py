# -*- coding: utf-8 -*-
"""UE Import Guard 插件自带集成测试（不在默认 testpaths 内）。

只通过 ``tak.core.runner`` / ``tak.core.registry`` 的公开接口验证插件，
不 ``import core`` / ``import rules``（插件目录不在 sys.path）。可在仓库根执行::

    python -m pytest tools/ue_import_guard/test_core.py -q
"""
from __future__ import annotations

import json
import os
import re

from tak.adapters.base import NullAdapter
from tak.core import registry, runner

TOOL_ID = "ue_import_guard"
_HERE = os.path.dirname(os.path.abspath(__file__))
_IDENTITY = [1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0,
             0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0]


def _snapshot():
    return {
        "dcc": "unreal", "version": "5.8", "unit": "cm", "up_axis": "Y",
        "joints": {
            "root": {"parent": None, "matrix": list(_IDENTITY), "depth": 0},
            "pelvis": {"parent": "root", "matrix": list(_IDENTITY), "depth": 1},
        },
        "frame_range": [1.0, 120.0], "skin": {}, "metadata": {"seed": 0},
    }


def _assets():
    return {
        "skeleton": {"bones": ["root", "pelvis", "spine_01", "neck", "head"],
                     "compatible_with": ["/Game/Characters/Default/SK_Default"],
                     "root_bone": "root"},
        "anim_sequences": [{"name": "/Game/Anim/AS_Run", "compression": "Default",
                            "root_motion": False, "curve_count": 1,
                            "curves": ["run_phase"]}],
        "skeletal_meshes": [{"name": "/Game/Characters/SKM_Body",
                             "material_slots": 2,
                             "lods": [{"allow_deformer": False}]}],
    }


def _run(options=None, snapshot=None):
    return runner.run_tool(TOOL_ID, snapshot if snapshot is not None else _snapshot(),
                           dcc="unreal", adapter=NullAdapter(),
                           options=dict(options or {}))


def _load(submodule):
    return registry.load_plugin_module(runner.find_plugin(TOOL_ID), submodule)


def test_manifest_and_discovery():
    with open(os.path.join(_HERE, "manifest.json"), "r", encoding="utf-8") as fh:
        data = json.load(fh)
    assert registry.validate_manifest(data) == []
    assert data["dcc"] == ["unreal"]
    assert data["ui"] == "both"
    assert TOOL_ID in [item["id"] for item in runner.list_tools(dcc="unreal")]


def test_empty_snapshot_passes():
    result = _run()
    assert result.passed is True
    assert result.report.findings == []


def test_clean_ue_assets_pass():
    result = _run({"ue_assets": _assets()})
    assert result.passed is True
    assert result.report.findings == []
    assert result.report.metadata["rules_count"] == 9


def test_missing_chain_bone_is_blocking():
    assets = _assets()
    assets["skeleton"]["bones"] = ["root", "pelvis", "spine_01", "head"]
    result = _run({"ue_assets": assets})
    assert result.passed is False
    assert TOOL_ID + ".required_bone_chain" in [
        item.rule_id for item in result.report.findings]


def test_validate_import_detects_rename():
    core = _load("core")
    before = _snapshot()
    after = json.loads(json.dumps(before))
    after["joints"]["hips"] = after["joints"].pop("pelvis")
    result = core.validate_import(before, after)
    assert result["passed"] is False
    assert set(result) == {"passed", "diffs", "blocking", "reason", "ignored"}


def test_validate_import_same_snapshot_passes():
    core = _load("core")
    result = core.validate_import({"snapshot": _snapshot()}, _snapshot())
    assert result["passed"] is True
    assert result["diffs"] == []


def test_web_panel_fragment_uses_theme_classes():
    ui = _load("ui_web")
    html = ui.render_panel_html(_run({"ue_assets": _assets()}))
    assert "tak-panel" in html
    assert "<html" not in html
    assert re.search(r"#[0-9a-fA-F]{3,8}\b", html) is None
