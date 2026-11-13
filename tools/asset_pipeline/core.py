# -*- coding: utf-8 -*-
"""导出 / 导入：把「导出」和「导入校验」做成一条对称的管线。

设计要点
--------
* **内容可选**：带绑定的整包、只要骨骼、只要蒙皮网格、只要控制器形，
  或只导一份动画缓存；选项来自枚举，不再靠每个 DCC 各写一套导出面板。
* **目标可选**：Maya / 3ds Max / MotionBuilder / Unreal / 通用（其它 DCC），
  由目标反推可用格式与默认选项，避免「导了 UE 打不开的 FBX」。
* **统一 profile**：一次勾选存成命名 profile（``~/.tak/config.json``），
  导出端与导入端读同一份 profile，字段对不上就是配置问题而不是猜。
* **导出到一个缓存位**：``cache_dir`` 是跨 DCC 中转用的共享目录，
  在 Maya 导出到缓存位，在 UE 侧打开同一工具读它做导入校验。

真正的写文件动作仍然由 DCC 适配器执行（``export_fbx`` / ``export_usd``），
本模块只负责「决定导出什么、导出到哪、导出前后对不对」。
"""
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional, Sequence

from tak.core import config as tak_config
from tak.core.rules import Severity
from tak.core.rules.base import Finding

TOOL_ID = "asset_pipeline"
TITLE = "导出 / 导入"

#: 可导出的内容项。
CONTENT_ITEMS: tuple = (
    {"id": "skeleton", "name": "骨骼",
     "description": "关节层级、朝向、命名与前缀"},
    {"id": "mesh", "name": "蒙皮网格",
     "description": "变形网格本体（几何 + UV + 法线）"},
    {"id": "skin", "name": "蒙皮权重",
     "description": "权重与影响骨数据，缺失会导致导入后无法变形"},
    {"id": "controls", "name": "控制器形状",
     "description": "NURBS / 曲线控制器，动画师操作面"},
    {"id": "animation", "name": "动画缓存",
     "description": "骨骼动画曲线，可作为跨 DCC 的动作缓存"},
    {"id": "materials", "name": "材质槽",
     "description": "材质分配与贴图引用路径"},
)

#: 预设组合，对应最常见的四种交付物。
PRESETS: tuple = (
    {"id": "full_rig", "name": "完整绑定",
     "content": ["skeleton", "mesh", "skin", "controls", "materials"]},
    {"id": "skeleton_only", "name": "仅骨骼",
     "content": ["skeleton"]},
    {"id": "mesh_only", "name": "仅蒙皮网格",
     "content": ["mesh", "skin"]},
    {"id": "anim_cache", "name": "动画缓存",
     "content": ["skeleton", "animation"]},
    {"id": "controls_only", "name": "仅控制器",
     "content": ["controls"]},
)

#: 目标 DCC / 引擎，``formats`` 第一个是默认格式。
TARGETS: tuple = (
    {"id": "unreal", "name": "Unreal Engine", "formats": ["fbx", "usd"],
     "options": {"apply_unit_scale": True, "smoothing_groups": False,
                 "preserve_bone_naming": True}},
    {"id": "maya", "name": "Maya", "formats": ["fbx", "usd", "ma"],
     "options": {"apply_unit_scale": True, "smoothing_groups": True,
                 "preserve_bone_naming": True}},
    {"id": "max", "name": "3ds Max", "formats": ["fbx", "usd"],
     "options": {"apply_unit_scale": True, "smoothing_groups": True,
                 "preserve_bone_naming": True}},
    {"id": "mobu", "name": "MotionBuilder", "formats": ["fbx"],
     "options": {"apply_unit_scale": True, "smoothing_groups": False,
                 "preserve_bone_naming": True}},
    {"id": "generic", "name": "通用（其它 DCC）", "formats": ["fbx", "usd", "abc"],
     "options": {"apply_unit_scale": True, "smoothing_groups": True,
                 "preserve_bone_naming": False}},
)

_MODE_EXPORT = "export"
_MODE_IMPORT = "import"


def _by_id(items: Sequence[Dict[str, Any]], item_id: str) -> Optional[Dict[str, Any]]:
    for item in items:
        if item["id"] == item_id:
            return item
    return None


def info() -> Dict[str, Any]:
    from tak.version import __version__
    return {
        "id": TOOL_ID, "name": TITLE, "version": __version__,
        "content": [dict(item) for item in CONTENT_ITEMS],
        "presets": [dict(item) for item in PRESETS],
        "targets": [dict(item) for item in TARGETS],
        "profiles": tak_config.export_profiles(),
    }


# --------------------------------------------------------------------------- #
# profile
# --------------------------------------------------------------------------- #
def default_profile(target: str = "unreal") -> Dict[str, Any]:
    spec = _by_id(TARGETS, target) or TARGETS[0]
    return {
        "mode": _MODE_EXPORT,
        "preset": "full_rig",
        "content": list(_by_id(PRESETS, "full_rig")["content"]),
        "target": spec["id"],
        "format": spec["formats"][0],
        "path": "",
        "use_cache_dir": False,
        "options": dict(spec["options"]),
    }


def normalize_profile(raw: Any) -> Dict[str, Any]:
    """把外部 profile 收窄成合法结构；未知值一律回默认，不抛异常。"""
    profile = default_profile()
    if not isinstance(raw, dict):
        return profile

    target = str(raw.get("target") or profile["target"])
    spec = _by_id(TARGETS, target) or _by_id(TARGETS, profile["target"])
    profile["target"] = spec["id"]

    fmt = str(raw.get("format") or spec["formats"][0]).lower()
    profile["format"] = fmt if fmt in spec["formats"] else spec["formats"][0]

    valid = {item["id"] for item in CONTENT_ITEMS}
    content = raw.get("content")
    if isinstance(content, (list, tuple)):
        kept = [str(c) for c in content if str(c) in valid]
        profile["content"] = kept
    elif isinstance(content, str) and content in valid:
        profile["content"] = [content]

    preset = str(raw.get("preset") or "")
    if preset in {item["id"] for item in PRESETS}:
        profile["preset"] = preset

    profile["mode"] = _MODE_IMPORT if raw.get("mode") == _MODE_IMPORT else _MODE_EXPORT
    profile["path"] = str(raw.get("path") or "")
    profile["use_cache_dir"] = bool(raw.get("use_cache_dir", False))

    options = dict(spec["options"])
    raw_options = raw.get("options")
    if isinstance(raw_options, dict):
        for key, value in raw_options.items():
            if isinstance(value, (bool, int, float, str)):
                options[str(key)] = value
    profile["options"] = options
    return profile


def load_profile(name: str) -> Optional[Dict[str, Any]]:
    raw = tak_config.export_profile(name)
    return normalize_profile(raw) if raw is not None else None


def save_profile(name: str, profile: Dict[str, Any]) -> Dict[str, Any]:
    return tak_config.save_export_profile(name, normalize_profile(profile))


def delete_profile(name: str) -> Dict[str, Any]:
    return tak_config.delete_export_profile(name)


def cache_dir() -> str:
    """跨 DCC 中转缓存位：``TAK_CACHE_DIR`` 优先，否则 ``~/.tak/cache``。"""
    override = os.environ.get("TAK_CACHE_DIR", "").strip()
    if override:
        return os.path.abspath(os.path.expanduser(override))
    return os.path.join(tak_config.config_home(), "cache")


def resolve_path(profile: Dict[str, Any], asset_name: str = "asset") -> str:
    """算出最终导出路径：显式路径 > 缓存位 + 文件名。"""
    explicit = str(profile.get("path") or "").strip()
    if explicit and not profile.get("use_cache_dir"):
        return os.path.abspath(os.path.expanduser(explicit))
    folder = cache_dir() if profile.get("use_cache_dir") else os.path.dirname(explicit) or cache_dir()
    stem = os.path.splitext(os.path.basename(explicit))[0] if explicit else str(asset_name or "asset")
    fmt = str(profile.get("format") or "fbx")
    return os.path.join(folder, "%s.%s" % (stem or "asset", fmt))


# --------------------------------------------------------------------------- #
# 计划
# --------------------------------------------------------------------------- #
def build_plan(snapshot: Any, profile: Dict[str, Any],
               asset_name: str = "asset") -> Dict[str, Any]:
    """把 profile + 场景摘要整理成一份可执行的导出计划（纯数据，不写文件）。"""
    data = snapshot if isinstance(snapshot, dict) else {}
    profile = normalize_profile(profile)
    content = list(profile["content"])
    joints = data.get("joints") if isinstance(data.get("joints"), dict) else {}
    meshes = data.get("meshes") if isinstance(data.get("meshes"), list) else []
    skin = data.get("skin") if isinstance(data.get("skin"), dict) else {}

    return {
        "tool_id": TOOL_ID,
        "asset_name": str(asset_name or "asset"),
        "target": profile["target"],
        "format": profile["format"],
        "path": resolve_path(profile, asset_name),
        "content": content,
        "include_skeleton": "skeleton" in content,
        "include_mesh": "mesh" in content,
        "include_skin": "skin" in content,
        "include_controls": "controls" in content,
        "include_animation": "animation" in content,
        "include_materials": "materials" in content,
        "options": dict(profile["options"]),
        "counts": {
            "joints": len(joints),
            "meshes": len(meshes),
            "skinned": len([m for m in meshes
                            if isinstance(m, dict) and m.get("skin")] ) if meshes else 0,
            "skin_entries": len(skin),
        },
    }


def describe_plan(plan: Dict[str, Any]) -> List[str]:
    """人读的一行行说明，UI 直接展示。"""
    labels = {item["id"]: item["name"] for item in CONTENT_ITEMS}
    target = _by_id(TARGETS, plan.get("target", "")) or {"name": plan.get("target", "")}
    lines = [
        "目标：%s（%s）" % (target["name"], str(plan.get("format", "")).upper()),
        "路径：%s" % plan.get("path", ""),
        "内容：%s" % ("、".join(labels.get(c, c) for c in plan.get("content", [])) or "（空）"),
    ]
    counts = plan.get("counts") or {}
    lines.append("场景：%d 关节 / %d 网格 / %d 蒙皮项"
                 % (counts.get("joints", 0), counts.get("meshes", 0),
                    counts.get("skin_entries", 0)))
    return lines


# --------------------------------------------------------------------------- #
# 前置检查
# --------------------------------------------------------------------------- #
def preflight(ctx: Any, profile: Dict[str, Any]) -> List[Finding]:
    """导出前检查：内容为空、路径非法、目标格式不支持、场景数据不足。"""
    findings: List[Finding] = []
    profile = normalize_profile(profile)
    content = profile["content"]

    if not content:
        findings.append(Finding(
            rule_id=TOOL_ID + ".content.empty",
            severity=Severity.ERROR,
            message="没有选择任何导出内容，导出的文件会是空的。",
            fix_hint="至少勾选「骨骼」或「蒙皮网格」。",
        ))

    spec = _by_id(TARGETS, profile["target"])
    if spec is None:
        findings.append(Finding(
            rule_id=TOOL_ID + ".target.unknown",
            severity=Severity.ERROR,
            message="未知导出目标：%s" % profile["target"],
            fix_hint="在目标下拉里重新选择。",
        ))
    elif profile["format"] not in spec["formats"]:
        findings.append(Finding(
            rule_id=TOOL_ID + ".format.unsupported",
            severity=Severity.ERROR,
            message="%s 不支持 %s 格式。" % (spec["name"], profile["format"]),
            fix_hint="可选格式：%s。" % "、".join(spec["formats"]),
        ))

    path = resolve_path(profile)
    folder = os.path.dirname(path)
    if folder and not os.path.isdir(folder):
        findings.append(Finding(
            rule_id=TOOL_ID + ".path.missing_dir",
            severity=Severity.WARNING,
            message="导出目录不存在：%s" % folder,
            fix_hint="运行时会尝试创建；若没有写权限请换一个路径。",
            node=folder,
        ))

    snapshot = getattr(ctx, "snapshot", None) or {}
    joints = snapshot.get("joints") if isinstance(snapshot.get("joints"), dict) else {}
    if "skeleton" in content and not joints:
        findings.append(Finding(
            rule_id=TOOL_ID + ".scene.no_joints",
            severity=Severity.WARNING,
            message="勾选了骨骼，但当前快照里没有采集到关节数据。",
            fix_hint="确认场景里存在骨骼，或在适配器里开启骨架采集。",
        ))

    # 复用导出校验插件的规则：一致性判定只有一份实现。这里自建 RuleContext
    # 而不要求调用方提供 rule_context()，是为了让预检也能在没有完整 ToolContext
    # 的场景（UI 里的「导出前再确认一次」）复用同一套判定。
    try:
        from tak.core import runner
        from tak.core.rules import run_rules
        from tak.core.rules.engine import RuleContext
        runner.load_tool_modules(runner.find_plugin("export_bridge"))
        rule_ctx = RuleContext(snapshot=snapshot, spec=getattr(ctx, "spec", {}) or {},
                               dcc=getattr(ctx, "dcc", "maya") or "maya")
        sub = run_rules(rule_ctx, plugin_id="export_bridge",
                        title=TITLE + " · 导出前置",
                        dcc=getattr(ctx, "dcc", "maya") or "maya")
        findings.extend(sub.findings)
    except Exception as exc:
        findings.append(Finding(
            rule_id=TOOL_ID + ".preflight.unavailable",
            severity=Severity.INFO,
            message="导出前置规则未加载，本次只做了基础检查：%s: %s"
                    % (type(exc).__name__, exc),
        ))
    return findings


def run_report(ctx: Any) -> Any:
    """工具默认运行 = 出计划 + 前置检查（不写文件）。"""
    from tak.core.report import Report

    options = getattr(ctx, "options", None) or {}
    profile = normalize_profile(options.get("profile"))
    asset_name = str(options.get("asset_name") or
                     (getattr(ctx, "snapshot", {}) or {}).get("name") or "asset")
    plan = build_plan(getattr(ctx, "snapshot", None) or {}, profile, asset_name)
    findings = preflight(ctx, profile)

    report = Report(title=TITLE, dcc=getattr(ctx, "dcc", "maya"),
                    findings=findings,
                    spec_version=str((getattr(ctx, "spec", {}) or {}).get("version", "1.0.0")))
    report.metadata.update({"plan": plan, "plan_lines": describe_plan(plan)})
    return report


# --------------------------------------------------------------------------- #
# 执行
# --------------------------------------------------------------------------- #
def execute(plan: Dict[str, Any], adapter: Any) -> Dict[str, Any]:
    """按计划调适配器写文件；返回 ``{"ok", "path", "format", "reason"}``。"""
    path = str(plan.get("path") or "")
    fmt = str(plan.get("format") or "fbx").lower()
    if not path:
        return {"ok": False, "path": "", "format": fmt, "reason": "导出路径为空"}

    folder = os.path.dirname(path)
    try:
        if folder and not os.path.isdir(folder):
            os.makedirs(folder, exist_ok=True)
    except OSError as exc:
        return {"ok": False, "path": path, "format": fmt,
                "reason": "无法创建目录：%s: %s" % (type(exc).__name__, exc)}

    if adapter is None:
        return {"ok": False, "path": path, "format": fmt,
                "reason": "没有可用的 DCC 适配器（离线环境不执行写文件）"}

    spec = dict(plan.get("options") or {})
    spec.update({"content": list(plan.get("content") or [])})
    try:
        if fmt == "usd":
            result = adapter.export_usd(path, spec)
        elif fmt == "fbx":
            result = adapter.export_fbx(path, spec)
        else:
            return {"ok": False, "path": path, "format": fmt,
                    "reason": "适配器未实现 %s 导出" % fmt}
    except Exception as exc:
        return {"ok": False, "path": path, "format": fmt,
                "reason": "%s: %s" % (type(exc).__name__, exc)}

    payload = result if isinstance(result, dict) else {"ok": bool(result)}
    payload.setdefault("path", path)
    payload.setdefault("format", fmt)
    payload.setdefault("ok", os.path.isfile(path))
    return payload


def import_check(sidecar_path: str, snapshot: Any,
                 profile: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """导入侧校验：读导出时落下的 sidecar，与导入后的快照比对。

    复用 ``ue_import_guard`` 的实现 —— 导入后一致性只有一套判定。
    """
    from tak.core import runner
    module = runner.load_tool_modules(runner.find_plugin("ue_import_guard"))
    return module.validate_import(
        {"path": sidecar_path}, snapshot,
        spec=normalize_profile(profile) if profile else None)
