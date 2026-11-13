# -*- coding: utf-8 -*-
"""Skeleton Mapper 插件编排层（P5.2）。

只消费 ``tak.core.mapping`` 的确定性纯函数，绝不 import DCC API、绝不写宿主
场景、绝不写文件。``run_report`` 把冲突 / 未解析项翻译成 Finding 并产出统一
Report；``plan`` 只产出候选，不落盘。

数据来源（优先级）
==================
1. ``ctx.options["source_bones"]`` / ``ctx.options["target_bones"]`` 直接指定。
2. 否则回落到 ``ctx.snapshot["joints"]``（当前场景骨架，作为 source）与
   ``ctx.spec["skeleton"]["required_joints"]``（规范必需关节，作为 target）。

options 里的 ``use_ai`` / ``mapping_asset`` / ``source_dcc`` / ``target_dcc`` /
``provider`` 会原样透传给 ``map_skeleton``。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from tak.core.mapping import map_skeleton

TOOL_ID = "skeleton_mapper"
TITLE = "Skeleton Mapper 骨骼语义映射"
VERSION = "0.1.0"


def info() -> Dict[str, Any]:
    """返回插件自描述，供工具面板与菜单使用。"""
    return {
        "id": TOOL_ID, "name": TITLE, "version": VERSION,
        "dcc": ["maya", "max", "mobu", "unreal"],
        "min_version": {"maya": "2023", "max": "2023", "mobu": "2023",
                        "unreal": "5.8"},
        "capabilities": ["scene.read", "skeleton.mapping", "mapping.asset",
                         "semantic.mapping", "report.render"],
        "permissions": ["scene.read"],
        "description": "确定性骨骼语义映射，冲突与低置信度项必须人工确认，不写宿主场景。",
        "rule_count": 0,
    }


def _names(value: Any) -> List[str]:
    if isinstance(value, dict):
        value = list(value.keys())
    if not isinstance(value, (list, tuple, set, frozenset)):
        return []
    return sorted({item.strip() for item in value
                   if isinstance(item, str) and item.strip()})


def _options(ctx: Any) -> Dict[str, Any]:
    value = getattr(ctx, "options", None)
    return dict(value) if isinstance(value, dict) else {}


def _snapshot(ctx: Any) -> Dict[str, Any]:
    value = getattr(ctx, "snapshot", None)
    if isinstance(value, dict):
        return value
    if hasattr(value, "to_dict"):
        try:
            converted = value.to_dict()
            if isinstance(converted, dict):
                return converted
        except Exception:
            return {}
    return {}


def _spec(ctx: Any) -> Dict[str, Any]:
    value = getattr(ctx, "spec", None)
    return dict(value) if isinstance(value, dict) else {}


def _required_joints(spec: Dict[str, Any], options: Dict[str, Any]) -> List[str]:
    for key in ("required_joints", "target_bones"):
        value = options.get(key)
        if isinstance(value, (list, tuple)):
            return _names(value)
    skeleton = spec.get("skeleton")
    if isinstance(skeleton, dict):
        return _names(skeleton.get("required_joints"))
    return []


def _bones(ctx: Any) -> Tuple[List[str], List[str]]:
    """解析源/目标骨骼；options 优先，否则 snapshot.joints + spec 必需关节。"""
    options = _options(ctx)
    snapshot = _snapshot(ctx)
    spec = _spec(ctx)
    joints = _names(snapshot.get("joints")) if isinstance(snapshot, dict) else []
    required = _required_joints(spec, options)

    source = options.get("source_bones")
    if not isinstance(source, (list, tuple, set, frozenset, dict)):
        source = joints or required
    target = options.get("target_bones")
    if not isinstance(target, (list, tuple, set, frozenset, dict)):
        target = required or joints
    return _names(source), _names(target)


def _map(ctx: Any) -> Dict[str, Any]:
    options = _options(ctx)
    source, target = _bones(ctx)
    return map_skeleton(
        source, target,
        mapping_asset=options.get("mapping_asset"),
        options=options,
        provider=options.get("provider"),
    )


def plan(ctx: Any) -> Dict[str, Any]:
    """只产出候选映射，不写任何文件、不修改宿主场景。"""
    return _map(ctx)


def _conflict_finding(item: Dict[str, Any]) -> Any:
    from tak.core.rules import Finding, Severity

    sources = item.get("sources") or ([item.get("source")]
                                      if item.get("source") else [])
    targets = item.get("targets") or ([item.get("target")]
                                      if item.get("target") else [])
    node = str(item.get("source") or (sources[0] if sources else "")
               or item.get("target") or "")
    return Finding(
        rule_id=TOOL_ID + ".conflict",
        severity=Severity.ERROR,
        message=str(item.get("message") or "映射存在冲突，需人工确认"),
        node=node,
        fix_hint="在 DCC 内人工核对骨骼语义后手动确认，绝不自动选择",
        data={"type": str(item.get("type") or "conflict"),
              "canonical": str(item.get("canonical") or ""),
              "sources": [str(value) for value in sources],
              "targets": [str(value) for value in targets]},
    )


def _unresolved_finding(item: Dict[str, Any]) -> Any:
    from tak.core.rules import Finding, Severity

    bone = str(item.get("bone") or "")
    reason = str(item.get("reason") or "unmapped")
    label = "低置信度需人工确认" if reason == "low_confidence" else "未解析骨骼"
    return Finding(
        rule_id=TOOL_ID + ".unresolved",
        severity=Severity.WARNING,
        message="%s：%s（%s）" % (label, bone, item.get("side") or ""),
        node=bone,
        fix_hint="补齐映射或由人工确认后再应用到下游 IK Retargeter",
        data={"bone": bone, "side": str(item.get("side") or ""),
              "semantic": str(item.get("semantic") or ""),
              "reason": reason, "target": str(item.get("target") or "")},
    )


def run_report(ctx: Any) -> Any:
    """跑骨骼语义映射，返回统一 Report；绝不写宿主场景。"""
    from tak.core.report import Report

    mapping = _map(ctx)
    spec = _spec(ctx)
    findings: List[Any] = []
    for item in mapping["conflicts"]:
        if isinstance(item, dict):
            findings.append(_conflict_finding(item))
    for item in mapping["unresolved"]:
        if isinstance(item, dict):
            findings.append(_unresolved_finding(item))

    report = Report(
        title=TITLE,
        dcc=str(getattr(ctx, "dcc", "maya") or "maya"),
        findings=findings,
        spec_version=str(spec.get("version", "1.0.0")),
    )
    metadata = mapping.get("metadata") or {}
    report.metadata.update({
        "tool_id": TOOL_ID,
        "mapping": mapping,
        "stats": mapping["stats"],
        "warnings": mapping["warnings"],
        "citations": metadata.get("citations", []),
        "consumers": metadata.get("consumers", []),
        "handoff": metadata.get("handoff", {}),
        "entries": len(mapping["entries"]),
        "conflicts": len(mapping["conflicts"]),
        "unresolved": len(mapping["unresolved"]),
        "source_dcc": mapping.get("source_dcc", ""),
        "target_dcc": mapping.get("target_dcc", ""),
    })
    return report
