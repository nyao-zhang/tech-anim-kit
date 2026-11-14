# -*- coding: utf-8 -*-
"""MCP stdio server：JSON-RPC 往返、工具清单、两阶段写入、零 DCC 依赖。"""
from __future__ import annotations

import io
import json

from tak.mcp import config_snippet, handle, serve
from tak.mcp import server as mcp


def rpc(method, params=None, rid=1):
    return handle({"jsonrpc": "2.0", "id": rid, "method": method,
                   "params": params or {}})


def test_initialize_and_notifications():
    result = rpc("initialize", {"protocolVersion": "2024-11-05",
                                "capabilities": {},
                                "clientInfo": {"name": "test", "version": "1"}})
    assert result["id"] == 1
    payload = result["result"]
    assert payload["protocolVersion"] == "2024-11-05"
    assert payload["serverInfo"]["name"] == "tak-scene-bridge"
    assert "tools" in payload["capabilities"]
    assert rpc("ping")["result"] == {}
    # notification 没有 id，绝不能回消息
    assert handle({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None
    assert handle({"jsonrpc": "2.0", "method": "notifications/cancelled"}) is None


def test_tools_list_and_meta_tools(monkeypatch):
    monkeypatch.setattr(mcp.tak_bridge, "bridge_status", lambda: {"running": False})
    payload = rpc("tools/list")["result"]
    names = {tool["name"] for tool in payload["tools"]}
    assert "tak_status" in names and "tak_list_actions" in names
    assert "tak_undo" in names and "tak_scene_summary" in names
    assert "tak_node_create" in names
    # 写动作必须能接收 confirm_token，否则客户端按 schema 会拒传
    create = next(t for t in payload["tools"] if t["name"] == "tak_node_create")
    props = create["inputSchema"]["properties"]
    assert "confirm_token" in props and "confirm" in props
    assert "confirm_token" not in next(
        t for t in payload["tools"] if t["name"] == "tak_scene_summary"
    )["inputSchema"]["properties"]


def test_status_tool_and_unknown_method():
    status = json.loads(rpc("tools/call", {"name": "tak_status",
                                           "arguments": {}}
                            )["result"]["content"][0]["text"])
    assert "running" in status and "hint" in status and "allow_write" in status
    listing = json.loads(rpc("tools/call", {"name": "tak_list_actions",
                                            "arguments": {}}
                             )["result"]["content"][0]["text"])
    assert listing["ok"] and len(listing["actions"]) >= 18
    error = rpc("no/such/method")
    assert error["error"]["code"] == -32601
    assert handle({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                   "params": {"name": "x", "arguments": "not-a-dict"}}
                  )["result"]["isError"] is True


def test_tools_call_when_bridge_not_running(monkeypatch):
    monkeypatch.setattr(mcp.tak_bridge, "bridge_status", lambda: {"running": False})
    result = rpc("tools/call", {"name": "tak_scene_summary", "arguments": {}})
    assert result["result"]["isError"] is True
    text = result["result"]["content"][0]["text"]
    assert "桥未启动" in text


def test_two_phase_write_is_forwarded_to_bridge(monkeypatch):
    sent = []

    def fake_call(path, method="GET", body=None, timeout=30.0):
        sent.append((path, method, dict(body or {})))
        if not body.get("confirm"):
            return {"ok": True, "status": "dry_run", "confirm_token": "tok-1",
                    "committed": False, "http_status": 200}
        return {"ok": True, "status": "committed", "committed": True,
                "http_status": 200}

    monkeypatch.setattr(mcp.tak_bridge, "bridge_status",
                        lambda: {"running": True, "url": "http://127.0.0.1:1"})
    monkeypatch.setattr(mcp.tak_bridge, "call", fake_call)

    args = {"kind": "locator", "name": "foot_ctrl"}
    first = json.loads(rpc("tools/call", {"name": "tak_node_create",
                                          "arguments": dict(args)}
                           )["result"]["content"][0]["text"])
    assert first["status"] == "dry_run"
    assert sent[0][2]["confirm"] is False and sent[0][2]["dry_run"] is True
    assert "confirm_token" not in sent[0][2]["params"]

    second_args = dict(args, confirm=True, confirm_token=first["confirm_token"])
    second = json.loads(rpc("tools/call", {"name": "tak_node_create",
                                           "arguments": second_args}
                            )["result"]["content"][0]["text"])
    assert second["committed"] is True
    body = sent[1][2]
    assert body["confirm"] is True and body["dry_run"] is False
    assert body["confirm_token"] == "tok-1"
    # 控制字段不能混进动作参数，否则白名单会拒绝
    assert body["params"] == args


def test_serve_handles_parse_error_and_multiple_lines(monkeypatch):
    monkeypatch.setattr(mcp.tak_bridge, "bridge_status", lambda: {"running": False})
    source = io.StringIO('{bad json}\n{"jsonrpc":"2.0","id":7,"method":"ping"}\n')
    sink = io.StringIO()
    assert serve(stdin=source, stdout=sink) == 0
    lines = [line for line in sink.getvalue().splitlines() if line.strip()]
    assert len(lines) == 2
    assert json.loads(lines[0])["error"]["code"] == -32700
    assert json.loads(lines[1])["id"] == 7


def test_config_snippet_uses_current_interpreter():
    import sys
    snippet = config_snippet()
    entry = snippet["mcpServers"]["tak-scene"]
    assert entry["command"] == sys.executable
    assert entry["args"] == ["-m", "tak.mcp"]
    assert entry["env"]["PYTHONPATH"]


def test_mcp_layer_has_no_dcc_imports():
    import os
    source = open(mcp.__file__, "r", encoding="utf-8").read()
    for forbidden in ("import maya", "from maya", "pymxs", "pyfbsdk"):
        assert forbidden not in source
    assert os.path.isfile(mcp.__file__)
