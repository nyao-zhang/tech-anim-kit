# -*- coding: utf-8 -*-
"""动作片段数据模型：纯 Python、纯数据、可 JSON 序列化、解析绝不抛异常。

``MotionClip`` 是动作库的最小单元，只回答四个问题：

* 这是哪个 clip（``clip_id`` / ``name`` / ``path``）；
* 有多长（``fps`` / ``frame_count`` / 派生 ``duration``）；
* 有哪些骨骼与标签（``bones`` / ``tags``）；
* 数值特征是多少（``features``，见 :mod:`tak.core.motion.features`）。

它**不持有采样数据**、**不解析 DCC 场景**、**不读取 FBX 二进制**。若调用方
手里有逐帧采样（``samples``），应把包含 ``samples`` 的原始 dict 直接交给
``extract_features()``，而不是塞进 ``MotionClip``。

容错原则：``clip_from_dict()`` 面对任何输入（非 dict、缺字段、类型错、数值
非法、NaN / Infinity）都退化为默认值并返回对象，绝不抛异常；因此 sidecar
损坏、扫描结果残缺都不会中断批量导入。
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

# --------------------------------------------------------------------------- #
# 常量
# --------------------------------------------------------------------------- #
DEFAULT_FPS = 30.0
"""缺省帧率：只在 fps 缺失或非法时使用。"""

_SPLIT_RE = re.compile(r"[,，、;；|/]+")
"""标签 / 骨骼字符串的切分符：中英文逗号、顿号、分号、竖线与斜杠。"""


# --------------------------------------------------------------------------- #
# 容错取值工具（全部不抛异常）
# --------------------------------------------------------------------------- #
def _as_float_or_none(value: Any) -> Optional[float]:
    """把值转成有限浮点数；bool / 空串 / 非数字 / NaN / Infinity 一律返回 None。"""
    if isinstance(value, bool) or value is None:
        return None
    try:
        if isinstance(value, (int, float)):
            number = float(value)
        elif isinstance(value, str):
            text = value.strip()
            if not text:
                return None
            number = float(text)
        else:
            return None
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return number


def _as_float(value: Any, default: float = 0.0) -> float:
    """转成有限浮点数，失败时返回 ``default``。"""
    number = _as_float_or_none(value)
    return default if number is None else number


def _as_int(value: Any, default: int = 0) -> int:
    """转成整数（浮点截断、数字字符串可解析），失败时返回 ``default``。"""
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return value
    number = _as_float_or_none(value)
    if number is None:
        return default
    return int(number)


def _as_bool(value: Any, default: bool = False) -> bool:
    """转成布尔：接受 bool / 0-1 数字 / true/yes/on/loop/循环 等常见写法。"""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value) != 0.0
    if isinstance(value, str):
        text = value.strip().lower()
        if text in ("1", "true", "yes", "y", "on", "loop", "looping", "循环", "是"):
            return True
        if text in ("0", "false", "no", "n", "off", "non-loop", "noloop",
                    "非循环", "否"):
            return False
    return default


def _as_text(value: Any, default: str = "") -> str:
    """转成去掉首尾空白的字符串；bool / 容器 / None 一律用 ``default``。"""
    if value is None or isinstance(value, bool):
        return default
    if isinstance(value, str):
        text = value.strip()
        return text if text else default
    if isinstance(value, (int, float)):
        return str(value)
    return default


def _as_text_list(value: Any) -> List[str]:
    """把字符串 / 列表 / 元组 / 集合 / dict 统一成去重保序的字符串列表。

    * 字符串按中英文逗号、顿号、分号、竖线、斜杠切分；
    * dict 取其键（``joints: {name: angle}`` 这类 sidecar 写法）；
    * 集合先按 ``str`` 排序，保证同一集合两次解析结果完全一致。
    """
    if value is None:
        return []
    if isinstance(value, str):
        parts: List[Any] = _SPLIT_RE.split(value)
    elif isinstance(value, dict):
        parts = list(value.keys())
    elif isinstance(value, (list, tuple)):
        parts = list(value)
    elif isinstance(value, (set, frozenset)):
        parts = sorted(value, key=lambda item: str(item))
    else:
        return []
    items: List[str] = []
    for part in parts:
        if isinstance(part, (dict, list, tuple, set, frozenset)):
            continue
        text = _as_text(part)
        if text and text not in items:
            items.append(text)
    return items


def _as_float_dict(value: Any) -> Dict[str, float]:
    """把 dict 归一成「非空字符串键 → 有限浮点数」；键按字符串排序保证确定性。"""
    if not isinstance(value, dict):
        return {}
    result: Dict[str, float] = {}
    for key in sorted(value.keys(), key=lambda item: str(item)):
        name = str(key).strip()
        if not name:
            continue
        number = _as_float_or_none(value[key])
        if number is None:
            continue
        result[name] = number
    return result


# --------------------------------------------------------------------------- #
# 数据模型
# --------------------------------------------------------------------------- #
@dataclass
class MotionClip:
    """一个动作片段；全部字段均可 JSON 序列化。

    ``duration`` 是**派生属性**（``frame_count / fps``），不参与初始化，
    但会写进 ``to_dict()``，保证内存对象与 sidecar JSON 互转不丢信息。
    """

    clip_id: str = ""
    name: str = ""
    path: str = ""
    fps: float = DEFAULT_FPS
    frame_count: int = 0
    loopable: bool = False
    root_motion: bool = False
    bones: List[str] = field(default_factory=list)
    tags: List[str] = field(default_factory=list)
    features: Dict[str, float] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def duration(self) -> float:
        """时长（秒）= ``frame_count / fps``；``fps <= 0`` 或帧数非正时返回 ``0.0``。

        ``fps`` 为非法类型（如字符串）时按 :data:`DEFAULT_FPS` 兜底，
        与 :func:`clip_from_dict` 的容错口径一致。
        """
        fps = _as_float(self.fps, DEFAULT_FPS)
        frames = _as_int(self.frame_count, 0)
        if fps <= 0.0 or frames <= 0:
            return 0.0
        return float(frames) / fps

    @property
    def motion_type(self) -> str:
        """动作类型（``MOTION_TYPES`` 的英文 key，缺省 ``"other"``）。

        延迟导入 :mod:`tak.core.motion.features` 以避免模块循环依赖：
        features 依赖 model，model 只在属性访问时才需要 features。
        """
        from .features import infer_motion_type

        return infer_motion_type(self)

    def to_dict(self) -> Dict[str, Any]:
        """导出为可直接 ``json.dumps`` 的 dict（列表 / 字典均为浅拷贝）。"""
        return {
            "clip_id": self.clip_id,
            "name": self.name,
            "path": self.path,
            "fps": _as_float(self.fps, DEFAULT_FPS),
            "frame_count": _as_int(self.frame_count, 0),
            "duration": self.duration,
            "loopable": bool(self.loopable),
            "root_motion": bool(self.root_motion),
            "bones": list(self.bones),
            "tags": list(self.tags),
            "features": dict(self.features),
            "metadata": dict(self.metadata),
        }


# --------------------------------------------------------------------------- #
# 容错解析
# --------------------------------------------------------------------------- #
def clip_from_dict(data: Any) -> MotionClip:
    """从任意 dict 构造 :class:`MotionClip`；缺字段用默认值，类型错就兜底。

    * ``clip_id`` 缺失时依次回退 ``id`` / ``uid``；仍为空则留空，
      由 :class:`~tak.core.motion.library.MotionLibrary` 赋确定性的兜底 id；
    * 只有 ``duration`` 没有 ``frame_count`` 时，按 ``round(duration * fps)`` 反推帧数；
    * ``samples`` 不进入模型（模型不持有采样数据），需要真实特征时把原始 dict
      直接交给 ``extract_features()``。
    """
    if isinstance(data, MotionClip):
        return data
    if not isinstance(data, dict):
        data = {}

    clip_id = _as_text(data.get("clip_id"))
    if not clip_id:
        clip_id = _as_text(data.get("id"))
    if not clip_id:
        clip_id = _as_text(data.get("uid"))

    name = _as_text(data.get("name"))
    if not name:
        name = _as_text(data.get("label"))
    if not name:
        name = _as_text(data.get("title"))

    path = _as_text(data.get("path"))
    if not path:
        path = _as_text(data.get("file"))

    fps = _as_float(data.get("fps"), DEFAULT_FPS)

    frame_count = _as_int(data.get("frame_count"), 0)
    if frame_count <= 0:
        frame_count = _as_int(data.get("frames"), 0)
    if frame_count <= 0:
        frame_count = _as_int(data.get("num_frames"), 0)
    if frame_count <= 0:
        # 只给了时长：按 fps 反推，避免 duration 归零。
        duration = _as_float(data.get("duration"), 0.0)
        if duration > 0.0 and fps > 0.0:
            frame_count = _as_int(duration * fps, 0)

    loopable = _as_bool(data.get("loopable"), _as_bool(data.get("loop"), False))
    root_motion = _as_bool(data.get("root_motion"), False)

    bones = _as_text_list(data.get("bones"))
    if not bones:
        bones = _as_text_list(data.get("joints"))

    tags = _as_text_list(data.get("tags"))
    if not tags:
        tags = _as_text_list(data.get("keywords"))

    features = _as_float_dict(data.get("features"))

    metadata = data.get("metadata")
    metadata = dict(metadata) if isinstance(metadata, dict) else {}

    return MotionClip(
        clip_id=clip_id,
        name=name,
        path=path,
        fps=fps,
        frame_count=max(frame_count, 0),
        loopable=loopable,
        root_motion=root_motion,
        bones=bones,
        tags=tags,
        features=features,
        metadata=metadata,
    )
