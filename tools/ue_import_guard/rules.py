# -*- coding: utf-8 -*-
"""UE Import Guard 的确定性规则（9 条，全部只读）。

数据来源与缺失策略见 ``core.py`` 模块 docstring：从
``ctx.snapshot["metadata"]["ue"]`` 或 ``ctx.options["ue_assets"]`` 读取，
**字段缺失一律跳过**，绝不臆造数值，也不产生 ERROR/BLOCKER。这样
``tests/test_builtin_rules.py`` 的 40 个 golden fixtures（不含 UE 字段）
不会因本插件误报。

规则只声明 ``dcc=["unreal"]``；``plugin_id`` 显式传入 ``TOOL_ID``，
不依赖模块名推断。fix_hint 只描述可操作动作，本插件不直接改资产。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from tak.core.rules import Finding, Severity, rule

TOOL_ID = "ue_import_guard"
_DCC = ["unreal"]

# 人形骨架的最小必需链；非人形（骨骼集合里链成员不足 2 个且未声明
# kind="humanoid"）不强制，避免把道具骨架误判成缺骨骼。
_REQUIRED_CHAIN = ("root", "pelvis", "spine_01", "neck", "head")
_FORBIDDEN_CHARS = (":", "|", " ")
_MAX_FINDINGS_PER_RULE = 24


# --------------------------------------------------------------------------
# 取数
# --------------------------------------------------------------------------
def _snapshot(ctx: Any) -> Dict[str, Any]:
    snapshot = getattr(ctx, "snapshot", None)
    if hasattr(snapshot, "to_dict") and not isinstance(snapshot, dict):
        try:
            snapshot = snapshot.to_dict()
        except Exception:
            snapshot = {}
    return snapshot if isinstance(snapshot, dict) else {}


def _ue(ctx: Any) -> Dict[str, Any]:
    """UE 资产数据；options 显式注入优先，其次 metadata.ue；都没有则空。"""
    options = getattr(ctx, "options", None)
    if isinstance(options, dict):
        value = options.get("ue_assets")
        if isinstance(value, dict):
            return value
    metadata = _snapshot(ctx).get("metadata")
    if isinstance(metadata, dict):
        value = metadata.get("ue")
        if isinstance(value, dict):
            return value
    return {}


def _section(ctx: Any, key: str) -> Optional[Dict[str, Any]]:
    value = _ue(ctx).get(key)
    return value if isinstance(value, dict) else None


def _entries(ctx: Any, key: str) -> List[Dict[str, Any]]:
    value = _ue(ctx).get(key)
    if not isinstance(value, (list, tuple)):
        return []
    return [item for item in value if isinstance(item, dict)]


def _spec_section(ctx: Any, key: str) -> Dict[str, Any]:
    spec = getattr(ctx, "spec", None)
    section = spec.get(key) if isinstance(spec, dict) else None
    return section if isinstance(section, dict) else {}


def _strings(value: Any) -> List[str]:
    if not isinstance(value, (list, tuple)):
        return []
    return [str(item) for item in value if isinstance(item, str) and str(item)]


def _asset_key(value: Any) -> str:
    """资产路径比较键：去空白、去引号、统一小写，取最后一段也参与匹配。"""
    if not isinstance(value, str):
        return ""
    text = value.strip().strip("'\"")
    return text.rsplit("/", 1)[-1].rsplit("\\", 1)[-1].lower()


def _finding(rule_id: str, severity: Severity, message: str, node: str = "",
             fix_hint: str = "", **extra: Any) -> Finding:
    return Finding(rule_id=rule_id, severity=severity, message=message,
                   node=node, fix_hint=fix_hint, data=dict(extra))


def _capped(items: List[Finding]) -> List[Finding]:
    return items[:_MAX_FINDINGS_PER_RULE]


# --------------------------------------------------------------------------
# 1. Skeleton 兼容性（compatible_with / 骨骼集合）
# --------------------------------------------------------------------------
@rule(TOOL_ID + ".skeleton_compatible", severity=Severity.ERROR,
      description="Skeleton 的 compatible_with 必须覆盖规范目标骨架，骨骼集合不得为空",
      dcc=_DCC, plugin_id=TOOL_ID)
def skeleton_compatible(ctx: Any) -> List[Finding]:
    """兼容列表为空或缺少目标骨架时，动画共享与重定向都会失败。"""
    skeleton = _section(ctx, "skeleton")
    if skeleton is None:
        return []
    findings: List[Finding] = []

    bones = skeleton.get("bones")
    if isinstance(bones, (list, tuple)) and not _strings(bones):
        findings.append(_finding(
            TOOL_ID + ".skeleton_compatible", Severity.ERROR,
            "Skeleton 的骨骼集合为空，无法导入任何动画",
            node=str(skeleton.get("root_bone") or ""),
            fix_hint="在 UE 中重建 Skeleton（至少包含 root 与一条脊柱链）后重新导入",
            bones=0))

    compatible = skeleton.get("compatible_with")
    if not isinstance(compatible, (list, tuple)):
        return _capped(findings)
    names = _strings(compatible)
    target = _target_skeleton(ctx)
    if not names:
        if target:
            findings.append(_finding(
                TOOL_ID + ".skeleton_compatible", Severity.WARNING,
                "compatible_with 为空，未声明与目标骨架 %r 的兼容关系" % target,
                node=target,
                fix_hint="在 Skeleton 资产的兼容列表中加入 %s，或确认该资产本就独立" % target,
                target=target))
        return _capped(findings)

    if target:
        keys = {_asset_key(name) for name in names}
        if _asset_key(target) not in keys:
            findings.append(_finding(
                TOOL_ID + ".skeleton_compatible", Severity.ERROR,
                "compatible_with %s 未覆盖规范目标骨架 %r" % (names, target),
                node=target,
                fix_hint="把 %s 加入 Skeleton 的兼容列表，或修改 spec.ue.skeleton_asset" % target,
                compatible=names, target=target))
    return _capped(findings)


def _target_skeleton(ctx: Any) -> str:
    value = _spec_section(ctx, "ue").get("skeleton_asset")
    return value.strip() if isinstance(value, str) and value.strip() else ""


# --------------------------------------------------------------------------
# 2. 必需骨骼链
# --------------------------------------------------------------------------
@rule(TOOL_ID + ".required_bone_chain", severity=Severity.ERROR,
      description="人形骨架必须包含 root/pelvis/spine_01/neck/head 骨骼链",
      dcc=_DCC, plugin_id=TOOL_ID)
def required_bone_chain(ctx: Any) -> List[Finding]:
    """骨骼链断裂会让重定向与动画曲线绑定失效。"""
    skeleton = _section(ctx, "skeleton")
    if skeleton is None:
        return []
    bones = skeleton.get("bones")
    if not isinstance(bones, (list, tuple)):
        return []
    names = _strings(bones)
    if not names:
        return []  # 空集合由 skeleton_compatible 报，避免重复
    findings: List[Finding] = []

    root_bone = skeleton.get("root_bone")
    if isinstance(root_bone, str) and root_bone and root_bone not in names:
        findings.append(_finding(
            TOOL_ID + ".required_bone_chain", Severity.ERROR,
            "root_bone %r 不在骨骼列表中" % root_bone, node=root_bone,
            fix_hint="确认 Skeleton 的根骨骼名，或修正采集脚本的 root_bone 字段",
            root_bone=root_bone))

    present = [name for name in _REQUIRED_CHAIN if name in names]
    humanoid = str(skeleton.get("kind") or "").lower() == "humanoid"
    if not humanoid and len(present) < 2:
        return _capped(findings)  # 道具 / 单链骨架不强制人形链
    missing = [name for name in _REQUIRED_CHAIN if name not in names]
    for name in missing:
        findings.append(_finding(
            TOOL_ID + ".required_bone_chain", Severity.ERROR,
            "缺少必需骨骼 %r（人形链 root→pelvis→spine_01→neck→head）" % name,
            node=name,
            fix_hint="补建 %r 并重建蒙皮与动画轨道，或在 spec 中声明该资产非人形" % name,
            missing=name))
    return _capped(findings)


# --------------------------------------------------------------------------
# 3. 骨骼命名一致性
# --------------------------------------------------------------------------
@rule(TOOL_ID + ".bone_naming", severity=Severity.WARNING,
      description="骨骼名不得带命名空间/分隔符，且不得仅靠大小写区分",
      dcc=_DCC, plugin_id=TOOL_ID)
def bone_naming(ctx: Any) -> List[Finding]:
    """命名不一致会让曲线、重定向与动画蓝图找不到骨骼。"""
    skeleton = _section(ctx, "skeleton")
    if skeleton is None:
        return []
    bones = skeleton.get("bones")
    if not isinstance(bones, (list, tuple)):
        return []
    findings: List[Finding] = []
    seen: Dict[str, List[str]] = {}

    for item in bones:
        if not isinstance(item, str):
            findings.append(_finding(
                TOOL_ID + ".bone_naming", Severity.WARNING,
                "骨骼列表中存在非字符串条目 %r" % (item,),
                fix_hint="重新采集 Skeleton 的 ReferenceSkeleton 骨骼名，确保全为字符串",
                value=str(item)))
            continue
        name = item
        if not name:
            continue
        hits = [char for char in _FORBIDDEN_CHARS if char in name]
        if hits:
            findings.append(_finding(
                TOOL_ID + ".bone_naming", Severity.ERROR,
                "骨骼名 %r 含禁用字符 %s，导入后路径会与 DCC 不一致" % (
                    name, "、".join(repr(char) for char in hits)),
                node=name,
                fix_hint="在 DCC 侧去掉命名空间前缀与空格后重新导出 FBX，再导入 UE",
                name=name, tokens=hits))
        seen.setdefault(name.lower(), []).append(name)

    for names in seen.values():
        if len(names) > 1:
            findings.append(_finding(
                TOOL_ID + ".bone_naming", Severity.WARNING,
                "骨骼名仅靠大小写区分：%s" % "、".join(sorted(names)),
                node=sorted(names)[0],
                fix_hint="删除重复骨骼或改用统一命名，避免 UE 骨骼名冲突",
                names=sorted(names)))
    return _capped(findings)


# --------------------------------------------------------------------------
# 4. 根运动设置
# --------------------------------------------------------------------------
@rule(TOOL_ID + ".root_motion", severity=Severity.ERROR,
      description="启用根运动的 AnimSequence 必须有可用的根骨骼",
      dcc=_DCC, plugin_id=TOOL_ID)
def root_motion(ctx: Any) -> List[Finding]:
    """没有根骨骼却开根运动，运行时会原地漂移或位移丢失。"""
    sequences = _entries(ctx, "anim_sequences")
    if not sequences:
        return []
    skeleton = _section(ctx, "skeleton") or {}
    bones = _strings(skeleton.get("bones"))
    root_bone = skeleton.get("root_bone") if isinstance(
        skeleton.get("root_bone"), str) else ""
    findings: List[Finding] = []

    for sequence in sequences:
        if "root_motion" not in sequence:
            continue  # 字段缺失跳过
        name = str(sequence.get("name") or "")
        value = sequence.get("root_motion")
        if not isinstance(value, bool):
            findings.append(_finding(
                TOOL_ID + ".root_motion", Severity.WARNING,
                "AnimSequence %r 的 root_motion 不是布尔值：%r" % (name, value),
                node=name,
                fix_hint="读取 enable_root_motion 并统一成 bool；不要用字符串占位",
                sequence=name, value=str(value)))
            continue
        if not value:
            continue
        if not bones:
            continue  # 骨骼集合缺失时无法判断，跳过而不是臆造
        if not root_bone or root_bone not in bones:
            findings.append(_finding(
                TOOL_ID + ".root_motion", Severity.ERROR,
                "AnimSequence %r 启用了根运动，但 Skeleton 没有可用的根骨骼 %r" % (
                    name, root_bone or "root"),
                node=name,
                fix_hint="在 Skeleton 中补出根骨骼，或关闭该序列的 Enable Root Motion",
                sequence=name, root_bone=root_bone))
    return _capped(findings)


# --------------------------------------------------------------------------
# 5. AnimSequence 压缩设置
# --------------------------------------------------------------------------
@rule(TOOL_ID + ".anim_compression", severity=Severity.WARNING,
      description="AnimSequence 压缩方案必须与规范 ue.animation_compression 一致",
      dcc=_DCC, plugin_id=TOOL_ID)
def anim_compression(ctx: Any) -> List[Finding]:
    """压缩方案不一致会导致曲线与精度在不同序列间产生偏差。"""
    expected = _spec_section(ctx, "ue").get("animation_compression")
    if not isinstance(expected, str) or not expected.strip():
        return []
    target = _asset_key(expected)
    sequences = _entries(ctx, "anim_sequences")
    findings: List[Finding] = []
    for sequence in sequences:
        if "compression" not in sequence:
            continue  # 字段缺失跳过
        name = str(sequence.get("name") or "")
        value = sequence.get("compression")
        if not isinstance(value, str) or not value.strip():
            findings.append(_finding(
                TOOL_ID + ".anim_compression", Severity.ERROR,
                "AnimSequence %r 的压缩设置为空" % name, node=name,
                fix_hint="在资产细节面板选择 %r（或项目约定的压缩方案）后重新保存" % expected,
                sequence=name, compression=""))
            continue
        if _asset_key(value) != target:
            findings.append(_finding(
                TOOL_ID + ".anim_compression", Severity.WARNING,
                "AnimSequence %r 的压缩方案 %r 与规范 %r 不一致" % (name, value, expected),
                node=name,
                fix_hint="把该序列的压缩方案改为 %r，或更新 spec.ue.animation_compression" % expected,
                sequence=name, compression=value, expected=expected))
    return _capped(findings)


# --------------------------------------------------------------------------
# 6. 曲线丢失
# --------------------------------------------------------------------------
@rule(TOOL_ID + ".curve_loss", severity=Severity.ERROR,
      description="curve_count 大于 0 时曲线列表不得为空，数量需与列表一致",
      dcc=_DCC, plugin_id=TOOL_ID)
def curve_loss(ctx: Any) -> List[Finding]:
    """曲线在导入时丢失，会让武器/表情等依赖曲线的逻辑全部失效。"""
    findings: List[Finding] = []
    for sequence in _entries(ctx, "anim_sequences"):
        count = sequence.get("curve_count")
        if isinstance(count, bool) or not isinstance(count, int):
            continue  # 字段缺失或类型不可信时跳过
        name = str(sequence.get("name") or "")
        curves = sequence.get("curves")
        if curves is None:
            continue  # 字段缺失跳过
        if not isinstance(curves, (list, tuple)):
            findings.append(_finding(
                TOOL_ID + ".curve_loss", Severity.WARNING,
                "AnimSequence %r 的 curves 不是列表：%r" % (name, curves),
                node=name, fix_hint="采集曲线名列表（float_curve_names）后重跑校验",
                sequence=name, curve_count=count))
            continue
        names = [str(item) for item in curves if isinstance(item, str) and str(item)]
        if count > 0 and not names:
            findings.append(_finding(
                TOOL_ID + ".curve_loss", Severity.ERROR,
                "AnimSequence %r 声明 %d 条曲线，但 UE 侧曲线列表为空（曲线丢失）" % (
                    name, count),
                node=name,
                fix_hint="确认导出时勾选曲线/动画通知，且 FBX 曲线未被压缩清理；"
                         "重新导入后核对动画曲线列表",
                sequence=name, curve_count=count, curves=0))
        elif count > 0 and len(names) < count:
            findings.append(_finding(
                TOOL_ID + ".curve_loss", Severity.WARNING,
                "AnimSequence %r 的曲线数量 %d 少于 curve_count=%d，可能部分丢失" % (
                    name, len(names), count),
                node=name,
                fix_hint="逐条核对曲线名，确认不是采集截断或压缩丢弃",
                sequence=name, curve_count=count, curves=len(names)))
        elif count == 0 and names:
            findings.append(_finding(
                TOOL_ID + ".curve_loss", Severity.WARNING,
                "AnimSequence %r 的 curve_count=0，但仍存在 %d 条曲线" % (
                    name, len(names)),
                node=name,
                fix_hint="修正 curve_count 的采集逻辑（应以 UE 资产为准）",
                sequence=name, curve_count=0, curves=len(names)))
    return _capped(findings)


# --------------------------------------------------------------------------
# 7. 材质槽缺失
# --------------------------------------------------------------------------
@rule(TOOL_ID + ".material_slots", severity=Severity.ERROR,
      description="SkeletalMesh 必须有材质槽，且槽位不得为空",
      dcc=_DCC, plugin_id=TOOL_ID)
def material_slots(ctx: Any) -> List[Finding]:
    """材质槽为 0 或为空槽，导入后模型会显示默认棋盘格。"""
    findings: List[Finding] = []
    for mesh in _entries(ctx, "skeletal_meshes"):
        if "material_slots" not in mesh:
            continue  # 字段缺失跳过
        name = str(mesh.get("name") or "")
        slots = mesh.get("material_slots")
        if isinstance(slots, bool):
            continue
        if isinstance(slots, int):
            if slots <= 0:
                findings.append(_finding(
                    TOOL_ID + ".material_slots", Severity.ERROR,
                    "SkeletalMesh %r 没有任何材质槽" % name, node=name,
                    fix_hint="在 DCC 中给模型分配材质后重新导出，或导入后在 UE 内补槽",
                    mesh=name, material_slots=slots))
            continue
        if isinstance(slots, (list, tuple)):
            if not slots:
                findings.append(_finding(
                    TOOL_ID + ".material_slots", Severity.ERROR,
                    "SkeletalMesh %r 的材质槽列表为空" % name, node=name,
                    fix_hint="补上材质槽并指定材质实例后重新导入",
                    mesh=name, material_slots=0))
                continue
            empty = [index for index, item in enumerate(slots)
                     if not isinstance(item, str) or not item.strip()]
            if empty:
                findings.append(_finding(
                    TOOL_ID + ".material_slots", Severity.WARNING,
                    "SkeletalMesh %r 存在空材质槽：索引 %s" % (
                        name, "、".join(str(index) for index in empty)),
                    node=name,
                    fix_hint="为空槽指定材质实例，或删除无用槽位后重新导入",
                    mesh=name, empty_slots=empty))
            continue
        findings.append(_finding(
            TOOL_ID + ".material_slots", Severity.WARNING,
            "SkeletalMesh %r 的 material_slots 类型不可识别：%r" % (name, slots),
            node=name,
            fix_hint="采集材质槽数量（int）或槽名列表（list[str]）",
            mesh=name))
    return _capped(findings)


# --------------------------------------------------------------------------
# 8. LOD 的 allow_deformer 标记
# --------------------------------------------------------------------------
@rule(TOOL_ID + ".lod_allow_deformer", severity=Severity.WARNING,
      description="LOD 的 allow_deformer 必须显式为 bool，且与 spec 允许策略一致",
      dcc=_DCC, plugin_id=TOOL_ID)
def lod_allow_deformer(ctx: Any) -> List[Finding]:
    """低 LOD 上开 deformer 会带来无法预估的运行时开销。"""
    allow = _spec_section(ctx, "ue").get("allow_mesh_deformer")
    spec_allowed = bool(allow) if isinstance(allow, bool) else False
    findings: List[Finding] = []
    for mesh in _entries(ctx, "skeletal_meshes"):
        lods = mesh.get("lods")
        if not isinstance(lods, (list, tuple)):
            continue  # 字段缺失跳过
        name = str(mesh.get("name") or "")
        for index, lod in enumerate(lods):
            if not isinstance(lod, dict) or "allow_deformer" not in lod:
                continue
            value = lod.get("allow_deformer")
            node = "%s[LOD%d]" % (name, index) if name else "LOD%d" % index
            if not isinstance(value, bool):
                findings.append(_finding(
                    TOOL_ID + ".lod_allow_deformer", Severity.WARNING,
                    "%s 的 allow_deformer 不是布尔值：%r" % (node, value), node=node,
                    fix_hint="按资产真实配置写 true/false，不要用 0/1 或字符串占位",
                    mesh=name, lod=index, value=str(value)))
                continue
            if value and not spec_allowed:
                findings.append(_finding(
                    TOOL_ID + ".lod_allow_deformer", Severity.WARNING,
                    "%s 允许 mesh deformer，但规范 ue.allow_mesh_deformer=false" % node,
                    node=node,
                    fix_hint="关闭该 LOD 的 Allow Deformer，或与图形确认后更新 spec",
                    mesh=name, lod=index, allow_deformer=True))
    return _capped(findings)


# --------------------------------------------------------------------------
# 9. 导入快照与 sidecar 不匹配
# --------------------------------------------------------------------------
@rule(TOOL_ID + ".sidecar_mismatch", severity=Severity.ERROR,
      description="UE 侧 Skeleton 快照必须与 Export Bridge 的 sidecar 一致",
      dcc=_DCC, plugin_id=TOOL_ID)
def sidecar_mismatch(ctx: Any) -> List[Finding]:
    """sidecar 是导出前的唯一事实源；对不上说明导入链路有信息损失。"""
    ue_import = _ue(ctx).get("import")
    if not isinstance(ue_import, dict):
        return []
    sidecar = ue_import.get("sidecar")
    if not isinstance(sidecar, dict):
        return []
    before = _snapshot_of(sidecar)
    after = _snapshot(ctx)
    if not _joint_names(before) or not _joint_names(after):
        return []  # 任一侧没有 joints 时无法比对，跳过

    try:
        from tak.core.diff import SkeletonSnapshot, diff_snapshots

        raw = diff_snapshots(SkeletonSnapshot.from_dict(before),
                             SkeletonSnapshot.from_dict(after))
    except (TypeError, ValueError):
        return []

    findings: List[Finding] = []
    for item in raw:
        field = str(item.get("field") or "")
        if field in ("joint_missing", "joint_added", "joint_renamed", "joint_count",
                     "parent"):
            findings.append(_finding(
                TOOL_ID + ".sidecar_mismatch", Severity.ERROR,
                "sidecar 与 UE 快照不一致：%s" % _describe(field, item),
                node=str(item.get("joint") or item.get("before") or ""),
                fix_hint="按字段核对导入选项（命名空间、骨骼过滤、重命名），"
                         "修正后重新导出并重跑校验",
                field=field, diff=dict(item)))
        elif field in ("unit", "up_axis"):
            findings.append(_finding(
                TOOL_ID + ".sidecar_mismatch", Severity.WARNING,
                "sidecar 与 UE 快照不一致：%s" % _describe(field, item),
                fix_hint="确认导入时单位与轴向设置，必要时在 spec.export.fbx 修正后重导出",
                field=field, diff=dict(item)))
        # bind_matrix / frame_range 不参与本规则：UE Python 不暴露骨骼静止
        # 变换，帧范围由 Export Bridge 侧负责。
    return _capped(findings)


def _snapshot_of(value: Any) -> Dict[str, Any]:
    """sidecar 可能是 {"snapshot": {...}}，也可能是裸快照。"""
    data = value if isinstance(value, dict) else {}
    if "joints" not in data and isinstance(data.get("snapshot"), dict):
        data = data.get("snapshot")
    return data if isinstance(data, dict) else {}


def _joint_names(snapshot: Dict[str, Any]) -> List[str]:
    joints = snapshot.get("joints")
    return list(joints) if isinstance(joints, dict) else []


def _describe(field: str, item: Dict[str, Any]) -> str:
    if field == "joint_missing":
        return "关节 %r 在 UE 侧丢失" % item.get("joint")
    if field == "joint_added":
        return "UE 侧多出关节 %r" % item.get("joint")
    if field == "joint_renamed":
        return "关节被重命名 %r → %r" % (item.get("before"), item.get("after"))
    if field == "joint_count":
        return "关节数量 %s → %s" % (item.get("before"), item.get("after"))
    if field == "parent":
        return "关节 %r 的父级变化 %r → %r" % (
            item.get("joint"), item.get("before"), item.get("after"))
    if field in ("unit", "up_axis"):
        return "%s %r → %r" % (field, item.get("before"), item.get("after"))
    return "字段 %r 发生变化" % field


RULE_IDS = (
    TOOL_ID + ".skeleton_compatible",
    TOOL_ID + ".required_bone_chain",
    TOOL_ID + ".bone_naming",
    TOOL_ID + ".root_motion",
    TOOL_ID + ".anim_compression",
    TOOL_ID + ".curve_loss",
    TOOL_ID + ".material_slots",
    TOOL_ID + ".lod_allow_deformer",
    TOOL_ID + ".sidecar_mismatch",
)
