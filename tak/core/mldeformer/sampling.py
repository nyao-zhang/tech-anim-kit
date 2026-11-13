# -*- coding: utf-8 -*-
"""ML Deformer 姿态采样计划：只规划「拍什么姿势」，不碰任何训练入口。

边界
====
UE 侧 ``MLDeformerAsset`` / ``NeuralMorphModel`` / ``DeformerGraphAsset`` 在
5.7 与 5.8 的 Python 侧均未暴露（见 ``docs/VALIDATION.md`` B2、``docs/WEB_PANEL.md``
W3 / W30，能力位 ``mldeformer.train`` 恒为 False）。因此本模块**只产出离线采样
计划**：关节 / 轴 / 角度 / 类别 / 标签，供美术按计划摆姿势、录制训练数据。
本模块不含、也不会声称含任何训练调用。

确定性
======
* 随机采样只用局部 ``random.Random(seed)``，不使用全局 ``random``；
* 采样顺序固定为「关节名升序 → 轴 x/y/z」，因此同一 seed 必然得到同一序列；
* 去重键为 ``(joint, axis, angle)``，重复时保留类别优先级更高的一条，
  并把后出现类别的标签并入 ``tags``（信息不丢、行不重复）；
* 排序键为「类别固定序 → 关节名升序 → 轴升序 → 角度升序」，不写时间戳，
  因此同一输入两次运行的计划可逐字节比较。

输入兜底
========
``snapshot`` 可为 ``SkeletonSnapshot``、其 ``to_dict()``、或直接是 joints 容器。
字段缺失 / 类型畸形一律降级为「空计划 + warnings」，绝不抛异常。
"""
from __future__ import annotations

import math
import random
import re
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

DATASET_CONTRACT = "1.0"
DEFAULT_SEED = 20240101
DEFAULT_MAX_POSES = 240
MAX_POSES_HARD_LIMIT = 2000

#: 类别固定序：同时决定去重优先级与排序先后。
CATEGORY_ORDER = ("extreme", "mid", "grid", "rest", "mirror", "random")
_CATEGORY_RANK = {name: index for index, name in enumerate(CATEGORY_ORDER)}
AXES = ("x", "y", "z")
_AXIS_RANK = {name: index for index, name in enumerate(AXES)}
#: 零位姿势不对应单一轴，单独给一个排在轴之前的 rank。
_REST_AXIS = "*"

#: 关节名启发式安全角度（度）。按顺序命中，先具体后笼统，命中即停。
_LIMIT_HEURISTICS: Tuple[Tuple[Tuple[str, ...], float], ...] = (
    (("lowerarm", "forearm", "elbow"), 150.0),
    (("lowerleg", "calf", "knee"), 150.0),
    (("upperarm", "shoulder", "clavicle"), 180.0),
    (("spine", "chest", "torso", "waist"), 45.0),
    (("pelvis", "hip"), 45.0),
    (("neck",), 60.0),
    (("head", "jaw", "eye"), 60.0),
    (("wrist", "hand", "finger", "thumb", "index", "middle", "ring", "pinky"), 90.0),
    (("ankle", "foot", "toe", "ball"), 90.0),
    (("thigh",), 120.0),
)
_DEFAULT_LIMIT = 90.0
#: 角度网格的相对位置（相对 [lo, hi] 归一化），刻意避开极值 0/1 与半值 1/4、3/4，
#: 否则对称区间下网格点会与 extreme / mid 完全重合而被去重吃掉。
_GRID_FRACTIONS = (1.0 / 6.0, 5.0 / 6.0)
#: 引擎旋转的绝对上限，防止畸形 limits 把角度放大到无意义区间。
_ANGLE_CEILING = 360.0
_MIRROR_RE = re.compile(r"^(?P<base>.+?)(?P<sep>[_.\-])(?P<side>l|left|r|right)$",
                        re.IGNORECASE)
_MIRROR_EPSILON = 1e-6


@dataclass
class PoseSamplePlan:
    """采样计划：纯 JSON 数据，可直接写盘或喂给 UI。

    ``poses`` 每项为 ``{"pose_id", "category", "joint", "axis", "angle",
    "label", "tags"}``；``categories`` 是最终存活 pose 的类别计数（按固定序）；
    ``warnings`` 说明降级 / 截断原因，``notes`` 写口径与边界声明。
    """

    contract: str = DATASET_CONTRACT
    joint_count: int = 0
    poses: List[Dict[str, Any]] = field(default_factory=list)
    categories: Dict[str, int] = field(default_factory=dict)
    seed: int = DEFAULT_SEED
    warnings: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """转成严格 JSON 安全的 dict（角度为 float、tags 为 list）。"""
        return {
            "contract": self.contract,
            "joint_count": int(self.joint_count),
            "poses": [dict(pose) for pose in self.poses],
            "categories": dict(self.categories),
            "seed": int(self.seed),
            "warnings": list(self.warnings),
            "notes": list(self.notes),
        }


# --------------------------------------------------------------------------- #
# 选项解析（全部容错，坏值降级为缺省 + warning）
# --------------------------------------------------------------------------- #
def _as_options(options: Any) -> Dict[str, Any]:
    return dict(options) if isinstance(options, dict) else {}


def _warn(warnings: List[str], message: str) -> None:
    if message not in warnings:
        warnings.append(message)


def _seed_of(options: Dict[str, Any], warnings: List[str]) -> int:
    value = options.get("seed", DEFAULT_SEED)
    if isinstance(value, bool) or not isinstance(value, (int, str, float)):
        _warn(warnings, "seed 类型非法，已使用缺省值 %d" % DEFAULT_SEED)
        return DEFAULT_SEED
    try:
        return int(value)
    except (TypeError, ValueError):
        _warn(warnings, "seed 非法，已使用缺省值 %d" % DEFAULT_SEED)
        return DEFAULT_SEED


def _budget_of(options: Dict[str, Any], warnings: List[str]) -> int:
    value = options.get("max_poses", DEFAULT_MAX_POSES)
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        _warn(warnings, "max_poses 类型非法，已使用缺省值 %d" % DEFAULT_MAX_POSES)
        return DEFAULT_MAX_POSES
    try:
        number = int(value)
    except (TypeError, ValueError):
        _warn(warnings, "max_poses 非法，已使用缺省值 %d" % DEFAULT_MAX_POSES)
        return DEFAULT_MAX_POSES
    if number <= 0:
        _warn(warnings, "max_poses 必须为正数，已使用缺省值 %d" % DEFAULT_MAX_POSES)
        return DEFAULT_MAX_POSES
    if number > MAX_POSES_HARD_LIMIT:
        _warn(warnings, "max_poses=%d 超出硬上限，已夹取到 %d"
              % (number, MAX_POSES_HARD_LIMIT))
        return MAX_POSES_HARD_LIMIT
    return number


def _categories_of(options: Dict[str, Any], warnings: List[str]) -> List[str]:
    """返回要采样的类别（按固定序）；缺省为全部类别。"""
    raw = options.get("categories")
    if raw is None:
        return list(CATEGORY_ORDER)
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, (list, tuple, set, frozenset)):
        _warn(warnings, "categories 类型非法，已按全部类别采样")
        return list(CATEGORY_ORDER)
    picked = set()
    for item in raw:
        if not isinstance(item, str):
            _warn(warnings, "categories 含非字符串项，已忽略")
            continue
        name = item.strip().lower()
        if name in _CATEGORY_RANK:
            picked.add(name)
        elif name:
            _warn(warnings, "未知类别已忽略：%s" % name)
    if not picked:
        _warn(warnings, "categories 未命中任何已知类别，返回空计划")
        return []
    return [name for name in CATEGORY_ORDER if name in picked]


def _finite(value: Any) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _normalize_range(pair: Any) -> Optional[Tuple[float, float]]:
    """把 [lo, hi] / 单值 规整为有序、有限、不超过 360° 的区间。"""
    if isinstance(pair, (list, tuple)):
        if len(pair) < 2:
            single = _finite(pair[0]) if pair else None
            if single is None:
                return None
            pair = (-abs(single), abs(single))
        else:
            low, high = _finite(pair[0]), _finite(pair[1])
            if low is None or high is None:
                return None
            if low > high:
                low, high = high, low
            pair = (low, high)
    else:
        single = _finite(pair)
        if single is None:
            return None
        pair = (-abs(single), abs(single))
    low = max(-_ANGLE_CEILING, min(_ANGLE_CEILING, float(pair[0])))
    high = max(-_ANGLE_CEILING, min(_ANGLE_CEILING, float(pair[1])))
    if low > high:
        low, high = high, low
    return (low, high)


def _axis_key(key: Any) -> Optional[str]:
    if not isinstance(key, str):
        return None
    token = key.strip().lower()
    if token.startswith("rotation_"):
        token = token[len("rotation_"):]
    if token.startswith("r") and len(token) == 2 and token[1:] in AXES:
        return token[1:]
    if token in AXES:
        return token
    if token in ("*", "all", "any"):
        return "*"
    return None


def _parse_limits(raw: Any, warnings: List[str]) -> Dict[str, Dict[str, Tuple[float, float]]]:
    """解析 ``options["limits"]``，非法项逐条 warning，不中断。"""
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        _warn(warnings, "limits 必须是 {关节: 区间} 的对象，已忽略")
        return {}
    parsed: Dict[str, Dict[str, Tuple[float, float]]] = {}
    for joint, spec in raw.items():
        if not isinstance(joint, str) or not joint.strip():
            _warn(warnings, "limits 含非法关节名，已忽略")
            continue
        name = joint.strip()
        entry: Dict[str, Tuple[float, float]] = {}
        if isinstance(spec, dict):
            for key, value in spec.items():
                axis = _axis_key(key)
                if axis is None:
                    _warn(warnings, "limits[%s] 含未知轴 %r，已忽略" % (name, key))
                    continue
                normalized = _normalize_range(value)
                if normalized is None:
                    _warn(warnings, "limits[%s][%s] 区间非法，已忽略" % (name, key))
                    continue
                entry[axis] = normalized
        else:
            normalized = _normalize_range(spec)
            if normalized is None:
                _warn(warnings, "limits[%s] 区间非法，已忽略" % name)
                continue
            entry["*"] = normalized
        if entry:
            parsed[name] = entry
    return parsed


# --------------------------------------------------------------------------- #
# 快照解析
# --------------------------------------------------------------------------- #
def _joint_names(snapshot: Any) -> List[str]:
    """从任意快照形态里提取关节名，升序去重；畸形输入返回空列表。"""
    data = snapshot
    if data is None:
        return []
    if not isinstance(data, dict) and hasattr(data, "to_dict"):
        try:
            data = data.to_dict()
        except Exception:
            return []
    if isinstance(data, (list, tuple)):
        raw: Any = data
    elif isinstance(data, dict):
        raw = data.get("joints")
    else:
        return []
    names: List[str] = []
    if isinstance(raw, dict):
        for key, value in raw.items():
            if isinstance(key, str) and key.strip():
                names.append(key.strip())
            elif isinstance(value, dict):
                name = value.get("name")
                if isinstance(name, str) and name.strip():
                    names.append(name.strip())
    elif isinstance(raw, (list, tuple)):
        for item in raw:
            if isinstance(item, str) and item.strip():
                names.append(item.strip())
            elif isinstance(item, dict):
                name = item.get("name")
                if isinstance(name, str) and name.strip():
                    names.append(name.strip())
    return sorted(set(names))


def _heuristic_limit(joint: str) -> float:
    token = re.sub(r"[^a-z]", "", joint.lower())
    for keys, limit in _LIMIT_HEURISTICS:
        if any(key in token for key in keys):
            return limit
    return _DEFAULT_LIMIT


def _axis_range(joint: str, axis: str,
                limits: Dict[str, Dict[str, Tuple[float, float]]]
                ) -> Tuple[float, float]:
    """关节某轴的角度区间：显式 limits 优先，且**绝不超出**它。"""
    entry = limits.get(joint)
    if entry:
        if axis in entry:
            return entry[axis]
        if "*" in entry:
            return entry["*"]
    limit = _heuristic_limit(joint)
    return (-limit, limit)


def _mirror_partner(joint: str) -> Optional[str]:
    match = _MIRROR_RE.match(joint)
    if not match:
        return None
    side = match.group("side").lower()
    target = "r" if side in ("l", "left") else "l"
    original = match.group("side")
    if original.isupper():
        replacement = target.upper()
    elif original[:1].isupper():
        replacement = target.upper()
    else:
        replacement = target
    return match.group("base") + match.group("sep") + replacement


# --------------------------------------------------------------------------- #
# 姿势生成
# --------------------------------------------------------------------------- #
def _normalize_angle(value: float) -> float:
    """角度统一保留 3 位小数，并消除 -0.0，保证 digest 稳定。"""
    rounded = round(float(value), 3)
    return 0.0 if rounded == 0 else rounded


def _axis_label(axis: str) -> str:
    return "全轴" if axis == _REST_AXIS else axis.upper()


def _make_pose(category: str, joint: str, axis: str, angle: float,
               note: str, tags: Sequence[str]) -> Dict[str, Any]:
    """构造一条 pose；角度归一化、标签去重排序、pose_id 由内容派生。"""
    angle = _normalize_angle(angle)
    tag_list = sorted({str(tag) for tag in tags if str(tag)})
    return {
        "pose_id": "%s:%s:%s:%s" % (category, joint, axis, _angle_text(angle)),
        "category": category,
        "joint": joint,
        "axis": axis,
        "angle": angle,
        "label": "%s · %s %s %s°%s" % (
            joint, _axis_label(axis), _category_label(category),
            _angle_text(angle), ("（%s）" % note) if note else ""),
        "tags": tag_list,
    }


def _angle_text(angle: float) -> str:
    """角度转成最短十进制文本（去掉尾随 0），用于稳定 pose_id 与 label。"""
    text = ("%.3f" % angle).rstrip("0").rstrip(".")
    return text if text not in ("", "-0") else "0"


def _category_label(category: str) -> str:
    """类别的中文显示名；未知类别原样返回。"""
    return {
        "extreme": "极值",
        "mid": "半极值",
        "grid": "网格",
        "rest": "零位",
        "mirror": "镜像",
        "random": "随机",
    }.get(category, category)


def _sort_key(pose: Dict[str, Any]) -> Tuple[Any, ...]:
    return (
        _CATEGORY_RANK.get(pose["category"], len(CATEGORY_ORDER)),
        pose["joint"],
        _AXIS_RANK.get(pose["axis"], -1 if pose["axis"] == _REST_AXIS else 99),
        pose["angle"],
    )


def _generate(category: str, joints: Sequence[str],
              limits: Dict[str, Dict[str, Tuple[float, float]]],
              rng: random.Random) -> List[Dict[str, Any]]:
    """按类别生成候选 pose（尚未去重与裁剪）；顺序固定为关节 → 轴。"""
    poses: List[Dict[str, Any]] = []
    if category == "rest":
        for joint in joints:
            poses.append(_make_pose("rest", joint, _REST_AXIS, 0.0,
                                    "所有轴归零", ["rest", "zero"]))
        return poses
    if category == "mirror":
        return _mirror_poses(joints, limits)
    for joint in joints:
        for axis in AXES:
            low, high = _axis_range(joint, axis, limits)
            if category == "extreme":
                for angle in (low, high):
                    poses.append(_make_pose("extreme", joint, axis, angle,
                                            "安全限值边界", ["extreme"]))
            elif category == "mid":
                for angle in (low / 2.0, high / 2.0):
                    poses.append(_make_pose("mid", joint, axis, angle,
                                            "半极值", ["mid"]))
            elif category == "grid":
                span = high - low
                for fraction in _GRID_FRACTIONS:
                    poses.append(_make_pose(
                        "grid", joint, axis, low + span * fraction,
                        "网格采样", ["grid"]))
            elif category == "random":
                angle = rng.uniform(low, high)
                poses.append(_make_pose("random", joint, axis, angle,
                                        "种子随机", ["random"]))
    return poses


def _mirror_poses(joints: Sequence[str],
                  limits: Dict[str, Dict[str, Tuple[float, float]]]
                  ) -> List[Dict[str, Any]]:
    """把一侧的极值镜像到对侧；超出对侧 limits 的镜像一律丢弃（绝不越界）。

    左右限值对称时，镜像角度必然与对侧自身的极值 / 半极值重合，按
    ``(joint, axis, angle)`` 去重后只剩一条（tags 里保留 ``mirror`` 标记）。
    因此对称骨架的 ``mirror`` 计数为 0 属预期行为，见 plan.notes。
    """
    available = set(joints)
    poses: List[Dict[str, Any]] = []
    for joint in joints:
        partner = _mirror_partner(joint)
        if not partner or partner not in available:
            continue
        for axis in AXES:
            low, high = _axis_range(joint, axis, limits)
            partner_low, partner_high = _axis_range(partner, axis, limits)
            for angle in (low, high):
                if angle < partner_low - _MIRROR_EPSILON:
                    continue
                if angle > partner_high + _MIRROR_EPSILON:
                    continue
                poses.append(_make_pose(
                    "mirror", partner, axis, angle,
                    "镜像自 %s" % joint, ["mirror", "mirror_of:%s" % joint]))
    return poses


def _merge_duplicates(candidates: Sequence[Dict[str, Any]]
                      ) -> Tuple[List[Dict[str, Any]], int]:
    """按 (joint, axis, angle) 去重；后出现的类别标签并入先出现的行。"""
    merged: Dict[Tuple[str, str, float], Dict[str, Any]] = {}
    dropped = 0
    for pose in candidates:
        key = (pose["joint"], pose["axis"], pose["angle"])
        existing = merged.get(key)
        if existing is None:
            merged[key] = pose
            continue
        dropped += 1
        tags = set(existing.get("tags") or []) | set(pose.get("tags") or [])
        existing["tags"] = sorted(tags)
    return list(merged.values()), dropped


def _truncate(poses: Sequence[Dict[str, Any]], limit: int
              ) -> Tuple[List[Dict[str, Any]], int]:
    """按 (类别, 关节) 轮转裁剪，避免预算把后半段关节或整类姿势砍掉。

    首轮先把每个「类别 × 关节」桶的第 1 条铺满，再逐轮加厚，因此
    ``max_poses`` 再紧也能保证类别齐全、各关节都有数据。
    """
    if len(poses) <= limit:
        return list(poses), 0
    buckets: Dict[Tuple[int, str], List[Dict[str, Any]]] = {}
    for pose in poses:
        key = (_CATEGORY_RANK.get(pose["category"], len(CATEGORY_ORDER)),
               pose["joint"])
        buckets.setdefault(key, []).append(pose)
    order = sorted(buckets)
    selected: List[Dict[str, Any]] = []
    round_index = 0
    while len(selected) < limit:
        progressed = False
        for key in order:
            bucket = buckets[key]
            if round_index >= len(bucket):
                continue
            selected.append(bucket[round_index])
            progressed = True
            if len(selected) >= limit:
                break
        if not progressed:
            break
        round_index += 1
    selected.sort(key=_sort_key)
    return selected, len(poses) - len(selected)


def _empty_plan(seed: int, warnings: List[str], notes: List[str]) -> PoseSamplePlan:
    return PoseSamplePlan(contract=DATASET_CONTRACT, joint_count=0, poses=[],
                          categories={}, seed=seed, warnings=list(warnings),
                          notes=list(notes))


# --------------------------------------------------------------------------- #
# 公开 API
# --------------------------------------------------------------------------- #
def build_plan(snapshot: Any, options: Optional[Dict[str, Any]] = None) -> PoseSamplePlan:
    """生成确定性姿态采样计划。

    参数
    ----
    snapshot
        ``SkeletonSnapshot``、``to_dict()`` 结果或 joints 容器；字段缺失 / 畸形
        时返回空计划并写 ``warnings``，**不抛异常**。
    options
        * ``max_poses``（缺省 240，硬上限 2000）
        * ``limits``：``{关节: {"x": [lo, hi], ...}}`` / ``{关节: [lo, hi]}`` /
          ``{关节: 单值}``；命中即硬边界，采样绝不越界。
        * ``categories``：只采指定类别（``extreme`` / ``mid`` / ``grid`` /
          ``rest`` / ``mirror`` / ``random``）。
        * ``seed``（缺省 20240101，仅影响 ``random`` 类别）
        * ``include_rest``（缺省 True）

    返回
    ----
    PoseSamplePlan：``poses`` 已按「类别固定序 → 关节升序 → 轴升序 → 角度升序」
    排序并去重，同一输入两次运行逐字节一致。
    """
    opts = _as_options(options)
    warnings: List[str] = []
    notes: List[str] = [
        "仅产出离线采样计划，不调用、也不假设存在任何训练入口；"
        "UE 侧 mldeformer.train 能力位为 false。",
        "角度单位：度；极值取安全限值边界，半极值为其 1/2，网格为 1/6 与 5/6 位"
        "（避开极值与半极值，避免去重后丢类别）。",
        "排序：类别固定序 → 关节名升序 → 轴升序 → 角度升序；随机仅用局部 Random(seed)。",
    ]
    seed = _seed_of(opts, warnings)
    budget = _budget_of(opts, warnings)
    wanted = _categories_of(opts, warnings)
    limits = _parse_limits(opts.get("limits"), warnings)
    include_rest = opts.get("include_rest", True)
    if not isinstance(include_rest, bool):
        _warn(warnings, "include_rest 非布尔值，已按 True 处理")
        include_rest = True
    joints = _joint_names(snapshot)
    if not joints:
        _warn(warnings, "快照为空或不含任何可用关节，已返回空采样计划")
        return _empty_plan(seed, warnings, notes)
    if not wanted:
        return _empty_plan(seed, warnings, notes)
    if not include_rest and "rest" in wanted:
        wanted = [name for name in wanted if name != "rest"]
        if not wanted:
            _warn(warnings, "include_rest=False 且未选择其它类别，返回空计划")
            return _empty_plan(seed, warnings, notes)

    rng = random.Random(seed)
    candidates: List[Dict[str, Any]] = []
    for category in CATEGORY_ORDER:
        if category not in wanted:
            continue
        candidates.extend(_generate(category, joints, limits, rng))

    poses, duplicates = _merge_duplicates(candidates)
    if duplicates:
        notes.append(
            "同 (joint, axis, angle) 的 %d 条候选已按类别优先级去重合并；"
            "左右限值对称时 mirror 会与 extreme / mid 重合，属预期。" % duplicates)
    poses.sort(key=_sort_key)
    poses, dropped = _truncate(poses, budget)
    if dropped:
        _warn(warnings, "候选 %d 条超出 max_poses=%d，已按类别/关节轮转裁剪 %d 条"
              % (len(candidates) - duplicates, budget, dropped))

    counts: Dict[str, int] = {}
    for category in CATEGORY_ORDER:
        number = sum(1 for pose in poses if pose["category"] == category)
        if number:
            counts[category] = number
    return PoseSamplePlan(contract=DATASET_CONTRACT, joint_count=len(joints),
                          poses=poses, categories=counts, seed=seed,
                          warnings=warnings, notes=notes)


def sample_summary(plan: Any) -> Dict[str, Any]:
    """给 UI 用的轻量摘要：数量、类别、Top 关节、角度区间。"""
    data = plan.to_dict() if hasattr(plan, "to_dict") else plan
    data = data if isinstance(data, dict) else {}
    poses = [pose for pose in (data.get("poses") or []) if isinstance(pose, dict)]
    counts: Dict[str, int] = {}
    joint_counts: Dict[str, int] = {}
    angles: List[float] = []
    for pose in poses:
        category = pose.get("category")
        joint = pose.get("joint")
        if isinstance(category, str):
            counts[category] = counts.get(category, 0) + 1
        if isinstance(joint, str):
            joint_counts[joint] = joint_counts.get(joint, 0) + 1
        angle = _finite(pose.get("angle"))
        if angle is not None:
            angles.append(angle)
    ordered = {name: counts[name] for name in CATEGORY_ORDER if name in counts}
    for name in sorted(set(counts) - set(ordered)):
        ordered[name] = counts[name]
    ranked = sorted(joint_counts.items(), key=lambda item: (-item[1], item[0]))
    joint_count = data.get("joint_count")
    if not isinstance(joint_count, int) or isinstance(joint_count, bool):
        joint_count = len(joint_counts)
    return {
        "pose_count": len(poses),
        "joint_count": joint_count,
        "categories": ordered,
        "top_joints": [{"joint": name, "pose_count": number}
                       for name, number in ranked[:5]],
        "angle_range": {
            "min": _normalize_angle(min(angles)) if angles else 0.0,
            "max": _normalize_angle(max(angles)) if angles else 0.0,
        },
    }


def category_order() -> List[str]:
    """返回类别固定序副本，供 UI / 测试与文档保持一致。"""
    return list(CATEGORY_ORDER)


def joint_angle_range(joint: str, axis: str,
                      limits: Optional[Dict[str, Any]] = None) -> Tuple[float, float]:
    """公开查询某关节某轴最终生效的角度区间（供测试与 UI 提示）。"""
    warnings: List[str] = []
    parsed = _parse_limits(limits, warnings)
    return _axis_range(str(joint), str(axis).lower(), parsed)


def iter_pose_keys(poses: Iterable[Any]) -> List[Tuple[str, str, float]]:
    """返回去重键列表，便于外部核对「同键只保留一条」。"""
    keys: List[Tuple[str, str, float]] = []
    for pose in poses or []:
        if not isinstance(pose, dict):
            continue
        keys.append((str(pose.get("joint")), str(pose.get("axis")),
                     float(pose.get("angle") or 0.0)))
    return keys
