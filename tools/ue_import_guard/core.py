# -*- coding: utf-8 -*-
"""UE Import Guard 的编排层（无 DCC / 无 UI 依赖，可直接在 CI 中单测）。

职责
----
1. 跑本插件注册的 UE 资产规则（``rules.py``，9 条，全部只读）；
2. 用 ``validate_import`` 把 Export Bridge 写出的 sidecar 快照与 UE 侧快照
   做 diff，把阻断性偏差翻译成 Finding；
3. 在 ``report.metadata`` 里写规则数、校验摘要、数据契约与 ``api_notes``。

UE 资产数据契约（后续工具与文档统一引用本段）
=============================================

数据从 ``ctx.snapshot["metadata"]["ue"]`` 或 ``ctx.options["ue_assets"]``
读取；两者同时存在时 **options 优先**（命令行 / 测试显式注入的更可信）。
结构如下（全部字段都可缺省）::

    {
      "skeleton": {
        "bones": ["root", "pelvis", "spine_01", ...],   # 骨骼名列表
        "compatible_with": ["/Game/Characters/Default/SK_Default"],
        "root_bone": "root",
        "kind": "humanoid"                              # 可选，缺省按骨骼集合推断
      },
      "anim_sequences": [
        {
          "name": "/Game/Anim/AS_Run",
          "skeleton": "/Game/Characters/Default/SK_Default",
          "compression": "Default",
          "root_motion": false,
          "curve_count": 2,
          "curves": ["run_phase", "lean"]
        }
      ],
      "skeletal_meshes": [
        {
          "name": "/Game/Characters/SKM_Body",
          "material_slots": 3,                          # 也接受 ["M_Body", "M_Face"]
          "lods": [{"allow_deformer": false}]
        }
      ],
      "import": {"sidecar": {"snapshot": {...}}}        # 也接受直接是快照 dict
    }

缺失策略
--------
**字段缺失一律跳过对应规则，绝不臆造数值。** 这也是 golden fixtures
（只有 joints / skin / frame_range / metadata.seed）能零误报的原因。

``validate_import`` 返回契约
============================

``validate_import(sidecar, ue_snapshot, spec=None) -> dict`` 返回::

    {
      "passed": bool,          # 无阻断性偏差即为 True
      "diffs": [ {...} ],      # diff_snapshots 原始字段 + blocking / message
      "blocking": [ {...} ],   # diffs 中参与阻断的子集
      "reason": str,           # 中文结论
      "ignored": ["bind_matrix"]  # 实际被忽略的 diff 字段名
    }

``sidecar`` 可为 Export Bridge 写出的 JSON dict（含 ``"snapshot"`` 键）、
直接快照 dict、``SkeletonSnapshot`` 或 None；``ue_snapshot`` 同理。任一
侧缺少 ``joints`` 时不做对比，直接返回 ``passed=True`` 并在 reason 里注明
跳过原因——UE Python 读不到资产时不应该误判为导入失败。

``spec["ue"]`` 可配置：

* ``import_matrix_tolerance``         矩阵容差，默认 1e-4；
* ``import_check_bind_matrices``      置 True 才比对 bind_matrix。**默认忽略**：
  UE Python 不暴露骨骼静止变换，真机探测（``validation/ue_adapter_probe_result.json``）
  已确认 bind_matrix 只会是单位阵占位；
* ``import_ignore_fields``            额外忽略的 diff 字段；
* ``import_warn_fields``              只提示不阻断的字段，默认 ``["frame_range"]``。
"""
from __future__ import annotations

import math
import time
from typing import Any, Dict, List, Optional, Tuple

TOOL_ID = "ue_import_guard"
TITLE = "UE Import Guard 导入校验"
VERSION = "0.1.0"

_DEFAULT_TOLERANCE = 0.0001
_DEFAULT_WARN_FIELDS = ("frame_range",)
_PROBE_SOURCE = "validation/ue_adapter_probe_result.json"

# 每条 diff 字段的中文说明（diffs / UI / reason 共用）。
_DIFF_MESSAGES: Dict[str, str] = {
    "unit": "场景单位不一致：%s → %s",
    "up_axis": "up_axis 不一致：%s → %s",
    "joint_count": "关节数量不一致：%s → %s",
    "joint_missing": "关节 %s 在 UE 侧丢失",
    "joint_added": "UE 侧多出关节 %s",
    "joint_renamed": "关节被重命名：%s → %s",
    "bind_matrix": "关节 %s 的绑定矩阵偏差超出容差",
    "parent": "关节 %s 的父级变化：%s → %s",
    "frame_range": "帧范围不一致：%s → %s",
}

_BLOCKING_FIELDS = frozenset({"unit", "up_axis", "joint_count", "joint_missing",
                              "joint_added", "joint_renamed", "parent",
                              "bind_matrix"})


# --------------------------------------------------------------------------
# 自描述
# --------------------------------------------------------------------------
def info() -> Dict[str, Any]:
    """插件自描述，供工具面板与菜单展示。"""
    return {
        "id": TOOL_ID,
        "name": TITLE,
        "version": VERSION,
        "dcc": ["unreal"],
        "min_version": {"unreal": "5.8"},
        "capabilities": ["scene.read", "import.validate", "report.render"],
        "permissions": ["scene.read", "import.validate"],
        "description": "只读校验 UE 资产与导入快照，输出可定位的阻断/提示结论。",
        "rule_count": 9,
        "source": _PROBE_SOURCE,
    }


def describe_data_contract() -> Dict[str, Any]:
    """返回 UE 资产数据的字段说明，供 Web 面板与文档直接展示。"""
    return {
        "sources": ["snapshot.metadata.ue", "options.ue_assets"],
        "priority": "options.ue_assets 优先；缺失时回落到 snapshot.metadata.ue",
        "missing_policy": "字段缺失 → 对应规则整条跳过，绝不臆造数值",
        "fields": {
            "skeleton.bones": {"type": "list[str]", "required": False,
                               "note": "骨骼名列表；用于必需骨骼链与命名一致性"},
            "skeleton.compatible_with": {"type": "list[str]", "required": False,
                                         "note": "Skeleton 兼容资产路径列表"},
            "skeleton.root_bone": {"type": "str", "required": False,
                                   "note": "根骨骼名；根运动规则用它判断是否落地"},
            "skeleton.kind": {"type": "str", "required": False,
                              "note": "可选；'humanoid' 时强制检查完整骨骼链"},
            "anim_sequences[].name": {"type": "str", "required": True,
                                      "note": "AnimSequence 资产路径"},
            "anim_sequences[].skeleton": {"type": "str", "required": False,
                                          "note": "引用 Skeleton 路径"},
            "anim_sequences[].compression": {"type": "str", "required": False,
                                             "note": "压缩方案；与 spec.ue.animation_compression 比对"},
            "anim_sequences[].root_motion": {"type": "bool", "required": False,
                                             "note": "是否启用根运动"},
            "anim_sequences[].curve_count": {"type": "int", "required": False,
                                             "note": "UE 侧曲线数量"},
            "anim_sequences[].curves": {"type": "list[str]", "required": False,
                                        "note": "UE 侧曲线名；与 curve_count 交叉校验"},
            "skeletal_meshes[].name": {"type": "str", "required": True,
                                       "note": "SkeletalMesh 资产路径"},
            "skeletal_meshes[].material_slots": {"type": "int|list[str]",
                                                 "required": False,
                                                 "note": "材质槽数量或槽名列表"},
            "skeletal_meshes[].lods[].allow_deformer": {"type": "bool",
                                                        "required": False,
                                                        "note": "LOD 是否允许 mesh deformer"},
            "import.sidecar": {"type": "dict", "required": False,
                               "note": "Export Bridge 写出的 sidecar（含 snapshot 键）"},
        },
        "example": {
            "skeleton": {"bones": ["root", "pelvis", "spine_01", "neck", "head"],
                         "compatible_with": ["/Game/Characters/Default/SK_Default"],
                         "root_bone": "root"},
            "anim_sequences": [{"name": "/Game/Anim/AS_Run",
                                "skeleton": "/Game/Characters/Default/SK_Default",
                                "compression": "Default", "root_motion": False,
                                "curve_count": 1, "curves": ["run_phase"]}],
            "skeletal_meshes": [{"name": "/Game/Characters/SKM_Body",
                                 "material_slots": 2,
                                 "lods": [{"allow_deformer": False}]}],
            "import": {"sidecar": {"snapshot": {"joints": {"root": {}}}}},
        },
    }


def api_notes() -> List[Dict[str, str]]:
    """UE 5.7/5.8 Python 侧的已知缺口（来源：真机探测结果 JSON）。"""
    return [
        {"symbol": "MLDeformerAsset", "status": "python_unavailable",
         "note": "5.7/5.8 Python 侧均未暴露该类型；引擎插件目录存在但不等于可脚本访问，"
                 "相关能力需在目标工程启用插件后复验。",
         "source": _PROBE_SOURCE},
        {"symbol": "NeuralMorphModel", "status": "python_unavailable",
         "note": "5.7/5.8 Python 侧均未暴露；不能据此断言功能不存在，"
                 "只能说明脚本层无法直接读写。",
         "source": _PROBE_SOURCE},
        {"symbol": "DeformerGraphAsset", "status": "python_unavailable",
         "note": "5.7/5.8 Python 侧均未暴露；LOD 的 allow_deformer 只能读资产字段，"
                 "无法进一步枚举 Deformer Graph 内部节点。",
         "source": _PROBE_SOURCE},
        {"symbol": "EditorDialogLibrary", "status": "missing",
         "note": "5.7/5.8 均不存在；需要弹窗时必须走 EditorDialog，"
                 "调用 EditorDialogLibrary 会直接 AttributeError。",
         "source": _PROBE_SOURCE},
    ]


# --------------------------------------------------------------------------
# 导入快照校验（纯函数）
# --------------------------------------------------------------------------
def validate_import(sidecar: Any, ue_snapshot: Any,
                    spec: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """对比 sidecar 快照与 UE 侧快照，返回阻断判定（详见模块 docstring）。"""
    result: Dict[str, Any] = {"passed": True, "diffs": [], "blocking": [],
                              "reason": "", "ignored": []}
    before = _snapshot_dict(sidecar)
    after = _snapshot_dict(ue_snapshot)
    if not _joints(before) or not _joints(after):
        result["reason"] = ("缺少可对比的 joints（sidecar=%d / ue=%d），跳过导入校验"
                            % (len(_joints(before)), len(_joints(after))))
        return result

    ue_spec = _ue_spec(spec)
    tolerance = _tolerance(ue_spec.get("import_matrix_tolerance"))
    try:
        from tak.core.diff import SkeletonSnapshot, diff_snapshots

        raw = diff_snapshots(SkeletonSnapshot.from_dict(before),
                             SkeletonSnapshot.from_dict(after),
                             matrix_tolerance=tolerance)
    except (TypeError, ValueError) as exc:
        result["reason"] = "快照 diff 失败，跳过导入校验: %s: %s" % (
            type(exc).__name__, exc)
        return result

    ignore_fields, warn_fields = _field_policy(ue_spec)
    diffs: List[Dict[str, Any]] = []
    blocking: List[Dict[str, Any]] = []
    ignored: List[str] = []
    for item in raw:
        field = str(item.get("field") or "")
        if field in ignore_fields:
            if field not in ignored:
                ignored.append(field)
            continue
        enriched = dict(item)
        enriched["blocking"] = field in _BLOCKING_FIELDS and field not in warn_fields
        enriched["message"] = _diff_message(field, item)
        diffs.append(enriched)
        if enriched["blocking"]:
            blocking.append(enriched)

    result["diffs"] = diffs
    result["blocking"] = blocking
    result["ignored"] = sorted(ignored)
    result["passed"] = not blocking
    if blocking:
        result["reason"] = "导入校验未通过：%d 处阻断性偏差（%s）" % (
            len(blocking), "、".join(_field_names(blocking)))
    elif diffs:
        result["reason"] = "导入校验通过：%d 处非阻断差异（%s）" % (
            len(diffs), "、".join(_field_names(diffs)))
    else:
        result["reason"] = "导入校验通过：sidecar 与 UE 快照一致"
    if ignored:
        result["reason"] += "；已忽略 %s" % "、".join(ignored)
    return result


# --------------------------------------------------------------------------
# Report 编排
# --------------------------------------------------------------------------
def run_report(ctx: Any) -> Any:
    """跑 UE 资产规则 + 导入校验，合并成一个 ``tak.core.report.Report``。"""
    from tak.core.report import Report
    from tak.core.rules.base import Finding, Severity

    started = time.time()
    spec = getattr(ctx, "spec", None)
    spec = spec if isinstance(spec, dict) else {}
    ue_assets = _ue_assets(ctx)

    report = _run_plugin_rules(ctx, spec)

    sidecar, ue_snapshot = _import_inputs(ctx, ue_assets)
    compared = bool(_joints(_snapshot_dict(sidecar))) \
        and bool(_joints(_snapshot_dict(ue_snapshot)))
    validation = validate_import(sidecar, ue_snapshot, spec)
    if compared:
        report.findings.extend(_validation_findings(validation, Finding, Severity))
        report.findings.sort(key=_sort_key)

    report.metadata["rules_count"] = len(report.metadata.get("rule_ids", []))
    report.metadata["duration_ms"] = round((time.time() - started) * 1000.0, 2)
    report.metadata["validate"] = {
        "compared": compared,
        "passed": bool(validation["passed"]),
        "diff_count": len(validation["diffs"]),
        "blocking_count": len(validation["blocking"]),
        "ignored": list(validation["ignored"]),
        "reason": str(validation["reason"]),
    }
    report.metadata["api_notes"] = api_notes()
    report.metadata["data_contract"] = describe_data_contract()
    report.metadata["ue_assets_present"] = bool(ue_assets)
    return report


def _run_plugin_rules(ctx: Any, spec: Dict[str, Any]) -> Any:
    """只跑本插件规则，避免内置规则混进 UE 导入结论。"""
    from tak.core.report import Report
    from tak.core.rules.engine import RuleContext, run_rules

    if hasattr(ctx, "rule_context"):
        rule_ctx = ctx.rule_context()
    else:
        rule_ctx = RuleContext(snapshot=_snapshot_dict(getattr(ctx, "snapshot", None)),
                               spec=spec,
                               dcc=str(getattr(ctx, "dcc", "unreal") or "unreal"),
                               options=dict(getattr(ctx, "options", None) or {}))
    report = run_rules(rule_ctx, title=TITLE, plugin_id=TOOL_ID)
    if not isinstance(report, Report):  # pragma: no cover - engine 契约固定
        report = Report(title=TITLE, dcc=str(getattr(ctx, "dcc", "unreal")))
    return report


def _validation_findings(validation: Dict[str, Any], finding_cls: Any,
                         severity_cls: Any) -> List[Any]:
    """把校验结果翻译成 Finding：阻断用 BLOCKER，其余差异用 INFO。"""
    findings: List[Any] = []
    blocking_ids = {id(item) for item in validation["blocking"]}
    for item in validation["blocking"]:
        findings.append(finding_cls(
            rule_id=TOOL_ID + ".import_blocking_diff",
            severity=severity_cls.BLOCKER,
            message=str(item.get("message") or "导入快照存在阻断性偏差"),
            node=_diff_node(item),
            fix_hint="按 diff 定位后修正资产或重新导出；该偏差不允许进入引擎",
            data={"diff": dict(item), "field": item.get("field")}))
    for item in validation["diffs"]:
        if id(item) in blocking_ids:
            continue
        findings.append(finding_cls(
            rule_id=TOOL_ID + ".import_informational_diff",
            severity=severity_cls.INFO,
            message=str(item.get("message") or "导入快照存在非阻断差异"),
            node=_diff_node(item),
            fix_hint="该字段不参与阻断判定，仅供核对",
            data={"diff": dict(item), "field": item.get("field")}))
    return findings


def _sort_key(finding: Any) -> tuple:
    order = {"blocker": 0, "error": 1, "warning": 2, "info": 3}
    severity = getattr(finding, "severity", "")
    value = severity.value if hasattr(severity, "value") else str(severity)
    return (order.get(value, 9), str(getattr(finding, "rule_id", "")),
            str(getattr(finding, "node", "")))


# --------------------------------------------------------------------------
# 取数
# --------------------------------------------------------------------------
def _as_dict(value: Any) -> Dict[str, Any]:
    if hasattr(value, "to_dict") and not isinstance(value, dict):
        try:
            value = value.to_dict()
        except Exception:
            return {}
    return dict(value) if isinstance(value, dict) else {}


def _snapshot_dict(value: Any) -> Dict[str, Any]:
    """归一化任何快照输入：裸快照 / {"snapshot": {...}} / SkeletonSnapshot。"""
    data = _as_dict(value)
    if "joints" not in data and isinstance(data.get("snapshot"), dict):
        data = _as_dict(data.get("snapshot"))
    return data


def _joints(snapshot: Dict[str, Any]) -> Dict[str, Any]:
    joints = snapshot.get("joints") if isinstance(snapshot, dict) else None
    return joints if isinstance(joints, dict) else {}


def _ue_assets(ctx: Any) -> Dict[str, Any]:
    options = getattr(ctx, "options", None)
    if isinstance(options, dict):
        value = options.get("ue_assets")
        if isinstance(value, dict):
            return value
    metadata = _snapshot_dict(getattr(ctx, "snapshot", None)).get("metadata")
    if isinstance(metadata, dict):
        value = metadata.get("ue")
        if isinstance(value, dict):
            return value
    return {}


def _import_inputs(ctx: Any, ue_assets: Dict[str, Any]) -> Tuple[Any, Any]:
    options = getattr(ctx, "options", None)
    options = options if isinstance(options, dict) else {}
    sidecar = options.get("sidecar")
    ue_snapshot = options.get("ue_snapshot")
    if sidecar is None:
        ue_import = ue_assets.get("import")
        if isinstance(ue_import, dict):
            sidecar = ue_import.get("sidecar")
    if ue_snapshot is None:
        ue_snapshot = getattr(ctx, "snapshot", None)
    return sidecar, ue_snapshot


def _ue_spec(spec: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not isinstance(spec, dict):
        return {}
    section = spec.get("ue")
    return section if isinstance(section, dict) else {}


def _tolerance(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return _DEFAULT_TOLERANCE
    number = float(value)
    if not math.isfinite(number) or number < 0:
        return _DEFAULT_TOLERANCE
    return number


def _field_policy(ue_spec: Dict[str, Any]) -> Tuple[set, set]:
    """返回 (忽略字段, 只提示字段)。

    bind_matrix 默认忽略：UE Python 不暴露骨骼静止变换，真机探测已确认
    该字段只会是单位阵占位，参与阻断会造成全量误报。
    """
    ignore = {str(item) for item in (ue_spec.get("import_ignore_fields") or [])
              if str(item)}
    if ue_spec.get("import_check_bind_matrices") is not True:
        ignore.add("bind_matrix")
    warn = {str(item) for item in (ue_spec.get("import_warn_fields")
                                   or list(_DEFAULT_WARN_FIELDS)) if str(item)}
    warn |= {"frame_range"}
    return ignore, warn


def _diff_message(field: str, raw: Dict[str, Any]) -> str:
    template = _DIFF_MESSAGES.get(field)
    if template is None:
        return "字段 %r 发生变化" % field
    if field == "joint_renamed":
        return template % (raw.get("before"), raw.get("after"))
    if field == "parent":
        return template % (raw.get("joint"), raw.get("before"), raw.get("after"))
    if field in ("unit", "up_axis", "joint_count", "frame_range"):
        return template % (raw.get("before"), raw.get("after"))
    return template % (raw.get("joint"),)


def _diff_node(item: Dict[str, Any]) -> str:
    """diff 的定位节点：只有骨骼级偏差才有 node，单位/轴向保持空。"""
    field = str(item.get("field") or "")
    if field in ("joint_missing", "joint_added", "bind_matrix"):
        return str(item.get("joint") or "")
    if field in ("joint_renamed", "parent"):
        return str(item.get("joint") or item.get("before") or "")
    return ""


def _field_names(items: List[Dict[str, Any]]) -> List[str]:
    names: List[str] = []
    for item in items:
        name = str(item.get("field") or "")
        if name and name not in names:
            names.append(name)
    return names
