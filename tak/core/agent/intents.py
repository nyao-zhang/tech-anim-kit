# -*- coding: utf-8 -*-
"""Agent Console 的确定性意图判定与受限候选推导（零 DCC / 零第三方依赖）。

职责边界
--------
* ``classify`` 只做关键词 / 正则路由，**绝不调用 LLM** 判意图；
* ``local_candidates`` 只从快照 + 规范**确定性推导**参数化 JSON 候选，
  且只产出 ``ACTION_REGISTRY`` 已注册的动作名（复用 spec_enforcer 的 4 个动作，
  本模块绝不注册任何新动作、不向全局注册表写入任何东西）；
* ``explain_findings`` 用规则引擎的确定性结果产出人话解释，离线即可工作。

安全底线：本模块不 import 任何 DCC 宿主 / Qt / 第三方包，也不执行任何动作。
"""
from __future__ import annotations

import math
import re
from typing import Any, Dict, List, Optional, Tuple

# --------------------------------------------------------------------------
# 意图常量（用字符串常量而非 Enum，保证所有输出天然可 JSON 序列化）
# --------------------------------------------------------------------------
EXPLAIN = "explain"
PLAN = "plan"
RUN = "run"
SEARCH = "search"
STATUS = "status"
UNKNOWN = "unknown"

INTENTS = (EXPLAIN, PLAN, RUN, SEARCH, STATUS, UNKNOWN)

# spec_enforcer 已注册的 4 个受限 DSL 动作名。本模块只「引用」这些名字，
# 注册动作由 spec_enforcer/actions.py 自身（幂等）完成。
SPEC_ENFORCER_ACTIONS = (
    "spec_enforcer.rename_joint",
    "spec_enforcer.set_control_color",
    "spec_enforcer.normalize_weights",
    "spec_enforcer.set_namespace",
)

_TOOL_ID = "spec_enforcer"

# 确定性推导的默认阈值（仅当 spec 未提供时使用，保证离线可复现）。
_DEFAULT_WEIGHT_TOLERANCE = 0.001
_DEFAULT_MAX_INFLUENCES = 4
_MAX_NODE_CANDIDATES = 5

# --------------------------------------------------------------------------
# 意图路由表：first-match-wins，顺序即优先级。
# 每条是 (intent, [正则...])；英文用 \b，大小写不敏感。
# --------------------------------------------------------------------------
_ROUTE: Tuple[Tuple[str, Tuple[str, ...]], ...] = (
    (RUN, (
        r"执行", r"应用", r"写入", r"提交", r"确认执行", r"确认应用",
        r"真的改", r"跑一下", r"\bapply\b", r"\bexecute\b",
        r"\bcommit\b", r"\brun\b",
    )),
    (PLAN, (
        r"计划", r"方案", r"修复", r"候选", r"建议", r"怎么办",
        r"如何处理", r"怎么处理", r"预览", r"出方案", r"生成计划",
        r"\bplan\b", r"\bpropose\b", r"\bcandidate", r"\bsuggest",
        r"\bfix\b", r"\bpreview\b", r"\bdry.?run\b",
    )),
    (SEARCH, (
        r"搜索", r"检索", r"查询", r"查找", r"查一下", r"查文档",
        r"知识库", r"文档", r"\bsearch\b", r"\bfind\b", r"\bquery\b",
        r"\blookup\b", r"\bdocs?\b", r"\bknowledge\b",
    )),
    (EXPLAIN, (
        r"解释", r"说明", r"为什么", r"什么原因", r"原因", r"分析",
        r"是怎么回事", r"啥意思", r"含义", r"解读", r"什么意思",
        r"\bexplain\b", r"\bwhy\b", r"\bwhat\b", r"\bdescribe\b",
        r"\bmeaning\b", r"\breason\b",
    )),
    (STATUS, (
        r"状态", r"历史", r"记录", r"审计", r"帮助", r"能做什么",
        r"能力", r"会话信息", r"信息", r"\bstatus\b", r"\bhistory\b",
        r"\baudit\b", r"\bhelp\b", r"\bcapabilit", r"\binfo\b",
    )),
)

_COMPILED: Tuple[Tuple[str, Tuple[Any, ...]], ...] = tuple(
    (intent, tuple(re.compile(p, re.IGNORECASE) for p in patterns))
    for intent, patterns in _ROUTE
)


def classify(message: Any, snapshot: Any = None) -> Dict[str, Any]:
    """确定性关键词 / 正则路由（中文 + 英文），返回意图分类结果。

    返回 ``{"intent", "confidence", "args", "matched"}``：

    * ``intent``: :data:`INTENTS` 之一；
    * ``confidence``: 0.0（UNKNOWN）到 0.99 的浮点，命中越多越高；
    * ``args``: 透传给下游的轻量参数（query 文本、SEARCH 的 top_k）；
    * ``matched``: 实际命中的文本片段列表（便于审计与展示）。

    ``snapshot`` 仅用于 API 兼容，分类结果不依赖快照内容。
    """
    text = "" if message is None else str(message).strip()
    args: Dict[str, Any] = {"query": text}
    for intent, patterns in _COMPILED:
        matched: List[str] = []
        if text:
            for pattern in patterns:
                for hit in pattern.finditer(text):
                    fragment = hit.group(0)
                    if fragment and fragment not in matched:
                        matched.append(fragment)
        if matched:
            confidence = round(min(0.99, 0.7 + 0.1 * len(matched)), 2)
            if intent == SEARCH:
                args["top_k"] = 3
            return {"intent": intent, "confidence": confidence,
                    "args": args, "matched": matched}
    return {"intent": UNKNOWN, "confidence": 0.0, "args": args, "matched": []}


# --------------------------------------------------------------------------
# 动作注册保障：只触发 spec_enforcer 自身（幂等）的 actions 子模块加载，
# 不加载其 rules，避免污染全局 RULE_REGISTRY；绝不注册新动作。
# --------------------------------------------------------------------------
def ensure_actions_registered() -> List[str]:
    """确保 spec_enforcer 的 4 个既有 DSL 动作已注册；返回当前动作名清单。

    任何导入失败都被吞掉：动作未注册时 :func:`local_candidates` 会安全地产出
    空候选（绝不会生成未注册动作名）。本函数只「复用」既有注册逻辑。
    """
    from tak.core.dsl import ACTION_REGISTRY

    registered = set(ACTION_REGISTRY.names)
    if set(SPEC_ENFORCER_ACTIONS).issubset(registered):
        return list(ACTION_REGISTRY.names)
    try:
        from tak.core import registry, runner

        plugin = runner.find_plugin(_TOOL_ID)
        registry.load_plugin_module(plugin, "actions")
    except Exception:
        pass
    return list(ACTION_REGISTRY.names)


# --------------------------------------------------------------------------
# 确定性候选推导
# --------------------------------------------------------------------------
def _as_dict(value: Any) -> Dict[str, Any]:
    if hasattr(value, "to_dict") and not isinstance(value, dict):
        try:
            value = value.to_dict()
        except Exception:
            return {}
    return dict(value) if isinstance(value, dict) else {}


def _number(value: Any) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    result = float(value)
    return result if math.isfinite(result) else None


def _spec_section(spec: Dict[str, Any], section: str) -> Dict[str, Any]:
    value = spec.get(section)
    return dict(value) if isinstance(value, dict) else {}


def _weight_candidate(snapshot: Dict[str, Any],
                      spec: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """权重和偏差 / 影响数超限 -> spec_enforcer.normalize_weights。"""
    skin = _as_dict(snapshot.get("skin"))
    skin_spec = _spec_section(spec, "skin")
    tolerance = _number(skin_spec.get("weight_sum_tolerance"))
    if tolerance is None:
        tolerance = _DEFAULT_WEIGHT_TOLERANCE
    limit = _number(skin_spec.get("max_influences"))
    if limit is None:
        limit = _DEFAULT_MAX_INFLUENCES
    deviation = _number(skin.get("weight_sum_deviation"))
    found = _number(skin.get("max_influences_found"))
    weight_bad = deviation is not None and abs(deviation) > tolerance
    influence_bad = found is not None and limit is not None and found > limit
    if not (weight_bad or influence_bad):
        return None
    params: Dict[str, Any] = {}
    if 0.0 <= tolerance <= 1.0:
        params["tolerance"] = tolerance
    if 1 <= limit <= 32:
        params["max_influences"] = int(limit)
    if not params:
        return None
    reasons = []
    if weight_bad:
        reasons.append("权重和偏差 %.4g 超出容差 %.4g" % (abs(deviation), tolerance))
    if influence_bad:
        reasons.append("单顶点影响骨数 %.4g 超过上限 %.4g" % (found, limit))
    return {"tool": _TOOL_ID + ".normalize_weights", "params": params,
            "reason": "；".join(reasons) + "，候选动作：归一化蒙皮权重"}


def _node_candidates(snapshot: Dict[str, Any],
                     spec: Dict[str, Any]) -> List[Dict[str, Any]]:
    """命名空间污染 / 负缩放 -> set_namespace / rename_joint 候选。"""
    joints = _as_dict(snapshot.get("joints"))
    naming = _spec_section(spec, "naming")
    pattern = naming.get("joint_pattern")
    pattern = pattern if isinstance(pattern, str) else ""
    results: List[Dict[str, Any]] = []
    for name in sorted(joints):
        if len(results) >= _MAX_NODE_CANDIDATES:
            break
        data = _as_dict(joints.get(name))
        namespace = data.get("namespace")
        namespaced = ":" in name or (isinstance(namespace, str) and bool(namespace))
        scale = data.get("scale")
        negative_scale = False
        if isinstance(scale, (list, tuple)):
            negative_scale = any(
                isinstance(v, (int, float)) and not isinstance(v, bool) and v < 0
                for v in scale)
        if namespaced:
            results.append({
                "tool": _TOOL_ID + ".set_namespace",
                "params": {"node": str(name), "namespace": ""},
                "reason": "关节 %r 带命名空间前缀，移回根命名空间" % name,
            })
        if negative_scale:
            params: Dict[str, Any] = {"node": str(name)}
            if pattern:
                params["pattern"] = pattern
            params["reason"] = "负缩放疑似镜像关节，需人工复核命名（不自动猜目标名）"
            results.append({
                "tool": _TOOL_ID + ".rename_joint",
                "params": params,
                "reason": "关节 %r 存在负缩放，需复核命名与冻结变换" % name,
            })
    return results


def local_candidates(snapshot: Any, spec: Any = None,
                     intent: str = "plan") -> Dict[str, Any]:
    """从快照确定性推导受限参数化候选计划。

    返回 ``{"actions": [...], "rationale": [...], "source": "deterministic"}``，
    其中 ``actions`` 即 ``ACTION_REGISTRY.build_plan`` 可直接消费的 payload 片段
    （每项 ``{"tool", "params"}``）。

    硬约束：产出前用 ``ACTION_REGISTRY.names`` 过滤，只保留已注册动作；
    未注册动作一律不生成。``intent`` 仅用于 API 兼容，候选内容只由 snapshot
    与 spec 决定，保证完全确定性、可复现。
    """
    from tak.core.dsl import ACTION_REGISTRY

    ensure_actions_registered()
    data = _as_dict(snapshot)
    spec_data = _as_dict(spec)
    registered = set(ACTION_REGISTRY.names)

    raw: List[Dict[str, Any]] = []
    weight = _weight_candidate(data, spec_data)
    if weight is not None:
        raw.append(weight)
    raw.extend(_node_candidates(data, spec_data))

    actions: List[Dict[str, Any]] = []
    rationale: List[str] = []
    seen = set()
    for item in raw:
        tool = item.get("tool")
        if tool not in registered:
            # 未注册动作绝不进入候选（例如动作模块加载失败时）。
            continue
        key = (tool, _stable_key(item.get("params")))
        if key in seen:
            continue
        seen.add(key)
        actions.append({"tool": tool, "params": dict(item.get("params") or {})})
        rationale.append(str(item.get("reason") or ""))

    rationale.append(
        "候选来源：确定性快照分析；仅包含 ACTION_REGISTRY 已注册动作；"
        "未经白名单校验、dry-run、权限授予与人工确认，绝不执行。"
    )
    if not actions:
        rationale.append("快照未发现可自动推导的修复信号，或所需动作未注册：无候选动作。")
    return {"actions": actions, "rationale": rationale,
            "source": "deterministic"}


def _stable_key(params: Any) -> str:
    """把 params 折叠成稳定字符串用于去重（不依赖 dict 顺序）。"""
    data = _as_dict(params)
    return repr(sorted((str(k), repr(v)) for k, v in data.items()))


# --------------------------------------------------------------------------
# 人话解释（确定性规则结果，不调 LLM 也能工作）
# --------------------------------------------------------------------------
_PLAIN: Dict[str, str] = {
    "naming.joint_pattern": "骨骼名不符合规范正则，导出后引擎可能找不到对应骨骼路径，需要按规范重命名。",
    "naming.forbidden_token": "骨骼名里含空格、分隔符或 copy 后缀等禁用字符，容易在导出与引用时出错。",
    "naming.namespace": "关节带命名空间前缀（冒号），导出到引擎后节点路径会变化，重定向与动画曲线可能失效。",
    "naming.side_tokens": "左右侧标记不符合规范或缺少镜像关节，会导致左右不对称或重定向错位。",
    "naming.case_collision": "存在仅靠大小写区分的关节名，规范区分大小写时会被视为两个不同节点，容易冲突。",
    "naming.control_namespace": "带命名空间的节点没有放在规范要求的控制命名空间下，控制器管理会混乱。",
    "skeleton.missing_required": "缺少规范要求的关键关节，绑定与导出链路可能不完整。",
    "skeleton.max_depth": "骨骼层级过深，导出与运行时计算成本升高，且容易产生精度问题。",
    "skeleton.negative_scale": "骨骼上有负缩放，通常意味着被镜像过，导出后顶点会沿轴向翻转、蒙皮错位。",
    "skeleton.unfrozen_transform": "关节残留未冻结的旋转或缩放，动画补偿会叠加到导出结果上。",
    "skeleton.joint_orient": "关节朝向矩阵不正交归一，会产生剪切或缩放残留。",
    "skeleton.parent_integrity": "父子层级存在缺失、环或 depth 不连续，骨架结构不可靠。",
    "skin.max_influences": "单顶点受过多骨骼影响，引擎侧会截断权重，导致 DCC 预览与运行时不一致。",
    "skin.weight_sum": "部分顶点权重和不是 1，蒙皮会整体偏胖或偏瘦，甚至塌陷拉丝。",
    "skin.unknown_deformer": "存在不在白名单里的 deformer，导出流程可能不支持。",
    "export.unit_mismatch": "场景单位与导出规范不一致，模型到引擎会被整体缩放。",
    "export.up_axis_mismatch": "场景 up axis 与导出规范不一致，模型朝向会翻转。",
    "export.frame_range_mismatch": "导出帧范围与规范不一致，动画片段会缺帧或多帧。",
    "dcc.version_unsupported": "当前 DCC 版本低于规范要求的最低版本，部分特性不可用。",
}


def _plain_text(rule_id: str, node: str, message: str) -> str:
    base = _PLAIN.get(rule_id)
    if base:
        return "%s 定位节点：%s。" % (base, node or "场景级（无具体节点）")
    return "规则 %s 报告：%s。定位节点：%s。" % (
        rule_id or "未知规则", message or "无详细信息", node or "场景级（无具体节点）")


def explain_findings(snapshot: Any, spec: Any = None,
                     limit: int = 5) -> List[Dict[str, Any]]:
    """用确定性规则结果产出人话解释条目（不调用 LLM 也能工作）。

    只跑 ``plugin_id="builtin"`` 的内置规则，保证与全局插件规则是否加载无关，
    输出完全可复现。每条是纯 ``dict``，可直接 JSON 序列化。
    """
    from tak.core.rules.engine import RuleContext, run_rules

    ctx = RuleContext(snapshot=_as_dict(snapshot), spec=_as_dict(spec) or None,
                      dcc="maya", options={})
    report = run_rules(ctx, plugin_id="builtin", title="Agent Console 规则解释")
    try:
        cap = max(0, int(limit))
    except (TypeError, ValueError):
        cap = 5
    items: List[Dict[str, Any]] = []
    for finding in list(report.findings)[:cap]:
        severity = finding.severity
        sev_value = severity.value if hasattr(severity, "value") else str(severity)
        message = str(getattr(finding, "message", "") or "")
        node = str(getattr(finding, "node", "") or "")
        items.append({
            "rule_id": str(getattr(finding, "rule_id", "") or ""),
            "severity": sev_value,
            "node": node,
            "message": message,
            "explanation": _plain_text(str(getattr(finding, "rule_id", "") or ""),
                                       node, message),
            "fix_hint": str(getattr(finding, "fix_hint", "") or ""),
        })
    if not items:
        items.append({
            "rule_id": "agent.no_findings", "severity": "info", "node": "",
            "message": "确定性规则未发现问题",
            "explanation": "当前快照在内置确定性规则下没有异常项，无需修复。",
            "fix_hint": "",
        })
    return items
