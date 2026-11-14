# -*- coding: utf-8 -*-
"""场景动作注册表：白名单 / 参数校验 / 两阶段确认 / 回滚 / MCP 描述。"""
from __future__ import annotations

import pytest

from tak.core import scene_actions as sa


@pytest.fixture
def fake_impl(monkeypatch):
    """把若干动作换成不发 DCC 调用的假 handler，pytest 结束后自动还原。"""
    calls = []

    def reader(params):
        calls.append(("read", params))
        return {"scene": "demo.ma", "joint": 3}

    def writer(params):
        calls.append(("write", params))
        return {"created": params["name"]}

    spec_read = sa.REGISTRY.get("scene.summary")
    spec_write = sa.REGISTRY.get("node.create")
    monkeypatch.setattr(spec_read, "handler", reader)
    monkeypatch.setattr(spec_write, "handler", writer)
    hooks = {"begin": lambda label: "undo-1",
             "commit": lambda token: calls.append(("commit", token)),
             "rollback": lambda token: calls.append(("rollback", token)),
             "undo_last": lambda token: {"ok": True}}
    monkeypatch.setitem(sa.REGISTRY._undo_hooks, "test", hooks)
    monkeypatch.setitem(sa.REGISTRY._last_write, "test", {"action": "node.create"})
    return calls


def test_capability_table_covers_four_user_needs():
    names = set(sa.REGISTRY.names())
    assert len(names) >= 18
    for required in ("scene.summary", "scene.list_nodes", "scene.get_node",
                     "rig.hierarchy", "rig.skin_info", "rig.influences",
                     "node.create", "node.rename", "node.delete",
                     "attr.set", "scene.undo_tak"):
        assert required in names
    described = sa.describe_actions()
    assert described["count"] == len(names)
    assert described["read_only_count"] + described["write_count"] == len(names)
    tools = sa.REGISTRY.mcp_tools()
    assert len(tools) == len(names)
    assert len({tool["name"] for tool in tools}) == len(tools)
    assert all(tool["inputSchema"]["type"] == "object" for tool in tools)


def test_tool_name_roundtrip():
    for name in sa.REGISTRY.names():
        spec = sa.REGISTRY.get(name)
        assert sa.REGISTRY.tool_to_action(spec.mcp_name) == name
    assert sa.REGISTRY.tool_to_action("not_a_tak_tool") == ""


def test_param_validation_is_strict():
    validate = sa.REGISTRY.validate
    assert validate("scene.list_nodes", {"limit": 3})[1] == {"limit": 3}
    for bad in ({"bogus": 1}, {"limit": "3"}, {"limit": 9999},
                {"limit": True}, {"pattern": "x" * 600}):
        with pytest.raises(sa.ActionError):
            validate("scene.list_nodes", bad)
    with pytest.raises(sa.ActionError):
        validate("node.create", {"kind": "locator"})
    with pytest.raises(sa.ActionError):
        validate("node.create", {"kind": "python_script", "name": "x"})
    with pytest.raises(sa.ActionError):
        validate("does.not.exist", {})
    # scalar 允许模型把数字写成字符串，也允许直接给数字
    assert validate("attr.set", {"node": "pCube1", "attribute": "tx",
                                 "value": "1.5"})[1]["value"] == "1.5"
    assert validate("attr.set", {"node": "pCube1", "attribute": "tx",
                                 "value": 1.5})[1]["value"] == 1.5


def test_read_action_executes_without_confirmation(fake_impl):
    result = sa.REGISTRY.execute("scene.summary", {}, dcc="test")
    assert result["ok"] and result["read_only"] and not result["dry_run"]
    assert result["result"]["scene"] == "demo.ma"
    assert fake_impl[0] == ("read", {})


def test_write_requires_preview_then_matching_token(fake_impl):
    params = {"kind": "locator", "name": "probe_loc"}
    preview = sa.REGISTRY.execute("node.create", params, dcc="test")
    assert preview["status"] == "dry_run" and preview["committed"] is False
    token = preview["confirm_token"]
    assert token

    # 没有 token / 没有 confirm / 只给 confirm 都不允许写
    assert sa.REGISTRY.execute("node.create", params, confirm=True,
                               dry_run=False, granted=["node.write"],
                               dcc="test")["status"] == "error"
    assert sa.REGISTRY.execute("node.create", params, confirm=False,
                               dry_run=False, confirm_token=token,
                               granted=["node.write"], dcc="test")["status"] == "dry_run"

    # 改了参数，token 立即失效
    tampered = dict(params, name="another")
    assert "不匹配" in sa.REGISTRY.execute(
        "node.create", tampered, confirm=True, dry_run=False,
        confirm_token=token, granted=["node.write"], dcc="test")["error"]

    # 缺权限
    denied = sa.REGISTRY.execute("node.create", params, confirm=True,
                                 dry_run=False, confirm_token=token,
                                 granted=[], dcc="test")
    assert not denied["ok"] and "缺少权限" in denied["error"]

    committed = sa.REGISTRY.execute("node.create", params, confirm=True,
                                    dry_run=False, confirm_token=token,
                                    granted=["node.write"], dcc="test")
    assert committed["ok"] and committed["committed"] is True
    assert committed["result"] == {"created": "probe_loc"}
    assert ("write", params) in fake_impl
    assert ("commit", "undo-1") in fake_impl
    # token 一次性
    assert sa.REGISTRY.execute("node.create", params, confirm=True,
                               dry_run=False, confirm_token=token,
                               granted=["node.write"], dcc="test")["status"] == "error"


def test_failed_write_rolls_back(monkeypatch):
    spec = sa.REGISTRY.get("node.create")
    events = []

    def boom(params):
        events.append("handler")
        raise RuntimeError("host exploded")

    monkeypatch.setattr(spec, "handler", boom)
    monkeypatch.setitem(sa.REGISTRY._undo_hooks, "test", {
        "begin": lambda label: "u",
        "commit": lambda token: events.append("commit"),
        "rollback": lambda token: events.append("rollback"),
    })
    params = {"kind": "locator", "name": "will_fail"}
    preview = sa.REGISTRY.execute("node.create", params, dcc="test")
    result = sa.REGISTRY.execute("node.create", params, confirm=True,
                                 dry_run=False,
                                 confirm_token=preview["confirm_token"],
                                 granted=["node.write"], dcc="test")
    assert not result["ok"] and "已回滚" in result["error"]
    assert "commit" not in events and "rollback" in events


def test_unimplemented_action_reports_clearly():
    spec = sa.REGISTRY.get("scene.frame_range")
    original = spec.handler
    spec.handler = sa._NOT_IMPLEMENTED
    try:
        result = sa.REGISTRY.execute("scene.frame_range", {})
        assert not result["ok"] and "没有宿主实现" in result["error"]
    finally:
        spec.handler = original


def test_undo_last_and_audit(monkeypatch):
    monkeypatch.setitem(sa.REGISTRY._undo_hooks, "test", {
        "undo_last": lambda token: {"undone": "node.create"},
    })
    monkeypatch.setitem(sa.REGISTRY._last_write, "test",
                        {"action": "node.create"})
    result = sa.REGISTRY.undo_last("test")
    assert result["ok"] and result["undone"] == "node.create"
    assert sa.REGISTRY.undo_last("test")["ok"] is False
    log = sa.REGISTRY.audit()
    assert log and all("seq" in row and "action" in row for row in log)


def test_actions_are_pure_python():
    """架构护栏：core 层不许出现 DCC import。"""
    import os
    source = open(sa.__file__, "r", encoding="utf-8").read()
    for forbidden in ("import maya", "from maya", "import pymxs", "import unreal",
                      "pyfbsdk", "eval(", "exec("):
        assert forbidden not in source, forbidden
    assert os.path.isfile(sa.__file__)
