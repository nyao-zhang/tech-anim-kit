# -*- coding: utf-8 -*-
"""自然语言指令：本地解析、function calling、降级、两阶段执行。"""
from __future__ import annotations

import json

import pytest

from tak.core.ai import commands
from tak.core import scene_actions as sa


@pytest.mark.parametrize("text,action,params", [
    ("场景里有多少关节", "scene.summary", {}),
    ("列出所有 joint", "scene.list_nodes", {"type": "joint"}),
    ("列出所有控制器", "scene.list_nodes", {"pattern": "*ctrl*"}),
    ("找 foot 相关的节点", "scene.find", {"query": "foot"}),
    ("查 pCube1 的蒙皮信息", "rig.skin_info", {"mesh": "pCube1"}),
    ("查 root 的层级", "rig.hierarchy", {"root": "root"}),
    ("当前帧范围是多少", "scene.frame_range", {}),
    ("当前选中了什么", "selection.get", {}),
    ("创建一个 locator 叫 foot_ctrl", "node.create",
     {"kind": "locator", "name": "foot_ctrl"}),
    ("创建一个 joint 叫 root_jnt", "node.create",
     {"kind": "joint", "name": "root_jnt"}),
    ("把 pCube1 改名为 body", "node.rename",
     {"node": "pCube1", "name": "body"}),
    ("删除 pCube1", "node.delete", {"node": "pCube1"}),
])
def test_local_parser(text, action, params):
    parsed = commands.parse_local(text)
    assert parsed["ok"] and parsed["calls"], parsed.get("hint")
    call = parsed["calls"][0]
    assert call["action"] == action
    assert call["params"] == params
    assert call["confirm_token"] == ""


def test_local_parser_refuses_to_guess():
    for text in ("今天天气怎么样", "查一下蒙皮", "创建个东西", "删掉它"):
        parsed = commands.parse_local(text)
        assert not parsed["ok"] or not parsed["calls"], text
        assert parsed.get("hint")


def test_system_prompt_forbids_code_generation():
    assert "不要生成 Python" in commands.SYSTEM_PROMPT
    assert "不要编造工具" in commands.SYSTEM_PROMPT


def test_model_function_calling_and_control_field_stripping(monkeypatch):
    import tak.core.ai.connection as connection

    class FakeClient:
        def chat_tools(self, messages, tools, **kwargs):
            assert messages[0]["role"] == "system"
            assert any(tool["name"] == "tak_scene_get_node" for tool in tools)
            return {"content": "", "tool_calls": [
                {"function": {
                    "name": "tak_node_create",
                    "arguments": json.dumps({"kind": "locator", "name": "ai_ctrl",
                                             "confirm": True,
                                             "confirm_token": "forged",
                                             "dry_run": False}),
                }},
                {"function": {"name": "tak_scene_summary", "arguments": "{}"}},
            ]}

    class FakeProvider:
        client = FakeClient()

    monkeypatch.setattr(connection, "resolve_settings",
                        lambda cfg=None: {"api_key_present": True})
    result = commands.parse_with_model("建一个控制器", provider=FakeProvider())
    assert result["ok"] and result["mode"] == "model"
    assert [call["action"] for call in result["calls"]] == [
        "node.create", "scene.summary"]
    # 模型不能自己给自己确认
    assert result["calls"][0]["params"] == {"kind": "locator", "name": "ai_ctrl"}
    assert result["calls"][0]["confirm_token"] == ""


def test_model_failure_falls_back_to_local(monkeypatch):
    monkeypatch.setattr(commands, "parse_with_model",
                        lambda text, provider=None: {
                            "ok": False, "mode": "model",
                            "error": "AITimeoutError: 超时", "calls": []})
    result = commands.resolve("列出所有 joint")
    assert result["mode"] == "local-fallback"
    assert result["calls"][0]["action"] == "scene.list_nodes"
    assert "超时" in result["model_error"]


def test_command_preview_then_execute(monkeypatch):
    spec = sa.REGISTRY.get("node.create")
    events = []
    monkeypatch.setattr(spec, "handler",
                        lambda params: events.append(params) or {"created": params["name"]})
    monkeypatch.setitem(sa.REGISTRY._undo_hooks, "maya", {
        "begin": lambda label: "u", "commit": lambda token: None,
        "rollback": lambda token: None,
    })
    first = commands.command("创建一个 locator 叫 foot_ctrl",
                             use_model=False, dcc="maya", allow_write=True)
    assert first["need_confirm"] is True
    assert first["results"][0]["status"] == "dry_run"
    assert first["calls"][0]["confirm_token"]
    assert not events  # 预览绝不碰宿主

    blocked = commands.command(calls=first["calls"], use_model=False,
                               dcc="maya", allow_write=False, execute=True)
    assert blocked["results"][0]["status"] == "dry_run" and not events

    done = commands.command(calls=first["calls"], dcc="maya",
                            allow_write=True, execute=True)
    assert done["results"][0]["committed"] is True
    assert events and events[0]["name"] == "foot_ctrl"
    assert "待确认" not in commands.format_result(done["results"][0])


def test_format_result_is_human_readable():
    assert "✗" in commands.format_result({"ok": False, "action": "scene.find",
                                          "error": "找不到"})
    assert "✓" in commands.format_result({"ok": True, "action": "scene.summary",
                                          "read_only": True,
                                          "result": {"scene": "a.ma"}})
    assert "待确认" in commands.format_result({"ok": True, "action": "node.create",
                                               "read_only": False,
                                               "status": "dry_run",
                                               "preview": {"operation": "CREATE"}})
