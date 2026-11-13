# -*- coding: utf-8 -*-
"""ML Deformer 数据工厂插件编排层（纯数据、零 DCC、零训练调用）。

职责
----
1. ``info()``              插件自描述，显式声明 ``training_api = False``；
2. ``plan(...)``           采样计划（该拍哪些姿势）；
3. ``manifest(...)``       数据清单 + 门槛校验（这批数据是什么、够不够）；
4. ``validate(...)``       对已有清单复跑门槛；
5. ``run_report(ctx)``     把门槛 issue 翻成统一 ``Report``，metadata 写入
                           plan / manifest / issues / safety；
6. ``markdown_summary()``  中文 Markdown 摘要，供 UI 与报告渲染。

边界（务必保留）
================
UE 侧 ``MLDeformerAsset`` / ``NeuralMorphModel`` / ``DeformerGraphAsset`` 在
5.7 与 5.8 的 Python 侧均未暴露（``docs/VALIDATION.md`` B2、``docs/WEB_PANEL.md``
W3 / W30），能力位 ``mldeformer.train`` 恒为 False。本插件**只产出数据集清单
与质量门槛报告**：不 import maya / pymxs / pyfbsdk / unreal，不访问网络，
不写场景，也不调用、不假设任何训练入口。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from tak.core.mldeformer import (DATASET_CONTRACT, build_manifest, build_plan,
                                 quality_gates, sample_summary, validate_manifest)

TOOL_ID = "mldeformer_factory"
TITLE = "ML Deformer 数据工厂"
VERSION = "0.1.0"
DCC_LIST = ["maya", "max", "mobu", "unreal"]
MIN_VERSION = {"maya": "2023", "max": "2023", "mobu": "2023", "unreal": "5.8"}
#: report.metadata 里最多保留多少条 pose（清单本身始终按全量统计）。
PLAN_PREVIEW_POSES = 40
_GATE_KEYS = ("min_total_poses", "max_total_poses", "min_poses_per_joint",
              "max_duplicate_ratio", "require_categories", "require_contract")


def safety() -> Dict[str, Any]:
    """统一的只读声明；``training_api`` 必须恒为 False。"""
    return {
        "read_only": True,
        "scene_write": False,
        "network": False,
        "training_api": False,
        "note": ("仅产出数据集清单与质量门槛报告；UE 侧 mldeformer.train 能力位为 "
                 "false，本工具不调用任何训练入口"),
    }


def info() -> Dict[str, Any]:
    """返回插件自描述，供工具面板与菜单使用。"""
    return {
        "id": TOOL_ID,
        "name": TITLE,
        "version": VERSION,
        "dcc": list(DCC_LIST),
        "min_version": dict(MIN_VERSION),
        "capabilities": ["mldeformer.dataset.plan", "mldeformer.dataset.manifest",
                         "mldeformer.dataset.validate", "report.render"],
        "permissions": ["scene.read"],
        "description": ("确定性姿态采样计划 + 内容寻址数据集清单 + 质量门槛报告；"
                        "只产出数据，不调用任何训练入口。"),
        "offline": True,
        "read_only": True,
        "deterministic": True,
        "training_api": False,
        "dataset_contract": DATASET_CONTRACT,
        "contract": DATASET_CONTRACT,
        "plan_api": "build_plan(snapshot, options) -> PoseSamplePlan",
        "manifest_api": "build_manifest(plan, options) -> dict",
        "validate_api": "validate_manifest(manifest, gates) -> dict",
        "limitation": ("UE 侧 MLDeformerAsset / NeuralMorphModel / DeformerGraphAsset "
                       "在 5.7 与 5.8 的 Python 侧均未暴露，因此没有训练入口可调"),
    }


def _as_options(options: Any) -> Dict[str, Any]:
    return dict(options) if isinstance(options, dict) else {}


def _snapshot_dict(snapshot: Any) -> Dict[str, Any]:
    """快照统一成 dict；畸形 / None 一律退化为空 dict（由核心层兜底报 warning）。"""
    if isinstance(snapshot, dict):
        return snapshot
    if hasattr(snapshot, "to_dict"):
        try:
            converted = snapshot.to_dict()
            if isinstance(converted, dict):
                return converted
        except Exception:
            return {}
    return {}


def _gate_options(options: Any) -> Dict[str, Any]:
    """把 ``options["gates"]`` 与顶层门槛键合并成扁平门槛覆盖。"""
    opts = _as_options(options)
    gates = dict(opts.get("gates")) if isinstance(opts.get("gates"), dict) else {}
    for key in _GATE_KEYS:
        if key in opts:
            gates[key] = opts[key]
    return gates


def _plan_payload(plan: Any) -> Dict[str, Any]:
    """截断后的计划预览：全量统计照常，只有 poses 列表受长度限制。"""
    data = plan.to_dict() if hasattr(plan, "to_dict") else {}
    data = data if isinstance(data, dict) else {}
    poses = data.get("poses")
    poses = poses if isinstance(poses, list) else []
    preview = {
        "contract": data.get("contract", DATASET_CONTRACT),
        "joint_count": data.get("joint_count", 0),
        "seed": data.get("seed", 0),
        "categories": dict(data.get("categories") or {}),
        "pose_count": len(poses),
        "poses": [dict(pose) for pose in poses[:PLAN_PREVIEW_POSES]],
        "truncated": len(poses) > PLAN_PREVIEW_POSES,
        "warnings": list(data.get("warnings") or []),
        "notes": list(data.get("notes") or []),
    }
    return preview


def plan(snapshot: Any = None, options: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """生成采样计划；返回严格可 JSON 序列化的 dict，绝不抛异常。"""
    opts = _as_options(options)
    plan_options = dict(opts.get("plan") or {}) if isinstance(opts.get("plan"), dict) else {}
    for key in ("max_poses", "limits", "categories", "seed", "include_rest"):
        if key in opts and key not in plan_options:
            plan_options[key] = opts[key]
    result = build_plan(_snapshot_dict(snapshot), plan_options)
    return {
        "ok": True,
        "tool_id": TOOL_ID,
        "contract": result.contract,
        "plan": result.to_dict(),
        "summary": sample_summary(result),
        "warnings": list(result.warnings),
        "safety": safety(),
    }


def manifest(snapshot: Any = None,
             options: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """生成数据清单并顺带跑一遍门槛，返回 JSON 安全的 dict。"""
    opts = _as_options(options)
    plan_options = dict(opts.get("plan") or {}) if isinstance(opts.get("plan"), dict) else {}
    for key in ("max_poses", "limits", "categories", "seed", "include_rest"):
        if key in opts and key not in plan_options:
            plan_options[key] = opts[key]
    gate_options = _gate_options(opts)
    result = build_plan(_snapshot_dict(snapshot), plan_options)
    manifest_data = build_manifest(result, gate_options)
    validation = validate_manifest(manifest_data, gate_options)
    return {
        "ok": bool(validation.get("ok")),
        "tool_id": TOOL_ID,
        "manifest": manifest_data,
        "validation": validation,
        "summary": sample_summary(result),
        "warnings": list(result.warnings),
        "safety": safety(),
    }


def validate(manifest_data: Any, gates: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """对已有清单复跑门槛；畸形输入返回一条 ``mld.manifest.invalid`` 错误。"""
    gate_options = _gate_options(gates)
    result = validate_manifest(manifest_data, gate_options)
    result = dict(result)
    result["tool_id"] = TOOL_ID
    result["safety"] = safety()
    return result


def _options_from_ctx(ctx: Any) -> Dict[str, Any]:
    options = getattr(ctx, "options", None)
    options = options if isinstance(options, dict) else {}
    nested = options.get("mldeformer")
    return dict(nested) if isinstance(nested, dict) else dict(options)


def _finding_hint(code: str) -> str:
    return {
        "mld.contract.mismatch": "升级消费方到同一契约版本后重跑，禁止混用清单。",
        "mld.pose_count.too_low": "放宽 max_poses 或补采更多关节 / 类别。",
        "mld.pose_count.over_budget": "收紧 max_poses 或分批出清单。",
        "mld.coverage.missing_joint": "确认快照里该关节存在并参与采样。",
        "mld.coverage.low": "对该关节补采极值与半极值，至少 4 条。",
        "mld.duplicate.too_high": "检查 limits 是否过窄导致角度退化重复。",
        "mld.category.missing": "把缺失类别加入 categories 重新采样。",
        "mld.extreme.missing": "必须包含 extreme 类别，否则学不到可动范围。",
        "mld.manifest.invalid": "传入 build_manifest 产出的清单对象。",
    }.get(code, "按报告提示调整采样参数后重跑。")


def run_report(ctx: Any) -> Any:
    """把门槛 issue 翻成统一 ``Report``；每条 ERROR / WARN 一条 Finding。"""
    from tak.core.report import Report
    from tak.core.rules import Finding, Severity

    options = _options_from_ctx(ctx)
    gate_options = _gate_options(options)
    snapshot = _snapshot_dict(getattr(ctx, "snapshot", None))
    result = build_plan(snapshot, options)
    manifest_data = build_manifest(result, gate_options)
    validation = validate_manifest(manifest_data, gate_options)

    severity_of = {"error": Severity.ERROR, "warning": Severity.WARNING,
                   "info": Severity.INFO}
    findings: List[Any] = []
    for issue in validation["issues"]:
        data = dict(issue.get("data") or {})
        data["code"] = issue["code"]
        findings.append(Finding(
            rule_id=issue["code"],
            severity=severity_of.get(issue["severity"], Severity.INFO),
            message=issue["message"],
            node=str(data.get("joint") or ""),
            fix_hint=_finding_hint(issue["code"]),
            data=data,
        ))

    counts = validation["counts"]
    categories = manifest_data["categories"]
    findings.append(Finding(
        rule_id="mldeformer_factory.summary",
        severity=Severity.INFO,
        message=("数据集 %s：%d pose / %d 关节 / %d 类 · 门槛 error %d / warning %d"
                 % (manifest_data["dataset_id"], manifest_data["pose_count"],
                    manifest_data["joint_count"], len(categories),
                    counts["error"], counts["warning"])),
        node=manifest_data["dataset_id"],
        fix_hint=("本工具只产出数据集清单与门槛报告；训练入口未暴露，"
                  "需在真实项目启用 ML Deformer 插件后另行验证。"),
        data={
            "dataset_id": manifest_data["dataset_id"],
            "version": manifest_data["version"],
            "pose_digest": manifest_data["pose_digest"],
            "pose_count": manifest_data["pose_count"],
            "joint_count": manifest_data["joint_count"],
            "categories": dict(categories),
            "duplicate_ratio": manifest_data["duplicate_ratio"],
            "ok": bool(validation["ok"]),
        },
    ))

    spec = getattr(ctx, "spec", None)
    spec = spec if isinstance(spec, dict) else {}
    report = Report(
        title=TITLE,
        dcc=str(getattr(ctx, "dcc", "maya") or "maya"),
        findings=findings,
        spec_version=str(spec.get("version", "1.0.0")),
    )
    report.metadata.update({
        "tool_id": TOOL_ID,
        "contract": manifest_data["contract"],
        "dataset_id": manifest_data["dataset_id"],
        "version": manifest_data["version"],
        "summary": sample_summary(result),
        "plan": _plan_payload(result),
        "manifest": manifest_data,
        "issues": [dict(issue) for issue in validation["issues"]],
        "gate_counts": dict(validation["counts"]),
        "gates": dict(validation["gates"]),
        "safety": safety(),
    })
    return report


def markdown_summary(manifest_data: Any, validation: Any) -> str:
    """渲染中文 Markdown 摘要（清单要点 + 门槛阈值 + issue 明细）。"""
    data = manifest_data if isinstance(manifest_data, dict) else {}
    check = validation if isinstance(validation, dict) else {}
    issues = [item for item in (check.get("issues") or []) if isinstance(item, dict)]
    counts = check.get("counts") if isinstance(check.get("counts"), dict) else {}
    gates = check.get("gates") if isinstance(check.get("gates"), dict) else quality_gates()
    categories = data.get("categories") if isinstance(data.get("categories"), dict) else {}
    coverage = data.get("coverage") if isinstance(data.get("coverage"), dict) else {}
    ok = bool(check.get("ok"))

    lines: List[str] = [
        "## ML Deformer 数据清单",
        "",
        "- 数据集 ID：`%s`（version 与 ID 同源，内容寻址）"
        % (data.get("dataset_id") or "（缺省）"),
        "- 契约：`%s` · pose `%s` · 关节 `%s` · 重复率 `%s`"
        % (data.get("contract") or "-", data.get("pose_count", 0),
           data.get("joint_count", 0), data.get("duplicate_ratio", 0.0)),
        "- 采集种子：`%s` · pose 摘要：`%s`"
        % (data.get("seed", 0), data.get("pose_digest") or "-"),
        "",
        "### 类别分布",
        "",
    ]
    if categories:
        lines.append("| 类别 | 条数 |")
        lines.append("| --- | --- |")
        for name in sorted(categories):
            lines.append("| %s | %s |" % (name, categories[name]))
    else:
        lines.append("（无类别：清单为空）")
    lines.extend(["", "### 关节覆盖（Top 8）", ""])
    ranked = sorted(coverage.items(), key=lambda item: (-int(item[1] or 0), item[0]))
    if ranked:
        lines.append("| 关节 | 条数 |")
        lines.append("| --- | --- |")
        for joint, number in ranked[:8]:
            lines.append("| %s | %s |" % (joint, number))
    else:
        lines.append("（无覆盖数据）")
    lines.extend(["", "### 质量门槛", "", "| 门槛 | 阈值 |", "| --- | --- |"])
    for key in ("min_total_poses", "max_total_poses", "min_poses_per_joint",
                "max_duplicate_ratio", "require_categories", "require_contract"):
        value = gates.get(key)
        if isinstance(value, (list, tuple)):
            value = "、".join(str(item) for item in value)
        lines.append("| `%s` | %s |" % (key, value))
    lines.extend(["", "### 结论", ""])
    lines.append("%s error `%s` / warning `%s`"
                 % ("✅ 通过" if ok else "❌ 未通过",
                    counts.get("error", 0), counts.get("warning", 0)))
    if issues:
        lines.extend(["", "| 严重度 | code | 说明 |", "| --- | --- | --- |"])
        for issue in issues:
            lines.append("| %s | `%s` | %s |" % (
                issue.get("severity", "info"), issue.get("code", ""),
                str(issue.get("message", "")).replace("|", "\\|")))
    lines.extend([
        "",
        "> 边界：仅产出数据集清单与质量门槛报告；UE 侧 `mldeformer.train` 能力位为 "
        "`false`，本工具不调用任何训练入口。",
    ])
    return "\n".join(lines) + "\n"
