# -*- coding: utf-8 -*-
"""IK Retarget Guard 编排层：纯数据检查、Report 与 P5.2 交接。

UE IK Retarget 数据契约（后续工具与文档统一引用本段）
====================================================

数据从 ``ctx.snapshot["metadata"]["retarget"]`` 或 ``ctx.options["retarget"]``
读取；两者同时存在时 **options.retarget 优先**。结构如下（所有字段均可缺省）::

    {
      "ik_rig": {
        "chains": [{"name": "LeftArm", "bones": ["upperarm_L", "lowerarm_L"]}],
        "retarget_root": "root",
        "goal": {}
      },
      "source_bones": ["root", "pelvis", "upperarm_L"],
      "target_bones": ["root", "pelvis", "LeftUpArm"],
      "retargeter": {
        "chain_mapping": {"LeftArm": "LeftArm"},
        "root_offset": [0, 0, 0],
        "pelvis_offset": [0, 0, 0]
      },
      "pose_deviation": {"max": 0.02, "per_bone": {"hand_L": 0.01}},
      "required_chains": ["Spine", "LeftArm", "RightArm", "LeftLeg", "RightLeg"],
      "thresholds": {
        "root_offset": 0.01, "pelvis_offset": 0.01, "pose_deviation": 0.01,
        "pose_per_bone": {"hand_L": 0.005}
      }
    }

也接受 ``root_offset_max`` / ``pelvis_offset_max`` / ``pose_deviation_max`` 直配，
以及 ``pose_deviation_thresholds.per_bone``。规范侧对应项为
``spec.ue.retarget_<字段>_tolerance``、``retarget_required_chains`` 与
``retarget_pose_per_bone_tolerances``。Root/Pelvis 与姿态容差默认值均为 0.01；
必需链默认 Spine、LeftArm、RightArm、LeftLeg、RightLeg。

缺失策略
--------
**字段缺失一律跳过对应规则，绝不臆造数值。** 数据整体缺失时六条规则全部跳过；
``pose_deviation.per_bone`` 的数值是实测偏差，不是阈值。UE5.8 真机虽已确认
``IKRigDefinition`` / ``IKRetargeter`` / ``ControlRig`` 有 Python 暴露，本插件仍只读
快照，不 import unreal，资产采集由 adapter / 调用方负责。

纯函数返回契约
--------------
``inspect_retarget(data, spec=None)`` 返回::

    {"passed": bool, "findings": [Finding.to_dict(), ...],
     "diffs": [{"field": "source_bone_only"|"target_bone_only", ...}],
     "mapping": suggest_mapping(...) 或空映射,
     "summary": {"total": int, "counts": {...}, "passed": bool, ...}}

``run_report`` 额外把 mapping 摘要、链映射表与 ``handoff`` 写入 metadata。
``handoff`` 指向 ``mapping.write_mapping_asset`` / ``load_mapping_asset`` 的版本化
JSON 契约，P5.2 Skeleton Mapper 可消费 entries 并保留 conflicts 供人工复核。
"""
from __future__ import annotations

import sys
from typing import Any, Dict, List, Optional, Tuple

TOOL_ID = "ik_retarget_guard"
TITLE = "IK Retarget Guard 重定向映射校验"
VERSION = "0.1.0"
_SEVERITIES = ("blocker", "error", "warning", "info")


def info() -> Dict[str, Any]:
    """返回插件自描述，供工具面板与菜单使用。"""
    return {
        "id": TOOL_ID, "name": TITLE, "version": VERSION,
        "dcc": ["unreal"], "min_version": {"unreal": "5.8"},
        "capabilities": ["scene.read", "skeleton.mapping", "retarget.validate",
                         "mapping.asset", "report.render"],
        "permissions": ["scene.read"],
        "description": "离线校验 IK Rig / IK Retargeter 映射与姿态偏差，不修改 UE 资产。",
        "rule_count": 6,
    }


def describe_data_contract() -> Dict[str, Any]:
    """返回机器可读的数据契约，供 P5.2、Web 面板与文档直接消费。"""
    return {
        "sources": ["snapshot.metadata.retarget", "options.retarget"],
        "priority": "options.retarget 优先；缺失时回落到 snapshot.metadata.retarget",
        "missing_policy": "字段缺失时对应规则整条跳过，绝不臆造数值或产生误报",
        "defaults": {"root_offset": 0.01, "pelvis_offset": 0.01,
                     "pose_deviation": 0.01,
                     "required_chains": ["Spine", "LeftArm", "RightArm",
                                         "LeftLeg", "RightLeg"]},
        "fields": {
            "ik_rig.chains": {"type": "list[object]", "required": False,
                              "note": "链对象含 name / bones；用于完整性与必需链校验"},
            "ik_rig.retarget_root": {"type": "str", "required": False,
                                     "note": "重定向根骨；缺失不推断"},
            "ik_rig.goal": {"type": "object", "required": False,
                            "note": "可选 IK Goal 采集数据，本批次只透传不判定"},
            "source_bones": {"type": "list[str]", "required": False,
                             "note": "源骨架骨骼名；与 target_bones 共同启用命名映射"},
            "target_bones": {"type": "list[str]", "required": False,
                             "note": "目标骨架骨骼名；与 source_bones 共同启用命名映射"},
            "retargeter.chain_mapping": {"type": "object|list", "required": False,
                                         "note": "源链到目标链；列表形式可保留重复 source 以检测冲突"},
            "retargeter.root_offset": {"type": "[float,float,float]", "required": False,
                                       "note": "Root 补偿偏移，按向量长度比较阈值"},
            "retargeter.pelvis_offset": {"type": "[float,float,float]", "required": False,
                                         "note": "Pelvis 补偿偏移，按向量长度比较阈值"},
            "pose_deviation.max": {"type": "float", "required": False,
                                   "note": "重定向后的全局最大实测偏差"},
            "pose_deviation.per_bone": {"type": "object[str,float]", "required": False,
                                        "note": "逐骨骼实测偏差；阈值由 thresholds 或 spec 提供"},
            "required_chains": {"type": "list[str]", "required": False,
                                "note": "覆盖默认可需 IK 链；空列表表示不强制"},
            "thresholds": {"type": "object", "required": False,
                           "note": "root_offset / pelvis_offset / pose_deviation / pose_per_bone"},
        },
        "example": {
            "ik_rig": {"chains": [{"name": "LeftArm",
                                   "bones": ["upperarm_L", "lowerarm_L", "hand_L"]}],
                       "retarget_root": "root", "goal": {}},
            "source_bones": ["upperarm_L", "lowerarm_L", "hand_L"],
            "target_bones": ["LeftUpArm", "LeftLowerArm", "LeftHand"],
            "retargeter": {"chain_mapping": {"LeftArm": "LeftArm"},
                           "root_offset": [0, 0, 0], "pelvis_offset": [0, 0, 0]},
            "pose_deviation": {"max": 0.02, "per_bone": {"hand_L": 0.01}},
        },
        "handoff": "mapping asset v1.0 的 entries/conflicts 可由 P5.2 Skeleton Mapper 直接消费",
    }


def _mapping_module() -> Any:
    """通过注册表按路径加载 mapping.py，兼容插件目录不在 sys.path 的约束。"""
    name = "tak_plugin_%s_mapping" % TOOL_ID
    module = sys.modules.get(name)
    if module is not None:
        return module
    from tak.core import registry, runner
    return registry.load_plugin_module(runner.find_plugin(TOOL_ID), "mapping")


def _ensure_rules() -> None:
    """直接调用纯函数时也确保规则注册；runner 缓存避免重复注册。"""
    from tak.core import runner
    runner.load_tool_modules(runner.find_plugin(TOOL_ID))


def _empty_mapping() -> Dict[str, Any]:
    return {"entries": [], "unmapped_source": [], "unmapped_target": [], "conflicts": []}


def _bone_diffs(data: Dict[str, Any], mapping: Dict[str, Any]) -> List[Dict[str, Any]]:
    """输出精确的集合差异；候选仅作建议，不改写原始骨骼名。"""
    if not isinstance(data.get("source_bones"), (list, tuple)) \
            or not isinstance(data.get("target_bones"), (list, tuple)):
        return []
    sources = {item.strip() for item in data.get("source_bones")
               if isinstance(item, str) and item.strip()}
    targets = {item.strip() for item in data.get("target_bones")
               if isinstance(item, str) and item.strip()}
    candidates = {item["source"]: item["target"] for item in mapping["entries"]}
    reverse = {item["target"]: item["source"] for item in mapping["entries"]}
    diffs: List[Dict[str, Any]] = []
    for name in sorted(sources - targets):
        diffs.append({"field": "source_bone_only", "bone": name,
                      "candidate": candidates.get(name, "")})
    for name in sorted(targets - sources):
        diffs.append({"field": "target_bone_only", "bone": name,
                      "candidate": reverse.get(name, "")})
    return diffs


def inspect_retarget(data: Any, spec: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """离线检查一份 retarget 数据，返回严格 JSON 可序列化字典。

    ``data`` 是模块 docstring 所述的 retarget 段，不是完整快照；``None``、空字典、
    非字典及缺字段输入均安全处理。函数不修改输入，不访问网络或真实 DCC。
    """
    from tak.core.rules.engine import RuleContext, run_rules

    _ensure_rules()
    retarget = data if isinstance(data, dict) else {}
    snapshot = {"metadata": {"retarget": retarget}}
    context = RuleContext(snapshot=snapshot,
                          spec=dict(spec) if isinstance(spec, dict) else None,
                          dcc="unreal")
    report = run_rules(context, title=TITLE, plugin_id=TOOL_ID)
    findings = [item.to_dict() for item in report.findings]
    if isinstance(retarget.get("source_bones"), (list, tuple)) \
            and isinstance(retarget.get("target_bones"), (list, tuple)):
        mapping = _mapping_module().suggest_mapping(
            retarget.get("source_bones"), retarget.get("target_bones"))
    else:
        mapping = _empty_mapping()
    diffs = _bone_diffs(retarget, mapping)
    counts = {name: 0 for name in _SEVERITIES}
    for item in findings:
        severity = str(item.get("severity") or "info")
        counts[severity] = counts.get(severity, 0) + 1
    passed = counts["blocker"] == 0 and counts["error"] == 0
    return {
        "passed": passed,
        "findings": findings,
        "diffs": diffs,
        "mapping": mapping,
        "summary": {
            "total": len(findings), "counts": counts, "passed": passed,
            "mapped": len(mapping["entries"]),
            "unmapped_source": len(mapping["unmapped_source"]),
            "unmapped_target": len(mapping["unmapped_target"]),
            "conflicts": len(mapping["conflicts"]),
            "diffs": len(diffs), "retarget_present": bool(retarget),
        },
    }


def _retarget_data(ctx: Any) -> Dict[str, Any]:
    options = getattr(ctx, "options", None)
    if isinstance(options, dict) and isinstance(options.get("retarget"), dict):
        return options["retarget"]
    snapshot = getattr(ctx, "snapshot", None)
    if hasattr(snapshot, "to_dict") and not isinstance(snapshot, dict):
        try:
            snapshot = snapshot.to_dict()
        except Exception:
            snapshot = {}
    metadata = snapshot.get("metadata") if isinstance(snapshot, dict) else None
    if isinstance(metadata, dict) and isinstance(metadata.get("retarget"), dict):
        return metadata["retarget"]
    return {}


def _chain_mapping_rows(data: Dict[str, Any]) -> List[Dict[str, str]]:
    rig = data.get("ik_rig")
    retargeter = data.get("retargeter")
    chains = rig.get("chains") if isinstance(rig, dict) else None
    raw = retargeter.get("chain_mapping") if isinstance(retargeter, dict) else None
    if not isinstance(chains, (list, tuple)) and raw is None:
        return []
    known = {item.get("name").strip() for item in chains or []
             if isinstance(item, dict) and isinstance(item.get("name"), str)
             and item.get("name").strip()}
    pairs: List[Tuple[str, str]] = []
    if isinstance(raw, dict):
        pairs = [(str(source), str(target)) for source, target in raw.items()]
    elif isinstance(raw, (list, tuple)):
        for item in raw:
            if isinstance(item, dict):
                pairs.append((str(item.get("source") or ""), str(item.get("target") or "")))
    mapped = {source: target for source, target in pairs if source}
    rows: List[Dict[str, str]] = []
    for source in sorted(known | set(mapped)):
        target = mapped.get(source, "")
        if source not in known:
            status = "unknown_source"
        elif target:
            status = "mapped"
        else:
            status = "missing_mapping"
        rows.append({"source_chain": source, "target_chain": target, "status": status})
    return rows


def _mapping_summary(mapping: Dict[str, Any]) -> Dict[str, Any]:
    entries = mapping.get("entries") or []
    confidences = [float(item.get("confidence") or 0.0) for item in entries
                   if isinstance(item, dict)]
    return {
        "entries": len(entries),
        "average_confidence": (round(sum(confidences) / len(confidences), 4)
                               if confidences else 0.0),
        "unmapped_source": len(mapping.get("unmapped_source") or []),
        "unmapped_target": len(mapping.get("unmapped_target") or []),
        "conflicts": len(mapping.get("conflicts") or []),
    }


def run_report(ctx: Any) -> Any:
    """把 ``inspect_retarget`` 的结果转换为统一 Report，并附 P5.2 handoff。"""
    from tak.core.report import Report
    from tak.core.rules import Finding, Severity
    from tak.core.rules.engine import list_rules

    data = _retarget_data(ctx)
    spec = getattr(ctx, "spec", None)
    spec = spec if isinstance(spec, dict) else {}
    inspected = inspect_retarget(data, spec)
    findings: List[Any] = []
    for item in inspected["findings"]:
        findings.append(Finding(
            rule_id=str(item.get("rule_id") or ""),
            severity=Severity(str(item.get("severity") or "info")),
            message=str(item.get("message") or ""), node=str(item.get("node") or ""),
            fix_hint=str(item.get("fix_hint") or ""),
            data=dict(item.get("data") or {})))
    report = Report(title=TITLE, dcc=str(getattr(ctx, "dcc", "unreal") or "unreal"),
                    findings=findings, spec_version=str(spec.get("version", "1.0.0")))
    catalog = [item["id"] for item in list_rules("unreal")
               if item.get("plugin_id") == TOOL_ID]
    report.metadata.update({
        "rule_ids": sorted(set(catalog)),
        "rules_count": len(set(catalog)),
        "retarget_present": bool(data),
        "summary": inspected["summary"],
        "diffs": inspected["diffs"],
        "mapping": inspected["mapping"],
        "mapping_summary": _mapping_summary(inspected["mapping"]),
        "chain_mapping": _chain_mapping_rows(data),
        "data_contract": describe_data_contract(),
        "handoff": {
            "consumer": "P5.2 Skeleton Mapper",
            "asset_version": "1.0",
            "writer": "tools/ik_retarget_guard/mapping.py:write_mapping_asset",
            "reader": "tools/ik_retarget_guard/mapping.py:load_mapping_asset",
            "entries": "mapping.entries（source/target/confidence/method）",
            "review_queue": "mapping.conflicts + mapping.unmapped_source/unmapped_target",
            "targets": ["UE IK Retargeter", "MotionBuilder Characterization",
                        "3ds Max Biped"],
            "note": "P5.2 可补充 AI 候选，但确定性规则与本资产格式保持不变；冲突须人工确认。",
        },
    })
    return report
