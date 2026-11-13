# -*- coding: utf-8 -*-
"""绑定知识库 RAG 插件编排层（纯数据、零 DCC / 零 UI / 不写场景）。

职责
----
1. ``info()``                 插件自描述；
2. ``search(...)``           薄封装，返回严格可 JSON 序列化的 dict，供 Web API
                             ``/api/knowledge`` 与 Agent Console 直接调用；
3. ``context_for(...)``      生成带引用、长度受限的上下文字符串，供 Agent Console
                             注入对话上下文；
4. ``run_report(ctx)``       把每个命中片段转成一条 INFO ``Finding``，
                             metadata 写入 citations / context / stats / safety。

query 取值优先级：``ctx.options["query"]`` → ``ctx.snapshot["metadata"]["query"]``
→ 缺省的规范概览查询。任何路径都不 import maya / pymxs / pyfbsdk / unreal，
不访问网络、不写场景、不写缓存文件；检索结果完全可复现。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from tak.core.knowledge import KnowledgeBase

TOOL_ID = "rig_kb"
TITLE = "绑定知识库 RAG"
VERSION = "0.1.0"
DCC_LIST = ["maya", "max", "mobu", "unreal"]
MIN_VERSION = {"maya": "2023", "max": "2023", "mobu": "2023", "unreal": "5.8"}
DEFAULT_QUERY = "绑定命名规范 蒙皮权重 FBX UE 导出 IK 重定向 DCC 兼容 概览"


def info() -> Dict[str, Any]:
    """返回插件自描述，供工具面板与菜单使用。"""
    return {
        "id": TOOL_ID,
        "name": TITLE,
        "version": VERSION,
        "dcc": list(DCC_LIST),
        "min_version": dict(MIN_VERSION),
        "capabilities": ["knowledge.search", "knowledge.context",
                         "knowledge.citations", "report.render"],
        "permissions": ["scene.read"],
        "description": ("本地离线 BM25 片段检索，返回带 doc_id/title/section/"
                        "excerpt 引用的命中片段；不联网、不写场景、结果可复现。"),
        "offline": True,
        "read_only": True,
        "search_api": "search(query, top_k=3, options=None) -> dict",
        "context_api": "context_for(query, top_k=3, max_chars=1200, options=None) -> str",
        "default_query": DEFAULT_QUERY,
    }


def _knowledge_base() -> KnowledgeBase:
    """每次新建实例：不缓存文件、不共享可变状态、任何环境都能跑。"""
    return KnowledgeBase()


def _as_options(options: Any) -> Dict[str, Any]:
    return dict(options) if isinstance(options, dict) else {}


def _top_k(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 3


def search(query: Any, top_k: int = 3,
           options: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """检索薄封装，返回可 JSON 序列化的 dict（供 ``/api/knowledge`` 调用）。

    返回 ``{"ok", "tool_id", "query", "top_k", "count", "results",
    "citations", "context", "stats", "offline", "warning"}``。
    空 query / 超长 query / 未知词均安全返回空结果，绝不抛异常。
    """
    kb = _knowledge_base()
    opts = _as_options(options)
    hits = kb.search(query, top_k=top_k, options=opts)
    context = kb.context_for(query, top_k=top_k, options=opts)
    warning = ""
    if not str(query or "").strip():
        warning = "query 为空，已返回空结果"
    elif not hits:
        warning = "未命中任何片段（可换用规范关键词，如 命名 / 权重 / FBX / IK）"
    return {
        "ok": True,
        "tool_id": TOOL_ID,
        "query": str(query or ""),
        "top_k": _top_k(top_k),
        "count": len(hits),
        "results": hits,
        "citations": [dict(hit["citation"]) for hit in hits],
        "context": context,
        "stats": kb.describe(),
        "offline": True,
        "warning": warning,
    }


def context_for(query: Any, top_k: int = 3, max_chars: int = 1200,
                options: Optional[Dict[str, Any]] = None) -> str:
    """生成带引用、长度受限的上下文（供 Agent Console 注入）。"""
    return _knowledge_base().context_for(
        query, top_k=top_k, max_chars=max_chars,
        options=_as_options(options))


def citations(query: Any, top_k: int = 3) -> List[Dict[str, Any]]:
    """返回结构化引用列表。"""
    return _knowledge_base().citations(query, top_k=top_k)


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
    """把检索命中转成统一 ``Report``；每个命中片段一条 INFO Finding。

    metadata 含 ``citations`` / ``context`` / ``stats`` / ``safety``，
    并附带 ``query`` / ``results`` / ``count``；绝不写场景。
    """
    from tak.core.report import Report
    from tak.core.rules import Finding, Severity

    query = _query_from_ctx(ctx)
    options = _as_options(getattr(ctx, "options", None))
    knowledge_options = options.get("knowledge")
    knowledge_options = (_as_options(knowledge_options)
                         if isinstance(knowledge_options, dict) else options)
    top_k = _top_k(options.get("top_k", 3))
    kb = _knowledge_base()
    hits = kb.search(query, top_k=top_k, options=knowledge_options)
    context = kb.context_for(query, top_k=top_k, options=knowledge_options)
    citation_list = [dict(hit["citation"]) for hit in hits]

    findings: List[Any] = []
    for hit in hits:
        findings.append(Finding(
            rule_id="rig_kb.hit",
            severity=Severity.INFO,
            message="[%s] %s · %s：%s" % (
                hit["doc_id"], hit["title"], hit["section"], hit["excerpt"]),
            node=hit["doc_id"],
            fix_hint="引用：doc_id=%s，section=%s" % (
                hit["doc_id"], hit["section"]),
            data={
                "score": hit["score"],
                "section": hit["section"],
                "excerpt": hit["excerpt"],
                "citation": dict(hit["citation"]),
                "tags": list(hit["tags"]),
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
        "citations": citation_list,
        "context": context,
        "stats": kb.describe(),
        "safety": {
            "read_only": True,
            "scene_write": False,
            "network": False,
            "offline": True,
            "deterministic": True,
            "note": ("仅检索仓库内 knowledge/*.md 种子文档；不写场景、不联网、"
                     "不写缓存文件；离线 embed 为占位向量，不参与排序"),
        },
        "warning": ("" if hits else "未命中任何片段，请换用规范关键词重试"),
    })
    return report
