# -*- coding: utf-8 -*-
"""DCC 本地桥（tak.core.bridge）回归测试。

桥是「WorkBuddy 调用 DCC」的唯一入口，所以这组测试的重点不是功能多少，
而是**四条安全边界**有没有守住：

1. 只绑回环；2. 除 health 外必须带 token；3. 不暴露未知工具；4. 停服清理发现文件。
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

import pytest

from tak.core import bridge


class FakeAdapter:
    """最小适配器：够 run_tool 跑起来，且不碰任何宿主。"""

    def describe(self):
        return {"dcc": "maya", "name": "FakeAdapter"}

    def get_skeleton_snapshot(self):
        return {
            "dcc": "maya", "version": "2026", "unit": "cm", "up_axis": "y",
            "joints": {"root": {"parent": None, "scale": [1.0, 1.0, 1.0],
                                "rotate": [0.0, 0.0, 0.0]}},
            "frame_range": [1.0, 120.0], "skin": {},
            "metadata": {"scene": "C:/tmp/rig.ma", "mesh_count": 2,
                         "health": {"orphan_nodes": [], "_meta": {"collected": []}}},
        }


@pytest.fixture()
def server(tmp_path, monkeypatch):
    monkeypatch.setenv("TAK_HOME", str(tmp_path))
    payload = bridge.start_bridge(dcc="maya", port=0, adapter=FakeAdapter())
    assert payload["ok"] is True
    try:
        yield payload
    finally:
        bridge.stop_bridge()


def _raw(path, token=None, method="GET", body=None):
    """直接发请求，绕过客户端封装里的 token 兜底。"""
    found = bridge.load_discovery()
    url = found["url"] + path
    data = None
    headers = {}
    if token is not None:
        headers["X-TAK-Token"] = token
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers,
                                     method=method)
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def _token():
    return bridge.tak_config.load()["bridge"]["token"]


# --------------------------------------------------------------------------
# 生命周期与发现文件
# --------------------------------------------------------------------------
def test_start_writes_discovery_and_stop_clears_it(tmp_path, monkeypatch):
    monkeypatch.setenv("TAK_HOME", str(tmp_path))
    payload = bridge.start_bridge(dcc="maya", port=0, adapter=FakeAdapter())
    try:
        assert payload["port"] > 0
        assert payload["host"] == "127.0.0.1"
        found = bridge.load_discovery()
        assert found["port"] == payload["port"]
        assert found["url"].startswith("http://127.0.0.1:")
        assert found["dcc"] == "maya"
    finally:
        bridge.stop_bridge()
    assert bridge.load_discovery() == {}


def test_second_start_reuses_the_running_bridge(server):
    again = bridge.start_bridge(dcc="maya", port=0, adapter=FakeAdapter())
    assert again["ok"] is True and again["reused"] is True
    assert again["port"] == server["port"]


def test_status_reports_owner(server):
    inside = bridge.bridge_status()
    assert inside["running"] is True and inside["owner"] == "当前进程"
    bridge.stop_bridge()
    outside = bridge.bridge_status()
    assert outside["running"] is False
    assert outside["hint"]


def test_token_is_generated_once_and_persisted(server, tmp_path):
    first = _token()
    assert len(first) == 64          # 32 字节 hex
    assert bridge.ensure_token() == first
    # 配置文件里确实落了盘，重启后还能用同一个 token
    with open(os.path.join(str(tmp_path), "config.json"), encoding="utf-8") as fh:
        assert json.load(fh)["bridge"]["token"] == first


# --------------------------------------------------------------------------
# 鉴权
# --------------------------------------------------------------------------
def test_health_needs_no_token_and_leaks_no_scene_data(server):
    status, payload = _raw("/bridge/health")
    assert status == 200
    assert payload["auth_required"] is True
    # 关键：探测接口不能顺手把场景信息带出去
    assert "joints" not in payload and "scene" not in payload


def test_scene_requires_token(server):
    status, payload = _raw("/bridge/scene")
    assert status == 401
    assert payload["ok"] is False
    status, payload = _raw("/bridge/scene", token="deadbeef")
    assert status == 401


def test_scene_with_token_returns_summary_not_a_full_dump(server):
    status, payload = _raw("/bridge/scene", token=_token())
    assert status == 200
    assert payload["joint_count"] == 1
    assert payload["health_keys"] == ["orphan_nodes"]
    assert "joints" not in payload           # 默认只给预览，不给全量
    status, full = _raw("/bridge/scene?full=1", token=_token())
    assert status == 200 and "joints" in full


# --------------------------------------------------------------------------
# 工具与运行
# --------------------------------------------------------------------------
def test_tools_endpoint_lists_visible_tools_only(server):
    status, payload = _raw("/bridge/tools", token=_token())
    ids = {item["id"] for item in payload["tools"]}
    assert {"qa_inspector", "asset_pipeline"} <= ids
    assert "rig_health_guard" not in ids      # internal 规则提供者不出现


def test_run_rejects_tools_outside_the_visible_list(server):
    status, payload = _raw("/bridge/run", token=_token(), method="POST",
                           body={"tool_id": "rig_health_guard"})
    assert status == 400
    assert "不在当前 DCC 的可见工具列表" in payload["error"]
    assert payload["available"]


def test_run_executes_a_tool_and_summarises_findings(server):
    status, payload = _raw("/bridge/run", token=_token(), method="POST",
                           body={"tool_id": "qa_inspector",
                                 "options": {"checks": ["rig_health_guard.orphan_nodes"]}})
    assert status == 200
    assert payload["ok"] is True and payload["tool_id"] == "qa_inspector"
    # 快照里 health 是空列表：说明「查过了没问题」，不应报缺口
    assert payload["metadata"]["health_gaps"] == []


def test_unknown_route_lists_available_endpoints(server):
    status, payload = _raw("/bridge/wat", token=_token())
    assert status == 400
    assert "/bridge/scene" in payload["error"]


# --------------------------------------------------------------------------
# 对话（只读）
# --------------------------------------------------------------------------
def test_chat_is_read_only_and_says_so(server):
    status, payload = _raw("/bridge/chat", token=_token(), method="POST",
                           body={"message": "解释当前快照存在的问题"})
    assert status == 200
    assert payload["ok"] is True
    assert "只读" in payload["note"]
    assert isinstance(payload["turn"], dict)
    assert "plan" in payload["turn"] or "intent" in payload["turn"]


def test_chat_requires_a_message(server):
    status, payload = _raw("/bridge/chat", token=_token(), method="POST", body={})
    assert status == 400 and payload["ok"] is False


# --------------------------------------------------------------------------
# 客户端封装与 CLI
# --------------------------------------------------------------------------
def test_client_call_round_trip(server):
    payload = bridge.call("/bridge/tools")
    assert payload["ok"] is True
    assert payload["status"] == 200


def test_client_reports_missing_bridge(tmp_path, monkeypatch):
    monkeypatch.setenv("TAK_HOME", str(tmp_path))
    payload = bridge.call("/bridge/tools")
    assert payload["ok"] is False
    assert "桥未启动" in payload["error"]


def test_main_status_runs_offline(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("TAK_HOME", str(tmp_path))
    assert bridge.main(["status"]) == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed["running"] is False
    assert printed["endpoints"]


# --------------------------------------------------------------------------
# 主线程超时：挂住比报错更难排查
# --------------------------------------------------------------------------
def test_busy_host_thread_returns_503_instead_of_hanging(tmp_path, monkeypatch):
    """主线程卡住时必须超时报错，而不是把 HTTP 请求无限挂起。

    Maya 里 ``executeInMainThreadWithResult`` 靠 idle 队列执行；主线程被模态
    对话框占住时队列不推进，请求会一直挂着。这里用一个永不返回的执行器模拟。
    """
    import threading
    monkeypatch.setenv("TAK_HOME", str(tmp_path))
    release = threading.Event()

    def stuck(func, *args, **kwargs):
        release.wait(30)          # 模拟主线程没空处理
        return None

    payload = bridge.start_bridge(dcc="maya", port=0, adapter=FakeAdapter(),
                                  call_timeout=0.3)
    try:
        # 手工换掉执行器（start_bridge 走的是适配器注册表）
        server = bridge.active()
        server._executor = stuck
        status, body = _raw("/bridge/scene", token=_token())
        assert status == 503
        assert "主线程" in body["error"]
        assert body["retry_after_ms"] > 0
    finally:
        release.set()
        bridge.stop_bridge()


# --------------------------------------------------------------------------
# 主线程超时：挂住比报错更难排查
# --------------------------------------------------------------------------
def test_busy_host_thread_returns_503_instead_of_hanging(tmp_path, monkeypatch):
    """主线程卡住时必须超时报错，而不是把 HTTP 请求无限挂起。

    Maya 里 ``executeInMainThreadWithResult`` 靠 idle 队列执行；主线程被模态
    对话框占住时队列不推进，请求会一直挂着。这里用一个永不返回的执行器模拟。
    """
    import threading
    monkeypatch.setenv("TAK_HOME", str(tmp_path))
    release = threading.Event()

    def stuck(func, *args, **kwargs):
        release.wait(30)          # 模拟主线程没空处理
        return None

    payload = bridge.start_bridge(dcc="maya", port=0, adapter=FakeAdapter(),
                                  call_timeout=0.3)
    try:
        # 手工换掉执行器（start_bridge 走的是适配器注册表）
        server = bridge.active()
        server._executor = stuck
        status, body = _raw("/bridge/scene", token=_token())
        assert status == 503
        assert "主线程" in body["error"]
        assert body["retry_after_ms"] > 0
    finally:
        release.set()
        bridge.stop_bridge()
