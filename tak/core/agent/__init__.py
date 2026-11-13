# -*- coding: utf-8 -*-
"""Agent Console 对话式控制台核心（批次 3 / P5.1）。

零 DCC import、零第三方依赖。只暴露确定性意图判定、受限候选推导、
人话解释与有状态会话；所有写入都必须经 DSL 执行器的完整安全链。
"""
from .intents import (
    EXPLAIN,
    INTENTS,
    PLAN,
    RUN,
    SEARCH,
    SPEC_ENFORCER_ACTIONS,
    STATUS,
    UNKNOWN,
    classify,
    ensure_actions_registered,
    explain_findings,
    local_candidates,
)
from .session import SAFETY_NOTES, AgentSession

__all__ = [
    "EXPLAIN", "PLAN", "RUN", "SEARCH", "STATUS", "UNKNOWN", "INTENTS",
    "SPEC_ENFORCER_ACTIONS",
    "classify", "local_candidates", "explain_findings",
    "ensure_actions_registered",
    "AgentSession", "SAFETY_NOTES",
]
