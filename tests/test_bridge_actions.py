# -*- coding: utf-8 -*-
"""桥的 /bridge/actions 与 /bridge/act：写开关、两阶段、status 不被 HTTP 码覆盖。"""
from __future__ import annotations

import json

import pytest

from tak.core import bridge, scene_actions as sa
from tak.core import config as tak_config


@pytest.fixture
def isolated_config(tmp_path, monkeypatch):
    monkeypatch.setenv("TAK_HOME", str(tmp_path))
    return tmp_path


def _server() -> bridge.BridgeServer:
    # 不 start()：_host_call 没有执行器时直调，适合纯 Python 回归。
    return bridge.BridgeServer(dcc="maya", token="test-token")


def test_write_switch_defaults_off_and_persists(isolated_config):
    assert bridge.allow_write() is False
    bridge.set_allow_write(True)
    assert bridge.allow_write() is True
    # 归一化层必须保留新键，否则「开关打开又被悄悄丢掉」
    payload = json.load(open(tak_config.config_path(), "r", encoding="utf-8"))
    assert payload["bridge"]["allow_write"] is True
    bridge.set_allow_write(False)
    assert bridge.allow_write() is False


def test_status_reports_write_switch(isolated_config):
    server = _server()
    assert "allow_write" in server.status()
    assert server.status()["allow_write"] is False


def test_actions_endpoint_lists_capabilities_and_mcp_tools(isolated_config):
    payload = _server().actions()
    assert payload["ok"] and payload["count"] >= 18
    assert len(payload["mcp_tools"]) == payload["count"]
    assert payload["allow_write"] is False
    assert "GET  /bridge/actions" in bridge.ENDPOINTS
    assert any("/bridge/act" in item for item in bridge.ENDPOINTS)


def test_read_action_runs_without_switch(isolated_config, monkeypatch):
    spec = sa.REGISTRY.get("scene.summary")
    monkeypatch.setattr(spec, "handler",
                        lambda params: {"scene": "bridge.ma", "joint": 4})
    payload = _server().act("scene.summary")
    assert payload["ok"] and payload["status"] == "ok"
    assert payload["result"]["scene"] == "bridge.ma"
    assert payload["committed"] is False


def test_write_action_blocked_until_switch_enabled(isolated_config, monkeypatch):
    # core 的 declare() 只声明能力、handler 由适配器注册；纯 Python 测试里
    # 显式装一个假实现，才能走到「预览 → 写开关」这段逻辑。
    spec = sa.REGISTRY.get("node.create")
    calls = []
    monkeypatch.setattr(spec, "handler",
                        lambda p: calls.append(p) or {"created": p["name"]})
    monkeypatch.setitem(sa.REGISTRY._undo_hooks, "maya", {
        "begin": lambda label: "u", "commit": lambda token: None,
        "rollback": lambda token: None,
    })
    server = _server()
    params = {"kind": "locator", "name": "bridge_loc"}
    preview = server.act("node.create", params)
    assert preview["status"] == "dry_run" and preview["confirm_token"]

    blocked = server.act("node.create", params, confirm=True, dry_run=False,
                         confirm_token=preview["confirm_token"])
    assert not blocked["ok"] and "外部写入未启用" in blocked["error"]

    bridge.set_allow_write(True)
    committed = server.act("node.create", params, confirm=True, dry_run=False,
                           confirm_token=preview["confirm_token"])
    assert committed["ok"] and committed["committed"] is True
    assert calls and calls[0]["name"] == "bridge_loc"


def test_unknown_action_is_rejected(isolated_config):
    payload = _server().act("scene.eval_python", {"code": "print(1)"})
    assert not payload["ok"] and "未知动作" in payload["error"]


def test_cli_lists_new_commands(isolated_config, capsys):
    assert bridge.main(["actions"]) in (0, 1)  # 桥没开时返回 1 也属预期
    out = capsys.readouterr().out
    assert "ok" in out or "error" in out
    bridge.main(["write", "on"])
    capsys.readouterr()
    assert bridge.allow_write() is True
    bridge.main(["write", "off"])
    capsys.readouterr()
    assert bridge.allow_write() is False
