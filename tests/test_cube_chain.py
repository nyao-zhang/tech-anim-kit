# -*- coding: utf-8 -*-
"""v0.8.0：AI function calling 422 修复 + 批量立方体链动作。

用户原话：
    「创建十个随机大小在10~100立方厘米的正方块，以大小体积做父子关系，
      最大的作为最父级」
v0.7.x 会同时卡在两处：tools 格式不对（HTTP 422 → 降级本地），本地解析
只认「创建一个 X 叫 Y」，于是回「创建需要给出名字」。
"""
from __future__ import annotations

import json
import math

import pytest

from tak.core import scene_actions as sa
from tak.core.ai import commands

REQUEST = ("创建十个随机大小在10~100立方厘米的正方块，"
           "以大小体积做父子关系，最大的作为最父级")


# --------------------------------------------------------------------------- #
# 1. AI 端点的 tools 格式
# --------------------------------------------------------------------------- #
def test_openai_tools_have_required_type_field():
    tools = sa.REGISTRY.openai_tools()
    assert tools
    for tool in tools:
        assert tool["type"] == "function"
        function = tool["function"]
        assert function["name"].startswith("tak_")
        assert function["description"]
        assert function["parameters"]["type"] == "object"
        assert function["parameters"]["additionalProperties"] is False
        # 与 MCP tools/list 是两种格式，不能混用
        assert "inputSchema" not in tool
    names = {tool["function"]["name"] for tool in tools}
    assert "tak_scene_create_cube_chain" in names


def test_batch_action_is_declared_and_is_a_write():
    spec = sa.REGISTRY.get("scene.create_cube_chain")
    assert spec is not None and not spec.read_only
    assert sa.WRITE_PERMISSION in spec.permissions
    assert spec.previewer is not None
    assert "count" in spec.params and "seed" in spec.params
    tool = {item["name"]: item for item in sa.REGISTRY.mcp_tools()}
    assert "tak_scene_create_cube_chain" in tool
    assert tool["tak_scene_create_cube_chain"]["inputSchema"]["properties"]["spacing"]


# --------------------------------------------------------------------------- #
# 2. 本地解析
# --------------------------------------------------------------------------- #
def test_parse_local_understands_the_original_request():
    parsed = commands.parse_local(REQUEST)
    assert parsed["ok"] and len(parsed["calls"]) == 1
    call = parsed["calls"][0]
    assert call["action"] == "scene.create_cube_chain"
    params = call["params"]
    assert params["count"] == 10
    assert params["min_size"] == 10 and params["max_size"] == 100
    assert params["size_by"] == "volume"      # 「立方厘米」按体积解释
    assert params["link"] is True
    assert params["prefix"] == "takCube"
    assert isinstance(params["seed"], int) and params["seed"] > 0
    # 不传 spacing → 动作按最大边长自动排开，避免 10 个同心方块看不见
    assert "spacing" not in params


def test_parse_local_variants():
    # 阿拉伯数字 + 中文数量
    p = commands.parse_local("生成 6 个随机大小的方块，按体积做父子链")["calls"][0]["params"]
    assert p["count"] == 6 and p["size_by"] == "volume"
    p = commands.parse_local("创建二十个随机立方体，按大小排父子")["calls"][0]["params"]
    assert p["count"] == 20
    # 只说「厘米」不按体积解释
    p = commands.parse_local("建 4 个 10~30 厘米的方块，按大小做父子")["calls"][0]["params"]
    assert p["size_by"] == "edge" and p["max_size"] == 30
    # 明确要求原点
    p = commands.parse_local("创建 3 个随机大小的方块，都放在原点")["calls"][0]["params"]
    assert p["spacing"] == 0 and p["link"] is False
    # 不提父子就不建链
    p = commands.parse_local("创建 5 个随机大小的立方体")["calls"][0]["params"]
    assert p["link"] is False


def test_parse_local_does_not_hijack_single_create():
    parsed = commands.parse_local("创建一个 locator 叫 foot_ctrl")
    assert parsed["calls"][0]["action"] == "node.create"
    assert parsed["calls"][0]["params"] == {"kind": "locator", "name": "foot_ctrl"}


# --------------------------------------------------------------------------- #
# 3. 预览 = 执行（确定性）
# --------------------------------------------------------------------------- #
def test_plan_is_deterministic_sorted_and_linked():
    params = {"count": 10, "min_size": 10, "max_size": 100,
              "size_by": "volume", "seed": 20261114,
              "prefix": "cube", "link": True}
    plan = sa.cube_chain_plan(params)
    assert sa.cube_chain_plan(dict(params)) == plan
    assert sa.cube_chain_plan(dict(params, seed=1)) != plan
    cubes = plan["cubes"]
    assert len(cubes) == 10 and plan["count"] == 10
    assert all(cubes[i]["volume_cm3"] >= cubes[i + 1]["volume_cm3"]
               for i in range(len(cubes) - 1))
    assert cubes[0]["parent"] == "" and plan["root"] == cubes[0]["name"]
    assert all(cubes[i]["parent"] == cubes[i - 1]["name"]
               for i in range(1, len(cubes)))
    assert plan["chain"] == [item["name"] for item in cubes]
    for item in cubes:
        assert math.isclose(item["edge_cm"] ** 3, item["volume_cm3"],
                            rel_tol=1e-4)
    assert plan["spacing_mode"] == "auto" and plan["spacing"] > 0
    assert "体积" in plan["interpretation"]


def test_plan_edge_mode_no_link_and_explicit_spacing():
    plan = sa.cube_chain_plan({"count": 3, "min_size": 2, "max_size": 5,
                               "size_by": "edge", "seed": 7, "link": False,
                               "spacing": 0})
    assert plan["spacing_mode"] == "none" and plan["spacing"] == 0
    assert plan["chain"] == ["takCube_01", "takCube_02", "takCube_03"]
    assert all(not item["parent"] for item in plan["cubes"])
    for item in plan["cubes"]:
        assert math.isclose(item["volume_cm3"], item["edge_cm"] ** 3,
                            rel_tol=1e-4)


@pytest.mark.parametrize("bad", [
    {"count": 0}, {"count": 51}, {"min_size": 100, "max_size": 10},
    {"min_size": -1}, {"size_by": "mass"},
    {"prefix": "1bad"}, {"prefix": "bad-name"}, {"spacing": -2},
])
def test_plan_rejects_bad_params(bad):
    params = {"count": 4, "min_size": 10, "max_size": 100, "seed": 1}
    params.update(bad)
    with pytest.raises(sa.ActionError):
        sa.cube_chain_plan(params)


def test_registry_validate_rejects_bad_params_before_preview():
    with pytest.raises(sa.ActionError):
        sa.REGISTRY.validate("scene.create_cube_chain", {"count": 999})
    with pytest.raises(sa.ActionError):
        sa.REGISTRY.validate("scene.create_cube_chain", {"surprise": 1})


def test_dry_run_then_commit_uses_identical_params(monkeypatch):
    spec = sa.REGISTRY.get("scene.create_cube_chain")
    seen = []

    def fake_handler(clean):
        seen.append(dict(clean))
        return sa.cube_chain_plan(clean)

    monkeypatch.setattr(spec, "handler", fake_handler)
    # 其它测试可能给 maya 注册过真实 undo hooks；本用例是纯 Python，隔离掉，
    # 否则 _begin() 会因 import 不到 maya.cmds 把整次提交判失败。
    monkeypatch.setattr(sa.REGISTRY, "_undo_hooks", {})
    params = {"count": 5, "min_size": 10, "max_size": 100,
              "size_by": "volume", "seed": 42, "link": True}
    first = sa.REGISTRY.execute("scene.create_cube_chain", params, dry_run=True)
    assert first["ok"] and first["status"] == "dry_run"
    assert not seen, "dry-run 绝不能碰宿主"
    preview = first["preview"]
    assert preview["operation"] == "CREATE_CUBE_CHAIN"
    token = first["confirm_token"]

    # 改一个参数，token 必须失效
    tampered = sa.REGISTRY.execute(
        "scene.create_cube_chain", dict(params, seed=43),
        confirm=True, dry_run=False, confirm_token=token,
        granted=[sa.WRITE_PERMISSION], dcc="maya")
    assert not tampered["ok"] and "不匹配" in tampered["error"]

    second = sa.REGISTRY.execute(
        "scene.create_cube_chain", params, confirm=True, dry_run=False,
        confirm_token=token, granted=[sa.WRITE_PERMISSION], dcc="maya")
    assert second["ok"] and second["status"] == "committed"
    assert len(seen) == 1 and seen[0]["seed"] == 42
    assert second["preview"] == preview, "确认时回带的必须还是当初那份预览"
    assert second["result"]["chain"] == preview["chain"]


def test_command_preview_with_fake_model_and_handler(monkeypatch):
    spec = sa.REGISTRY.get("scene.create_cube_chain")
    monkeypatch.setattr(spec, "handler", lambda clean: sa.cube_chain_plan(clean))
    reply = commands.command(REQUEST, use_model=False, dcc="maya")
    assert reply["ok"] and reply["need_confirm"]
    item = reply["results"][0]
    assert item["status"] == "dry_run"
    assert item["preview"]["count"] == 10
    assert reply["calls"][0]["confirm_token"]
    line = commands.format_result(item)
    assert "10 个立方体" in line and "最大为最父级" in line
    assert "seed=" in line and len(line) < 400


# --------------------------------------------------------------------------- #
# 4. Maya handler（用假 cmds，不依赖 Maya 进程）
# --------------------------------------------------------------------------- #
class _FakeCmds:
    def __init__(self, existing=()):
        self.existing = set(existing)
        self.created = []
        self.parents = []
        self.translations = []

    def objExists(self, name):
        return name in self.existing

    def polyCube(self, name, width, height, depth):
        assert width == height == depth and width > 0
        self.created.append((name, width))
        self.existing.add(name)
        return [name, name + "Shape"]

    def rename(self, node, name):
        return name

    def parent(self, child, parent):
        self.parents.append((child, parent))
        return [parent + "|" + child]

    def setAttr(self, plug, value):
        self.translations.append((plug, value))


def test_maya_handler_creates_and_links_using_the_plan(monkeypatch):
    from tak.adapters.maya import scene_ops

    fake = _FakeCmds()
    monkeypatch.setattr(scene_ops, "_c", lambda: fake)
    params = {"count": 6, "min_size": 10, "max_size": 100,
              "size_by": "volume", "seed": 99, "link": True,
              "prefix": "chainCube"}
    plan = sa.cube_chain_plan(params)
    result = scene_ops.cube_chain_create(params)

    assert len(fake.created) == 6
    assert [name for name, _ in fake.created] == plan["chain"]
    assert [width for _, width in fake.created] == [
        item["edge_cm"] for item in plan["cubes"]]
    assert fake.parents == [
        (plan["cubes"][i]["name"], plan["cubes"][i - 1]["name"])
        for i in range(1, 6)]
    assert all(value == plan["spacing"] for _, value in fake.translations)
    assert result["root"] == plan["root"] and result["count"] == 6
    assert result["parent_of"][plan["cubes"][1]["name"]] == plan["root"]


def test_maya_handler_refuses_partial_creation_on_name_clash(monkeypatch):
    from tak.adapters.maya import scene_ops

    fake = _FakeCmds(existing=["takCube_03"])
    monkeypatch.setattr(scene_ops, "_c", lambda: fake)
    with pytest.raises(ValueError, match="已存在"):
        scene_ops.cube_chain_create({"count": 5, "seed": 1})
    assert not fake.created, "查重必须在创建之前整体失败"


def test_maya_handler_registered_and_previewer_stays_core():
    """不真的调 register_all()：它会改全局 REGISTRY，影响同进程其它测试。"""
    from tak.adapters.maya import scene_ops

    assert "scene.create_cube_chain" in scene_ops._HANDLERS
    assert scene_ops._HANDLERS["scene.create_cube_chain"] is scene_ops.cube_chain_create
    # 预览器必须留在 core：MCP / Web 进程没有 Maya 也要能 dry-run
    assert "scene.create_cube_chain" not in scene_ops._PREVIEWS
    assert sa.REGISTRY.get("scene.create_cube_chain").previewer is sa._preview_cube_chain
