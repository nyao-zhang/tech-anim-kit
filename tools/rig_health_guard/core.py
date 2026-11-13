# -*- coding: utf-8 -*-
"""Rig Health Guard 的编排层（无 DCC、无 UI 依赖）。

职责只有三件事：

1. 通过 ``tak.core.rules.engine.run_rules`` 跑本插件注册的确定性规则；
2. 在 ``report.metadata`` 里补充规则数、耗时、历史工单占位；
3. 为 ERROR / BLOCKER 生成**只读**的 AI 解释层。

AI 边界
-------
* 判定完全由 ``rules.py`` 决定，AI 输出只写进
  ``report.metadata["ai_explanations"]``，不新增/删除/修改任何 Finding，
  也不参与 ``passed`` 计算；
* 默认离线：``tak.core.ai.get_provider`` 在无 API key 时返回 MockProvider，
  此时直接使用本地确定性模板，不发起网络请求、不抛异常；
* 真实 provider 调用失败时静默降级到本地模板，并记录降级原因。
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional, Tuple

TOOL_ID = "rig_health_guard"
TITLE = "Rig Health Guard 场景体检"

_BLOCKING = ("error", "blocker")

# 每条规则的人话解释模板；未命中时回退到 Finding.message。
_PLAIN_EXPLANATION: Dict[str, str] = {
    TOOL_ID + ".orphan_nodes":
        "这个节点没有父级，也没有被蒙皮、约束或动画曲线引用，属于孤立节点。"
        "它不会影响当前绑定，但会让场景变脏，导出时可能被误带进引擎。",
    TOOL_ID + ".negative_scale":
        "骨骼上有负缩放，意味着这个关节被镜像过。DCC 里看起来正常，"
        "但导出后顶点会沿轴向翻转，蒙皮和动画都会错位。",
    TOOL_ID + ".unfrozen_transform":
        "关节上残留了非单位的缩放或旋转。绑定阶段通常要求冻结变换，"
        "否则动画层的补偿会叠加到导出结果上，产生难以定位的偏移。",
    TOOL_ID + ".namespace_pollution":
        "关节名带了命名空间前缀（冒号）。导出到引擎后节点路径会变化，"
        "重定向、引用和动画曲线都可能找不到目标。",
    TOOL_ID + ".duplicate_shape":
        "多个 transform 共用同一个 shape 节点。替换模型或调整显示层时，"
        "这些节点会互相串改，属于典型的场景结构错误。",
    TOOL_ID + ".skin_influence_over_limit":
        "单个顶点受过多骨骼影响。引擎侧的蒙皮通常只保留有限根影响骨，"
        "超出的权重会被截断，导致 DCC 预览与运行时结果不一致。",
    TOOL_ID + ".skin_weight_sum":
        "部分顶点的权重和不是 1。蒙皮会整体偏胖或偏瘦，"
        "严重时在引擎里出现塌陷或拉丝。",
    TOOL_ID + ".legacy_reference":
        "场景里残留了旧版 reference。它可能带来隐藏命名空间、重复节点，"
        "以及保存时无法解析的外部路径。",
    TOOL_ID + ".control_uncolored":
        "控制器没有设置颜色。这不会影响导出，但会降低动画师区分左右侧"
        "与功能分组的效率。",
}

_AI_NOTE = "AI 只负责解释与建议，判定结果完全来自确定性规则。"


def info() -> Dict[str, Any]:
    """插件自描述，供工具面板与菜单展示。"""
    return {
        "id": TOOL_ID,
        "name": "Rig Health Guard 场景体检",
        "version": "0.1.0",
        "dcc": ["maya", "max", "mobu", "unreal"],
        "capabilities": ["scene.read", "report.render", "ai.explain"],
        "description": "只读检查绑定场景健康度，输出可定位、可 dry-run 的修复建议。",
    }


def run_report(context: Any) -> Any:
    """运行体检并返回 ``tak.core.report.Report``。

    ``context`` 为 ``tak.core.runner.ToolContext``。两种作用域：

    * 默认 ``plugin_only``：只跑本插件的专项规则，便于单测与隔离排查；
    * ``options["include_builtin"] = True``（UI / 钩子默认使用）：连同
      ``tak.core.rules.builtin`` 的规范规则一起跑，得到完整体检结论。

    无论哪种作用域，判定都来自确定性规则，AI 只写解释层。
    """
    from tak.core.rules.engine import run_rules

    started = time.time()
    options = getattr(context, "options", None) or {}
    include_builtin = bool(options.get("include_builtin", False))
    scope = None if include_builtin else TOOL_ID
    report = run_rules(context.rule_context(), title=TITLE, plugin_id=scope)
    report.metadata["scope"] = "all" if include_builtin else "plugin_only"

    # 数据源缺失 ≠ 场景健康。9 条插件规则全部依赖 metadata["health"]，取不到就
    # 整组跳过；若这里不发声，菜单点开会显示「通过 / 0 条结论」，而事实上一条
    # 规则都没跑。用 INFO 而不是 WARNING：不改判定、不阻断流程，但让结论列表
    # 里能看见「本次没检查」。
    health_state = _health_state(getattr(context, "snapshot", None))
    if not health_state["present"]:
        from tak.core.rules.base import Finding, Severity

        if include_builtin:
            _message = ("当前快照未提供 metadata.health，体检专项规则未执行"
                        "（内置规范规则已跑）；场景层面的问题不会被发现。")
        else:
            _message = ("当前快照未提供 metadata.health，本次未执行体检规则；"
                        "结论为空并不代表场景健康。")
        report.findings.append(Finding(
            rule_id=TOOL_ID + ".datasource.missing",
            severity=Severity.INFO,
            message=_message,
            fix_hint="由适配器在采集阶段补充 health 数据，或改用 "
                     "tak.core.runner.evaluate() 走内置规范规则。",
            data={"health_section": health_state, "scope": report.metadata["scope"]},
        ))
    findings = list(report.findings)

    explanations, ai_meta = _build_explanations(findings)

    report.metadata["rules_count"] = len(report.metadata.get("rule_ids", []))
    report.metadata["duration_ms"] = round((time.time() - started) * 1000.0, 2)
    report.metadata["ai_explanations"] = explanations
    report.metadata["ai"] = ai_meta
    report.metadata["history"] = {
        "linked": False,
        "tickets": [],
        "note": "预留接口：接入历史体检库/工单后，可按场景指纹关联历史问题与修复记录；"
                "当前仅占位，不发起任何外部请求。",
    }
    report.metadata["health_section"] = health_state
    return report


# --------------------------------------------------------------------------
# AI 解释层（只读、可降级）
# --------------------------------------------------------------------------
def _build_explanations(findings: List[Any]) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """为 ERROR/BLOCKER 逐条生成解释，返回 (解释列表, ai 元信息)。

    任何异常都在本函数内消化：AI 层不可用绝不阻断体检报告。
    """
    blocked = [item for item in findings if _severity(item) in _BLOCKING]
    meta: Dict[str, Any] = {
        "offline": True,
        "model": "local-template",
        "routed_model": "",
        "degraded": "",
        "note": _AI_NOTE,
    }
    if not blocked:
        meta["degraded"] = "无阻断级问题，未调用 AI 解释层"
        return [], meta

    provider = None
    try:
        from tak.core.ai import get_provider, provider_for_task

        routed = provider_for_task("explain")
        meta["routed_model"] = routed
    except Exception as exc:  # 导入失败也要能出报告
        meta["degraded"] = "AI 模块不可用: %s: %s" % (type(exc).__name__, exc)
        return [_local_explanation(item) for item in blocked], meta

    try:
        provider = get_provider(task="explain")
    except Exception as exc:
        meta["degraded"] = "provider 初始化失败，已降级为本地模板: %s: %s" % (
            type(exc).__name__, exc)
        return [_local_explanation(item) for item in blocked], meta

    model = str(getattr(provider, "model", "") or "")
    offline = model.lower() in ("mock", "offline", "")
    meta["offline"] = offline
    meta["model"] = model or "local-template"

    results: List[Dict[str, Any]] = []
    for item in blocked:
        explanation = _local_explanation(item)
        if not offline:
            try:
                reply = provider.chat(_messages(item))
                if isinstance(reply, str) and reply.strip():
                    explanation["explanation"] = reply.strip()
                    explanation["model"] = model
            except Exception as exc:
                # 一次失败即整体降级，避免对每条 finding 反复超时。
                meta["offline"] = True
                meta["degraded"] = "provider 调用失败，已降级为本地模板: %s: %s" % (
                    type(exc).__name__, exc)
                offline = True
        results.append(explanation)
    return results, meta


def _local_explanation(finding: Any) -> Dict[str, Any]:
    """确定性模板解释：离线、可复现，不依赖任何网络。"""
    rule_id = str(getattr(finding, "rule_id", ""))
    node = str(getattr(finding, "node", "") or "")
    message = str(getattr(finding, "message", "") or "")
    plain = _PLAIN_EXPLANATION.get(rule_id)
    if plain:
        text = "%s 定位节点：%s。" % (plain, node or "场景级（无具体节点）")
    else:
        text = "规则 %s 报告：%s。定位节点：%s。" % (
            rule_id or "未知规则", message or "无详细信息",
            node or "场景级（无具体节点）")
    return {
        "rule_id": rule_id,
        "severity": _severity(finding),
        "node": node,
        "explanation": text,
        "suggestion": _suggestion(finding),
        "model": "local-template",
    }


def _suggestion(finding: Any) -> str:
    """把 fix_hint 与结构化 fix 拼成 dry-run 建议（只描述，不执行）。"""
    hint = str(getattr(finding, "fix_hint", "") or "").strip()
    data = getattr(finding, "data", None)
    fix = data.get("fix") if isinstance(data, dict) else None
    parts = ["dry-run 建议（不会自动改写场景）"]
    parts.append(hint or "请在 DCC 中手动检查该节点")
    if isinstance(fix, dict) and fix.get("op"):
        parts.append("候选动作 %s（需人工确认后执行）" % fix.get("op"))
    return parts[0] + "：" + "；".join(parts[1:])


def _messages(finding: Any) -> List[Dict[str, str]]:
    return [
        {"role": "system",
         "content": "你是绑定技术美术助手，只解释体检问题并给出 dry-run 建议，"
                    "不参与判定，不生成 DCC 脚本。"},
        {"role": "user",
         "content": "规则=%s；节点=%s；问题=%s；现有建议=%s" % (
             getattr(finding, "rule_id", ""), getattr(finding, "node", ""),
             getattr(finding, "message", ""), getattr(finding, "fix_hint", ""))},
    ]


def _severity(finding: Any) -> str:
    value = getattr(finding, "severity", "")
    return value.value if hasattr(value, "value") else str(value)


def _health_state(snapshot: Any) -> Dict[str, Any]:
    if hasattr(snapshot, "to_dict") and not isinstance(snapshot, dict):
        try:
            snapshot = snapshot.to_dict()
        except Exception:
            snapshot = {}
    data = snapshot if isinstance(snapshot, dict) else {}
    metadata = data.get("metadata")
    health = metadata.get("health") if isinstance(metadata, dict) else None
    present = isinstance(health, dict)
    return {
        "present": present,
        "keys": sorted(str(key) for key in health) if present else [],
        "note": "" if present else "快照未提供 metadata.health，插件规则整组跳过。",
    }
