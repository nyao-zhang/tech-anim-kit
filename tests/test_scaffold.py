# -*- coding: utf-8 -*-
"""脚手架：新工具生成后立刻可被发现、加载并通过自带单测。"""
import pytest

from tak.core.registry import discover, load_plugin_module
from tak.core.rules.base import RULE_REGISTRY
from tools.new_tool import scaffold


def test_scaffold_creates_discoverable_plugin(tmp_path):
    path = scaffold("my_probe_tool", "探针工具", ["maya", "unreal"],
                    tools_dir=str(tmp_path))
    plugins = discover(tools_dir=str(tmp_path))
    assert [p.id for p in plugins] == ["my_probe_tool"]
    plugin = plugins[0]
    assert plugin.dcc == ["maya", "unreal"]
    assert plugin.entry == "my_probe_tool"

    core = load_plugin_module(plugin, "core")
    assert core.info()["id"] == "my_probe_tool"
    assert core.TOOL_ID == "my_probe_tool"
    assert (path.rstrip("/\\") + "/manifest.json").replace("\\", "/").endswith("manifest.json")

    load_plugin_module(plugin, "rules")
    ids = [r.id for r in RULE_REGISTRY if r.plugin_id == "my_probe_tool"]
    assert "my_probe_tool.scene_has_joints" in ids


def test_scaffold_rejects_bad_id_and_dcc(tmp_path):
    with pytest.raises(ValueError, match="小写"):
        scaffold("Bad-ID", "Bad", ["maya"], tools_dir=str(tmp_path))
    with pytest.raises(ValueError, match="未知 DCC"):
        scaffold("good_id", "Good", ["blender"], tools_dir=str(tmp_path))


def test_scaffold_refuses_overwrite(tmp_path):
    scaffold("dup_tool", "Dup", ["maya"], tools_dir=str(tmp_path))
    with pytest.raises(FileExistsError):
        scaffold("dup_tool", "Dup", ["maya"], tools_dir=str(tmp_path))
    assert scaffold("dup_tool", "Dup", ["maya"], tools_dir=str(tmp_path), force=True)
