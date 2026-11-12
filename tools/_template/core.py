# -*- coding: utf-8 -*-
"""工具插件的核心逻辑（无 DCC / 无 UI 依赖，可直接在 CI 中单测）。

约定：
    - 确定性判定放 rules.py，用 tak.core.rules.rule 注册。
    - 需要改写场景的操作放 dsl 动作，经 ACTION_REGISTRY 白名单调用。
    - 本文件只做编排，不 import maya / pymxs / pyfbsdk / unreal。
"""
from __future__ import annotations

from typing import Any, Dict, List

TOOL_ID = "__TEMPLATE_ID__"


def info() -> Dict[str, Any]:
    """返回插件自描述，供工具面板展示。"""
    return {"id": TOOL_ID, "version": "0.1.0", "capabilities": ["scene.read"]}


def run(context: Any) -> List[Any]:
    """运行工具，返回 Finding 列表。context 为 tak.core.rules.RuleContext。"""
    from tak.core.rules.base import rules_for
    findings: List[Any] = []
    for rule_obj in rules_for(context.dcc, plugin_id=TOOL_ID):
        findings.extend(rule_obj.run(context))
    return findings
