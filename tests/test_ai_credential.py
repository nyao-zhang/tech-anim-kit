# -*- coding: utf-8 -*-
"""凭据文件与 AI 连接向导的回归测试。

这一组盯的是「用户能不能真的用上」：密钥存哪、谁优先、面板里没保存能不能先测、
模型名错了会不会给出可用的名字。起真服务来测，因为这一层的价值全在真实往返。
"""
from __future__ import annotations

import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from tak.core.ai import connection, credential


# --------------------------------------------------------------------------
# 凭据文件
# --------------------------------------------------------------------------
@pytest.fixture()
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("TAK_HOME", str(tmp_path))
    for name in connection.KNOWN_KEY_ENVS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv("TAK_AI_BASE_URL", raising=False)
    return tmp_path


def test_save_then_read_roundtrip(home):
    assert credential.describe()["present"] is False
    result = credential.save("sk-abcdef123456", provider="deepseek")
    assert result["ok"] is True
    assert result["tail"] == "…3456"
    assert credential.api_key() == "sk-abcdef123456"
    # 文件里必须是明文 key（这是它存在的前提），但 describe() 不能带出来
    with open(credential.credential_path(), "r", encoding="utf-8") as fh:
        raw = json.load(fh)
    assert raw["api_key"] == "sk-abcdef123456"
    assert "api_key" not in credential.describe()


def test_key_never_lands_in_config_json(home):
    """config.json 是可以发给同事的，里面绝不能有密钥。"""
    credential.save("sk-should-not-leak-9999")
    from tak.core import config as tak_config
    tak_config.save(tak_config.load())
    with open(tak_config.config_path(), "r", encoding="utf-8") as fh:
        text = fh.read()
    assert "sk-should-not-leak" not in text


def test_env_beats_file(home, monkeypatch):
    credential.save("sk-from-file-1111")
    monkeypatch.setenv("TAK_AI_API_KEY", "sk-from-env-2222")
    settings = connection.resolve_settings()
    assert settings["api_key_source"] == "env"
    assert settings["api_key"] == "sk-from-env-2222"


def test_file_used_when_no_env(home):
    credential.save("sk-from-file-3333")
    settings = connection.resolve_settings()
    assert settings["api_key_source"] == "file"
    assert settings["api_key"] == "sk-from-file-3333"
    assert settings["ready"] is True


def test_known_env_is_picked_up_as_fallback(home, monkeypatch):
    """用户在别处早就配了 DEEPSEEK_API_KEY，面板不该还显示「没有密钥」。"""
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-ds-4444")
    settings = connection.resolve_settings()
    assert settings["api_key_source"] == "env"
    assert settings["api_key_env_used"] == "DEEPSEEK_API_KEY"


def test_clear_is_idempotent(home):
    credential.save("sk-x-5555")
    assert credential.clear()["removed"] is True
    assert credential.clear()["ok"] is True
    assert credential.describe()["present"] is False


def test_broken_file_is_treated_as_no_credential(home):
    os.makedirs(os.path.dirname(credential.credential_path()), exist_ok=True)
    with open(credential.credential_path(), "w", encoding="utf-8") as fh:
        fh.write("{ not json")
    assert credential.api_key() == ""
    assert connection.resolve_settings()["ready"] is False


def test_newline_in_key_is_rejected(home):
    """从终端复制多带整段是很常见的失误，写进去只会得到一个看不懂的 401。"""
    result = credential.save("sk-abc\ndef")
    assert result["ok"] is False
    assert "换行" in result["error"]


def test_short_key_is_saved_but_flagged(home):
    result = credential.save("ollama")
    assert result["ok"] is True
    assert result["short"] is True


def test_describe_reports_where_the_key_came_from(home):
    credential.save("sk-trace-6666", provider="deepseek")
    payload = connection.describe()
    assert "本机凭据文件" in payload["hint"]
    assert payload["credential_path"].endswith("credential.json")


# --------------------------------------------------------------------------
# 预设
# --------------------------------------------------------------------------
def test_every_preset_is_complete_and_unique():
    ids = [item["id"] for item in connection.PRESETS]
    assert len(ids) == len(set(ids))
    for item in connection.PRESETS:
        for field in ("id", "name", "base_url", "model", "key_env"):
            assert field in item, (item.get("id"), field)
        assert item["name"].strip()
    # 自定义项必须是最后一项：preset() 找不到时回落到它
    assert connection.PRESETS[-1]["id"] == "custom"


def test_presets_do_not_advertise_retired_deepseek_models():
    """deepseek-chat / deepseek-reasoner 已于 2026-07-24 弃用，预设不能再给。"""
    deepseek = connection.preset("deepseek")
    assert deepseek["model"] not in ("deepseek-chat", "deepseek-reasoner")
    assert "v4" in deepseek["model"]


def test_detect_provider_maps_url_back_to_preset():
    assert connection.detect_provider("https://api.deepseek.com") == "deepseek"
    assert connection.detect_provider("https://api.deepseek.com/v1") == "deepseek"
    assert connection.detect_provider("http://127.0.0.1:11434/v1") == "ollama"
    assert connection.detect_provider("https://my-gateway.internal/v1") == "custom"
    assert connection.detect_provider("") == ""


def test_apply_preset_writes_endpoint_but_not_a_key(home):
    connection.apply_preset("deepseek")
    from tak.core import config as tak_config
    ai = tak_config.load()["ai"]
    assert ai["base_url"] == "https://api.deepseek.com"
    assert ai["model"] == "deepseek-v4-flash"
    assert "api_key" not in ai           # 只有变量名，没有密钥
    assert credential.describe()["present"] is False


# --------------------------------------------------------------------------
# 探测（真起一个 OpenAI 兼容服务）
# --------------------------------------------------------------------------
class _Server(BaseHTTPRequestHandler):
    chat_mode = "ok"          # ok | notfound
    models = ["real-model-a", "real-model-b"]
    models_path = "/v1/models"
    seen = []

    def log_message(self, fmt, *args):  # noqa: N802
        return

    def do_GET(self):  # noqa: N802
        _Server.seen.append({"method": "GET", "path": self.path})
        if self.path.rstrip("/") != _Server.models_path:
            self._send(404, {"error": "no route"})
            return
        self._send(200, {"data": [{"id": name} for name in _Server.models]})

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length).decode("utf-8"))
        _Server.seen.append({"method": "POST", "path": self.path,
                             "body": body,
                             "auth": self.headers.get("Authorization")})
        if _Server.chat_mode == "notfound":
            self._send(404, {"error": "model not found"})
            return
        self._send(200, {"choices": [{"message": {"content": "pong"}}]})

    def _send(self, status, payload):
        raw = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


@pytest.fixture()
def server(home):
    _Server.chat_mode = "ok"
    _Server.seen = []
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Server)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = "http://127.0.0.1:%d/v1" % httpd.server_address[1]
    try:
        yield base
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_probe_overrides_work_before_saving_anything(server, home):
    """面板里改了地址和密钥，不点保存也能先测 —— 这是向导的核心。"""
    result = connection.probe(timeout=5, overrides={
        "base_url": server, "model": "real-model-a", "api_key": "sk-temp-7777"})
    assert result["ok"] is True
    assert result["api_key_source"] == "override"
    assert result["model"] == "real-model-a"
    call = _Server.seen[-1]
    assert call["auth"] == "Bearer sk-temp-7777"


def test_probe_without_model_asks_the_endpoint_first(server, home):
    result = connection.probe(timeout=5, overrides={
        "base_url": server, "model": "", "api_key": "sk-8888"})
    assert result["ok"] is True
    assert result["model"] == "real-model-a"
    assert result["model_source"] == "endpoint"
    methods = [item["method"] for item in _Server.seen]
    assert methods[0] == "GET"        # 先问 /models，再发对话


def test_probe_404_lists_the_models_that_do_exist(server, home):
    _Server.chat_mode = "notfound"
    result = connection.probe(timeout=5, overrides={
        "base_url": server, "model": "old-retired-model", "api_key": "sk-9999"})
    assert result["ok"] is False
    assert result["status"] == 404
    assert "real-model-a" in result["available_models"]
    assert "real-model-a" in result["hint"]


def test_list_models_reports_http_errors_without_raising(home, server):
    payload = connection.list_models(base_url=server + "/nope", api_key="sk")
    assert payload["ok"] is False
    assert payload["status"] == 404


def test_probe_without_any_key_never_touches_the_network(server, home):
    _Server.seen = []
    result = connection.probe(timeout=3, overrides={"base_url": server})
    assert result["ok"] is False
    assert _Server.seen == []
    assert "本机凭据文件" in result["hint"]


def test_saved_key_is_used_by_probe_and_provider(server, home):
    connection.save_endpoint(server, "real-model-a")
    credential.save("sk-saved-0000")
    assert connection.probe(timeout=5)["ok"] is True
    from tak.core.ai.providers import HTTPProvider, get_provider
    provider = get_provider(offline=None)
    assert isinstance(provider, HTTPProvider)
    assert provider.client.api_key == "sk-saved-0000"
