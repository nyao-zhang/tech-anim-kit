# -*- coding: utf-8 -*-
"""Agent Console 会话：对话式解释 / 候选 / 计划 / 确认执行（零 DCC 依赖）。

安全模型（务必遵守）
--------------------
* ``chat()`` **绝不写场景**：只做意图判定、规则解释、候选推导、计划校验与
  dry-run 预览；绝不调用 ``ACTION_REGISTRY.execute``；
* ``confirm()`` 是**唯一可能写场景**的入口，且必须
  ``confirm=True and dry_run=False and granted 覆盖 required_permissions``；
  执行链完全交给 DSL 执行器（白名单 -> dry-run -> 权限 -> 人工确认 ->
  begin_undo -> 逐条 -> 失败整体 rollback）；
* LLM 输出只能是参数化 JSON（tool + params）且必须经
  ``ACTION_REGISTRY.build_plan`` 白名单校验；解析失败 / 未注册动作一律拒绝，
  绝不自由生成 DCC 脚本、绝不执行；
* 所有公开输出都是可 JSON 序列化的纯数据，使用稳定递增序号，
  不写 wall-clock / 随机数，保证可复现。

本模块零 DCC import、零第三方依赖，顶层不 import maya/pymxs/pyfbsdk/unreal/PySide。
"""
from __future__ import annotations

import copy
from typing import Any, Dict, List, Optional

from tak.core.dsl import ACTION_REGISTRY, DSLError

from . import intents

# 安全边界常量（纯字符串元组，天然可 JSON 序列化）。
SAFETY_NOTES = (
    "AI 只负责解释与生成候选；确定性判定一律走 tak.core.rules 规则引擎。",
    "LLM 输出只能是参数化 JSON（tool + params），必须经 ACTION_REGISTRY.build_plan "
    "白名单校验；解析失败或未注册动作一律拒绝，绝不执行，绝不生成 DCC 脚本。",
    "写入需完整链路：白名单校验 -> dry-run 预览 -> 权限授予(granted) -> "
    "人工确认(confirm=True) -> begin_undo -> 逐条执行 -> 失败整体 rollback。",
    "chat() 绝不写场景；唯一可能写场景的入口是 "
    "confirm(plan_id, confirm=True, dry_run=False, granted=[...])。",
)

_STATUSES = ("explained", "plan_ready", "rejected", "status", "searched")


class AgentSession:
    """有状态、可复现、默认离线的对话会话。"""

    def __init__(self, session_id: str = "default", provider: Any = None,
                 adapter: Any = None, spec: Any = None,
                 permissions: Any = None, max_turns: int = 50) -> None:
        if provider is None:
            from tak.core.ai import get_provider

            provider = get_provider(task="default")
        if adapter is None:
            from tak.adapters.base import NullAdapter

            adapter = NullAdapter()
        self.session_id = str(session_id or "default")
        self.provider = provider
        self.adapter = adapter
        self.spec = dict(spec) if isinstance(spec, dict) else {}
        self.permissions = list(permissions or [])
        try:
            self.max_turns = max(1, int(max_turns))
        except (TypeError, ValueError):
            self.max_turns = 50

        # 保证 PLAN / SEARCH 等路径所需的既有 DSL 动作可用（幂等，绝不注册新动作）。
        intents.ensure_actions_registered()

        self._turn = 0
        self._plan_seq = 0
        self._history: List[Dict[str, Any]] = []
        self._audit: List[Dict[str, Any]] = []
        self._plans: Dict[str, Dict[str, Any]] = {}
        self._plan_order: List[str] = []

    # ------------------------------------------------------------------
    # 内部工具
    # ------------------------------------------------------------------
    def _safety_block(self) -> Dict[str, Any]:
        return {
            "chat_writes_scene": False,
            "only_write_entry": "confirm(plan_id, confirm=True, dry_run=False, granted=[...])",
            "chain": ["whitelist(build_plan)", "dry-run(preview)",
                      "permissions(granted)", "human-confirm(confirm=True)",
                      "undo(begin_undo/commit/rollback)"],
            "notes": list(SAFETY_NOTES),
        }

    def _blank(self, turn: int, intent_value: str) -> Dict[str, Any]:
        """chat 返回值的骨架，固定含约定的全部键。"""
        return {
            "session_id": self.session_id,
            "turn": turn,
            "intent": intent_value,
            "reply": "",
            "candidates": [],
            "plan": None,
            "plan_id": None,
            "status": "explained",
            "citations": [],
            "safety": self._safety_block(),
            # 附加：PLAN 路径的 dry-run diff 与拒绝原因（不破坏上面约定键）。
            "preview": [],
            "reason": "",
        }

    def _store_plan(self, plan: Any, intent_value: str) -> str:
        self._plan_seq += 1
        plan_id = "plan-%d" % self._plan_seq
        self._plans[plan_id] = {
            "plan": plan,
            "plan_seq": self._plan_seq,
            "created_turn": self._turn,
            "intent": intent_value,
            "actions": copy.deepcopy(plan.to_dict().get("actions", [])),
        }
        self._plan_order.append(plan_id)
        return plan_id

    def _record_history(self, turn: int, intent_value: str,
                        message: str, reply: str,
                        status: str, plan_id: Any) -> None:
        self._history.append({
            "seq": len(self._history) + 1,
            "turn": turn,
            "role": "user",
            "content": str(message),
            "intent": intent_value,
            "status": status,
            "plan_id": plan_id,
        })
        self._history.append({
            "seq": len(self._history) + 1,
            "turn": turn,
            "role": "assistant",
            "content": reply,
            "intent": intent_value,
            "status": status,
            "plan_id": plan_id,
        })
        if len(self._history) > self.max_turns:
            self._history = self._history[-self.max_turns:]

    def _append_audit(self, entry: Dict[str, Any]) -> None:
        record = dict(entry)
        record["seq"] = len(self._audit) + 1
        self._audit.append(record)

    # ------------------------------------------------------------------
    # 唯一把「参数化 JSON」变成待确认计划的地方（白名单校验 choke-point）
    # ------------------------------------------------------------------
    def propose(self, payload: Any, snapshot: Any = None) -> Dict[str, Any]:
        """把参数化 JSON（可来自 LLM，但只能是 tool + params）存为待确认计划。

        经 ``ACTION_REGISTRY.build_plan`` 白名单校验；解析失败 / 未注册动作 /
        参数越界一律 ``rejected``。只产出 dry-run diff，**绝不执行、绝不写场景**。
        ``snapshot`` 仅用于 API 兼容。
        """
        try:
            plan = ACTION_REGISTRY.build_plan(payload)
            preview = ACTION_REGISTRY.preview(plan)
        except (DSLError, TypeError, ValueError) as exc:
            return {
                "ok": False,
                "status": "rejected",
                "reason": "计划未通过 ACTION_REGISTRY 白名单校验: %s" % exc,
                "plan_id": None,
                "preview": [],
                "plan": None,
            }
        plan_id = self._store_plan(plan, intents.PLAN)
        return {
            "ok": True,
            "status": "plan_ready",
            "reason": "",
            "plan_id": plan_id,
            "preview": preview,
            "plan": plan.to_dict(),
        }

    # ------------------------------------------------------------------
    # provider / 知识库：只读、可降级
    # ------------------------------------------------------------------
    def _provider_chat(self, message: str,
                       explanations: List[Dict[str, Any]]) -> str:
        """AI 解释层：只把文本放进 reply，判定仍来自确定性函数。"""
        try:
            messages = [
                {"role": "system",
                 "content": "你是绑定技术美术助手，只解释规则结论并给出 dry-run 建议；"
                            "不参与确定性判定，不生成任何 DCC 脚本，不执行任何动作。"},
                {"role": "user",
                 "content": "用户问题：%s\n确定性规则结论：%s" % (
                     message,
                     "；".join(item["explanation"] for item in explanations[:3])),
                 },
            ]
            reply = self.provider.chat(messages)
            return reply.strip() if isinstance(reply, str) else ""
        except Exception as exc:
            return "（AI 解释层不可用，已降级为确定性规则解释：%s）" % type(exc).__name__

    def _knowledge_context(self, query: str, top_k: int = 3):
        """SEARCH 的知识库检索；模块不可用时优雅跳过。

        另一个子任务并行实现 ``tak.core.knowledge.KnowledgeBase(...)
        .context_for(query, top_k=3)``（返回带引用的字符串）。若同时提供
        ``citations(query, top_k)`` 则优先取结构化引用；否则把上下文字符串
        折叠为单条文本引用。返回 ``(citations, note)``，全部为纯数据。
        """
        try:
            from tak.core.knowledge import KnowledgeBase
        except Exception:
            return None, "知识库模块当前不可用（tak.core.knowledge 尚未加载），已跳过检索。"
        try:
            kb = KnowledgeBase()
            context = kb.context_for(query, top_k=top_k)
        except Exception as exc:
            return None, "知识库检索失败，已跳过：%s: %s" % (type(exc).__name__, exc)

        def _plain(value: Any) -> Any:
            return value if isinstance(value, (str, int, float, bool, type(None))) else str(value)

        citations: List[Dict[str, Any]] = []
        # 优先：结构化引用（doc_id / title / section / excerpt / path）。
        try:
            structured = kb.citations(query, top_k=top_k)
        except Exception:
            structured = None
        if isinstance(structured, (list, tuple)):
            for item in structured:
                if isinstance(item, dict):
                    citations.append({str(key): _plain(value)
                                      for key, value in item.items()})

        if not citations:
            # 降级：把 context_for 的返回折叠为纯文本引用。
            if isinstance(context, (list, tuple)):
                for item in context:
                    if isinstance(item, dict):
                        citations.append({str(key): _plain(value)
                                          for key, value in item.items()})
                    else:
                        citations.append({"text": str(item)})
            elif isinstance(context, str) and context.strip():
                citations = [{"text": context.strip()}]
        return citations, ""

    # ------------------------------------------------------------------
    # 主入口：chat（绝不写场景）
    # ------------------------------------------------------------------
    def chat(self, message: Any, snapshot: Any = None) -> Dict[str, Any]:
        """处理一条消息；返回纯数据，绝不写场景、绝不调用 DSL execute。"""
        self._turn += 1
        turn = self._turn
        classification = intents.classify(message, snapshot)
        intent_value = classification["intent"]
        query = str(classification.get("args", {}).get("query", message or ""))
        result = self._blank(turn, intent_value)
        data = snapshot if snapshot is not None else {}

        if intent_value == intents.EXPLAIN:
            explanations = intents.explain_findings(data, spec=self.spec, limit=5)
            lines = ["确定性规则解释（判定来自规则引擎，不来自 AI）："]
            for item in explanations:
                lines.append("- [%s] %s" % (str(item["severity"]).upper(),
                                            item["explanation"]))
            provider_text = self._provider_chat(query, explanations)
            if provider_text:
                lines.append("AI 补充解释：" + provider_text)
            result["reply"] = "\n".join(lines)
            result["status"] = "explained"

        elif intent_value == intents.PLAN:
            candidates = intents.local_candidates(data, spec=self.spec,
                                                  intent="plan")
            result["candidates"] = copy.deepcopy(list(candidates.get("actions") or []))
            proposed = self.propose(candidates, snapshot=snapshot)
            result["plan"] = proposed["plan"]
            result["plan_id"] = proposed["plan_id"]
            result["status"] = proposed["status"]
            result["preview"] = proposed["preview"]
            result["reason"] = proposed["reason"]
            if proposed["ok"]:
                result["reply"] = (
                    "已生成待确认计划 plan_id=%s（%d 个候选动作）。以下为 dry-run diff，"
                    "chat 未对场景做任何修改；如要执行请调用 confirm(...) 并二次确认。"
                    % (proposed["plan_id"], len(proposed["preview"])))
            else:
                result["reply"] = "候选计划被拒绝：" + proposed["reason"]

        elif intent_value == intents.RUN:
            # RUN 在 chat 里绝不执行；仅提示去 confirm()。
            pending = self.pending_plans()
            hint = ""
            if pending:
                hint = " 当前有 %d 个待确认计划，最近一个 plan_id=%s。" % (
                    len(pending), pending[-1]["plan_id"])
            result["reply"] = (
                "chat 不写场景：执行必须走 confirm(plan_id, confirm=True, "
                "dry_run=False, granted=['node.write']) 并经人工二次确认，"
                "执行器会开启 undo 并在失败时整体回滚。" + hint)
            result["status"] = "explained"

        elif intent_value == intents.SEARCH:
            citations, note = self._knowledge_context(query, top_k=3)
            result["citations"] = citations or []
            result["status"] = "searched"
            if note:
                result["reply"] = "已识别检索意图；" + note
            else:
                result["reply"] = "检索到 %d 条知识库上下文（只读引用，不写场景）。" % len(
                    result["citations"])

        elif intent_value == intents.STATUS:
            described = self.describe()
            result["reply"] = (
                "会话 %s：已进行 %d 轮、审计记录 %d 条、待确认计划 %d 个；"
                "provider=%s、适配器=%s、已注册动作 %d 个。"
                % (described["session_id"], described["turn"],
                   described["audit_count"], described["pending_plan_count"],
                   described["provider"]["model"], described["adapter"].get("name", ""),
                   len(described["registered_actions"])))
            result["status"] = "status"

        else:  # UNKNOWN
            result["reply"] = (
                "未识别的意图（置信度 0.0）。本控制台支持：解释问题、生成修复计划(plan)、"
                "执行指引(run)、检索(search)、查看状态(status)。"
                "判定走规则、写入需白名单 + dry-run + 权限 + 人工确认 + undo；"
                "chat 绝不写场景。")
            result["status"] = "explained"

        self._record_history(turn, intent_value, str(message or ""),
                             result["reply"], result["status"], result["plan_id"])
        return result

    # ------------------------------------------------------------------
    # 唯一可能写场景的入口：confirm
    # ------------------------------------------------------------------
    def confirm(self, plan_id: str, confirm: bool = False,
                granted: Any = None, dry_run: bool = True) -> Dict[str, Any]:
        """确认 / 执行待确认计划；默认 dry-run，任何异常都转成数据、绝不抛。

        * ``dry_run=True``（默认）或 ``confirm=False``：只返回 dry-run diff，
          不写场景；
        * 真正执行：``confirm=True`` + ``dry_run=False`` + ``granted`` 覆盖
          ``required_permissions``；调用 ``ACTION_REGISTRY.execute(...)``。
        """
        effective_granted = list(granted) if granted is not None else list(self.permissions)

        def finish(out: Dict[str, Any]) -> Dict[str, Any]:
            self._append_audit({
                "event": "confirm",
                "plan_id": str(plan_id),
                "session_id": self.session_id,
                "confirm": bool(confirm),
                "dry_run": bool(out.get("dry_run", dry_run)),
                "ok": bool(out.get("ok")),
                "committed": bool(out.get("committed")),
                "status": out.get("status", "rejected" if not out.get("ok") else "ok"),
                "error": str(out.get("error", "") or ""),
                "required_permissions": list(out.get("required_permissions") or []),
                "granted": list(effective_granted),
            })
            return out

        entry = self._plans.get(str(plan_id))
        if entry is None:
            return finish({
                "ok": False,
                "error": "未知或已失效的 plan_id: %s" % plan_id,
                "plan_id": str(plan_id), "session_id": self.session_id,
                "dry_run": bool(dry_run), "committed": False,
                "preview": [], "granted": list(effective_granted),
            })

        plan = entry["plan"]
        try:
            preview = ACTION_REGISTRY.preview(plan)
        except (DSLError, TypeError, ValueError) as exc:
            return finish({
                "ok": False, "error": "计划无效: %s" % exc,
                "plan_id": str(plan_id), "session_id": self.session_id,
                "dry_run": bool(dry_run), "committed": False,
                "preview": [], "granted": list(effective_granted),
            })
        required = sorted({perm for row in preview
                           for perm in (row.get("permissions") or [])})

        try:
            exec_result = ACTION_REGISTRY.execute(
                plan, self.adapter, granted=effective_granted,
                dry_run=bool(dry_run), confirm=bool(confirm))
        except Exception as exc:  # 任何异常（含缺权限 / 未确认）都转数据，不抛
            return finish({
                "ok": False,
                "error": "%s: %s" % (type(exc).__name__, exc),
                "plan_id": str(plan_id), "session_id": self.session_id,
                "dry_run": bool(dry_run), "committed": False,
                "preview": preview, "required_permissions": required,
                "granted": list(effective_granted),
            })

        is_dry = bool(exec_result.get("dry_run", False))
        committed = bool(exec_result.get("committed", False))
        status = "committed" if committed else ("dry_run" if is_dry else "failed")
        return finish({
            "ok": True,
            "status": status,
            "plan_id": str(plan_id),
            "session_id": self.session_id,
            "dry_run": is_dry,
            "committed": committed,
            "preview": preview,
            "required_permissions": exec_result.get("required_permissions", required),
            "granted": list(effective_granted),
            "result": exec_result,
        })

    # ------------------------------------------------------------------
    # 只读查询
    # ------------------------------------------------------------------
    def history(self) -> List[Dict[str, Any]]:
        """对话历史（单调递增 seq，可 JSON 序列化，可复现）。"""
        return copy.deepcopy(self._history)

    def audit(self) -> List[Dict[str, Any]]:
        """确认 / 执行审计日志（单调递增 seq，可复现）。"""
        return copy.deepcopy(self._audit)

    def pending_plans(self) -> List[Dict[str, Any]]:
        """待确认计划清单（纯数据，按创建顺序）。"""
        items: List[Dict[str, Any]] = []
        for pid in self._plan_order:
            stored = self._plans.get(pid)
            if stored is None:
                continue
            items.append({
                "plan_id": pid,
                "plan_seq": stored["plan_seq"],
                "created_turn": stored["created_turn"],
                "intent": stored["intent"],
                "actions": copy.deepcopy(stored["actions"]),
                "status": "pending",
            })
        return items

    def describe(self) -> Dict[str, Any]:
        """会话自描述（纯数据，无 wall-clock / 随机数）。"""
        try:
            adapter_info = self.adapter.describe()
            if not isinstance(adapter_info, dict):
                adapter_info = {"name": type(self.adapter).__name__}
        except Exception:
            adapter_info = {"name": type(self.adapter).__name__}
        return {
            "session_id": self.session_id,
            "provider": {
                "model": str(getattr(self.provider, "model", "") or ""),
                "class": type(self.provider).__name__,
            },
            "adapter": adapter_info,
            "spec_version": str(self.spec.get("version", "")),
            "permissions": list(self.permissions),
            "max_turns": self.max_turns,
            "turn": self._turn,
            "history_count": len(self._history),
            "audit_count": len(self._audit),
            "pending_plan_count": len(self._plans),
            "capabilities": ["intent.classify", "rules.explain", "candidates.local",
                             "dsl.plan", "dsl.preview", "dsl.confirm",
                             "knowledge.search", "report.render"],
            "registered_actions": list(ACTION_REGISTRY.names),
            "statuses": list(_STATUSES),
            "safety": list(SAFETY_NOTES),
        }

    def reset(self) -> None:
        """清空历史 / 审计 / 待确认计划并复位计数器。"""
        self._history = []
        self._audit = []
        self._plans = {}
        self._plan_order = []
        self._turn = 0
        self._plan_seq = 0
