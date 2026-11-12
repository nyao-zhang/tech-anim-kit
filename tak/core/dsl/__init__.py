# -*- coding: utf-8 -*-
"""受限 DSL：LLM 只产出参数化 JSON，执行器负责白名单与回滚。"""
from .executor import ACTION_REGISTRY, Action, ActionRegistry, ActionSpec, DSLError, ExecutionPlan

__all__ = ["ACTION_REGISTRY", "Action", "ActionRegistry", "ActionSpec", "DSLError", "ExecutionPlan"]
