# -*- coding: utf-8 -*-
"""仅使用标准库的 OpenAI 兼容 HTTP 客户端。

网络、超时、HTTP 4xx/5xx 与响应解析错误分别分类；仅网络、超时和
5xx 会指数退避重试。默认最多重试 3 次（首次调用之外）。
"""
from __future__ import annotations

import json
import os
import socket
import time
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional

from .budget import BudgetTracker, estimate_tokens

DEFAULT_BASE_URL = "https://api.openai.com/v1"


class AIError(RuntimeError):
    """AI HTTP 调用错误基类。"""

    category = "error"


class AINetworkError(AIError):
    category = "network"


class AITimeoutError(AIError):
    category = "timeout"


class AIClientError(AIError):
    """HTTP 4xx，通常不应重试。"""

    category = "4xx"

    def __init__(self, message: str, status: int) -> None:
        super().__init__(message)
        self.status = status


class AIServerError(AIError):
    """HTTP 5xx，可安全重试由调用方决定。"""

    category = "5xx"

    def __init__(self, message: str, status: int) -> None:
        super().__init__(message)
        self.status = status


class AIParseError(AIError):
    category = "parse"


class OpenAIClient:
    """OpenAI 兼容 chat/completions 与 embeddings 客户端。"""

    def __init__(self, base_url: Optional[str] = None, api_key: Optional[str] = None,
                 model: str = "DeepSeek-V4.1-Flash", timeout: float = 30.0,
                 max_retries: int = 3, retry_backoff: float = 0.5,
                 budget: Optional[BudgetTracker] = None) -> None:
        self.base_url = (base_url or os.environ.get("TAK_AI_BASE_URL")
                         or DEFAULT_BASE_URL).rstrip("/")
        self.api_key = api_key or os.environ.get("TAK_AI_API_KEY") or ""
        self.model = model
        self.timeout = float(timeout)
        self.max_retries = max(0, int(max_retries))
        self.retry_backoff = max(0.0, float(retry_backoff))
        self.budget = budget or BudgetTracker()

    def _request_once(self, path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        try:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise AIParseError("请求 JSON 序列化失败: %s" % exc) from exc

        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.api_key:
            headers["Authorization"] = "Bearer %s" % self.api_key
        request = urllib.request.Request(
            self.base_url + path, data=body, headers=headers, method="POST"
        )
        response = None
        try:
            response = urllib.request.urlopen(request, timeout=self.timeout)
            raw = response.read()
        except urllib.error.HTTPError as exc:
            try:
                detail = exc.read().decode("utf-8", errors="replace")[:500]
            except Exception:
                detail = ""
            message = "HTTP %d: %s" % (exc.code, detail or exc.reason)
            if 400 <= exc.code < 500:
                raise AIClientError(message, exc.code) from exc
            if 500 <= exc.code < 600:
                raise AIServerError(message, exc.code) from exc
            raise AINetworkError(message) from exc
        except (socket.timeout, TimeoutError) as exc:
            raise AITimeoutError("AI 请求超时: %s" % exc) from exc
        except urllib.error.URLError as exc:
            if isinstance(exc.reason, (socket.timeout, TimeoutError)):
                raise AITimeoutError("AI 请求超时: %s" % exc.reason) from exc
            raise AINetworkError("AI 网络错误: %s" % exc.reason) from exc
        except OSError as exc:
            raise AINetworkError("AI 网络错误: %s" % exc) from exc
        finally:
            if response is not None:
                close = getattr(response, "close", None)
                if close is not None:
                    close()

        try:
            decoded = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise AIParseError("AI 响应不是有效 JSON: %s" % exc) from exc
        if not isinstance(decoded, dict):
            raise AIParseError("AI 响应顶层必须是 JSON 对象")
        return decoded

    def _post(self, path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        for attempt in range(self.max_retries + 1):
            try:
                return self._request_once(path, payload)
            except (AINetworkError, AITimeoutError, AIServerError):
                if attempt >= self.max_retries:
                    raise
                time.sleep(self.retry_backoff * (2 ** attempt))
        raise AssertionError("unreachable")

    def chat(self, messages: List[Dict[str, Any]], model: Optional[str] = None,
             **kwargs: Any) -> str:
        """调用 chat/completions；model 参数可覆盖当前任务的默认模型。"""
        payload = dict(kwargs)
        payload.update({"model": model or self.model, "messages": messages})
        started = time.monotonic()
        try:
            response = self._post("/chat/completions", payload)
            content = response["choices"][0]["message"]["content"]
            if not isinstance(content, str):
                raise TypeError("content 不是字符串")
        except Exception as exc:
            self.budget.record_call(estimate_tokens(payload),
                                    time.monotonic() - started, failed=True)
            if isinstance(exc, AIError):
                raise
            if isinstance(exc, (KeyError, IndexError, TypeError)):
                raise AIParseError("chat/completions 响应结构不完整: %s" % exc) from exc
            raise
        self.budget.record_call(estimate_tokens(payload) + estimate_tokens(content),
                                time.monotonic() - started)
        return content

    def chat_tools(self, messages: List[Dict[str, Any]],
                   tools: List[Dict[str, Any]], model: Optional[str] = None,
                   tool_choice: Any = "auto", **kwargs: Any) -> Dict[str, Any]:
        """带 function calling 的 chat/completions，返回**完整** assistant message。

        和 :meth:`chat` 的区别：这里要的是 ``message.tool_calls``，不是 content
        字符串。模型只被允许输出「动作名 + 参数 JSON」，参数是否合法仍由
        ``tak.core.scene_actions`` 的白名单校验说了算 —— 模型说的话绝不当代码跑。
        """
        payload = dict(kwargs)
        payload.update({"model": model or self.model, "messages": messages,
                        "tools": tools, "tool_choice": tool_choice})
        started = time.monotonic()
        try:
            response = self._post("/chat/completions", payload)
            message = response["choices"][0]["message"]
            if not isinstance(message, dict):
                raise TypeError("message 不是对象")
        except Exception as exc:
            self.budget.record_call(estimate_tokens(payload),
                                    time.monotonic() - started, failed=True)
            if isinstance(exc, AIError):
                raise
            if isinstance(exc, (KeyError, IndexError, TypeError)):
                raise AIParseError("chat/completions 响应结构不完整: %s" % exc) from exc
            raise
        self.budget.record_call(estimate_tokens(payload) + estimate_tokens(message),
                                time.monotonic() - started)
        return message

    def vision(self, images: List[str], prompt: str, model: Optional[str] = None,
               **kwargs: Any) -> str:
        """使用 OpenAI image_url 内容块发送图片 URL 或 data URI。"""
        content = [{"type": "text", "text": prompt}]
        content.extend({"type": "image_url", "image_url": {"url": image}}
                       for image in images)
        return self.chat([{"role": "user", "content": content}], model=model, **kwargs)

    def embed(self, texts: List[str], model: Optional[str] = None,
              **kwargs: Any) -> List[List[float]]:
        payload = dict(kwargs)
        payload.update({"model": model or self.model, "input": texts})
        started = time.monotonic()
        try:
            response = self._post("/embeddings", payload)
            data = response["data"]
            if not isinstance(data, list):
                raise TypeError("data 不是列表")
            data = sorted(data, key=lambda item: item.get("index", 0))
            embeddings = [[float(value) for value in item["embedding"]] for item in data]
        except Exception as exc:
            self.budget.record_call(estimate_tokens(payload),
                                    time.monotonic() - started, failed=True)
            if isinstance(exc, AIError):
                raise
            if isinstance(exc, (KeyError, IndexError, TypeError, ValueError)):
                raise AIParseError("embeddings 响应结构不完整: %s" % exc) from exc
            raise
        self.budget.record_call(estimate_tokens(payload) + estimate_tokens(embeddings),
                                time.monotonic() - started)
        return embeddings
