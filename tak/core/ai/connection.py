# -*- coding: utf-8 -*-
"""AI 连接解析与探测：把「对话控制台能不能直接连上 AI」变成可验证的事。

四层优先级（后者只在前者缺失时生效）
------------------------------------
1. 环境变量 ``TAK_AI_BASE_URL`` / ``TAK_AI_API_KEY``（或配置里指定的变量名）：
   CI 与临时切换用，不改文件；
2. ``~/.tak/config.json`` 的 ``ai.base_url`` / ``ai.model`` / ``ai.api_key_env``：
   长期配置，面板里能直接改；
3. ``~/.tak/credential.json``：面板里填的密钥（见 :mod:`tak.core.ai.credential`）；
4. 默认值（``https://api.deepseek.com`` + ``TAK_AI_API_KEY``）。

**密钥不写 config.json**：那个文件是可以发给同事的。密钥要么在环境变量里，
要么在上面的 credential.json 里（权限收到当前用户）。

``probe()`` 是一次真实但极小的请求（``max_tokens=8``），只回答
「地址对不对、钥匙对不对、模型名对不对」三个问题，不参与任何判定。
模型名不确定时它会去问端点 ``GET /models`` —— 用户不该被迫背模型名。
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

from tak.core import config as tak_config

from . import credential

#: DeepSeek 官方端点。**不写** ``/v1``：官方两者都接受，但 ``/v1`` 会让人
#: 以为那是「版本号」，进而把模型名也写成 v1 风格。
DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_KEY_ENV = "TAK_AI_API_KEY"
DEFAULT_MODEL = "DeepSeek-V4.1-Flash"

#: 供应商预设。UI 下拉选一个就把地址、模型、密钥变量名、申请地址一起填好。
#:
#: 模型名是**初值不是真理** —— 各家改名很快（DeepSeek 的 deepseek-chat /
#: deepseek-reasoner 就在 2026-07-24 弃用了）。填错时 probe 会去拉 /models
#: 把真实可用的名字列出来，所以这里只要「大概率对」就够。
PRESETS: Tuple[Dict[str, str], ...] = (
    {
        "id": "deepseek",
        "name": "DeepSeek 官方（推荐，最便宜）",
        "base_url": "https://api.deepseek.com",
        "model": "deepseek-v4-flash",
        "key_env": "DEEPSEEK_API_KEY",
        "key_url": "https://platform.deepseek.com/api_keys",
        "note": "deepseek-v4-flash 对应 V4.1-Flash；复杂推理换 deepseek-v4-pro。"
                "旧的 deepseek-chat / deepseek-reasoner 已于 2026-07-24 弃用。",
    },
    {
        "id": "moonshot",
        "name": "月之暗面 Kimi",
        "base_url": "https://api.moonshot.cn/v1",
        "model": "kimi-k2-turbo-preview",
        "key_env": "MOONSHOT_API_KEY",
        "key_url": "https://platform.moonshot.cn/console/api-keys",
        "note": "模型名变动频繁；填错时点「测试连接」会自动列出可用模型。",
    },
    {
        "id": "dashscope",
        "name": "阿里云百炼（通义千问）",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "model": "qwen-plus",
        "key_env": "DASHSCOPE_API_KEY",
        "key_url": "https://bailian.console.aliyun.com/?apiKey=1",
        "note": "兼容模式端点，模型名如 qwen-plus / qwen-max / qwen-vl-max。",
    },
    {
        "id": "zhipu",
        "name": "智谱 GLM",
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "model": "glm-4-flash",
        "key_env": "ZHIPUAI_API_KEY",
        "key_url": "https://bigmodel.cn/usercenter/apikeys",
        "note": "glm-4-flash 有免费额度，适合先跑通再换 glm-4-plus。",
    },
    {
        "id": "siliconflow",
        "name": "硅基流动（多模型聚合）",
        "base_url": "https://api.siliconflow.cn/v1",
        "model": "deepseek-ai/DeepSeek-V3",
        "key_env": "SILICONFLOW_API_KEY",
        "key_url": "https://cloud.siliconflow.cn/account/ak",
        "note": "一个 key 能调多家模型，模型名带厂商前缀。",
    },
    {
        "id": "openai",
        "name": "OpenAI 官方",
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-4.1-mini",
        "key_env": "OPENAI_API_KEY",
        "key_url": "https://platform.openai.com/api-keys",
        "note": "国内直连通常不通，需要自备代理并把 base_url 指向代理地址。",
    },
    {
        "id": "ollama",
        "name": "本地 Ollama（零成本、数据不出机）",
        "base_url": "http://127.0.0.1:11434/v1",
        "model": "qwen2.5:7b-instruct",
        "key_env": "OLLAMA_API_KEY",
        "key_url": "https://ollama.com/download",
        "note": "密钥随便填（如 ollama）。先 ollama pull qwen2.5:7b-instruct；"
                "显存不足换 3b，效果会明显下降。",
    },
    {
        "id": "custom",
        "name": "自定义 / 自建网关",
        "base_url": "",
        "model": "",
        "key_env": DEFAULT_KEY_ENV,
        "key_url": "",
        "note": "任何 OpenAI 兼容端点都行，base_url 一般以 /v1 结尾。",
    },
)

#: 常见密钥环境变量名 —— 向导会按这个顺序找，找到就提示「已发现」。
KNOWN_KEY_ENVS: Tuple[str, ...] = (
    "TAK_AI_API_KEY", "DEEPSEEK_API_KEY", "MOONSHOT_API_KEY",
    "DASHSCOPE_API_KEY", "ZHIPUAI_API_KEY", "SILICONFLOW_API_KEY",
    "OPENAI_API_KEY", "MINIMAX_API_KEY",
)


def preset(preset_id: str) -> Dict[str, str]:
    for item in PRESETS:
        if item["id"] == preset_id:
            return dict(item)
    return dict(PRESETS[-1])


def detect_provider(base_url: str) -> str:
    """按地址猜供应商 id，用于回填下拉框。"""
    text = str(base_url or "").strip().lower().rstrip("/")
    if not text:
        return ""
    for item in PRESETS:
        host = item["base_url"].lower().rstrip("/")
        if host and (text == host or text.startswith(host)):
            return item["id"]
    return "custom"


def detected_env_keys() -> List[Dict[str, str]]:
    """扫一遍已知密钥变量名，返回「已经配好的」。值只给尾部四位。"""
    found: List[Dict[str, str]] = []
    for name in KNOWN_KEY_ENVS:
        value = os.environ.get(name, "").strip()
        if value:
            found.append({"name": name,
                          "tail": "…" + value[-4:] if len(value) > 4 else "…"})
    return found


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
    key_source = "env" if api_key else "none"
    key_env_used = key_env if api_key else ""
    if not api_key and key_env != DEFAULT_KEY_ENV:
        api_key = os.environ.get(DEFAULT_KEY_ENV, "").strip()
        if api_key:
            key_source, key_env_used = "env", DEFAULT_KEY_ENV
    if not api_key:
        # 面板里填的密钥（用户在界面里明确保存过，比「碰巧存在的变量」更明确）。
        stored = credential.api_key()
        if stored:
            api_key, key_source = stored, "file"
    if not api_key:
        # 兜底：用户在别处早就配过 DEEPSEEK_API_KEY / OPENAI_API_KEY 之类，
        # 面板不该还显示「没有密钥」。找到就用，并把真实变量名报出去 ——
        # 否则用户会以为自己配的那个名字被忽略了。
        for name in KNOWN_KEY_ENVS:
            value = os.environ.get(name, "").strip()
            if value:
                api_key, key_source, key_env_used = value, "env", name
                break

    # model 只在用户显式配过时才有值。这里不要回落到 DEFAULT_MODEL：那样
    # ``get_provider`` 里「配置优先」会一直命中，把 vision → MiniMax 这类
    # 任务路由整个顶掉（单测 test_ai_providers 就是抓这个的）。
    model = str(ai.get("model") or "").strip()

    return {
        "base_url": base_url.rstrip("/"),
        "base_url_source": url_source,
        "model": model,
        "api_key": api_key,
        "api_key_env": key_env,
        "api_key_env_used": key_env_used,
        "api_key_present": bool(api_key),
        "api_key_source": key_source,
        "offline": not bool(api_key),
        "ready": bool(api_key) and bool(base_url),
        "config_path": tak_config.config_path(),
        "credential_path": credential.credential_path(),
    }


def describe(cfg: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """给面板用的可读摘要：能连 / 不能连，为什么，改哪里。"""
    settings = resolve_settings(cfg)
    if settings["ready"]:
        where = {"env": "环境变量", "file": "本机凭据文件"}.get(
            settings["api_key_source"], settings["api_key_source"])
        hint = "已就绪：%s → %s（密钥来自%s）" % (
            settings["base_url"],
            settings["model"] or "按任务路由选模型", where)
    else:
        hint = ("没有可用密钥：可在「AI 连接」里填入并保存到本机凭据文件，"
                "或设置环境变量 %s" % settings["api_key_env"])
    payload = dict(settings)
    payload["hint"] = hint
    # 别把密钥带出去 —— describe() 的结果会被写进日志和 JSON。
    payload.pop("api_key", None)
    return payload


def list_models(base_url: Optional[str] = None, api_key: Optional[str] = None,
                timeout: float = 8.0) -> Dict[str, Any]:
    """向端点问 ``GET /models``，拿到真实可用的模型名。

    这是「别让用户背模型名」的关键：各家改名很快，把猜测写进预设一定会过时。
    失败不抛异常，返回 ``{"ok": False, ...}``，调用方据此决定要不要提示。
    """
    settings = resolve_settings()
    base = (base_url or settings["base_url"] or "").rstrip("/")
    key = api_key if api_key is not None else settings["api_key"]
    result: Dict[str, Any] = {"ok": False, "base_url": base, "models": [],
                              "status": None, "error": ""}
    if not base:
        result["error"] = "没有 base_url"
        return result
    request = urllib.request.Request(base + "/models", method="GET")
    if key:
        request.add_header("Authorization", "Bearer " + key)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8", "replace")
            result["status"] = int(getattr(response, "status", 200) or 200)
    except urllib.error.HTTPError as exc:
        result["status"] = int(exc.code)
        result["error"] = "HTTP %s" % exc.code
        return result
    except Exception as exc:
        result["error"] = "%s: %s" % (type(exc).__name__, exc)
        return result

    try:
        payload = json.loads(raw)
    except Exception:
        result["error"] = "响应不是 JSON"
        return result
    items = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(items, list):
        items = payload if isinstance(payload, list) else []
    models: List[str] = []
    for item in items:
        if isinstance(item, dict):
            name = str(item.get("id") or item.get("name") or "").strip()
        else:
            name = str(item or "").strip()
        if name:
            models.append(name)
    result["models"] = models
    result["ok"] = True
    return result


def probe(cfg: Optional[Dict[str, Any]] = None, timeout: float = 10.0,
          message: str = "ping",
          overrides: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """发一次最小请求验证连通性。绝不抛异常，结果本身就是报告。

    ``overrides`` 用于「还没保存就想先测一下」：面板里改了地址或密钥，不必先
    写配置再测。键：``base_url`` / ``model`` / ``api_key``。
    """
    settings = resolve_settings(cfg)
    pending = overrides if isinstance(overrides, dict) else {}

    base_url = str(pending.get("base_url") or settings["base_url"]).strip().rstrip("/")
    api_key = str(pending.get("api_key") or settings["api_key"]).strip()
    pending_model = pending.get("model")
    model = str(pending_model if pending_model is not None
                else settings["model"]).strip()
    if model and pending_model is not None and str(pending_model).strip():
        model_source = "override"
    else:
        model_source = "config" if model else ""

    result: Dict[str, Any] = {
        "ok": False,
        "base_url": base_url,
        "base_url_source": ("override" if pending.get("base_url")
                            else settings["base_url_source"]),
        "model": model,
        "model_source": model_source,
        "api_key_env": settings["api_key_env"],
        "api_key_present": bool(api_key),
        "api_key_source": ("override" if pending.get("api_key")
                           else settings["api_key_source"]),
        "latency_ms": None,
        "status": None,
        "reply": "",
        "error": "",
        "hint": "",
        "available_models": [],
    }
    if not api_key:
        result["error"] = "没有可用密钥（环境变量 %s 与本机凭据文件都没有）" % (
            settings["api_key_env"],)
        result["hint"] = ("在「AI 连接」里粘贴密钥并保存到本机凭据文件，"
                          "或设置环境变量 %s 后重启 DCC。" % settings["api_key_env"])
        return result

    # 模型没配就先问端点要一个 —— 直接拿一个可能不存在的默认名去请求，
    # 用户只会看到一条看不懂的 404。
    if not model:
        listing = list_models(base_url=base_url, api_key=api_key,
                             timeout=min(timeout, 8.0))
        if listing.get("ok") and listing.get("models"):
            model = listing["models"][0]
            model_source = "endpoint"

    from .http_client import AIClientError, OpenAIClient
    from .providers import provider_for_task

    if not model:
        model = provider_for_task("default")
        model_source = "routing"
    result["model"] = model
    result["model_source"] = model_source

    client = OpenAIClient(base_url=base_url, api_key=api_key, model=model,
                          timeout=timeout, max_retries=1, retry_backoff=0.0)
    started = time.time()
    try:
        reply = client.chat([{"role": "user", "content": message}],
                            max_tokens=8, temperature=0.0)
    except AIClientError as exc:
        result["latency_ms"] = round((time.time() - started) * 1000, 1)
        result["status"] = getattr(exc, "status", None)
        result["error"] = "%s: %s" % (type(exc).__name__, exc)
        if result["status"] == 404:
            # 404 十次里有七次是模型名过时。把端点真实提供的名字列出来。
            listing = list_models(base_url=base_url, api_key=api_key,
                                  timeout=min(timeout, 8.0))
            if listing.get("ok"):
                result["available_models"] = listing["models"][:40]
        result["hint"] = _error_hint(result["status"], str(exc),
                                     result["available_models"])
        return result
    except Exception as exc:
        result["latency_ms"] = round((time.time() - started) * 1000, 1)
        result["error"] = "%s: %s" % (type(exc).__name__, exc)
        result["hint"] = "检查 base_url 是否是 OpenAI 兼容端点，以及网络是否可达。"
        return result

    result["ok"] = True
    result["latency_ms"] = round((time.time() - started) * 1000, 1)
    result["status"] = 200
    result["reply"] = str(reply or "")[:80]
    result["hint"] = "连接正常。对话控制台的解释与候选会走这个端点（模型 %s）。" % model
    return result


def _error_hint(status: Optional[int], message: str,
                available: Optional[List[str]] = None) -> str:
    if status == 401:
        return "密钥无效或已过期：重新粘贴一次，注意别带空格和换行。"
    if status == 402:
        return "余额不足：去平台充值，或换一个有免费额度的模型。"
    if status == 404:
        if available:
            return ("模型名不存在。这个端点可用的是：%s。"
                    "地址也要确认指向正确的版本路径（多为 /v1）。"
                    % "、".join(available[:8]))
        return ("地址对了但路径或模型名不对：base_url 一般要指向 /v1，"
                "模型名也可能已改名。点一次「拉取模型列表」看真实可用的名字。")
    if status == 429:
        return "被限流或额度用尽：稍后重试，或换模型 / 换 key。"
    if status and status >= 500:
        return "服务端错误：多半是网关问题，确认 base_url 指向的是正确环境。"
    lowered = message.lower()
    if "timed out" in lowered or "timeout" in lowered:
        return "请求超时：确认网络可达；国内直连 OpenAI 通常需要代理。"
    if "getaddrinfo" in lowered or "name or service not known" in lowered:
        return "域名解析失败：检查 base_url 是否拼错、是否需要代理。"
    if "connection refused" in lowered:
        return "连接被拒绝：本地端点（如 Ollama）没启动，或端口写错。"
    return "先确认 base_url 与模型名；仍失败时用 curl 手工请求一次同一端点对照。"


def apply_preset(preset_id: str, save: bool = True) -> Dict[str, Any]:
    """把某个预设写进 ``ai.base_url`` / ``ai.model`` / ``ai.api_key_env``。

    只写地址类信息，**不写密钥** —— 密钥走 credential.json 或环境变量。
    """
    item = preset(preset_id)
    data = tak_config.load()
    ai = data.get("ai") if isinstance(data.get("ai"), dict) else {}
    ai["base_url"] = item["base_url"]
    ai["model"] = item["model"]
    ai["api_key_env"] = item["key_env"] or DEFAULT_KEY_ENV
    data["ai"] = ai
    if save:
        tak_config.save(data)
    return {"ok": True, "preset": item, "saved": bool(save)}


def save_endpoint(base_url: str, model: str = "",
                  api_key_env: str = "", save: bool = True) -> Dict[str, Any]:
    """保存地址与模型（不碰密钥）。空 base_url 表示回到默认值。"""
    data = tak_config.load()
    ai = data.get("ai") if isinstance(data.get("ai"), dict) else {}
    ai["base_url"] = str(base_url or "").strip()
    ai["model"] = str(model or "").strip()
    if api_key_env:
        ai["api_key_env"] = str(api_key_env).strip()
    data["ai"] = ai
    if save:
        tak_config.save(data)
    return {"ok": True, "ai": dict(ai), "saved": bool(save)}
