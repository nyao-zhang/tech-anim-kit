# -*- coding: utf-8 -*-
"""规则数据结构与注册装饰器。

规则是「确定性判定」的唯一载体：能写 if 的判定一律放这里，
AI 只负责解释报错、生成候选修复、打标与诊断，不参与判定。
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

from tak.adapters.base import DCCAdapter

RULE_REGISTRY: List["Rule"] = []


class Severity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    BLOCKER = "blocker"


@dataclass
class Finding:
    rule_id: str
    severity: Severity
    message: str
    node: str = ""
    fix_hint: str = ""
    data: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "severity": self.severity.value,
            "message": self.message,
            "node": self.node,
            "fix_hint": self.fix_hint,
            "data": self.data,
        }


@dataclass
class RuleContext:
    spec: Dict[str, Any]
    adapter: DCCAdapter
    snapshot: Optional[Dict[str, Any]] = None
    options: Dict[str, Any] = field(default_factory=dict)

    @property
    def dcc(self) -> str:
        return self.adapter.dcc.value


@dataclass
class Rule:
    id: str
    fn: Callable[[RuleContext], List[Finding]]
    severity: Severity = Severity.WARNING
    description: str = ""
    dcc: List[str] = field(default_factory=lambda: ["maya", "max", "mobu", "unreal"])
    fix_available: bool = False
    plugin_id: str = ""

    def run(self, context: RuleContext) -> List[Finding]:
        started = time.time()
        findings = list(self.fn(context) or [])
        for item in findings:
            item.data.setdefault("duration_ms", round((time.time() - started) * 1000, 2))
        return findings


def rule(rule_id: str, severity: Severity = Severity.WARNING,
         description: str = "", dcc: Optional[List[str]] = None,
         fix_available: bool = False, plugin_id: str = ""):
    """注册规则。插件的 rules.py 在导入时自动完成注册。"""
    def decorator(fn):
        inferred = fn.__module__.replace("tak_plugin_", "").rsplit("_", 1)[0]
        RULE_REGISTRY.append(Rule(
            id=rule_id,
            fn=fn,
            severity=severity,
            description=description or (fn.__doc__ or "").strip().split("\n")[0],
            dcc=dcc or ["maya", "max", "mobu", "unreal"],
            fix_available=fix_available,
            plugin_id=plugin_id or inferred,
        ))
        return fn
    return decorator


def rules_for(dcc: str, plugin_id: str = "") -> List[Rule]:
    """按 DCC 与插件筛选规则。"""
    return [r for r in RULE_REGISTRY
            if dcc in r.dcc and (not plugin_id or r.plugin_id == plugin_id)]
