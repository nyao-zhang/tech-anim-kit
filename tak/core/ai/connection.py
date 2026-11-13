# -*- coding: utf-8 -*-
"""AI 连接解析与探测：把「对话控制台能不能直接连上 WorkBuddy」变成可验证的事。

三层优先级（后者只在前者缺失时生效）
------------------------------------
1. 环境变量 ``TAK_AI_BASE_URL`` / ``TAK_AI_API_KEY``：CI 与临时切换用，不改文件；
2. ``~/.tak/config.json`` 的 ``ai.base_url`` / ``ai.model`` / ``ai.api_key_env``：
   长期配置，面板里能直接改；
3. 默认值（``https://api.openai.com/v1`` + ``TAK_AI_API_KEY``）。

**密钥永不落配置文件**：配置里只有环境变量名。这样把 config.json 发给同事、
或者它被同步到网盘，都不会顺带泄露凭据。

``probe()`` 是一次真实但极小的请求（``max_tokens`` 很小），只用来回答
「地址对不对、钥匙对不对、模型名对不对」三个问题，不参与任何判定。
"""
from __future__ import annotations

import os
import time
from typing import Any, Dict, Optional

from tak.core import config as tak_config

DEFAULT_BASE_URL = "https://api.openai.com/v1"
DEFAULT_KEY_ENV = "TAK_AI_API_KEY"
DEFAULT_MODEL = "DeepSeek-V4.1-Flash"


def resolve_settings(cfg: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """解析当前生效的连接设置，并标注每一项的来源。"""
    data = cfg if isinstance(cfg, dict) else tak_config.load()
    ai = data.get("ai") if isinstance(data.get("ai"), dict) else {}

    env_url = os.environ.get("TAK_AI_BASE_URL", "").strip()
    cfg_url = str(ai.get("base_url") or "").strip()
    base_url = env_url or cfg_url or DEFAULT_BASE_URL
    url_source = "env" if env_url else ("config" if cfg_url else "default")

    key_env = str(ai.get("api_key_env") or "").strip() or DEFAULT_KEY_ENV
    api_key = os.environ.get(key_env, "").strip()
    if not api_key and key_env != DEFAULT_KEY_ENV:
        api_key = os.environ.get(DEFAULT_KEY_ENV, "").strip()

    # model 只在用户显式配过时才有值。这里不要回落到 DEFAULT_MODEL：那样
    # ``get_provider`` 里「配置优先」会一直命中，把 vision → MiniMax 这类
    # 任务路由整个顶掉（单测 test_ai_providers 就是抓这个的）。
    model = str(ai.get("model") or "").strip()

    return {
        "base_url": base_url.rstrip("/"),
        "base_url_source": url_source,
        "model": model,
        "api_key_env": key_env,
        "api_key_present": bool(api_key),
        "offline": not bool(api_key),
        "ready": bool(api_key) and bool(base_url),
        "config_path": tak_config.config_path(),
    }


def describe(cfg: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """给面板用的可读摘要：能连 / 不能连，为什么，改哪里。"""
    settings = resolve_settings(cfg)
    if settings["ready"]:
        hint = "已就绪：地址 %s，模型 %s" % (
            settings["base_url"], settings["model"] or "%s（按任务路由）" % DEFAULT_MODEL)
    else:
        hint = ("缺少密钥：请设置环境变量 %s（配置里只存变量名，不存密钥）"
                % settings["api_key_env"])
    payload = dict(settings)
    payload["hint"] = hint
    return payload


def probe(cfg: Optional[Dict[str, Any]] = None, timeout: float = 10.0,
          message: str = "ping") -> Dict[str, Any]:
    """发一次最小请求验证连通性。绝不抛异常，结果本身就是报告。"""
    settings = resolve_settings(cfg)
    result: Dict[str, Any] = {
        "ok": False,
        "base_url": settings["base_url"],
        "model": settings["model"],
        "api_key_env": settings["api_key_env"],
        "api_key_present": settings["api_key_present"],
        "latency_ms": None,
        "status": None,
        "reply": "",
        "error": "",
        "hint": "",
    }
    if not settings["api_key_present"]:
        result["error"] = "未找到环境变量 %s" % settings["api_key_env"]
        result["hint"] = ("在系统环境变量里设置 %s 后重启 DCC；"
                          "或先把地址改成能直连的网关。" % settings["api_key_env"])
        return result

    from .http_client import AIClientError, OpenAIClient

    from .providers import provider_for_task

    client = OpenAIClient(base_url=settings["base_url"],
                          api_key=os.environ.get(settings["api_key_env"], "").strip()
                          or os.environ.get(DEFAULT_KEY_ENV, "").strip(),
                          model=settings["model"] or provider_for_task("default"),
                          timeout=timeout,
                          max_retries=1, retry_backoff=0.0)
    started = time.time()
    try:
        reply = client.chat([{"role": "user", "content": message}],
                            max_tokens=8, temperature=0.0)
    except AIClientError as exc:
        result["latency_ms"] = round((time.time() - started) * 1000, 1)
        result["status"] = getattr(exc, "status", None)
        result["error"] = "%s: %s" % (type(exc).__name__, exc)
        result["hint"] = _error_hint(result["status"], str(exc))
        return result
    except Exception as exc:
        result["latency_ms"] = round((time.time() - started) * 1000, 1)
        result["error"] = "%s: %s" % (type(exc).__name__, exc)
        result["hint"] = "检查 base_url 是否是 OpenAI 兼容的 /v1 端点，以及网络是否可达。"
        return result

    result["ok"] = True
    result["latency_ms"] = round((time.time() - started) * 1000, 1)
    result["status"] = 200
    result["reply"] = str(reply or "")[:80]
    result["hint"] = "连接正常。对话控制台的解释与候选会走这个端点。"
    return result


def _error_hint(status: Optional[int], message: str) -> str:
    if status == 401:
        return "密钥无效或已过期：换一个 %s 的取值。" % DEFAULT_KEY_ENV
    if status == 404:
        return ("地址对了但没有这个路径：base_url 需要指向 /v1，"
                "模型名也要确认在服务端存在。")
    if status == 429:
        return "被限流或额度用尽：稍后重试，或换模型。"
    if status and status >= 500:
        return "服务端错误：多半是网关问题，检查 base_url 是否指向了正确的环境。"
    if "timed out" in message.lower() or "timeout" in message.lower():
        return "请求超时：确认网络可达，必要时把 base_url 换成本地/内网代理。"
    return "先确认 base_url 与模型名；仍失败时用 curl 手工验证一次同一端点。"
