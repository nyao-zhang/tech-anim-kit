# -*- coding: utf-8 -*-
"""AI provider 全链路测试；所有 HTTP 响应均由 monkeypatch 构造。"""
import io
import json
import socket
import threading
import urllib.error
import urllib.request

import pytest

from tak.core.ai import (
    MODEL_DEEPSEEK,
    MODEL_KIMI,
    MODEL_MINIMAX,
    AIClientError,
    AINetworkError,
    AIParseError,
    AIServerError,
    AITimeoutError,
    BudgetTracker,
    HTTPProvider,
    MockProvider,
    OfflineProvider,
    OpenAIClient,
    get_provider,
    provider_for_task,
)


def _response(payload):
    if isinstance(payload, bytes):
        return io.BytesIO(payload)
    return io.BytesIO(json.dumps(payload).encode("utf-8"))


def _chat_response(text="ok"):
    return _response({"choices": [{"message": {"role": "assistant", "content": text}}]})


def _http_error(request, status):
    return urllib.error.HTTPError(
        request.full_url, status, "test error", {}, io.BytesIO(b'{"error":"test"}')
    )


def test_offline_full_chain_without_network(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("离线 provider 不得访问网络")

    monkeypatch.setattr(urllib.request, "urlopen", forbidden)
    provider = OfflineProvider()
    assert provider.chat([{"role": "user", "content": "解释"}]).startswith("[mock]")
    assert provider.vision(["image.png"], "检查布局") == "[mock] 1 张图，提示词 4 字符"
    assert provider.embed(["ab", "abcd"]) == [[2.0, 2.0, 1.0], [4.0, 4.0, 1.0]]
    assert provider.budget.snapshot()["calls"] == 3
    assert provider.budget.snapshot()["failures"] == 0


def test_offline_results_are_reproducible():
    first = OfflineProvider()
    second = OfflineProvider()
    messages = [{"role": "user", "content": "相同输入"}]
    assert first.chat(messages) == second.chat(messages)
    assert first.vision(["a", "b"], "prompt") == second.vision(["a", "b"], "prompt")
    assert first.embed(["甲", "乙", "甲"]) == second.embed(["甲", "乙", "甲"])


def test_task_routing_uses_three_model_tiers():
    assert provider_for_task("explain") == MODEL_DEEPSEEK
    assert provider_for_task("candidates") == MODEL_DEEPSEEK
    assert provider_for_task("tagging") == MODEL_DEEPSEEK
    assert provider_for_task("complex") == MODEL_KIMI
    assert provider_for_task("semantic_mapping") == MODEL_KIMI
    assert provider_for_task("multimodal") == MODEL_MINIMAX
    assert provider_for_task("unknown") == MODEL_DEEPSEEK


def test_get_provider_routes_task_and_allows_model_override(tmp_path, monkeypatch):
    # 隔离真实 ~/.tak：用户机器上配了 ai.model 与 credential.json 之后，
    # 面板配置会覆盖任务路由，这个测试的本意是「未显式配置时按任务分工」。
    monkeypatch.setenv("TAK_HOME", str(tmp_path))
    monkeypatch.setenv("TAK_AI_API_KEY", "test-only")
    monkeypatch.setenv("TAK_AI_BASE_URL", "https://example.invalid/v1")
    provider = get_provider(task="vision", offline=False)
    assert isinstance(provider, HTTPProvider)
    assert provider.model == MODEL_MINIMAX
    assert get_provider(task="complex", offline=False).model == MODEL_KIMI
    assert get_provider(task="vision", model="custom-model", offline=False).model == "custom-model"


def test_missing_configuration_and_explicit_offline_fall_back(tmp_path, monkeypatch):
    # 同上：不隔离的话本机 credential.json 里的真实 key 会让「缺配置」不成立。
    monkeypatch.setenv("TAK_HOME", str(tmp_path))
    monkeypatch.delenv("TAK_AI_API_KEY", raising=False)
    monkeypatch.delenv("TAK_AI_OFFLINE", raising=False)
    assert isinstance(get_provider(offline=False), MockProvider)
    monkeypatch.setenv("TAK_AI_API_KEY", "test-only")
    assert isinstance(get_provider(offline=True), MockProvider)
    monkeypatch.setenv("TAK_AI_OFFLINE", "1")
    assert isinstance(get_provider(), MockProvider)


def test_chat_uses_environment_and_per_call_model(monkeypatch):
    monkeypatch.setenv("TAK_AI_BASE_URL", "https://example.invalid/v1/")
    monkeypatch.setenv("TAK_AI_API_KEY", "test-only")
    captured = []

    def fake_urlopen(request, timeout):
        captured.append((request, timeout))
        return _chat_response("done")

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    client = OpenAIClient(model="default-model", timeout=7)
    assert client.chat([{"role": "user", "content": "hi"}], model="task-model",
                       temperature=0.2) == "done"
    request, timeout = captured[0]
    payload = json.loads(request.data.decode("utf-8"))
    assert request.full_url == "https://example.invalid/v1/chat/completions"
    assert request.get_header("Authorization") == "Bearer test-only"
    assert payload == {
        "model": "task-model",
        "messages": [{"role": "user", "content": "hi"}],
        "temperature": 0.2,
    }
    assert timeout == 7


def test_vision_builds_image_url_content_blocks(monkeypatch):
    captured = []

    def fake_urlopen(request, timeout):
        captured.append(json.loads(request.data.decode("utf-8")))
        return _chat_response("vision ok")

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    client = OpenAIClient(base_url="https://example.invalid/v1", api_key="test-only")
    assert client.vision(["https://example.invalid/a.png", "data:image/png;base64,AA"],
                         "review") == "vision ok"
    content = captured[0]["messages"][0]["content"]
    assert content[0] == {"type": "text", "text": "review"}
    assert content[1] == {
        "type": "image_url",
        "image_url": {"url": "https://example.invalid/a.png"},
    }
    assert content[2]["image_url"]["url"] == "data:image/png;base64,AA"


def test_embeddings_are_returned_in_input_order(monkeypatch):
    def fake_urlopen(request, timeout):
        assert request.full_url.endswith("/embeddings")
        return _response({"data": [
            {"index": 1, "embedding": [2, 3]},
            {"index": 0, "embedding": [1, 4]},
        ]})

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    client = OpenAIClient(base_url="https://example.invalid/v1", api_key="test-only")
    assert client.embed(["a", "b"]) == [[1.0, 4.0], [2.0, 3.0]]


def test_server_error_retries_with_exponential_backoff(monkeypatch):
    calls = []
    sleeps = []

    def fake_urlopen(request, timeout):
        calls.append(request)
        if len(calls) < 3:
            raise _http_error(request, 500)
        return _chat_response("recovered")

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr("tak.core.ai.http_client.time.sleep", sleeps.append)
    client = OpenAIClient(base_url="https://example.invalid/v1", api_key="test-only",
                          retry_backoff=0.25)
    assert client.chat([{"role": "user", "content": "retry"}]) == "recovered"
    assert len(calls) == 3
    assert sleeps == [0.25, 0.5]
    assert client.budget.calls == 1
    assert client.budget.failures == 0


def test_network_error_exhausts_three_retries_and_records_failure(monkeypatch):
    calls = []

    def fake_urlopen(request, timeout):
        calls.append(request)
        raise urllib.error.URLError("offline test")

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr("tak.core.ai.http_client.time.sleep", lambda delay: None)
    client = OpenAIClient(base_url="https://example.invalid/v1", api_key="test-only",
                          max_retries=3)
    with pytest.raises(AINetworkError) as caught:
        client.chat([])
    assert caught.value.category == "network"
    assert len(calls) == 4
    assert client.budget.calls == 1
    assert client.budget.failures == 1


def test_timeout_has_dedicated_error_category(monkeypatch):
    def fake_urlopen(request, timeout):
        raise socket.timeout("slow")

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    client = OpenAIClient(base_url="https://example.invalid/v1", api_key="test-only",
                          max_retries=0)
    with pytest.raises(AITimeoutError) as caught:
        client.embed(["text"])
    assert caught.value.category == "timeout"


def test_client_error_is_not_retried(monkeypatch):
    calls = []

    def fake_urlopen(request, timeout):
        calls.append(request)
        raise _http_error(request, 400)

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    client = OpenAIClient(base_url="https://example.invalid/v1", api_key="test-only")
    with pytest.raises(AIClientError) as caught:
        client.chat([])
    assert caught.value.category == "4xx"
    assert caught.value.status == 400
    assert len(calls) == 1


def test_server_error_after_retries_keeps_5xx_category(monkeypatch):
    def fake_urlopen(request, timeout):
        raise _http_error(request, 503)

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr("tak.core.ai.http_client.time.sleep", lambda delay: None)
    client = OpenAIClient(base_url="https://example.invalid/v1", api_key="test-only")
    with pytest.raises(AIServerError) as caught:
        client.chat([])
    assert caught.value.category == "5xx"
    assert caught.value.status == 503


@pytest.mark.parametrize("payload", [b"not-json", {"choices": []}])
def test_invalid_chat_response_is_parse_error(monkeypatch, payload):
    monkeypatch.setattr(urllib.request, "urlopen",
                        lambda request, timeout: _response(payload))
    client = OpenAIClient(base_url="https://example.invalid/v1", api_key="test-only")
    with pytest.raises(AIParseError) as caught:
        client.chat([])
    assert caught.value.category == "parse"
    assert client.budget.failures == 1


def test_budget_json_export_and_reset(tmp_path):
    budget = BudgetTracker()
    budget.record_call(tokens=12, elapsed_seconds=0.5)
    budget.record_call(tokens=8, elapsed_seconds=0.25, failed=True)
    assert budget.snapshot() == {
        "calls": 2, "tokens": 12 + 8, "elapsed_seconds": 0.75, "failures": 1,
    }
    output = tmp_path / "budget.json"
    text = budget.export_json(str(output))
    assert json.loads(text) == json.loads(output.read_text(encoding="utf-8"))
    assert json.loads(text)["failures"] == 1
    budget.reset()
    assert budget.snapshot() == {
        "calls": 0, "tokens": 0, "elapsed_seconds": 0.0, "failures": 0,
    }


def test_budget_is_thread_safe():
    budget = BudgetTracker()

    def record_many():
        for _ in range(50):
            budget.record_call(tokens=2, elapsed_seconds=0.01)

    threads = [threading.Thread(target=record_many) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert budget.calls == 400
    assert budget.tokens == 800
    assert budget.failures == 0
    assert budget.elapsed_seconds == pytest.approx(4.0)
