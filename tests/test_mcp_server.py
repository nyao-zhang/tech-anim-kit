# -*- coding: utf-8 -*-
"""MCP stdio server：JSON-RPC 往返、工具清单、两阶段写入、零 DCC 依赖。"""
from __future__ import annotations

import io
import json
import os

from tak.core import scene_actions as sa
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


def test_config_snippet_uses_current_interpreter(monkeypatch):
    import sys
    monkeypatch.delenv("TAK_MCP_PYTHON", raising=False)
    snippet = config_snippet()
    entry = snippet["mcpServers"]["tak-scene"]
    assert entry["command"] == sys.executable
    # v0.7.2 起优先用自包含启动器：env 被客户端清空也能 import tak / 找到桥
    assert entry["args"] == [mcp.entry_script()]
    assert os.path.isfile(entry["args"][0])
    assert entry["env"]["PYTHONPATH"]
    assert entry["env"]["PYTHONIOENCODING"] == "utf-8"
    assert entry["env"]["TAK_HOME"]
    assert mcp.diagnose_config(entry) == []


def test_config_snippet_falls_back_to_module_when_entry_missing(monkeypatch):
    """启动器文件不存在（例如只装了 tak 包）时仍要能用 -m 启动。"""
    monkeypatch.setattr(mcp, "entry_script", lambda: "")
    entry = config_snippet()["mcpServers"]["tak-scene"]
    assert entry["args"] == ["-m", "tak.mcp"]


def test_config_snippet_never_points_at_dcc_host(monkeypatch):
    """在 Maya 里点「MCP 配置」时 sys.executable 是 maya.exe，不能写进配置。

    这是 v0.7.0 的真实故障：客户端去启动 Maya GUI，永远等不到 initialize。
    """
    import sys
    monkeypatch.delenv("TAK_MCP_PYTHON", raising=False)
    fake_maya = r"C:\Program Files\Autodesk\Maya2026\bin\maya.exe"
    monkeypatch.setattr(sys, "executable", fake_maya)
    monkeypatch.setattr(sys, "_base_executable", fake_maya, raising=False)
    exe, source = mcp.python_executable()
    entry = config_snippet()["mcpServers"]["tak-scene"]
    assert entry["command"] == exe
    assert entry["command"] != fake_maya
    assert not entry["command"].lower().endswith("maya.exe")
    assert mcp._is_python_exe(exe) or exe == "python"
    assert source not in ("当前解释器", "sys._base_executable")


def test_config_snippet_honors_explicit_python(monkeypatch, tmp_path):
    fake = tmp_path / "python.exe"
    fake.write_text("")
    monkeypatch.setenv("TAK_MCP_PYTHON", str(fake))
    assert config_snippet()["mcpServers"]["tak-scene"]["command"] == str(fake)
    assert mcp.python_executable()[1] == "TAK_MCP_PYTHON"


def test_python_exe_detection_rejects_dcc_hosts():
    for name in ("maya.exe", "3dsmax.exe", "MotionBuilder.exe",
                 "UnrealEditor.exe", "blender.exe"):
        assert not mcp._is_python_exe(r"C:\bin\%s" % name)
    assert mcp._is_python_exe(r"C:\bin\python.exe")
    assert mcp._is_python_exe(r"C:\bin\python3.11")
    assert mcp._is_python_exe(r"C:\bin\mayapy.exe")


def test_config_snippet_env_survives_minimal_client_env(monkeypatch, tmp_path):
    """客户端把 env 当全量替换时，TAK_HOME 保证桥发现文件仍可定位。"""
    import os
    import subprocess
    import sys

    monkeypatch.setenv("TAK_HOME", str(tmp_path))
    entry = config_snippet()["mcpServers"]["tak-scene"]
    assert entry["env"]["TAK_HOME"] == str(tmp_path)
    env = {
        "PYTHONPATH": entry["env"]["PYTHONPATH"],
        "PYTHONIOENCODING": "utf-8",
        "TAK_HOME": entry["env"]["TAK_HOME"],
        "SystemRoot": os.environ.get("SystemRoot", r"C:\Windows"),
        "PATH": os.environ.get("PATH", ""),
    }
    out = subprocess.run(
        [sys.executable, "-c",
         "from tak.core import bridge; print(bridge.discovery_path())"],
        env=env, capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    assert str(tmp_path).lower() in out.stdout.lower()


def test_stdio_initialize_roundtrip_with_real_process():
    """真实子进程起 server 并等 initialize；纯 ASCII 输出，防止 Windows 编码炸管道。"""
    import os
    import subprocess
    import sys
    import threading

    root = os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(mcp.__file__))))
    env = dict(os.environ)
    env["PYTHONPATH"] = root
    env["PYTHONIOENCODING"] = "utf-8"
    proc = subprocess.Popen([sys.executable, "-m", "tak.mcp"],
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, cwd=root, env=env)
    try:
        request = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                   "params": {"protocolVersion": "2024-11-05",
                              "capabilities": {},
                              "clientInfo": {"name": "pytest", "version": "1"}}}
        proc.stdin.write((json.dumps(request) + "\n").encode("ascii"))
        proc.stdin.flush()
        box: dict = {}

        def _read():
            box["line"] = proc.stdout.readline()

        reader = threading.Thread(target=_read, daemon=True)
        reader.start()
        reader.join(20)
        assert "line" in box, "20 秒内没有收到 initialize 响应"
        raw = box["line"]
        raw.decode("ascii")  # 非 ASCII 会抛，正是要拦的回归
        answer = json.loads(raw.decode("ascii"))
        assert answer["id"] == 1
        assert answer["result"]["serverInfo"]["name"] == "tak-scene-bridge"
        assert "先用" in answer["result"]["instructions"]
    finally:
        proc.kill()
        try:
            proc.wait(timeout=5)
        except Exception:
            pass


def test_serve_survives_bridge_status_failure(monkeypatch):
    """启动日志里的探测失败不能让整个 stdio 进程静默退出。"""
    def broken():
        raise RuntimeError("discovery 文件损坏")

    monkeypatch.setattr(mcp.tak_bridge, "bridge_status", broken)
    source = io.StringIO('{"jsonrpc":"2.0","id":3,"method":"ping"}\n')
    sink = io.StringIO()
    assert serve(stdin=source, stdout=sink) == 0
    assert json.loads(sink.getvalue().strip())["id"] == 3


def test_mcp_layer_has_no_dcc_imports():
    import os
    source = open(mcp.__file__, "r", encoding="utf-8").read()
    for forbidden in ("import maya", "from maya", "pymxs", "pyfbsdk"):
        assert forbidden not in source
    assert os.path.isfile(mcp.__file__)


def test_tools_list_flags_stale_bridge_capabilities(monkeypatch):
    """DCC 没重启时桥上报的是旧能力表；tools/list 必须把差异说清楚。

    v0.8.0 加 scene.create_cube_chain 后，用户当前 Maya GUI 里的桥仍是
    v0.7.2 的 18 个动作，工具数不会自己变成 22。
    """
    stale = [tool for tool in sa.REGISTRY.mcp_tools()
             if tool["name"] != "tak_scene_create_cube_chain"]
    monkeypatch.setattr(mcp.tak_bridge, "bridge_status",
                        lambda: {"running": True, "allow_write": False})
    monkeypatch.setattr(mcp.tak_bridge, "call",
                        lambda *a, **k: {"ok": True, "mcp_tools": stale})
    payload = rpc("tools/list")["result"]
    assert payload["bridge_running"] is True
    assert payload["capabilities_stale"] is True
    assert "tak_scene_create_cube_chain" in payload["stale_hint"]
    assert "重启" in payload["stale_hint"]


def test_tools_list_not_stale_when_bridge_is_current(monkeypatch):
    current = sa.REGISTRY.mcp_tools()
    monkeypatch.setattr(mcp.tak_bridge, "bridge_status",
                        lambda: {"running": True, "allow_write": False})
    monkeypatch.setattr(mcp.tak_bridge, "call",
                        lambda *a, **k: {"ok": True, "mcp_tools": current})
    payload = rpc("tools/list")["result"]
    assert payload["capabilities_stale"] is False
    assert payload["stale_hint"] == ""
    assert len(payload["tools"]) == len(sa.REGISTRY.mcp_tools()) + len(mcp._META_TOOLS)
