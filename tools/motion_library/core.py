# -*- coding: utf-8 -*-
"""动作库语义检索插件编排层（纯数据、零 DCC / 零 UI / 不写场景）。

职责
----
1. ``info()``        插件自描述；
2. ``search(...)``   薄封装，返回严格可 JSON 序列化的 dict，供 Web API
                     与 Agent Console 直接调用；
3. ``context_for``   生成带 clip_id / 名称 / 时长 / 类型、长度受限的上下文；
4. ``similar_to``    按特征余弦相似度找相近动作；
5. ``run_report``    把每个命中转成一条 INFO ``Finding``，metadata 写入
                     results / stats / safety / query / warning。

query 取值优先级与 ``tools/rig_kb/core.py`` 一致：
``ctx.options["query"]`` → ``ctx.snapshot["metadata"]["query"]`` → 缺省空串
（空串表示「列出全部动作」）。本插件**不注册任何 @rule**：排序与过滤全部由
``tak.core.motion`` 的确定性代码给出，AI 只做语义重排候选。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from tak.core.motion import MotionLibrary
from tak.core.motion.library import MAX_TOP_K

TOOL_ID = "motion_library"
TITLE = "动作库语义检索"
VERSION = "0.1.0"
DCC_LIST = ["maya", "max", "mobu", "unreal"]
MIN_VERSION = {"maya": "2023", "max": "2023", "mobu": "2023", "unreal": "5.8"}
DEFAULT_QUERY = ""
DEFAULT_TOP_K = 5
DEFAULT_CONTEXT_CHARS = 800


# --------------------------------------------------------------------------- #
# 基础封装
# --------------------------------------------------------------------------- #
def info() -> Dict[str, Any]:
    """返回插件自描述，供工具面板与菜单使用。"""
    return {
        "id": TOOL_ID,
        "name": TITLE,
        "version": VERSION,
        "dcc": list(DCC_LIST),
        "min_version": dict(MIN_VERSION),
        "capabilities": ["motion.search", "motion.context", "motion.similar",
                         "report.render"],
        "permissions": ["scene.read"],
        "description": ("本地离线动作库检索：BM25 文本分 + 动作类型加权 + "
                        "数值过滤 + 特征相似度；只读、不解析 FBX 二进制、"
                        "不联网、不写场景，结果完全可复现。"),
        "offline": True,
        "read_only": True,
        "search_api": "search(query, top_k=5, options=None) -> dict",
        "context_api": "context_for(query, top_k=3, max_chars=800) -> str",
        "similar_api": "similar_to(clip_id, top_k=5) -> list",
        "default_query": DEFAULT_QUERY,
        "default_query_note": "空 query 表示按名称列出全部动作",
    }


def _library() -> MotionLibrary:
    """每次新建实例：不缓存文件、不共享可变状态、任何环境都能跑。"""
    return MotionLibrary()


def _as_options(options: Any) -> Dict[str, Any]:
    return dict(options) if isinstance(options, dict) else {}


def _top_k(value: Any, default: int = DEFAULT_TOP_K) -> int:
    """与 ``MotionLibrary`` 一致的 top_k 归一：0..MAX_TOP_K。"""
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default
    return min(max(number, 0), MAX_TOP_K)


def _search_options(options: Dict[str, Any]) -> Dict[str, Any]:
    """支持 ``options["motion"]`` 嵌套写法，也兼容顶层 filters / motion_type。"""
    nested = options.get("motion")
    if isinstance(nested, dict):
        return dict(nested)
    picked: Dict[str, Any] = {}
    for key in ("filters", "motion_type"):
        if key in options:
            picked[key] = options[key]
    return picked


def _warning(query: str, hits: List[Dict[str, Any]],
             clip_count: int) -> str:
    if clip_count == 0:
        return "动作库为空，请先导入 motion_index.json 或传入 clips"
    if hits:
        return ""
    if query:
        return ("未命中任何动作（可换用 走路 / 奔跑 / 攻击 / 循环 等关键词，"
                "或清空 query 列出全部）")
    return "当前过滤条件排除了全部动作，请放宽 filters"


# --------------------------------------------------------------------------- #
# 对外 API
# --------------------------------------------------------------------------- #
def search(query: Any, top_k: int = DEFAULT_TOP_K,
           options: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """检索薄封装，返回可 JSON 序列化的 dict。

    返回 ``{"ok", "tool_id", "query", "top_k", "count", "results", "clips",
    "stats", "offline", "warning"}``；空 query 返回全部动作（按 clip_id 升序），
    空库 / 无命中返回空列表，绝不抛异常。
    """
    library = _library()
    opts = _as_options(options)
    hits = library.search(query, top_k=top_k, options=_search_options(opts))
    raw = str(query or "")
    return {
        "ok": True,
        "tool_id": TOOL_ID,
        "query": raw,
        "top_k": _top_k(top_k),
        "count": len(hits),
        "results": hits,
        "clips": [clip.to_dict() for clip in library.clips],
        "stats": library.describe(),
        "offline": True,
        "warning": _warning(raw.strip(), hits, len(library.clips)),
    }


def context_for(query: Any, top_k: int = 3,
                max_chars: int = DEFAULT_CONTEXT_CHARS) -> str:
    """生成带 clip_id / 名称 / 时长 / 类型、长度硬截断的上下文。"""
    return _library().context_for(query, top_k=top_k, max_chars=max_chars)


def similar_to(clip_id: Any, top_k: int = DEFAULT_TOP_K) -> List[Dict[str, Any]]:
    """按特征余弦相似度返回相近动作（排除自身，排序确定）。"""
    return _library().similar_to(clip_id, top_k=top_k)


def _query_from_ctx(ctx: Any) -> str:
    options = getattr(ctx, "options", None)
    if isinstance(options, dict):
        value = options.get("query")
        if value is not None and str(value).strip():
            return str(value)
    snapshot = getattr(ctx, "snapshot", None)
    if hasattr(snapshot, "to_dict") and not isinstance(snapshot, dict):
        try:
            snapshot = snapshot.to_dict()
        except Exception:
            snapshot = {}
    if isinstance(snapshot, dict):
        metadata = snapshot.get("metadata")
        if isinstance(metadata, dict):
            value = metadata.get("query")
            if value is not None and str(value).strip():
                return str(value)
    return DEFAULT_QUERY


def run_report(ctx: Any) -> Any:
    """把检索命中转成统一 ``Report``；每个命中一条 INFO Finding。

    metadata 含 ``results`` / ``stats`` / ``safety`` / ``query`` / ``warning``；
    ``safety`` 明确只读、不写场景、不联网、结果确定性可复现。
    """
    from tak.core.report import Report
    from tak.core.rules import Finding, Severity

    query = _query_from_ctx(ctx)
    options = _as_options(getattr(ctx, "options", None))
    top_k = _top_k(options.get("top_k", DEFAULT_TOP_K))
    library = _library()
    hits = library.search(query, top_k=top_k,
                          options=_search_options(options))
    warning = _warning(query.strip(), hits, len(library.clips))

    findings: List[Any] = []
    for hit in hits:
        findings.append(Finding(
            rule_id="motion_library.hit",
            severity=Severity.INFO,
            message="[%s] %s · %s · 时长 %.2fs：%s" % (
                hit["clip_id"], hit["name"], hit["motion_type"],
                hit["duration"], hit["why"]),
            node=hit["clip_id"],
            fix_hint="引用：clip_id=%s，类型=%s，时长=%.2fs" % (
                hit["clip_id"], hit["motion_type"], hit["duration"]),
            data={
                "score": hit["score"],
                "text_score": hit["text_score"],
                "type_score": hit["type_score"],
                "motion_type": hit["motion_type"],
                "duration": hit["duration"],
                "loopable": hit["loopable"],
                "root_motion": hit["root_motion"],
                "tags": list(hit["tags"]),
                "matched_terms": list(hit["matched_terms"]),
                "why": hit["why"],
                "rank": hit["rank"],
            },
        ))

    spec = getattr(ctx, "spec", None)
    spec = spec if isinstance(spec, dict) else {}
    report = Report(
        title=TITLE,
        dcc=str(getattr(ctx, "dcc", "maya") or "maya"),
        findings=findings,
        spec_version=str(spec.get("version", "1.0.0")),
    )
    report.metadata.update({
        "tool_id": TOOL_ID,
        "query": query,
        "top_k": top_k,
        "count": len(hits),
        "results": hits,
        "stats": library.describe(),
        "warning": warning,
        "safety": {
            "read_only": True,
            "scene_write": False,
            "network": False,
            "offline": True,
            "deterministic": True,
            "note": ("只读动作库元数据与数值特征；不解析 FBX 二进制、不写场景、"
                     "不联网、不写缓存文件；AI 仅可做语义重排候选"),
        },
    })
    return report
