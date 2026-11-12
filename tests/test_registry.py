# -*- coding: utf-8 -*-
"""插件发现、manifest 校验与按文件加载。"""
import json
import os

import pytest

from tak.core.registry import (
    discover, load_manifest, load_plugin_module, plugin_dirs, validate_manifest,
)

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_TEMPLATE = os.path.join(_ROOT, "tools", "_template", "manifest.json")


def test_template_is_not_discovered():
    assert all(p.id != "__TEMPLATE_ID__" for p in discover())


def test_manifest_required_fields():
    for payload in ({}, {"id": "x"}, {"id": "x", "name": "X", "dcc": [], "min_version": {}, "entry": "x"}):
        assert validate_manifest(payload)


def test_manifest_rejects_unknown_dcc_and_ui():
    base = {"id": "my_tool", "name": "My", "dcc": ["maya"],
            "min_version": {"maya": "2023"}, "entry": "my_tool"}
    assert validate_manifest(dict(base, ui="qt")) == []
    assert validate_manifest(dict(base, dcc=["blender"]))
    assert validate_manifest(dict(base, ui="imgui"))


def test_external_plugin_dir_via_env(tmp_path, monkeypatch):
    plugin = tmp_path / "my_ext"
    plugin.mkdir()
    (plugin / "manifest.json").write_text(json.dumps({
        "id": "my_ext", "name": "External", "dcc": ["unreal"],
        "min_version": {"unreal": "5.8"}, "entry": "my_ext"}), encoding="utf-8")
    monkeypatch.setenv("TAK_PLUGIN_PATH", str(tmp_path))
    assert "my_ext" in [p.id for p in discover()]
    assert "my_ext" in [p.id for p in discover(dcc="unreal")]
    assert "my_ext" not in [p.id for p in discover(dcc="maya")]


def test_filter_by_dcc(tmp_path):
    plugin = tmp_path / "ue_only"
    plugin.mkdir()
    (plugin / "manifest.json").write_text(json.dumps({
        "id": "ue_only", "name": "UE", "dcc": ["unreal"],
        "min_version": {}, "entry": "ue_only"}), encoding="utf-8")
    assert [p.id for p in discover(tools_dir=str(tmp_path), dcc="unreal")] == ["ue_only"]
    assert discover(tools_dir=str(tmp_path), dcc="maya") == []


def test_load_template_module_by_file():
    plugin = load_manifest(_TEMPLATE)
    module = load_plugin_module(plugin, "core")
    assert module.info()["id"] == "__TEMPLATE_ID__"
    assert plugin.path.endswith(os.path.join("tools", "_template")) or plugin.path.endswith("_template")


def test_plugin_dirs_skips_underscore_dirs():
    assert all(not os.path.basename(p).startswith("_") for p in plugin_dirs())
