# -*- coding: utf-8 -*-
"""绑定知识库 RAG：本地离线 BM25 片段检索（零第三方 / 零 DCC / 不联网）。

公开 API
--------
``KnowledgeBase`` / ``Document`` / ``load_seed_documents`` / ``tokenize`` /
``chunk_text`` 从 ``index`` 直接导出；模块级 ``search`` / ``context_for`` /
``citations`` / ``describe`` 是「默认种子知识库」的便捷入口，供 Agent Console
与 Web API 直接调用，每次新建实例、不写任何缓存文件。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from .index import (
    MAX_CHUNK_CHARS, SEED_DIR, Document, KnowledgeBase, chunk_text,
    load_seed_documents, tokenize,
)

__all__ = [
    "Document", "KnowledgeBase", "load_seed_documents", "tokenize",
    "chunk_text", "search", "context_for", "citations", "describe",
    "SEED_DIR", "MAX_CHUNK_CHARS",
]


def search(query: Any, top_k: int = 3) -> List[Dict[str, Any]]:
    """便捷函数：用默认种子库检索，等同 ``KnowledgeBase().search(...)``。"""
    return KnowledgeBase().search(query, top_k=top_k)


def context_for(query: Any, top_k: int = 3, max_chars: int = 1200,
                options: Optional[Dict[str, Any]] = None) -> str:
    """便捷函数：生成带引用、长度受限的上下文（供 Agent Console 注入）。"""
    return KnowledgeBase().context_for(query, top_k=top_k,
                                       max_chars=max_chars, options=options)


def citations(query: Any, top_k: int = 3) -> List[Dict[str, Any]]:
    """便捷函数：返回结构化引用列表。"""
    return KnowledgeBase().citations(query, top_k=top_k)


def describe() -> Dict[str, Any]:
    """便捷函数：返回默认种子库的统计与 embedding 状态。"""
    return KnowledgeBase().describe()
