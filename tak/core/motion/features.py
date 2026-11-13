# -*- coding: utf-8 -*-
"""动作类型词表与数值特征提取（纯计算，不解析任何文件）。

两类输入
--------
1. **带采样**：``{"samples": [{"frame": int, "root": [x, y, z],
   "joints": {name: [rx, ry, rz]}}, ...]}`` —— 逐帧真实计算速度、位移、
   循环误差、对称性与姿态方差；``features["estimated"] = 0.0``。
2. **不带采样**：只用 ``frame_count / fps / root_motion / loopable / bones /
   tags`` 推导保守占位值，``features["estimated"] = 1.0`` 明确标记「非实测」。

所有特征单位在 :func:`extract_features` 的 docstring 里逐项写明；结果统一
四舍五入到 6 位小数，同样的输入永远得到同样的输出（无随机数、无时间戳）。

坐标与角度约定：``root`` 为厘米（cm），``joints`` 为**角度**（度，deg），
时间由 ``frame / fps`` 得到（``fps <= 0`` 时按 30 fps 兜底），因此速度单位
分别是 ``cm/s`` 与 ``deg/s``。
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional

from .model import (DEFAULT_FPS, MotionClip, _as_bool, _as_float, _as_float_dict,
                    _as_int, _as_text, _as_text_list)

# --------------------------------------------------------------------------- #
# 常量
# --------------------------------------------------------------------------- #
MOTION_TYPES: Dict[str, str] = {
    "idle": "待机",
    "walk": "行走",
    "run": "奔跑",
    "jump": "跳跃",
    "attack": "攻击",
    "hit": "受击",
    "turn": "转身",
    "crouch": "蹲伏",
    "climb": "攀爬",
    "dance": "舞蹈",
    "death": "死亡",
    "other": "其它",
}
"""12 类动作（英文 key → 中文显示名）；``other`` 必须排在最后作为兜底。"""

MOTION_KEYWORDS: Dict[str, List[str]] = {
    "idle": ["idle", "stand", "breath", "待机", "呼吸", "站立", "静止"],
    "walk": ["walk", "walking", "走路", "行走", "步行", "散步", "走"],
    "run": ["run", "running", "sprint", "jog", "奔跑", "跑步", "冲刺", "跑"],
    "jump": ["jump", "jumping", "leap", "hop", "跳跃", "起跳", "腾空", "跳"],
    "attack": ["attack", "slash", "punch", "kick", "combo", "攻击", "挥砍",
               "出拳", "连击", "砍"],
    "hit": ["hit", "damage", "impact", "受击", "挨打", "受伤", "被击"],
    "turn": ["turn", "rotate", "pivot", "转身", "转向", "左转", "右转", "转"],
    "crouch": ["crouch", "sneak", "duck", "蹲伏", "下蹲", "潜行", "蹲"],
    "climb": ["climb", "ladder", "攀爬", "爬梯", "爬"],
    "dance": ["dance", "dancing", "groove", "舞蹈", "跳舞", "挥手"],
    "death": ["death", "die", "dead", "死亡", "倒地", "阵亡"],
    "other": [],
}
"""中英文关键词表；匹配基于 :func:`tokenize` 的词元（英文按词元精确命中，
中文靠单字 + bigram，因此「走路」既能被「走」也能被「走路」命中）。"""

_TYPE_PRIORITY = ("crouch", "climb", "death", "hit", "attack", "jump",
                  "turn", "dance", "run", "walk", "idle", "other")
"""推断优先级：越「具体」的类型越靠前。

例如 ``Crouch_Idle_01`` 同时含 crouch 与 idle 关键词，应先判为蹲伏而不是待机；
``Hit_Front_01`` 同理优先受击而非攻击。``MOTION_TYPES`` 的展示顺序保持不变。
"""

_CJK_RANGES = ((0x3400, 0x4DBF), (0x4E00, 0x9FFF),
               (0x3040, 0x30FF), (0xAC00, 0xD7AF))

# 保守占位值（无采样时使用），单位见 extract_features。
_EST_ROOT_SPEED_SLOW = 80.0      # cm/s：root_motion 且时长 >= 1s
_EST_ROOT_SPEED_FAST = 120.0     # cm/s：root_motion 且时长 < 1s（短动作更快）
_EST_JOINT_SPEED = 45.0          # deg/s
_EST_LOOP_ERROR = 5.0            # deg
_EST_SYMMETRY = 0.5              # 无量纲

_SIDE_SUFFIXES = (("_left", "_right"), (".left", ".right"),
                  ("_l", "_r"), (".l", ".r"), ("_左", "_右"))
_SIDE_PREFIXES = (("left_", "right_"), ("l_", "r_"))


# --------------------------------------------------------------------------- #
# 分词（英文小写词元 + 中文单字与 bigram）
# --------------------------------------------------------------------------- #
def _is_cjk(ch: str) -> bool:
    code = ord(ch)
    for low, high in _CJK_RANGES:
        if low <= code <= high:
            return True
    return False


def _is_word_char(ch: str) -> bool:
    """英文 / 数字字符；下划线**不算**词内字符，便于 ``Walk_Fwd_01`` 被关键词命中。"""
    return ("a" <= ch <= "z") or ("0" <= ch <= "9")


def tokenize(text: Any) -> List[str]:
    """英文小写词元 + 中文单字与相邻 bigram；保序、不去重、纯确定性。

    与 ``tak.core.knowledge`` 的分词差异：这里把 ``_`` 当分隔符，
    使 ``Walk_Fwd_01`` → ``walk / fwd / 01``、``hand_l`` → ``hand / l``，
    从而「walk」「hand」这类关键词可以直接命中骨骼名与 clip 名。
    """
    if text is None:
        return []
    raw = str(text).lower()
    tokens: List[str] = []
    index = 0
    size = len(raw)
    while index < size:
        ch = raw[index]
        if _is_cjk(ch):
            end = index
            while end < size and _is_cjk(raw[end]):
                end += 1
            run = raw[index:end]
            for offset in range(len(run)):
                tokens.append(run[offset])
                if offset + 1 < len(run):
                    tokens.append(run[offset:offset + 2])
            index = end
        elif _is_word_char(ch):
            end = index
            while end < size and _is_word_char(raw[end]):
                end += 1
            tokens.append(raw[index:end])
            index = end
        else:
            index += 1
    return tokens


# --------------------------------------------------------------------------- #
# 动作类型推断
# --------------------------------------------------------------------------- #
def _view(clip_or_dict: Any) -> Dict[str, Any]:
    """把 MotionClip / dict / 其它输入统一成字段 dict（只读视图，不复制大对象）。"""
    if isinstance(clip_or_dict, MotionClip):
        return clip_or_dict.to_dict()
    if isinstance(clip_or_dict, dict):
        return clip_or_dict
    return {}


def _type_alias(value: Any) -> str:
    """把 ``walk`` / ``Walk`` / ``行走`` 统一成 ``walk``；未知返回空串。"""
    text = _as_text(value).lower()
    if not text:
        return ""
    if text in MOTION_TYPES:
        return text
    for key, display in MOTION_TYPES.items():
        if text == display:
            return key
    return ""


def infer_motion_type(clip_or_dict: Any) -> str:
    """按「元数据声明 → 名称/标签关键词 → other」的顺序推断动作类型。

    元数据里的 ``metadata["motion_type"]`` 优先级最高（人工/管线标注最可信）；
    关键词匹配按 :data:`_TYPE_PRIORITY`（具体类型优先）取首个命中，保证同一输入
    永远得到同一类型（例如「蹲伏 待机」判为 ``crouch`` 而不是 ``idle``）。
    """
    data = _view(clip_or_dict)
    metadata = data.get("metadata")
    if isinstance(metadata, dict):
        declared = _type_alias(metadata.get("motion_type"))
        if declared:
            return declared
    declared = _type_alias(data.get("motion_type"))
    if declared:
        return declared

    text = " ".join([_as_text(data.get("name"))] +
                    _as_text_list(data.get("tags")))
    tokens = set(tokenize(text))
    if not tokens:
        return "other"
    for key in _TYPE_PRIORITY:
        for keyword in MOTION_KEYWORDS.get(key, ()):
            if keyword.lower() in tokens:
                return key
    return "other"


# --------------------------------------------------------------------------- #
# 采样归一化
# --------------------------------------------------------------------------- #
def _as_vec3(value: Any) -> Optional[List[float]]:
    """取前三个有限数值作为向量；长度不足 / 含非法值时返回 None。"""
    if not isinstance(value, (list, tuple)) or len(value) < 3:
        return None
    vector: List[float] = []
    for item in value[:3]:
        if isinstance(item, bool):
            return None
        try:
            number = float(item)
        except (TypeError, ValueError):
            return None
        if not math.isfinite(number):
            return None
        vector.append(number)
    return vector


def _rows(clip_or_dict: Any) -> List[Dict[str, Any]]:
    """把 ``samples`` 归一成 ``[{"frame", "root", "joints"}, ...]``；非法输入返回 []。"""
    samples: Any = None
    if isinstance(clip_or_dict, MotionClip):
        samples = clip_or_dict.metadata.get("samples")
    elif isinstance(clip_or_dict, dict):
        samples = clip_or_dict.get("samples")
        if samples is None:
            metadata = clip_or_dict.get("metadata")
            if isinstance(metadata, dict):
                samples = metadata.get("samples")
    if not isinstance(samples, (list, tuple)) or not samples:
        return []

    rows: List[Dict[str, Any]] = []
    last_frame = -1
    for position, item in enumerate(samples):
        if not isinstance(item, dict):
            continue
        frame = _as_int(item.get("frame"), -1)
        if frame < 0:
            frame = last_frame + 1 if last_frame >= 0 else position
        last_frame = frame
        joints: Dict[str, List[float]] = {}
        raw_joints = item.get("joints")
        if isinstance(raw_joints, dict):
            for name in sorted(raw_joints.keys(), key=lambda key: str(key)):
                vector = _as_vec3(raw_joints[name])
                if vector is not None:
                    joints[str(name)] = vector
        rows.append({"frame": frame, "root": _as_vec3(item.get("root")),
                     "joints": joints})
    return rows


def _times(rows: List[Dict[str, Any]], fps: float) -> List[float]:
    """把帧号换成单调不减的秒数（首帧为 0），保证 dt 恒为正。"""
    if not rows:
        return []
    step = 1.0 / fps
    base = rows[0]["frame"]
    times: List[float] = []
    previous = 0.0
    for row in rows:
        moment = (row["frame"] - base) / fps
        if moment < 0.0 or moment <= previous:
            moment = previous + step
        times.append(moment)
        previous = moment
    return times


# --------------------------------------------------------------------------- #
# 数值小工具
# --------------------------------------------------------------------------- #
def _dist(left: List[float], right: List[float]) -> float:
    """三维欧氏距离。"""
    total = 0.0
    for axis in range(3):
        delta = left[axis] - right[axis]
        total += delta * delta
    return math.sqrt(total)


def _mean(values: List[float]) -> float:
    return sum(values) / float(len(values)) if values else 0.0


def _variance(values: List[float]) -> float:
    """总体方差（除以 n）；样本不足 2 个时返回 0.0。"""
    if len(values) < 2:
        return 0.0
    average = _mean(values)
    return sum((value - average) ** 2 for value in values) / float(len(values))


def _round(value: float) -> float:
    """统一保留 6 位小数，并把 -0.0 归一成 0.0。"""
    number = round(float(value), 6)
    return 0.0 if number == 0.0 else number


def _side_pair(name: str) -> Optional[Any]:
    """识别左右对称命名，返回 ``(基名, "l"|"r")``；不匹配返回 None。

    支持 ``hand_l / hand_r``、``hand.L / hand.R``、``hand_left / hand_right``
    与 ``left_hand / right_hand``（含中文 ``_左 / _右``）等常见写法。
    """
    text = name.strip().lower()
    if not text:
        return None
    for left, right in _SIDE_SUFFIXES:
        if text.endswith(left) and len(text) > len(left):
            return text[:len(text) - len(left)], "l"
        if text.endswith(right) and len(text) > len(right):
            return text[:len(text) - len(right)], "r"
    for left, right in _SIDE_PREFIXES:
        if text.startswith(left) and len(text) > len(left):
            return text[len(left):], "l"
        if text.startswith(right) and len(text) > len(right):
            return text[len(right):], "r"
    return None


def _symmetry(rows: List[Dict[str, Any]]) -> float:
    """左右对称度：``1 / (1 + 平均角度差)``，完全对称 = 1.0，无成对骨骼 = 0.0。

    平均角度差 = 所有帧、所有成对左右骨骼、三个轴向的 ``|L - R|`` 均值（deg）。
    """
    groups: Dict[str, Dict[str, str]] = {}
    names = set()
    for row in rows:
        names.update(row["joints"].keys())
    for name in sorted(names):
        pair = _side_pair(name)
        if pair is None:
            continue
        base, side = pair
        groups.setdefault(base, {})[side] = name

    diffs: List[float] = []
    for base in sorted(groups.keys()):
        sides = groups[base]
        if "l" not in sides or "r" not in sides:
            continue
        left_name, right_name = sides["l"], sides["r"]
        for row in rows:
            left = row["joints"].get(left_name)
            right = row["joints"].get(right_name)
            if left is None or right is None:
                continue
            for axis in range(3):
                diffs.append(abs(left[axis] - right[axis]))
    if not diffs:
        return 0.0
    return 1.0 / (1.0 + _mean(diffs))


# --------------------------------------------------------------------------- #
# 特征提取
# --------------------------------------------------------------------------- #
def _structural_features(data: Dict[str, Any]) -> Dict[str, float]:
    """与采样无关的结构特征：时长 / 帧率 / 帧数 / 骨骼数 / 循环 / 根运动。"""
    fps = _as_float(data.get("fps"), DEFAULT_FPS)
    frames = max(_as_int(data.get("frame_count"), 0), 0)
    duration = 0.0
    if fps > 0.0 and frames > 0:
        duration = float(frames) / fps
    return {
        "duration": _round(duration),
        "fps": _round(fps),
        "frame_count": float(frames),
        "bone_count": float(len(_as_text_list(data.get("bones")))),
        "loopable": 1.0 if _as_bool(data.get("loopable"), False) else 0.0,
        "root_motion": 1.0 if _as_bool(data.get("root_motion"), False) else 0.0,
    }


def _estimated_features(features: Dict[str, float],
                        structural: Dict[str, float]) -> None:
    """无采样时按结构特征填保守占位值，只填缺失键，绝不覆盖已有数值。

    ``root_travel`` / ``root_displacement`` 由**生效的** ``root_speed_mean``
    （可能是 sidecar 声明值）乘时长推导，避免声明值与推导值自相矛盾。
    """
    duration = structural["duration"]
    has_root = structural["root_motion"] > 0.0
    loopable = structural["loopable"] > 0.0
    if "root_speed_mean" in features:
        root_speed = features["root_speed_mean"]
    elif has_root:
        root_speed = (_EST_ROOT_SPEED_SLOW if duration >= 1.0
                      else _EST_ROOT_SPEED_FAST)
    else:
        root_speed = 0.0
    placeholders = {
        "root_speed_mean": root_speed,
        "root_speed_max": root_speed * 2.0,
        "root_travel": root_speed * duration,
        "root_displacement": 0.0 if loopable else root_speed * duration,
        "joint_speed_mean": _EST_JOINT_SPEED,
        "joint_speed_max": _EST_JOINT_SPEED * 2.0,
        "loop_error": 0.0 if loopable else _EST_LOOP_ERROR,
        "symmetry": _EST_SYMMETRY,
        "pose_variance": 0.0,
    }
    for key in sorted(placeholders.keys()):
        if key not in features:
            features[key] = placeholders[key]


def _sampled_features(rows: List[Dict[str, Any]], fps: float) -> Dict[str, float]:
    """从归一化采样真实计算速度 / 位移 / 循环误差 / 对称性 / 姿态方差。"""
    times = _times(rows, fps)
    features: Dict[str, float] = {}

    root_speeds: List[float] = []
    travel = 0.0
    roots = [row["root"] for row in rows]
    for index in range(1, len(rows)):
        left, right = roots[index - 1], roots[index]
        if left is None or right is None:
            continue
        dt = times[index] - times[index - 1]
        if dt <= 0.0:
            continue
        distance = _dist(left, right)
        travel += distance
        root_speeds.append(distance / dt)
    root_positions = [root for root in roots if root is not None]
    displacement = (_dist(root_positions[0], root_positions[-1])
                    if len(root_positions) >= 2 else 0.0)
    features["root_speed_mean"] = _mean(root_speeds)
    features["root_speed_max"] = max(root_speeds) if root_speeds else 0.0
    features["root_travel"] = travel
    features["root_displacement"] = displacement

    joint_speeds: List[float] = []
    for index in range(1, len(rows)):
        dt = times[index] - times[index - 1]
        if dt <= 0.0:
            continue
        left_joints = rows[index - 1]["joints"]
        right_joints = rows[index]["joints"]
        for name in sorted(right_joints.keys()):
            previous = left_joints.get(name)
            if previous is None:
                continue
            joint_speeds.append(_dist(previous, right_joints[name]) / dt)
    features["joint_speed_mean"] = _mean(joint_speeds)
    features["joint_speed_max"] = max(joint_speeds) if joint_speeds else 0.0

    if len(rows) >= 2:
        first, last = rows[0]["joints"], rows[-1]["joints"]
        errors: List[float] = []
        for name in sorted(first.keys()):
            if name not in last:
                continue
            for axis in range(3):
                errors.append(abs(first[name][axis] - last[name][axis]))
        features["loop_error"] = max(errors) if errors else 0.0
    else:
        features["loop_error"] = 0.0

    features["symmetry"] = _symmetry(rows)

    variances: List[float] = []
    names = set()
    for row in rows:
        names.update(row["joints"].keys())
    for name in sorted(names):
        values = [row["joints"][name] for row in rows if name in row["joints"]]
        if len(values) < 2:
            continue
        per_axis = [_variance([vector[axis] for vector in values])
                    for axis in range(3)]
        variances.append(_mean(per_axis))
    features["pose_variance"] = _mean(variances)
    return features


def extract_features(clip_or_dict: Any) -> Dict[str, float]:
    """计算动作数值特征；任何残缺输入都安全返回，绝不抛异常。

    结构特征（两种情况都输出）
    --------------------------
    * ``duration``      秒，``frame_count / fps``，``fps <= 0`` 时为 0；
    * ``fps``           帧/秒；
    * ``frame_count``   帧；
    * ``bone_count``    骨骼条数；
    * ``loopable``      1.0 / 0.0；
    * ``root_motion``   1.0 / 0.0。

    采样特征（``samples`` 有效时真实计算，``estimated = 0.0``）
    ----------------------------------------------------------
    记相邻帧时间差 ``dt = t[i] - t[i-1]``（秒），``t = (frame - 首帧) / fps``
    （采样按给定顺序处理；帧号非单调时时间轴强制单调，每步至少 ``1/fps``）：

    * ``root_speed_mean``     相邻帧 ``|Δroot| / dt`` 的均值，cm/s；
    * ``root_speed_max``      上述速度的最大值，cm/s；
    * ``root_travel``         相邻帧 ``|Δroot|`` 累加，cm；
    * ``root_displacement``   首末 root 直线距离 ``|root[-1] - root[0]|``，cm；
    * ``joint_speed_mean``    同帧同名的关节 ``|Δangle| / dt`` 均值，deg/s；
    * ``joint_speed_max``     上述关节角速度最大值，deg/s；
    * ``loop_error``          首末帧共有骨骼的三轴角度差最大绝对值，deg；
    * ``symmetry``            ``1 / (1 + 左右骨骼三轴角度差均值)``，0..1；
    * ``pose_variance``       各骨骼三轴角度跨帧方差的均值，deg²。

    占位特征（无有效采样，``estimated = 1.0``）
    ------------------------------------------
    ``root_motion`` 为真时 ``root_speed_mean`` 取 80 cm/s（时长 < 1s 时 120），
    ``root_speed_max`` 取两倍，``root_travel = root_speed_mean * duration``，
    循环动作 ``root_displacement = 0``、``loop_error = 0``，非循环
    ``loop_error = 5 deg``；其余为 ``joint_speed_mean = 45 deg/s``、
    ``joint_speed_max = 90 deg/s``、``symmetry = 0.5``、``pose_variance = 0``。
    已经显式提供的特征（sidecar 声明值）不会被占位值覆盖。

    ``samples`` 为空 / 全部非法时走占位分支（``estimated = 1.0``）；只有
    单帧时按实测处理（``estimated = 0.0``）：位移、速度、循环误差全为 0，
    如实反映「这一帧没有运动」。
    """
    data = _view(clip_or_dict)
    features = dict(_as_float_dict(data.get("features")))
    structural = _structural_features(data)
    for key in sorted(structural.keys()):
        features[key] = structural[key]

    fps = _as_float(data.get("fps"), DEFAULT_FPS)
    fps_eff = fps if fps > 0.0 else DEFAULT_FPS
    rows = _rows(clip_or_dict)
    if rows:
        computed = _sampled_features(rows, fps_eff)
        for key in sorted(computed.keys()):
            features[key] = _round(computed[key])
        features["estimated"] = 0.0
    else:
        _estimated_features(features, structural)
        # 已有 estimated 标记（例如从已实测的索引里读回）保持不变，避免把实测值
        # 误标成占位值；新条目缺省标记为 1.0。
        features.setdefault("estimated", 1.0)

    result: Dict[str, float] = {}
    for key in sorted(features.keys()):
        number = features[key]
        if isinstance(number, bool):
            number = 1.0 if number else 0.0
        try:
            value = float(number)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(value):
            continue
        result[key] = _round(value)
    return result


def _display_terms(terms: List[str]) -> List[str]:
    """把命中的词元压成适合展示的关键词列表：去掉被长词包含的单字，最多 4 个。"""
    kept: List[str] = []
    for term in terms:
        if len(term) == 1 and any(len(other) > 1 and term in other
                                  for other in terms):
            continue
        if term not in kept:
            kept.append(term)
    return kept[:4]


__all__ = ["MOTION_TYPES", "MOTION_KEYWORDS", "extract_features",
           "infer_motion_type", "tokenize"]
