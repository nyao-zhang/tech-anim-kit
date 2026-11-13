# -*- coding: utf-8 -*-
"""Export Bridge 的核心逻辑（纯函数，无 DCC / 无 UI 依赖）。

导出前后各留一份快照，diff 出偏差再决定是否放行，是避免「DCC 里看着对、
进引擎就错位」的最低成本手段。本模块只做三件事：

1. ``pre_export``  —— 把导出前快照写成 sidecar（骨架 JSON）；
2. ``verify_pair`` —— 对比 before / after 快照并分类偏差；
3. ``post_export`` —— 读 sidecar + verify_pair，产出可渲染结果。

所有函数都可在无宿主环境下直接单测；耗时的宿主调用（真正的 FBX 导出）
由 adapter 负责，本模块从不 import maya / pymxs / pyfbsdk / unreal。

UE 侧导入校验**不在本插件实现**：``ue_import_hook`` 只返回 delegated 占位，
把 sidecar 路径交接给 P3.2 的 ``ue_import_guard``。
"""
from __future__ import annotations

import json
import math
import os
from typing import Any, Dict, List, Optional, Tuple

from tak.core.diff import SkeletonSnapshot, diff_snapshots

TOOL_ID = "export_bridge"
TITLE = "Export Bridge 导出前后校验"
VERSION = "0.1.0"

SIDECAR_VERSION = 1
SIDECAR_SUFFIX = ".tak_snapshot.json"
UE_TARGET = "ue_import_guard"

# diff_snapshots 的 field 与本插件的偏差类型一一对应。
DIFF_TYPES = ("unit", "up_axis", "joint_count", "joint_missing", "joint_added",
              "joint_renamed", "bind_matrix", "parent", "frame_range")

_DEFAULT_TOLERANCE = 0.0001

_MESSAGES = {
    "unit": "导出前后场景单位不一致：%s → %s",
    "up_axis": "导出前后 up_axis 不一致：%s → %s",
    "joint_count": "导出前后关节数量不一致：%s → %s",
    "joint_missing": "关节 %s 在导出后丢失",
    "joint_added": "导出后多出关节 %s",
    "joint_renamed": "关节被重命名：%s → %s",
    "bind_matrix": "关节 %s 的绑定矩阵偏差超出容差",
    "parent": "关节 %s 的父级发生变化：%s → %s",
    "frame_range": "导出前后帧范围不一致：%s → %s",
}


def info() -> Dict[str, Any]:
    """插件自描述，供工具面板与菜单展示。"""
    return {
        "id": TOOL_ID,
        "name": "Export Bridge 导出前后校验",
        "version": VERSION,
        "dcc": ["maya", "max", "mobu", "unreal"],
        "capabilities": ["scene.read", "export.fbx", "diff.verify", "report.render"],
        "description": "sidecar 快照 + 导出前后 diff，阻断性偏差拦下导出。",
    }


# --------------------------------------------------------------------------
# 导出前后对比
# --------------------------------------------------------------------------
def verify_pair(before: Any, after: Any, spec: Optional[Dict[str, Any]] = None,
                matrix_tolerance: Optional[float] = _DEFAULT_TOLERANCE) -> Dict[str, Any]:
    """对比导出前后快照，输出分类后的偏差与阻断判定。

    - ``before`` / ``after`` 支持 dict、``SkeletonSnapshot`` 或 None；
    - ``matrix_tolerance`` 为 None 时回退到 spec 的
      ``export.fbx.verify.matrix_tolerance``，再回退到 1e-4；
    - spec 可用 ``export.fbx.verify.blocking`` / ``ignore`` 覆盖阻断字段集合。
    """
    tolerance = _tolerance(spec, matrix_tolerance)
    raw_diffs = diff_snapshots(before, after, tolerance)
    blocking_fields, ignored_fields = _field_policy(spec)
    diffs: List[Dict[str, Any]] = []
    blocking: List[Dict[str, Any]] = []
    for raw in raw_diffs:
        field = str(raw.get("field") or "")
        if field in ignored_fields:
            continue
        item = _diff_item(raw, field, field in blocking_fields)
        diffs.append(item)
        if item["blocking"]:
            blocking.append(item)

    if not diffs:
        reason = "导出前后快照一致，允许继续"
    elif blocking:
        reason = "检测到 %d 项阻断性偏差（%s），应阻断导出" % (
            len(blocking), "、".join(_types(blocking)))
    else:
        reason = "检测到 %d 项非阻断偏差（%s），仅提示" % (
            len(diffs), "、".join(_types(diffs)))
    return {
        "passed": not blocking,
        "diffs": diffs,
        "blocking": blocking,
        "reason": reason,
        "matrix_tolerance": tolerance,
        "blocking_fields": sorted(blocking_fields),
        "ignored_fields": sorted(ignored_fields),
        "readable": True,
    }


def _diff_item(raw: Dict[str, Any], field: str, is_blocking: bool) -> Dict[str, Any]:
    item = {str(key): _json_safe(value) for key, value in raw.items()}
    item["type"] = field
    item["field"] = field
    item["blocking"] = bool(is_blocking)
    item["message"] = _message(field, raw)
    return item


def _message(field: str, raw: Dict[str, Any]) -> str:
    template = _MESSAGES.get(field)
    if template is None:
        return "导出前后字段 %r 发生变化" % field
    if field == "joint_renamed":
        return template % (raw.get("before"), raw.get("after"))
    if field == "parent":
        return template % (raw.get("joint"), raw.get("before"), raw.get("after"))
    if field in ("unit", "up_axis", "joint_count", "frame_range"):
        return template % (raw.get("before"), raw.get("after"))
    return template % (raw.get("joint"),)


def _tolerance(spec: Optional[Dict[str, Any]], given: Optional[float]) -> float:
    if given is not None:
        return float(given)
    value = _verify_spec(spec).get("matrix_tolerance")
    if isinstance(value, (int, float)) and not isinstance(value, bool) \
            and math.isfinite(float(value)) and float(value) >= 0:
        return float(value)
    return _DEFAULT_TOLERANCE


def _field_policy(spec: Optional[Dict[str, Any]]) -> Tuple[set, set]:
    """返回 (阻断字段集合, 忽略字段集合)。默认全部 diff 类型都阻断。"""
    verify = _verify_spec(spec)
    ignore = _str_set(verify.get("ignore"))
    blocking = _str_set(verify.get("blocking"))
    if not blocking:
        blocking = {item for item in DIFF_TYPES if item not in ignore}
    blocking = {item for item in blocking if item not in ignore}
    return blocking, ignore


def _verify_spec(spec: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not isinstance(spec, dict):
        return {}
    export = spec.get("export")
    fbx = export.get("fbx") if isinstance(export, dict) else None
    verify = fbx.get("verify") if isinstance(fbx, dict) else None
    return verify if isinstance(verify, dict) else {}


def _str_set(value: Any) -> set:
    if not isinstance(value, (list, tuple)):
        return set()
    return {str(item) for item in value if str(item)}


def _types(items: List[Dict[str, Any]]) -> List[str]:
    seen: List[str] = []
    for item in items:
        name = str(item.get("type") or "")
        if name and name not in seen:
            seen.append(name)
    return seen


# --------------------------------------------------------------------------
# sidecar 读写
# --------------------------------------------------------------------------
def pre_export(snapshot: Any, out_path: str) -> Dict[str, Any]:
    """把导出前快照写成 sidecar 骨架 JSON，返回路径与归一化后的 before 数据。

    父目录不存在时自动创建；快照先归一化成 ``SkeletonSnapshot`` 字典，
    保证 sidecar 是纯 JSON（无宿主类型、无非有限浮点）。
    """
    path = os.path.abspath(str(out_path))
    parent = os.path.dirname(path)
    if parent and not os.path.isdir(parent):
        os.makedirs(parent, exist_ok=True)
    before = normalize_snapshot(snapshot)
    payload = {
        "tool": TOOL_ID,
        "version": VERSION,
        "sidecar_version": SIDECAR_VERSION,
        "snapshot": before,
    }
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(_json_safe(payload), fh, ensure_ascii=False, indent=2,
                  sort_keys=True)
        fh.write("\n")
    return {"path": path, "before": before, "written": True,
            "sidecar_version": SIDECAR_VERSION}


def post_export(sidecar_path: str, after_snapshot: Any,
                spec: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """读 sidecar + 对比 after 快照，返回结果 dict（含可渲染的 report 数据）。"""
    path = os.path.abspath(str(sidecar_path))
    before, error = _read_sidecar(path)
    if error:
        result: Dict[str, Any] = {
            "passed": False, "diffs": [], "blocking": [],
            "reason": error, "readable": False, "sidecar": path,
            "matrix_tolerance": _DEFAULT_TOLERANCE,
            "blocking_fields": sorted(DIFF_TYPES), "ignored_fields": [],
        }
        result["findings"] = [item.to_dict() for item in _findings(result)]
        result["report"] = _report_payload(result)
        return result

    verify = verify_pair(before, after_snapshot, spec)
    verify["sidecar"] = path
    verify["sidecar_version"] = SIDECAR_VERSION
    verify["readable"] = True
    verify["before_summary"] = _summary(before)
    verify["after_summary"] = _summary(normalize_snapshot(after_snapshot))
    verify["findings"] = [item.to_dict() for item in _findings(verify)]
    verify["report"] = _report_payload(verify)
    return verify


def _read_sidecar(path: str) -> Tuple[Dict[str, Any], str]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            payload = json.load(fh)
    except (OSError, ValueError) as exc:
        return {}, "sidecar 不可读（%s: %s）" % (type(exc).__name__, exc)
    snapshot = payload.get("snapshot") if isinstance(payload, dict) else None
    if not isinstance(snapshot, dict):
        return {}, "sidecar 缺少 snapshot 对象: %s" % path
    return snapshot, ""


def normalize_snapshot(snapshot: Any) -> Dict[str, Any]:
    """把 dict / SkeletonSnapshot / None 归一化成纯 JSON 的骨架快照字典。"""
    if hasattr(snapshot, "to_dict") and not isinstance(snapshot, dict):
        try:
            snapshot = snapshot.to_dict()
        except Exception:
            snapshot = {}
    data = snapshot if isinstance(snapshot, dict) else {}
    return _json_safe(SkeletonSnapshot.from_dict(data).to_dict())


def _summary(snapshot: Dict[str, Any]) -> Dict[str, Any]:
    joints = snapshot.get("joints")
    return {
        "unit": snapshot.get("unit"),
        "up_axis": snapshot.get("up_axis"),
        "frame_range": _json_safe(snapshot.get("frame_range")),
        "joint_count": len(joints) if isinstance(joints, dict) else 0,
    }


# --------------------------------------------------------------------------
# 交接给 UE 侧（占位，不假称已实现）
# --------------------------------------------------------------------------
def ue_import_hook(sidecar: Any) -> Dict[str, Any]:
    """UE 侧导入校验的交接占位：只声明委托目标，不实现 UE 逻辑。"""
    return {
        "status": "delegated",
        "target": UE_TARGET,
        "sidecar": _sidecar_ref(sidecar),
        "implemented": False,
        "note": "UE 侧导入校验由 P3.2 的 %s 消费本 sidecar；"
                "Export Bridge 侧不实现、也不假称已实现。" % UE_TARGET,
    }


def _sidecar_ref(sidecar: Any) -> Any:
    if isinstance(sidecar, str):
        return {"path": sidecar}
    if isinstance(sidecar, dict):
        snapshot = sidecar.get("snapshot")
        return {
            "path": sidecar.get("path"),
            "tool": sidecar.get("tool"),
            "sidecar_version": sidecar.get("sidecar_version"),
            "has_snapshot": isinstance(snapshot, dict),
        }
    return None


def sidecar_contract() -> Dict[str, Any]:
    """sidecar 文件约定，写进报告 metadata 供 P3.2 与外部工具消费。"""
    return {
        "tool": TOOL_ID,
        "suffix": SIDECAR_SUFFIX,
        "sidecar_version": SIDECAR_VERSION,
        "payload": {
            "tool": "export_bridge",
            "version": VERSION,
            "sidecar_version": SIDECAR_VERSION,
            "snapshot": "SkeletonSnapshot 字典（joints / skin / frame_range / metadata）",
        },
        "producer": "pre_export(snapshot, out_path)",
        "consumer": "post_export(sidecar_path, after_snapshot, spec=None)",
        "ue_handoff": "ue_import_hook(sidecar) → %s（P3.2 实现）" % UE_TARGET,
    }


# --------------------------------------------------------------------------
# Report 组装
# --------------------------------------------------------------------------
def run_report(ctx: Any) -> Any:
    """把 verify 结果转成 Report：阻断级偏差用 BLOCKER，提示用 INFO。"""
    from tak.core.report import Report

    result = _resolve(ctx)
    spec = getattr(ctx, "spec", None)
    spec = spec if isinstance(spec, dict) else {}
    report = Report(title=TITLE, dcc=str(getattr(ctx, "dcc", "maya") or "maya"),
                    spec_version=str(spec.get("version") or "1.0.0"))
    report.findings.extend(_findings(result))
    report.metadata["verify"] = _public_result(result)
    report.metadata["sidecar"] = sidecar_contract()
    report.metadata["ue_import"] = ue_import_hook(result.get("sidecar"))
    report.metadata["delegation"] = {
        "ue_import_guard": "P3.2",
        "note": "本插件只负责导出前后的 DCC 侧校验，不做 UE 侧资产校验。",
    }
    return report


def _resolve(ctx: Any) -> Dict[str, Any]:
    """按 options 决定对比方式；信息不足时返回「跳过」结果而非报错。"""
    spec = getattr(ctx, "spec", None)
    spec = spec if isinstance(spec, dict) else None
    options = getattr(ctx, "options", None)
    options = options if isinstance(options, dict) else {}
    snapshot = _as_dict(getattr(ctx, "snapshot", None))

    sidecar = options.get("sidecar")
    if isinstance(sidecar, str) and sidecar:
        after = options.get("after")
        return post_export(sidecar, after if after is not None else snapshot, spec)

    before = options.get("before")
    after = options.get("after")
    if before is not None and after is not None:
        return verify_pair(before, after, spec)

    before_path = options.get("before_path")
    after_path = options.get("after_path")
    if isinstance(before_path, str) and isinstance(after_path, str):
        before_data, error = _read_sidecar_file(before_path, "before")
        if error:
            return _skip_result(error, readable=False)
        after_data, error = _read_sidecar_file(after_path, "after")
        if error:
            return _skip_result(error, readable=False)
        return verify_pair(before_data, after_data, spec)

    return _skip_result("未提供 before/after 快照或 sidecar，跳过导出前后对比")


def _read_sidecar_file(path: str, label: str) -> Tuple[Dict[str, Any], str]:
    try:
        with open(os.path.abspath(path), "r", encoding="utf-8") as fh:
            payload = json.load(fh)
    except (OSError, ValueError) as exc:
        return {}, "%s 快照不可读（%s: %s）" % (label, type(exc).__name__, exc)
    if isinstance(payload, dict) and "snapshot" in payload and "joints" not in payload:
        payload = payload.get("snapshot")
    if not isinstance(payload, dict):
        return {}, "%s 快照不是 JSON 对象: %s" % (label, path)
    return payload, ""


def _skip_result(reason: str, readable: bool = True) -> Dict[str, Any]:
    result: Dict[str, Any] = {
        "passed": True, "diffs": [], "blocking": [], "reason": reason,
        "readable": readable, "skipped": True,
        "matrix_tolerance": _DEFAULT_TOLERANCE,
        "blocking_fields": sorted(DIFF_TYPES), "ignored_fields": [],
    }
    return result


def _public_result(result: Dict[str, Any]) -> Dict[str, Any]:
    """报告里只放 JSON 安全、体积可控的字段。"""
    keys = ("passed", "diffs", "blocking", "reason", "readable", "skipped",
            "matrix_tolerance", "blocking_fields", "ignored_fields",
            "sidecar", "sidecar_version", "before_summary", "after_summary")
    return {key: _json_safe(result[key]) for key in keys if key in result}


def _findings(result: Dict[str, Any]) -> List[Any]:
    """把对比结果翻译成 Finding（阻断用 BLOCKER，提示用 INFO）。"""
    from tak.core.rules import Finding, Severity

    findings: List[Finding] = []
    readable = result.get("readable", True)
    if not readable:
        findings.append(Finding(
            rule_id=TOOL_ID + ".sidecar_unreadable", severity=Severity.ERROR,
            message=str(result.get("reason") or "sidecar 不可读"),
            fix_hint="确认导出前已调用 pre_export()，且 sidecar 路径未被移走",
            data={"sidecar": result.get("sidecar")}))
        return findings

    for item in result.get("blocking") or []:
        findings.append(Finding(
            rule_id=TOOL_ID + ".blocking_diff", severity=Severity.BLOCKER,
            message=str(item.get("message") or "导出前后快照存在阻断性偏差"),
            node=str(item.get("joint") or ""),
            fix_hint="按 diff 定位并修复后重新导出；该偏差不允许进入引擎",
            data={"diff": _json_safe(item), "field": item.get("type")}))

    blocking_set = {id(item) for item in (result.get("blocking") or [])}
    for item in result.get("diffs") or []:
        if id(item) in blocking_set:
            continue
        findings.append(Finding(
            rule_id=TOOL_ID + ".informational_diff", severity=Severity.INFO,
            message=str(item.get("message") or "导出前后存在非阻断差异"),
            node=str(item.get("joint") or ""),
            fix_hint="该字段不参与阻断判定，仅供核对",
            data={"diff": _json_safe(item), "field": item.get("type")}))

    if not result.get("diffs") and not result.get("skipped"):
        findings.append(Finding(
            rule_id=TOOL_ID + ".pair_match", severity=Severity.INFO,
            message="导出前后快照一致：%s" % (result.get("reason") or "无差异"),
            fix_hint="无需处理",
            data={"sidecar": result.get("sidecar")}))
    elif result.get("skipped"):
        findings.append(Finding(
            rule_id=TOOL_ID + ".verify_skipped", severity=Severity.INFO,
            message=str(result.get("reason") or "未提供对比数据"),
            fix_hint="通过 options 传入 before/after 快照或 sidecar 路径后再校验",
            data={}))
    return findings


def _report_payload(result: Dict[str, Any]) -> Dict[str, Any]:
    findings = [item.to_dict() if hasattr(item, "to_dict") else dict(item)
                for item in (result.get("findings") or [])]
    counts = {"blocker": 0, "error": 0, "warning": 0, "info": 0}
    for item in findings:
        severity = str(item.get("severity") or "")
        if severity in counts:
            counts[severity] += 1
    return {
        "title": TITLE,
        "passed": bool(result.get("passed", True)),
        "reason": result.get("reason", ""),
        "counts": counts,
        "findings": findings,
    }


# --------------------------------------------------------------------------
# 工具函数
# --------------------------------------------------------------------------
def _as_dict(value: Any) -> Dict[str, Any]:
    if hasattr(value, "to_dict") and not isinstance(value, dict):
        try:
            value = value.to_dict()
        except Exception:
            return {}
    return dict(value) if isinstance(value, dict) else {}


def _json_safe(value: Any) -> Any:
    """递归转成严格 JSON 可序列化结构（非有限浮点转字符串）。"""
    if isinstance(value, float):
        return value if math.isfinite(value) else str(value)
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (set, frozenset)):
        return sorted(_json_safe(item) for item in value)
    if value is None or isinstance(value, (str, int, bool)):
        return value
    return str(value)
