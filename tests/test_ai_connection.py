# -*- coding: utf-8 -*-
"""AI 连接层（tak.core.ai.connection）测试：真实起一个假的 OpenAI 兼容服务。

为什么要起真服务而不是 mock 掉 client：这一层的价值全在「地址/密钥/模型
三者都对不对」，mock 掉 client 就什么都没验证。用 127.0.0.1 上的小服务把
完整链路（解析设置 → 组请求 → 发 HTTP → 解析响应 → 生成诊断）跑一遍。
"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from tak.core.ai import connection
from tak.core.ai.providers import MockProvider, HTTPProvider, get_provider


class _FakeOpenAI(BaseHTTPRequestHandler):
    """最小 chat/completions 实现，行为由类属性控制。"""

    mode = "ok"          # ok | unauthorized | notfound | garbled
    seen = []

    def log_message(self, fmt, *args):  # noqa: N802
        return

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length).decode("utf-8"))
        _FakeOpenAI.seen.append({"path": self.path, "body": body,
                                 "auth": self.headers.get("Authorization")})
        if self.mode == "unauthorized":
            payload, status = {"error": "bad key"}, 401
        elif self.mode == "notfound":
            payload, status = {"error": "no such model"}, 404
        elif self.mode == "garbled":
            raw = b"not json at all"
            self.send_response(200)
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
            return
        else:
            payload = {"choices": [{"message": {"content": "pong"}}]}
            status = 200
        raw = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


@pytest.fixture()
def fake_api(monkeypatch, tmp_path):
    _FakeOpenAI.mode = "ok"
    _FakeOpenAI.seen = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _FakeOpenAI)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = "http://127.0.0.1:%d/v1" % server.server_address[1]
    monkeypatch.setenv("TAK_HOME", str(tmp_path))
    monkeypatch.setenv("TAK_AI_BASE_URL", base)
    monkeypatch.setenv("TAK_AI_API_KEY", "sk-test-123")
    monkeypatch.delenv("TAK_AI_OFFLINE", raising=False)
    try:
        yield base
    finally:
        server.shutdown()
        server.server_close()


def _write_config(tmp_path, ai):
    import os
    with open(os.path.join(str(tmp_path), "config.json"), "w",
              encoding="utf-8") as fh:
        json.dump({"schema": 1, "ai": ai}, fh)


# --------------------------------------------------------------------------
# 设置解析
# --------------------------------------------------------------------------
def test_env_wins_over_config(fake_api, tmp_path):
    _write_config(tmp_path, {"base_url": "https://config.example/v1",
                             "model": "config-model"})
    settings = connection.resolve_settings()
    assert settings["base_url"] == fake_api
    assert settings["base_url_source"] == "env"
    assert settings["model"] == "config-model"   # 模型仍取配置
    assert settings["api_key_present"] is True


def test_config_base_url_used_when_env_missing(monkeypatch, tmp_path):
    monkeypatch.setenv("TAK_HOME", str(tmp_path))
    monkeypatch.delenv("TAK_AI_BASE_URL", raising=False)
    _write_config(tmp_path, {"base_url": "https://gateway.local/v1/"})
    settings = connection.resolve_settings()
    assert settings["base_url"] == "https://gateway.local/v1"   # 去掉尾斜杠
    assert settings["base_url_source"] == "config"


def test_missing_key_means_offline_not_an_exception(monkeypatch, tmp_path):
    monkeypatch.setenv("TAK_HOME", str(tmp_path))
    monkeypatch.delenv("TAK_AI_API_KEY", raising=False)
    payload = connection.describe()
    assert payload["ready"] is False
    assert payload["offline"] is True
    assert "TAK_AI_API_KEY" in payload["hint"]


def test_custom_key_env_name_is_honoured(monkeypatch, tmp_path):
    monkeypatch.setenv("TAK_HOME", str(tmp_path))
    monkeypatch.delenv("TAK_AI_API_KEY", raising=False)
    monkeypatch.setenv("MY_WORKBUDDY_KEY", "sk-custom")
    _write_config(tmp_path, {"api_key_env": "MY_WORKBUDDY_KEY"})
    settings = connection.resolve_settings()
    assert settings["api_key_env"] == "MY_WORKBUDDY_KEY"
    assert settings["api_key_present"] is True


def test_bad_base_url_is_rejected_at_load(monkeypatch, tmp_path):
    monkeypatch.setenv("TAK_HOME", str(tmp_path))
    _write_config(tmp_path, {"base_url": "gateway.local/v1"})   # 缺协议头
    from tak.core import config as tak_config
    data = tak_config.load()
    assert data["ai"]["base_url"] == ""
    assert any("ai.base_url" in item for item in data["_warnings"])


# --------------------------------------------------------------------------
# 探测
# --------------------------------------------------------------------------
def test_probe_succeeds_against_a_live_endpoint(fake_api):
    result = connection.probe(timeout=5)
    assert result["ok"] is True
    assert result["status"] == 200
    assert result["reply"] == "pong"
    assert result["latency_ms"] is not None
    call = _FakeOpenAI.seen[-1]
    assert call["path"].endswith("/chat/completions")
    assert call["auth"] == "Bearer sk-test-123"
    assert call["body"]["max_tokens"] == 8


def test_probe_reports_401_with_actionable_hint(fake_api):
    _FakeOpenAI.mode = "unauthorized"
    result = connection.probe(timeout=5)
    assert result["ok"] is False
    assert result["status"] == 401
    assert "密钥无效" in result["hint"]


def test_probe_reports_404_as_path_problem(fake_api):
    _FakeOpenAI.mode = "notfound"
    result = connection.probe(timeout=5)
    assert result["status"] == 404
    assert "/v1" in result["hint"]


def test_probe_survives_non_json_response(fake_api):
    _FakeOpenAI.mode = "garbled"
    result = connection.probe(timeout=5)
    assert result["ok"] is False
    assert "AIParseError" in result["error"]
    assert result["hint"]


def test_probe_without_key_never_touches_the_network(monkeypatch, tmp_path):
    monkeypatch.setenv("TAK_HOME", str(tmp_path))
    monkeypatch.delenv("TAK_AI_API_KEY", raising=False)
    _FakeOpenAI.seen = []
    result = connection.probe(timeout=2)
    assert result["ok"] is False
    assert _FakeOpenAI.seen == []


# --------------------------------------------------------------------------
# provider 选择
# --------------------------------------------------------------------------
def test_get_provider_uses_config_when_key_present(fake_api):
    provider = get_provider(offline=None)
    assert isinstance(provider, HTTPProvider)
    assert provider.client.base_url == fake_api


def test_get_provider_falls_back_to_mock_without_key(monkeypatch, tmp_path):
    monkeypatch.setenv("TAK_HOME", str(tmp_path))
    monkeypatch.delenv("TAK_AI_API_KEY", raising=False)
    assert isinstance(get_provider(offline=None), MockProvider)


def test_explicit_offline_still_wins(fake_api):
    assert isinstance(get_provider(offline=True), MockProvider)


def test_explicit_offline_env_still_wins(fake_api, monkeypatch):
    monkeypatch.setenv("TAK_AI_OFFLINE", "1")
    assert isinstance(get_provider(offline=None), MockProvider)


def test_online_is_the_default_when_a_key_exists(fake_api, monkeypatch):
    """配好密钥就该能连，不需要再去找一个没写在界面上的环境变量。"""
    monkeypatch.delenv("TAK_AI_OFFLINE", raising=False)
    assert isinstance(get_provider(offline=None), HTTPProvider)
