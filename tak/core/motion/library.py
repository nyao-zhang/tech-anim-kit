# -*- coding: utf-8 -*-
"""动作库：确定性 BM25 文本检索 + 类型加权 + 数值过滤 + 特征相似度。

设计要点
--------
* 零第三方、零 DCC：只依赖标准库与本包 ``model`` / ``features``；
* 文本分 = ``name`` / ``tags`` / ``bones`` 的 BM25（``name`` 重复计权 2 次）；
* 类型分 = 查询中的中英文动作关键词命中 ``clip.motion_type`` 时加权，
  ``options["motion_type"]`` 显式指定时权重更高；
* 数值分 = ``options["filters"]`` 命中即**排除**（不是降权）；
* 排序恒定：``score 降序 → clip_id 升序 → 建库顺序升序``；不写时间戳、
  随机数或缓存文件，同一份输入两次调用结果逐字段相同；
* ``provider`` 只是接口位（未来可接真实语义重排），离线时**绝不调用**。
"""
from __future__ import annotations

import math
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .features import (MOTION_KEYWORDS, MOTION_TYPES, _display_terms,
                       extract_features, infer_motion_type, tokenize)
from .model import MotionClip, clip_from_dict

# --------------------------------------------------------------------------- #
# 常量
# --------------------------------------------------------------------------- #
CONTRACT_VERSION = "1.0"

MAX_TOP_K = 50
MAX_QUERY_CHARS = 2000
MAX_QUERY_TERMS = 64
MAX_CONTEXT_BONES = 40

_K1 = 1.5
_B = 0.75
_NAME_REPEAT = 2          # clip 名比标签 / 骨骼更能代表动作语义
_TYPE_WEIGHT = 3.0        # 查询关键词命中类型
_FORCED_TYPE_WEIGHT = 6.0  # options["motion_type"] 显式指定

# 相似度使用的特征权重（未列出的特征不参与，``estimated`` 永远排除）。
SIMILARITY_WEIGHTS: Dict[str, float] = {
    "bone_count": 0.4,
    "duration": 1.0,
    "fps": 0.3,
    "frame_count": 0.3,
    "joint_speed_max": 0.9,
    "joint_speed_mean": 1.1,
    "loop_error": 1.0,
    "loopable": 0.6,
    "pose_variance": 0.7,
    "root_displacement": 0.6,
    "root_motion": 0.7,
    "root_speed_max": 1.0,
    "root_speed_mean": 1.2,
    "root_travel": 0.8,
    "symmetry": 0.8,
}

_HUMAN_BONES = (
    "root", "pelvis", "spine_01", "spine_02", "neck_01", "head",
    "clavicle_l", "upperarm_l", "lowerarm_l", "hand_l",
    "clavicle_r", "upperarm_r", "lowerarm_r", "hand_r",
    "thigh_l", "calf_l", "foot_l", "thigh_r", "calf_r", "foot_r",
)
_EXPRESSIVE_BONES = _HUMAN_BONES + ("tail_01", "tail_02")

_FILTER_KEYS = ("min_duration", "max_duration", "loopable", "root_motion",
                "has_bone", "min_fps")


# --------------------------------------------------------------------------- #
# 内置示例库（小型、可读、可复现；数值为演示用的保守估值）
# --------------------------------------------------------------------------- #
def _builtin_specs() -> List[Dict[str, Any]]:
    """内置 12 个动作的原始定义（每次返回深拷贝，外部修改不影响库）。"""
    walk = {
        "root_speed_mean": 82.0, "root_speed_max": 96.0,
        "root_travel": 98.4, "root_displacement": 98.4,
        "joint_speed_mean": 58.0, "joint_speed_max": 140.0,
        "loop_error": 0.4, "symmetry": 0.86, "pose_variance": 12.5,
    }
    return [
        {
            "clip_id": "walk_fwd_01",
            "name": "Walk_Fwd_01 走路 前进 循环",
            "path": "builtin/locomotion/walk_fwd_01.fbx",
            "fps": 30.0, "frame_count": 36, "loopable": True,
            "root_motion": True, "bones": list(_HUMAN_BONES),
            "tags": ["locomotion", "walk", "loop", "走路", "循环", "前进"],
            "features": dict(walk),
            "metadata": {"source": "builtin", "note": "演示用保守估值"},
        },
        {
            "clip_id": "walk_back_01",
            "name": "Walk_Back_01 走路 后退 循环",
            "path": "builtin/locomotion/walk_back_01.fbx",
            "fps": 30.0, "frame_count": 40, "loopable": True,
            "root_motion": True, "bones": list(_HUMAN_BONES),
            "tags": ["locomotion", "walk", "loop", "走路", "循环", "后退"],
            "features": {
                "root_speed_mean": 76.0, "root_speed_max": 90.0,
                "root_travel": 101.33, "root_displacement": 101.33,
                "joint_speed_mean": 54.0, "joint_speed_max": 132.0,
                "loop_error": 0.6, "symmetry": 0.84, "pose_variance": 13.1,
            },
            "metadata": {"source": "builtin", "note": "演示用保守估值"},
        },
        {
            "clip_id": "run_fwd_01",
            "name": "Run_Fwd_01 奔跑 前进 循环",
            "path": "builtin/locomotion/run_fwd_01.fbx",
            "fps": 30.0, "frame_count": 24, "loopable": True,
            "root_motion": True, "bones": list(_HUMAN_BONES),
            "tags": ["locomotion", "run", "sprint", "loop", "奔跑", "循环"],
            "features": {
                "root_speed_mean": 260.0, "root_speed_max": 310.0,
                "root_travel": 208.0, "root_displacement": 208.0,
                "joint_speed_mean": 120.0, "joint_speed_max": 300.0,
                "loop_error": 0.8, "symmetry": 0.81, "pose_variance": 26.4,
            },
            "metadata": {"source": "builtin", "note": "演示用保守估值"},
        },
        {
            "clip_id": "idle_loop_01",
            "name": "Idle_Loop_01 待机 呼吸 循环",
            "path": "builtin/idle/idle_loop_01.fbx",
            "fps": 30.0, "frame_count": 90, "loopable": True,
            "root_motion": False, "bones": list(_HUMAN_BONES),
            "tags": ["idle", "breath", "loop", "待机", "呼吸", "循环"],
            "features": {
                "root_speed_mean": 0.0, "root_speed_max": 0.0,
                "root_travel": 0.0, "root_displacement": 0.0,
                "joint_speed_mean": 12.0, "joint_speed_max": 28.0,
                "loop_error": 0.2, "symmetry": 0.94, "pose_variance": 2.1,
            },
            "metadata": {"source": "builtin", "note": "演示用保守估值"},
        },
        {
            "clip_id": "jump_start_01",
            "name": "Jump_Start_01 跳跃 起跳",
            "path": "builtin/locomotion/jump_start_01.fbx",
            "fps": 30.0, "frame_count": 20, "loopable": False,
            "root_motion": True, "bones": list(_HUMAN_BONES),
            "tags": ["jump", "start", "跳跃", "起跳", "腾空"],
            "features": {
                "root_speed_mean": 150.0, "root_speed_max": 240.0,
                "root_travel": 100.0, "root_displacement": 60.0,
                "joint_speed_mean": 110.0, "joint_speed_max": 260.0,
                "loop_error": 8.0, "symmetry": 0.72, "pose_variance": 34.8,
            },
            "metadata": {"source": "builtin", "note": "演示用保守估值"},
        },
        {
            "clip_id": "attack_slash_01",
            "name": "Attack_Slash_01 攻击 挥砍 连击",
            "path": "builtin/combat/attack_slash_01.fbx",
            "fps": 30.0, "frame_count": 30, "loopable": False,
            "root_motion": False, "bones": list(_HUMAN_BONES),
            "tags": ["attack", "slash", "combo", "攻击", "挥砍", "连击"],
            "features": {
                "root_speed_mean": 45.0, "root_speed_max": 70.0,
                "root_travel": 45.0, "root_displacement": 12.0,
                "joint_speed_mean": 180.0, "joint_speed_max": 420.0,
                "loop_error": 9.5, "symmetry": 0.55, "pose_variance": 58.2,
            },
            "metadata": {"source": "builtin", "note": "演示用保守估值"},
        },
        {
            "clip_id": "hit_front_01",
            "name": "Hit_Front_01 受击 前方 硬直",
            "path": "builtin/combat/hit_front_01.fbx",
            "fps": 30.0, "frame_count": 15, "loopable": False,
            "root_motion": False, "bones": list(_HUMAN_BONES),
            "tags": ["hit", "damage", "受击", "挨打", "硬直"],
            "features": {
                "root_speed_mean": 30.0, "root_speed_max": 55.0,
                "root_travel": 15.0, "root_displacement": 8.0,
                "joint_speed_mean": 210.0, "joint_speed_max": 460.0,
                "loop_error": 12.0, "symmetry": 0.48, "pose_variance": 66.5,
            },
            "metadata": {"source": "builtin", "note": "演示用保守估值"},
        },
        {
            "clip_id": "turn_left_90_01",
            "name": "Turn_Left_90_01 转身 左转 90度",
            "path": "builtin/locomotion/turn_left_90_01.fbx",
            "fps": 30.0, "frame_count": 20, "loopable": False,
            "root_motion": True, "bones": list(_HUMAN_BONES),
            "tags": ["turn", "rotate", "转身", "左转", "转向"],
            "features": {
                "root_speed_mean": 62.0, "root_speed_max": 95.0,
                "root_travel": 41.3, "root_displacement": 20.0,
                "joint_speed_mean": 70.0, "joint_speed_max": 150.0,
                "loop_error": 3.2, "symmetry": 0.68, "pose_variance": 18.7,
            },
            "metadata": {"source": "builtin", "note": "演示用保守估值"},
        },
        {
            "clip_id": "crouch_idle_01",
            "name": "Crouch_Idle_01 蹲伏 待机 循环",
            "path": "builtin/idle/crouch_idle_01.fbx",
            "fps": 30.0, "frame_count": 60, "loopable": True,
            "root_motion": False, "bones": list(_HUMAN_BONES),
            "tags": ["crouch", "idle", "loop", "蹲伏", "待机", "循环"],
            "features": {
                "root_speed_mean": 0.0, "root_speed_max": 0.0,
                "root_travel": 0.0, "root_displacement": 0.0,
                "joint_speed_mean": 9.0, "joint_speed_max": 20.0,
                "loop_error": 0.3, "symmetry": 0.90, "pose_variance": 1.4,
            },
            "metadata": {"source": "builtin", "note": "演示用保守估值"},
        },
        {
            "clip_id": "climb_ladder_01",
            "name": "Climb_Ladder_01 攀爬 上梯 循环",
            "path": "builtin/locomotion/climb_ladder_01.fbx",
            "fps": 30.0, "frame_count": 40, "loopable": True,
            "root_motion": True, "bones": list(_HUMAN_BONES),
            "tags": ["climb", "ladder", "loop", "攀爬", "爬梯", "循环"],
            "features": {
                "root_speed_mean": 40.0, "root_speed_max": 55.0,
                "root_travel": 53.3, "root_displacement": 53.3,
                "joint_speed_mean": 95.0, "joint_speed_max": 210.0,
                "loop_error": 1.1, "symmetry": 0.79, "pose_variance": 22.6,
            },
            "metadata": {"source": "builtin", "note": "演示用保守估值"},
        },
        {
            "clip_id": "dance_wave_01",
            "name": "Dance_Wave_01 舞蹈 挥手 循环",
            "path": "builtin/social/dance_wave_01.fbx",
            "fps": 30.0, "frame_count": 120, "loopable": True,
            "root_motion": False, "bones": list(_EXPRESSIVE_BONES),
            "tags": ["dance", "wave", "loop", "舞蹈", "挥手", "循环"],
            "features": {
                "root_speed_mean": 0.0, "root_speed_max": 0.0,
                "root_travel": 0.0, "root_displacement": 0.0,
                "joint_speed_mean": 130.0, "joint_speed_max": 340.0,
                "loop_error": 0.9, "symmetry": 0.62, "pose_variance": 44.3,
            },
            "metadata": {"source": "builtin", "note": "演示用保守估值"},
        },
        {
            "clip_id": "death_fall_01",
            "name": "Death_Fall_01 死亡 倒地",
            "path": "builtin/combat/death_fall_01.fbx",
            "fps": 30.0, "frame_count": 45, "loopable": False,
            "root_motion": False, "bones": list(_EXPRESSIVE_BONES),
            "tags": ["death", "die", "死亡", "倒地", "阵亡"],
            "features": {
                "root_speed_mean": 20.0, "root_speed_max": 40.0,
                "root_travel": 30.0, "root_displacement": 22.0,
                "joint_speed_mean": 85.0, "joint_speed_max": 230.0,
                "loop_error": 15.5, "symmetry": 0.58, "pose_variance": 39.9,
            },
            "metadata": {"source": "builtin", "note": "演示用保守估值"},
        },
    ]


def builtin_clips() -> List[Dict[str, Any]]:
    """返回内置示例库的原始定义（12 个动作，深拷贝，可安全修改）。"""
    copies: List[Dict[str, Any]] = []
    for spec in _builtin_specs():
        item = dict(spec)
        item["bones"] = list(spec["bones"])
        item["tags"] = list(spec["tags"])
        item["features"] = dict(spec["features"])
        item["metadata"] = dict(spec["metadata"])
        copies.append(item)
    return copies


# --------------------------------------------------------------------------- #
# 小工具
# --------------------------------------------------------------------------- #
def _as_options(options: Any) -> Dict[str, Any]:
    return dict(options) if isinstance(options, dict) else {}


def _number(value: Any) -> Optional[float]:
    """转成有限浮点数；bool / 非法值返回 None。"""
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return number


def _normalize_top_k(value: Any, default: int = 5) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default
    return min(max(number, 0), MAX_TOP_K)


def _query_terms(query: Any) -> List[str]:
    raw = str(query or "")[:MAX_QUERY_CHARS]
    seen = set()
    terms: List[str] = []
    for term in tokenize(raw):
        if term in seen:
            continue
        seen.add(term)
        terms.append(term)
        if len(terms) >= MAX_QUERY_TERMS:
            break
    return terms


def _parse_filters(filters: Any) -> Dict[str, Any]:
    """把 ``options["filters"]`` 归一成可比较的规格；未知键直接忽略。"""
    if not isinstance(filters, dict):
        return {}
    spec: Dict[str, Any] = {}
    for key in ("min_duration", "max_duration", "min_fps"):
        number = _number(filters.get(key))
        if number is not None:
            spec[key] = number
    for key in ("loopable", "root_motion"):
        value = filters.get(key)
        if isinstance(value, bool):
            spec[key] = value
        elif isinstance(value, (int, float)) and not isinstance(value, bool):
            spec[key] = float(value) != 0.0
        elif isinstance(value, str):
            text = value.strip().lower()
            if text in ("1", "true", "yes", "y", "on", "是", "循环"):
                spec[key] = True
            elif text in ("0", "false", "no", "n", "off", "否", "非循环"):
                spec[key] = False
    bone = filters.get("has_bone")
    if isinstance(bone, str):
        names = [bone.strip()] if bone.strip() else []
    elif isinstance(bone, (list, tuple)):
        names = [str(item).strip() for item in bone if str(item).strip()]
    else:
        names = []
    if names:
        spec["has_bone"] = names
    return spec


def _pass_filters(clip: MotionClip, spec: Dict[str, Any]) -> bool:
    """数值过滤：不满足即排除（不是降权）。"""
    if not spec:
        return True
    duration = clip.duration
    if "min_duration" in spec and duration < spec["min_duration"]:
        return False
    if "max_duration" in spec and duration > spec["max_duration"]:
        return False
    if "loopable" in spec and bool(clip.loopable) != spec["loopable"]:
        return False
    if "root_motion" in spec and bool(clip.root_motion) != spec["root_motion"]:
        return False
    if "min_fps" in spec:
        fps_value = _number(clip.fps)
        if fps_value is None or fps_value < spec["min_fps"]:
            return False
    if "has_bone" in spec:
        owned = {bone.strip().lower() for bone in clip.bones}
        for name in spec["has_bone"]:
            if name.lower() not in owned:
                return False
    return True


def _filter_note(spec: Dict[str, Any], clip: MotionClip) -> str:
    """生成「为什么通过过滤」的中文说明。"""
    notes: List[str] = []
    if "min_duration" in spec and "max_duration" in spec:
        notes.append("时长 %.2fs 在 %.2f–%.2fs 区间"
                     % (clip.duration, spec["min_duration"],
                        spec["max_duration"]))
    elif "min_duration" in spec:
        notes.append("时长 %.2fs ≥ %.2fs" % (clip.duration, spec["min_duration"]))
    elif "max_duration" in spec:
        notes.append("时长 %.2fs ≤ %.2fs" % (clip.duration, spec["max_duration"]))
    if "min_fps" in spec:
        notes.append("帧率 %.1f ≥ %.1f" % (clip.fps, spec["min_fps"]))
    if "loopable" in spec:
        notes.append("仅循环动作" if spec["loopable"] else "仅非循环动作")
    if "root_motion" in spec:
        notes.append("含根运动" if spec["root_motion"] else "不含根运动")
    if "has_bone" in spec:
        notes.append("含骨骼 %s" % "、".join(spec["has_bone"]))
    return "，".join(notes)


def _types_in_query(terms: Iterable[str]) -> List[str]:
    """查询命中的动作类型（按 MOTION_TYPES 声明顺序，去重）。"""
    tokens = set(terms)
    found: List[str] = []
    for key, keywords in MOTION_KEYWORDS.items():
        for keyword in keywords:
            if keyword.lower() in tokens and key not in found:
                found.append(key)
                break
    return found


def _hit(clip: MotionClip, score: float, text_score: float, type_score: float,
         matched_terms: List[str], why: str, rank: int) -> Dict[str, Any]:
    """统一的命中结构（搜索结果与相似结果共用，前端可直接渲染）。"""
    return {
        "clip_id": clip.clip_id,
        "name": clip.name,
        "score": round(float(score), 6),
        "text_score": round(float(text_score), 6),
        "type_score": round(float(type_score), 6),
        "motion_type": infer_motion_type(clip),
        "duration": round(clip.duration, 6),
        "loopable": bool(clip.loopable),
        "root_motion": bool(clip.root_motion),
        "bones": list(clip.bones),
        "tags": list(clip.tags),
        "path": clip.path,
        "rank": rank,
        "matched_terms": list(matched_terms),
        "why": why,
    }


# --------------------------------------------------------------------------- #
# 动作库
# --------------------------------------------------------------------------- #
class MotionLibrary:
    """确定性动作库检索器。

    ``clips`` 为 ``None`` 时加载内置示例库；``provider`` 只是语义重排接口位，
    离线环境（默认）永不调用，检索结果不依赖任何外部服务。
    """

    def __init__(self, clips: Optional[List[Any]] = None,
                 provider: Any = None) -> None:
        self.provider = provider
        self.clips: List[MotionClip] = []
        self._entries: List[Dict[str, Any]] = []
        self._postings: Dict[str, Dict[int, int]] = {}
        self._lengths: List[int] = []
        self._idf: Dict[str, float] = {}
        self._avgdl = 0.0
        self._built = False

        source = builtin_clips() if clips is None else clips
        if isinstance(source, dict):
            # 容忍直接传 to_index() 结构：只取其中的 clips 列表。
            raw = source.get("clips")
            source = raw if isinstance(raw, (list, tuple)) else []
        used: List[str] = []
        for index, item in enumerate(list(source)):
            clip = self._append(item, index, used)
            self.clips.append(clip)
        self.build()

    # -- 建库 -------------------------------------------------------------- #
    def _append(self, item: Any, index: int, used: List[str]) -> MotionClip:
        """解析一条 clip、补确定性 id、并按原始输入计算特征。"""
        clip = clip_from_dict(item)
        base = clip.clip_id or ("clip_%04d" % (index + 1))
        candidate = base
        serial = 2
        while candidate in used:
            candidate = "%s_%d" % (base, serial)
            serial += 1
        used.append(candidate)
        clip.clip_id = candidate
        # 用原始输入算特征：dict 里可能带 samples，而 MotionClip 不持有采样数据。
        clip.features = extract_features(item)
        return clip

    def build(self) -> "MotionLibrary":
        """建倒排索引（BM25 词频 / 长度 / IDF），可重复调用。"""
        try:
            self._entries = []
            self._postings = {}
            self._lengths = []
            self._idf = {}
            self._avgdl = 0.0
            for index, clip in enumerate(self.clips):
                tokens = (tokenize(clip.name) * _NAME_REPEAT
                          + tokenize(" ".join(clip.tags))
                          + tokenize(" ".join(clip.bones)))
                tf: Dict[str, int] = {}
                for term in tokens:
                    tf[term] = tf.get(term, 0) + 1
                self._entries.append({
                    "clip": clip,
                    "motion_type": infer_motion_type(clip),
                    "length": len(tokens),
                })
                self._lengths.append(len(tokens))
                for term in sorted(tf.keys()):
                    self._postings.setdefault(term, {})[index] = tf[term]
            total = len(self._entries)
            if total:
                for term in sorted(self._postings.keys()):
                    document_frequency = len(self._postings[term])
                    self._idf[term] = math.log(
                        1.0 + (total - document_frequency + 0.5)
                        / (document_frequency + 0.5))
                self._avgdl = sum(self._lengths) / float(total)
            self._built = True
        except Exception:
            # 建库失败也不抛：退化成空索引，search 自然返回 []。
            self._entries = []
            self._postings = {}
            self._lengths = []
            self._idf = {}
            self._avgdl = 0.0
        return self

    def add_clip(self, clip: Any) -> MotionClip:
        """追加一个 clip（dict 或 MotionClip）并重建索引，返回入库对象。"""
        used = [item.clip_id for item in self.clips]
        added = self._append(clip, len(self.clips), used)
        self.clips.append(added)
        self.build()
        return added

    # -- 检索 -------------------------------------------------------------- #
    def _bm25(self, terms: List[str],
              allowed: Optional[set]) -> Tuple[Dict[int, float],
                                               Dict[int, List[str]]]:
        """BM25 打分，同时记录每条 clip 命中的查询词元（保查询顺序）。"""
        scores: Dict[int, float] = {}
        matched: Dict[int, List[str]] = {}
        avgdl = self._avgdl or 1.0
        for term in terms:
            bucket = self._postings.get(term)
            if not bucket:
                continue
            idf = self._idf.get(term, 0.0)
            if idf <= 0.0:
                continue
            weight = idf * (_K1 + 1.0)
            for index in sorted(bucket.keys()):
                if allowed is not None and index not in allowed:
                    continue
                frequency = bucket[index]
                length = self._lengths[index] or 1
                denominator = frequency + _K1 * (1.0 - _B + _B * length / avgdl)
                if denominator <= 0.0:
                    continue
                scores[index] = scores.get(index, 0.0) + (
                    weight * frequency / denominator)
                bucket_terms = matched.setdefault(index, [])
                if term not in bucket_terms:
                    bucket_terms.append(term)
        return scores, matched

    def search(self, query: Any, top_k: int = 5,
               options: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        """文本 + 类型 + 过滤检索；空 query 列出全部，空库 / 无命中返回 []。

        ``options``:

        * ``motion_type``：显式指定动作类型（权重高于关键词推断）；
        * ``filters``：``min_duration / max_duration / loopable / root_motion /
          has_bone / min_fps``，不满足的**直接排除**。

        每项命中含 ``clip_id / name / score / text_score / type_score /
        motion_type / duration / loopable / root_motion / bones / tags / path /
        rank / matched_terms / why``。
        """
        try:
            limit = _normalize_top_k(top_k, 5)
            if limit == 0:
                return []
            opts = _as_options(options)
            filters = _parse_filters(opts.get("filters"))
            forced = str(opts.get("motion_type") or "").strip().lower()
            if forced not in MOTION_TYPES:
                forced = ""

            allowed = {index for index, entry in enumerate(self._entries)
                       if _pass_filters(entry["clip"], filters)}
            raw = str(query or "").strip()

            if not raw:
                entries = sorted(
                    (index for index in allowed),
                    key=lambda i: (self._entries[i]["clip"].clip_id, i))
                results: List[Dict[str, Any]] = []
                for rank, index in enumerate(entries[:limit], 1):
                    results.append(_hit(
                        self._entries[index]["clip"], 0.0, 0.0, 0.0, [],
                        "空查询：按名称列出全部动作", rank))
                return results

            terms = _query_terms(raw)
            if not terms:
                return []
            scores, matched = self._bm25(terms, allowed)
            query_types = _types_in_query(terms)

            ranked: List[Tuple[float, str, int]] = []
            total_scores: Dict[int, Tuple[float, float]] = {}
            for index in sorted(set(scores.keys())):
                clip = self._entries[index]["clip"]
                text_score = scores[index]
                type_score = 0.0
                if forced:
                    if self._entries[index]["motion_type"] == forced:
                        type_score += _FORCED_TYPE_WEIGHT
                elif self._entries[index]["motion_type"] in query_types:
                    type_score += _TYPE_WEIGHT
                total = text_score + type_score
                if total <= 0.0:
                    continue
                total_scores[index] = (text_score, type_score)
                ranked.append((total, clip.clip_id, index))

            # 纯类型命中（查询词元不在文本里但类型匹配）也要返回。
            for index in sorted(allowed):
                if index in total_scores:
                    continue
                type_score = 0.0
                if forced:
                    if self._entries[index]["motion_type"] == forced:
                        type_score += _FORCED_TYPE_WEIGHT
                elif self._entries[index]["motion_type"] in query_types:
                    type_score += _TYPE_WEIGHT
                if type_score <= 0.0:
                    continue
                total_scores[index] = (0.0, type_score)
                ranked.append((type_score, self._entries[index]["clip"].clip_id,
                               index))

            ranked.sort(key=lambda item: (-item[0], item[1], item[2]))
            results: List[Dict[str, Any]] = []
            for rank, (total, _clip_id, index) in enumerate(ranked[:limit], 1):
                clip = self._entries[index]["clip"]
                text_score, type_score = total_scores[index]
                terms_hit = matched.get(index, [])
                display = _display_terms(terms_hit)
                parts: List[str] = []
                if display:
                    parts.append("命中关键词：%s" % "、".join(display))
                if type_score > 0.0:
                    label = MOTION_TYPES.get(self._entries[index]["motion_type"],
                                             self._entries[index]["motion_type"])
                    origin = ("显式指定" if forced
                              and self._entries[index]["motion_type"] == forced
                              else "查询命中")
                    parts.append("类型%s：%s" % (origin, label))
                note = _filter_note(filters, clip)
                if note:
                    parts.append(note)
                why = "；".join(parts) or "匹配动作库条目"
                results.append(_hit(clip, total, text_score, type_score,
                                    display, why, rank))
            return results
        except Exception:
            return []

    def similar_to(self, clip_id: Any, top_k: int = 5) -> List[Dict[str, Any]]:
        """按加权特征余弦相似度找相近动作（排除自身，排序完全确定）。

        每个特征先在库内做 min-max 归一化，再乘 :data:`SIMILARITY_WEIGHTS`
        的权重；没有区分度（全库同值）的特征自动跳过。分数范围 0..1。
        """
        try:
            limit = _normalize_top_k(top_k, 5)
            if limit == 0:
                return []
            target_id = str(clip_id or "")
            target = None
            for clip in self.clips:
                if clip.clip_id == target_id:
                    target = clip
                    break
            if target is None:
                return []

            keys: List[Tuple[str, float, float, float]] = []
            for key in sorted(SIMILARITY_WEIGHTS.keys()):
                values = [clip.features[key] for clip in self.clips
                          if key in clip.features]
                if len(values) < 2:
                    continue
                low, high = min(values), max(values)
                if high - low <= 0.0:
                    continue
                keys.append((key, low, high, SIMILARITY_WEIGHTS[key]))

            def vector(clip: MotionClip) -> List[float]:
                if not keys:
                    return []
                result: List[float] = []
                for key, low, high, weight in keys:
                    value = clip.features.get(key)
                    if value is None:
                        result.append(0.0)
                        continue
                    result.append(((value - low) / (high - low)) * weight)
                return result

            target_vector = vector(target)
            scored: List[Tuple[float, str, int]] = []
            for index, clip in enumerate(self.clips):
                if clip.clip_id == target.clip_id:
                    continue
                score = 0.0
                if target_vector:
                    score = _cosine(target_vector, vector(clip))
                scored.append((max(score, 0.0), clip.clip_id, index))
            scored.sort(key=lambda item: (-item[0], item[1], item[2]))

            results: List[Dict[str, Any]] = []
            for rank, (score, _clip_id, index) in enumerate(scored[:limit], 1):
                clip = self.clips[index]
                why = "特征向量余弦相似度 %.4f（共同特征 %d 项）" % (
                    score, len(keys))
                results.append(_hit(clip, score, 0.0, 0.0, [], why, rank))
            return results
        except Exception:
            return []

    def context_for(self, query: Any, top_k: int = 3,
                    max_chars: int = 800) -> str:
        """生成带 clip_id / 名称 / 时长 / 类型的中文上下文，长度硬截断。"""
        try:
            limit = int(max_chars)
        except (TypeError, ValueError):
            limit = 800
        if limit <= 0:
            return ""
        hits = self.search(query, top_k=top_k)
        if not hits:
            return ""
        lines = ["动作库上下文（本地离线索引；只读，未解析 FBX 二进制）："]
        for number, hit in enumerate(hits, 1):
            lines.append(
                "[%d] clip_id=%s | %s | 时长 %.2fs | 类型 %s | 循环=%s | 骨骼 %d"
                % (number, hit["clip_id"], hit["name"], hit["duration"],
                   MOTION_TYPES.get(hit["motion_type"], hit["motion_type"]),
                   "是" if hit["loopable"] else "否", len(hit["bones"])))
        return "\n".join(lines)[:limit]

    # -- 描述与序列化 ------------------------------------------------------ #
    def describe(self) -> Dict[str, Any]:
        """库规模、类型分布与骨骼清单（骨骼最多 40 个，按名称升序）。"""
        types: Dict[str, int] = {key: 0 for key in MOTION_TYPES}
        bones = set()
        for clip in self.clips:
            motion_type = infer_motion_type(clip)
            types[motion_type] = types.get(motion_type, 0) + 1
            bones.update(bone for bone in clip.bones if bone)
        return {
            "contract": CONTRACT_VERSION,
            "clip_count": len(self.clips),
            "types": types,
            "bone_count": len(bones),
            "bones": sorted(bones)[:MAX_CONTEXT_BONES],
            "has_audio": False,
            "offline": True,
        }

    def to_index(self) -> Dict[str, Any]:
        """稳定 JSON 结构（``json.dumps(sort_keys=True)`` 即可写入 sidecar）。"""
        return {
            "contract": CONTRACT_VERSION,
            "clip_count": len(self.clips),
            "clips": [clip.to_dict() for clip in self.clips],
        }

    @classmethod
    def from_index(cls, data: Any) -> "MotionLibrary":
        """从 :meth:`to_index` 的结构（或其 ``clips`` 列表）还原动作库。

        结构非法时返回**空库**而不是内置示例库：调用方（loader）负责决定
        是否回退，避免「读坏了 index 却悄悄换成演示数据」。
        """
        clips: Any = None
        if isinstance(data, dict):
            clips = data.get("clips")
        elif isinstance(data, (list, tuple)):
            clips = list(data)
        if not isinstance(clips, (list, tuple)):
            return cls(clips=[])
        return cls(clips=list(clips))


# --------------------------------------------------------------------------- #
# 向量工具（不依赖 numpy）
# --------------------------------------------------------------------------- #
def _cosine(left: List[float], right: List[float]) -> float:
    """加权余弦相似度；任一向量为零向量时返回 0.0。"""
    size = min(len(left), len(right))
    if size == 0:
        return 0.0
    dot = norm_left = norm_right = 0.0
    for position in range(size):
        dot += left[position] * right[position]
        norm_left += left[position] * left[position]
        norm_right += right[position] * right[position]
    if norm_left <= 0.0 or norm_right <= 0.0:
        return 0.0
    return dot / math.sqrt(norm_left * norm_right)
