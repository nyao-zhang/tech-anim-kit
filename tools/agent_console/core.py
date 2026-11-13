# -*- coding: utf-8 -*-
"""Agent Console 插件编排层（无 DCC / 无 UI 依赖）。

职责
----
* ``run_report(ctx)``：用 :class:`AgentSession` 对当前快照做一次**确定性** explain
  + 候选计划 dry-run 预览，产出 INFO / WARNING 的 ``Report``；``metadata`` 含
  session / capabilities / safety / candidates / preview / plan。**绝不写场景**；
* ``create_session`` / ``chat`` / ``confirm``：给 Web API（``/api/agent``）的薄封装，
  三者都返回可 JSON 序列化的纯数据；chat 绝不执行，confirm 是唯一写入口。

安全边界：判定走 ``tak.core.rules``，AI 只解释和生成参数化 JSON 候选；任何写入
都要经 ``ACTION_REGISTRY.build_plan`` 白名单 + dry-run + 权限 + 人工确认 + undo。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

TOOL_ID = "agent_console"
TITLE = "Agent Console 对话式控制台"
VERSION = "0.1.0"

_CAPABILITIES = [
    "intent.classify", "rules.explain", "candidates.local", "dsl.plan",
    "dsl.preview", "dsl.confirm", "knowledge.search", "report.render",
]

# 进程内会话表：Web API 通过 session_id 复用同一会话。
_SESSIONS: Dict[str, Any] = {}


def info() -> Dict[str, Any]:
    """插件自描述，供工具面板与菜单展示。"""
    from tak.core.agent import SAFETY_NOTES

    return {
        "id": TOOL_ID,
        "name": "Agent Console 对话式控制台",
        "version": VERSION,
        "dcc": ["maya", "max", "mobu", "unreal"],
        "capabilities": list(_CAPABILITIES),
        "description": "对话式控制台：意图与判定走确定性规则，AI 只解释和生成参数化候选；"
                       "写入需白名单 + dry-run + 权限 + 人工确认 + undo，chat 绝不写场景。",
        "safety": list(SAFETY_NOTES),
    }


def _as_dict(value: Any) -> Dict[str, Any]:
    if hasattr(value, "to_dict") and not isinstance(value, dict):
        try:
            value = value.to_dict()
        except Exception:
            return {}
    return dict(value) if isinstance(value, dict) else {}


# --------------------------------------------------------------------------
# 报告（只读；用 session 做一次确定性 explain + 候选 dry-run 预览）
# --------------------------------------------------------------------------
def run_report(ctx: Any) -> Any:
    """对当前快照产出对话式控制台 Report；只读，绝不写场景。"""
    from tak.core.agent import (SAFETY_NOTES, AgentSession,
                                explain_findings, local_candidates)
    from tak.core.dsl import ACTION_REGISTRY, DSLError
    from tak.core.report import Report
    from tak.core.rules.base import Finding, Severity

    snapshot = _as_dict(getattr(ctx, "snapshot", None))
    spec = getattr(ctx, "spec", None)
    spec = spec if isinstance(spec, dict) else {}
    dcc = str(getattr(ctx, "dcc", "maya") or "maya")

    # 1) 用 session 跑确定性 explain 与 PLAN（chat 全程只读，绝不调用 execute）。
    session = AgentSession(session_id="report", spec=spec)
    explain_turn = session.chat("解释当前快照存在的问题", snapshot=snapshot)
    plan_turn = session.chat("生成候选修复计划", snapshot=snapshot)

    # 2) 结构化内容来自确定性纯函数（可复现、离线可跑）。
    explanations = explain_findings(snapshot, spec=spec, limit=5)
    candidates = local_candidates(snapshot, spec=spec, intent="plan")

    # 3) 候选经白名单校验 + dry-run diff；只产出，绝不执行。
    try:
        plan = ACTION_REGISTRY.build_plan(candidates)
        preview = ACTION_REGISTRY.preview(plan)
        plan_payload = plan.to_dict()
        plan_error = ""
    except (DSLError, TypeError, ValueError) as exc:
        preview, plan_payload, plan_error = [], {}, str(exc)

    findings: List[Any] = [
        Finding(
            rule_id=TOOL_ID + ".session",
            severity=Severity.INFO,
            message="Agent Console 已就绪：意图走确定性路由、判定走规则、AI 只解释与候选；"
                    "chat 绝不写场景，写入必须二次人工确认。",
            fix_hint="通过 create_session / chat / confirm 使用。",
            data={"session_id": "report"},
        )
    ]
    for item in explanations:
        severity = (Severity.WARNING
                    if item["severity"] in ("error", "blocker", "warning")
                    else Severity.INFO)
        findings.append(Finding(
            rule_id=TOOL_ID + ".finding",
            severity=severity,
            message=item["explanation"],
            node=item["node"],
            fix_hint=item["fix_hint"],
            data={"source_rule": item["rule_id"], "severity": item["severity"]},
        ))
    for row in preview:
        findings.append(Finding(
            rule_id=TOOL_ID + ".candidate",
            severity=Severity.INFO,
            message="候选动作 %s（dry-run，待人工确认，未执行）" % row["tool"],
            node=str((row.get("params") or {}).get("node", "")),
            fix_hint="需 confirm(plan_id, confirm=True, dry_run=False, "
                     "granted=['node.write']) 二次确认后才会写场景。",
            data=row,
        ))

    report = Report(
        title=TITLE,
        dcc=dcc,
        findings=findings,
        spec_version=str(spec.get("version", "1.0.0")),
    )
    report.metadata.update({
        "tool_id": TOOL_ID,
        "session": session.describe(),
        "capabilities": list(_CAPABILITIES),
        "safety": list(SAFETY_NOTES),
        "candidates": candidates,
        "candidate_count": len(candidates.get("actions") or []),
        "preview": preview,
        "plan": plan_payload,
        "plan_error": plan_error,
        "explanations": explanations,
        "chat_turns": [
            {"status": explain_turn["status"], "intent": explain_turn["intent"]},
            {"status": plan_turn["status"], "intent": plan_turn["intent"],
             "plan_id": plan_turn["plan_id"]},
        ],
        "dry_run_only": True,
    })
    return report


# --------------------------------------------------------------------------
# Web API 薄封装：三者都返回可 JSON 序列化的纯数据
# --------------------------------------------------------------------------
def create_session(session_id: str = "default", provider: Any = None,
                   adapter: Any = None, spec: Any = None,
                   permissions: Any = None, max_turns: int = 50) -> Dict[str, Any]:
    """创建（或覆盖）一个会话并返回其自描述 dict。

    返回 ``AgentSession.describe()``，可直接 JSON 序列化供 ``/api/agent`` 使用；
    会话对象本身存入模块级 ``_SESSIONS``，由 chat / confirm 按 session_id 复用。
    """
    from tak.core.agent import AgentSession

    session = AgentSession(session_id=session_id, provider=provider,
                           adapter=adapter, spec=spec, permissions=permissions,
                           max_turns=max_turns)
    _SESSIONS[session.session_id] = session
    return session.describe()


def _session_for(session_id: str, spec: Any = None) -> Any:
    from tak.core.agent import AgentSession

    session = _SESSIONS.get(session_id)
    if session is None:
        session = AgentSession(session_id=session_id, spec=spec)
        _SESSIONS[session_id] = session
    return session


def chat(message: Any, snapshot: Any = None, session_id: str = "default",
         spec: Any = None, **kwargs: Any) -> Dict[str, Any]:
    """对话入口（绝不写场景）。

    签名 ``chat(message, snapshot=None, session_id="default", spec=None) -> dict``。
    兼容直接把 Web 的 JSON body 作为 ``message`` 传入：body 为 dict 时按
    ``message / query / snapshot / session_id / spec`` 解包。返回纯数据 dict。
    """
    if isinstance(message, dict):
        payload = message
        message = payload.get("message") or payload.get("query") or ""
        snapshot = payload.get("snapshot", snapshot)
        session_id = payload.get("session_id", session_id)
        spec = payload.get("spec", spec)
    session = _session_for(str(session_id or "default"), spec=spec)
    return session.chat(message, snapshot=snapshot)


def confirm(plan_id: Any, confirm: bool = False, granted: Any = None,
            dry_run: bool = True, session_id: str = "default") -> Dict[str, Any]:
    """确认 / 执行入口（唯一可能写场景，默认 dry-run）。

    签名 ``confirm(plan_id, confirm=False, granted=None, dry_run=True,
    session_id="default") -> dict``。兼容把 Web 的 JSON body 作为 ``plan_id``
    传入：body 为 dict 时按 ``plan_id / confirm / granted / dry_run /
    session_id`` 解包。任何异常都转成 ``{"ok": False, "error": ...}``，不抛。
    """
    if isinstance(plan_id, dict):
        payload = plan_id
        plan_id = payload.get("plan_id") or ""
        confirm = bool(payload.get("confirm", confirm))
        granted = payload.get("granted", granted)
        dry_run = bool(payload.get("dry_run", dry_run))
        session_id = payload.get("session_id", session_id)
    session = _SESSIONS.get(str(session_id or "default"))
    if session is None:
        return {
            "ok": False,
            "error": "会话不存在：%s（请先调用 create_session 或 chat）" % session_id,
            "plan_id": str(plan_id), "dry_run": bool(dry_run),
            "committed": False, "preview": [],
        }
    return session.confirm(plan_id, confirm=confirm, granted=granted,
                           dry_run=dry_run)
