# -*- coding: utf-8 -*-
"""批次 3：Web AI 工作台端点的路由与安全边界回归。

用可控假模块替换 tak.core 的 AI 实现，因此这些测试在子模块尚未加载、
离线无密钥、无 DCC 的环境里也能稳定验证「服务端强制 dry-run」等不变量。
"""
from __future__ import annotations

import os
import sys
from types import ModuleType

import pytest

import tak.core
from tak.ui.web import server

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class FakeSession:
    def __init__(self):
        self.calls = []

    def chat(self, message, snapshot=None):
        self.calls.append(("chat", message, snapshot))
        return {"status": "explained", "reply": "ok"}

    def confirm(self, plan_id, confirm=False, granted=None, dry_run=True):
        self.calls.append(("confirm", plan_id, confirm, list(granted or []), dry_run))
        return {"ok": True, "dry_run": dry_run, "committed": False}

    def history(self):
        return [{"turn": 1}]

    def describe(self):
        return {"session_id": "web", "safety": ["dry-run"]}


class FakeKB:
    def __init__(self):
        self.calls = []

    def search(self, query, top_k=3, options=None):
        self.calls.append((query, top_k))
        return [{"doc_id": "d1", "title": "文档", "score": 1.0,
                 "excerpt": "片段", "citation": "knowledge/rig_naming.md"}]


def _install_fake_module(monkeypatch, package_name, module_name, **attributes):
    package = ModuleType(package_name)
    package.__path__ = []  # type: ignore[attr-defined]
    module = ModuleType(module_name)
    for key, value in attributes.items():
        setattr(module, key, value)
    monkeypatch.setitem(sys.modules, package_name, package)
    monkeypatch.setitem(sys.modules, module_name, module)
    short = package_name.rsplit(".", 1)[-1]
    monkeypatch.setattr(tak.core, short, package, raising=False)
    setattr(package, module_name.rsplit(".", 1)[-1], module)
    return module


@pytest.fixture
def fake_agent(monkeypatch):
    session = FakeSession()
    monkeypatch.setattr(server, "_agent_session", lambda session_id: session)
    return session


@pytest.fixture
def fake_kb(monkeypatch):
    kb = FakeKB()
    monkeypatch.setattr(server, "_knowledge_base", lambda: kb)
    return kb


@pytest.fixture
def fake_vision(monkeypatch):
    calls = []

    def inspect_frames(frames, prompt=None, provider=None, snapshot=None, options=None):
        calls.append((list(frames), dict(snapshot or {}), dict(options or {})))
        return {"ok": True, "candidates": [], "source": "deterministic_fallback"}

    _install_fake_module(monkeypatch, "tak.core.vision", "tak.core.vision.frames",
                         inspect_frames=inspect_frames)
    return calls


def test_health_reports_ai_modules():
    status, payload = server.handle_api("GET", "/api/health")
    assert status == 200 and payload["ok"] is True
        # mapping / motion 随工具一起删除，不能继续出现在能力表里 —— 否则前端
    # 会照着这份表渲染注定 503 的页签。
    assert set(payload["ai"]) == {"agent", "knowledge", "vision"}
    assert all(isinstance(value, bool) for value in payload["ai"].values())


def test_agent_chat_never_writes(fake_agent):
    status, payload = server.handle_api(
        "POST", "/api/agent",
        body={"action": "chat", "message": "检查骨架", "snapshot": {"joints": {}}})
    assert status == 200
    assert payload["result"]["status"] == "explained"
    assert fake_agent.calls[0][0] == "chat"
    assert fake_agent.calls[0][2] == {"joints": {}}


def test_agent_confirm_forces_dry_run_without_all_conditions(fake_agent):
    server.handle_api("POST", "/api/agent", body={
        "action": "confirm", "plan_id": "plan-1", "confirm": True,
        "dry_run": False, "granted": []})
    assert fake_agent.calls[-1][2] is False      # confirm=False
    assert fake_agent.calls[-1][4] is True       # dry_run=True

    server.handle_api("POST", "/api/agent", body={
        "action": "confirm", "plan_id": "plan-1", "confirm": True,
        "dry_run": True, "granted": ["node.write"]})
    assert fake_agent.calls[-1][2] is False
    assert fake_agent.calls[-1][4] is True


def test_agent_confirm_only_accepts_whitelisted_permissions(fake_agent):
    status, payload = server.handle_api("POST", "/api/agent", body={
        "action": "confirm", "plan_id": "plan-1", "confirm": True,
        "dry_run": False, "granted": ["node.write", "scene.write", "export.fbx"]})
    assert status == 200
    assert fake_agent.calls[-1][2] is True
    assert fake_agent.calls[-1][3] == ["node.write"]
    assert fake_agent.calls[-1][4] is False


def test_agent_missing_plan_id_and_bad_method(fake_agent):
    status, payload = server.handle_api("POST", "/api/agent",
                                        body={"action": "confirm"})
    assert status == 400 and payload["ok"] is False
    status, _ = server.handle_api("GET", "/api/agent")
    assert status == 405


def test_agent_history_describe_and_reset(fake_agent):
    for action in ("history", "describe", "reset"):
        status, payload = server.handle_api("POST", "/api/agent",
                                            body={"action": action})
        assert status == 200 and payload["action"] == action


def test_knowledge_endpoint(fake_kb):
    status, payload = server.handle_api(
        "GET", "/api/knowledge?q=%E6%9D%83%E9%87%8D&top_k=2")
    assert status == 200
    assert payload["query"] == "权重"
    assert payload["top_k"] == 2 and payload["count"] == 1
    assert payload["hits"][0]["citation"].endswith(".md")
    assert fake_kb.calls[-1] == ("权重", 2)


def test_knowledge_requires_query_and_valid_top_k(fake_kb):
    status, _ = server.handle_api("GET", "/api/knowledge")
    assert status == 400
    status, _ = server.handle_api("GET", "/api/knowledge?q=a&top_k=oops")
    assert status == 400


def test_vision_endpoint(fake_vision):
    frames = [{"frame": 1, "image": "data:image/png;base64,AA"}]
    status, payload = server.handle_api(
        "POST", "/api/vision",
        body={"frames": frames, "snapshot": {"joints": {}}, "options": {"top_k": 2}})
    assert status == 200
    assert payload["result"]["source"] == "deterministic_fallback"
    assert fake_vision[-1][0] == frames
    assert fake_vision[-1][2] == {"top_k": 2}


def test_vision_rejects_non_array(fake_vision):
    status, payload = server.handle_api("POST", "/api/vision", body={"frames": {}})
    assert status == 400 and payload["ok"] is False


def test_panel_has_ai_workbench_and_offline_only():
    with open(os.path.join(ROOT, "tak", "ui", "web", "panel.html"),
              "r", encoding="utf-8") as fh:
        html = fh.read()
    assert 'id="aiWorkbench"' in html
    for element in ("agentSendBtn", "kbSearchBtn", "visionRunBtn"):
        assert 'id="%s"' % element in html
    assert "cdn" not in html.lower()
    with open(os.path.join(ROOT, "tak", "ui", "web", "app.js"),
              "r", encoding="utf-8") as fh:
        script = fh.read()
    for route in ("/api/agent", "/api/knowledge", "/api/vision"):
        assert route in script


def test_serve_in_thread_is_reused_and_stoppable():
    first = server.serve_in_thread(port=0)
    try:
        assert first["ok"] is True and first["reused"] is False
        second = server.serve_in_thread(port=0, tab="kb")
        assert second["reused"] is True
        assert second["port"] == first["port"]
        assert second["url"].endswith("?tab=kb")
    finally:
        assert server.stop_panel()["stopped"] is True
    assert server.stop_panel()["stopped"] is False


def test_removed_capabilities_are_really_gone():
    """骨骼映射 / 动作检索随工具一起删除：接口必须 404，页签不能再出现。

    留着「调用必 503」的接口比删掉更糟 —— 前端看到接口存在就会渲染页签，
    用户点进去只能拿到一条报错。
    """
    for route in ("/api/motion", "/api/mapping"):
        status, payload = server.handle_api("POST", route, body={})
        assert status == 404, route
        assert payload["ok"] is False
    with open(os.path.join(ROOT, "tak", "ui", "web", "panel.html"),
              "r", encoding="utf-8") as fh:
        html = fh.read()
    for token in ('data-tab="motion"', 'data-tab="mapping"',
                  'id="motionRunBtn"', 'id="mappingRunBtn"'):
        assert token not in html, token
    assert 'data-tab="chat"' in html and 'data-tab="kb"' in html
