# -*- coding: utf-8 -*-
"""动作索引 sidecar 读写与目录扫描：只读文件系统，**绝不解析 FBX 二进制**。

``motion_index.json`` 是动作库的稳定 sidecar 结构::

    {"contract": "1.0", "clip_count": 12, "clips": [MotionClip.to_dict(), ...]}

``scan_directory()`` 对每个候选 ``*.fbx`` 按以下优先级取值：

1. **同名 sidecar** ``<stem>.motion.json``（最可信，直接给出类型 / 时长 / 特征）；
2. 同目录 ``motion_index.json``，按 ``path`` 文件名或 ``clip_id`` 匹配条目；
3. **文件名启发式**：从文件名与父目录名提取动作类型关键词、``loop`` 标记、
   ``NNfps`` 帧率与 ``NNf`` 帧数，其余留空，``features["estimated"] = 1.0``。

为什么不解 FBX：FBX 是二进制容器，解析它要么依赖第三方 SDK（违反「零第三方
依赖」），要么自写二进制解码器（对导出器版本极其脆弱）。因此本模块只读目录
元数据与 JSON sidecar，**从不 open() FBX 内容**。真实的速度 / 循环误差等特征
应由上游采样阶段以 ``samples`` 或 ``features`` 写入 sidecar。
"""
from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, List, Optional

from .features import extract_features, infer_motion_type
from .library import MotionLibrary
from .model import _as_text

DEFAULT_PATTERN = "*.fbx"
SIDECAR_SUFFIX = ".motion.json"
INDEX_FILENAME = "motion_index.json"

_FPS_RE = re.compile(r"(\d{1,3})\s*fps", re.IGNORECASE)
_FRAME_RE = re.compile(r"(?<![0-9a-z])(\d{2,4})\s*f(?![a-z0-9])", re.IGNORECASE)
_LOOP_TOKENS = ("loop", "looping", "cycle", "循环", "loopable")
_NON_LOOP_TOKENS = ("noloop", "nonloop", "非循环", "oneshot", "one_shot")
_ROOT_TOKENS = ("rootmotion", "root_motion", "rm", "根运动")


# --------------------------------------------------------------------------- #
# 小工具
# --------------------------------------------------------------------------- #
def _read_json(path: str) -> Any:
    """读取 JSON；文件缺失 / 权限错误 / 编码错误 / 语法错误一律返回 None。"""
    try:
        if not os.path.isfile(path):
            return None
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError, UnicodeError):
        return None


def _normalized(path: str) -> str:
    """统一成前向斜杠的绝对路径（跨 DCC / 跨平台展示一致）。"""
    try:
        absolute = os.path.abspath(path)
    except OSError:
        absolute = path
    return str(absolute).replace(os.sep, "/").replace("\\", "/")


def _matches(filename: str, pattern: Any) -> bool:
    """轻量通配匹配：支持 ``*``、``*.*``、``*.ext`` 与精确文件名。

    不引入 ``fnmatch`` / ``glob``，避免额外 I/O 语义与隐藏的递归行为。
    """
    text = str(pattern or DEFAULT_PATTERN).strip()
    name = filename.lower()
    if text in ("", "*", "*.*"):
        return True
    if text.startswith("*."):
        return name.endswith(text[1:].lower())
    if text.startswith("*") and text.count("*") == 1:
        return name.endswith(text[1:].lower())
    return name == text.lower()


def _sidecar_for(directory: str, filename: str) -> Optional[Dict[str, Any]]:
    """找到某个 fbx 对应的 sidecar 条目（同名 sidecar 优先，其次目录索引）。"""
    stem = os.path.splitext(filename)[0]
    direct = _read_json(os.path.join(directory, stem + SIDECAR_SUFFIX))
    if isinstance(direct, dict):
        return direct

    index = _read_json(os.path.join(directory, INDEX_FILENAME))
    entries: List[Any] = []
    if isinstance(index, dict):
        raw = index.get("clips")
        entries = list(raw) if isinstance(raw, (list, tuple)) else []
    elif isinstance(index, (list, tuple)):
        entries = list(index)
    for item in entries:
        if not isinstance(item, dict):
            continue
        item_path = str(item.get("path") or "").replace("\\", "/")
        if os.path.basename(item_path).lower() == filename.lower():
            return item
        if str(item.get("clip_id") or "") == stem:
            return item
    return None


def _filename_hints(text: str) -> Dict[str, Any]:
    """从文件名 / 父目录名提取类型、循环标记、帧率与帧数（纯启发式）。"""
    lowered = str(text or "").lower()
    tokens = set(re.split(r"[^0-9a-z\u4e00-\u9fff]+", lowered))

    fps: Optional[float] = None
    match = _FPS_RE.search(lowered)
    if match:
        try:
            value = float(match.group(1))
            if value > 0.0:
                fps = value
        except (TypeError, ValueError):
            fps = None

    frame_count = 0
    for match in _FRAME_RE.finditer(lowered):
        try:
            value = int(match.group(1))
        except (TypeError, ValueError):
            continue
        if value > 0:
            frame_count = value
            break

    loopable = any(token in tokens for token in _LOOP_TOKENS)
    if any(token in tokens for token in _NON_LOOP_TOKENS):
        loopable = False
    root_motion = any(token in tokens for token in _ROOT_TOKENS)
    motion_type = infer_motion_type({"name": lowered})
    return {
        "fps": fps,
        "frame_count": frame_count,
        "loopable": loopable,
        "root_motion": root_motion,
        "motion_type": motion_type,
    }


def _candidate(full_path: str, sidecar: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """把单个 fbx 组装成候选 clip dict（不读文件内容）。"""
    filename = os.path.basename(full_path)
    stem = os.path.splitext(filename)[0]
    parent = os.path.basename(os.path.dirname(os.path.abspath(full_path)))
    path = _normalized(full_path)

    if isinstance(sidecar, dict):
        item = dict(sidecar)
        item["clip_id"] = _as_text(item.get("clip_id")) or stem
        item["name"] = _as_text(item.get("name")) or stem
        item["path"] = _as_text(item.get("path")) or path
        metadata = item.get("metadata")
        metadata = dict(metadata) if isinstance(metadata, dict) else {}
        metadata["source"] = "sidecar"
        metadata.setdefault("sidecar_dir", parent)
        item["metadata"] = metadata
        item["features"] = extract_features(item)
        return item

    hints = _filename_hints("%s %s" % (stem, parent))
    motion_type = hints["motion_type"]
    tags: List[str] = []
    if motion_type and motion_type != "other":
        tags.append(motion_type)
    if hints["loopable"]:
        tags.extend(["loop", "循环"])
    item = {
        "clip_id": stem,
        "name": stem,
        "path": path,
        "fps": hints["fps"] if hints["fps"] is not None else 30.0,
        "frame_count": hints["frame_count"],
        "loopable": hints["loopable"],
        "root_motion": hints["root_motion"],
        "bones": [],
        "tags": tags,
        "features": {},
        "metadata": {
            "source": "filename_heuristic",
            "parent_dir": parent,
            "note": "未找到 sidecar，仅按文件名推断；未解析 FBX 二进制",
        },
    }
    if motion_type and motion_type != "other":
        item["metadata"]["motion_type"] = motion_type
    item["features"] = extract_features(item)
    return item


# --------------------------------------------------------------------------- #
# 对外 API
# --------------------------------------------------------------------------- #
def load_index(path: Any) -> MotionLibrary:
    """读取 ``motion_index.json``；文件缺失 / 损坏 / 空索引时回退内置示例库。

    回退而不是抛异常，是为了让 UI 与 Agent Console 在索引尚未生成时仍可用；
    调用方可用 ``library.describe()["clip_count"]`` 判断是否拿到了真实索引。
    """
    data = _read_json(str(path or ""))
    if not isinstance(data, dict):
        return MotionLibrary()
    library = MotionLibrary.from_index(data)
    if not library.clips:
        return MotionLibrary()
    return library


def save_index(library: Any, path: Any) -> str:
    """把动作库写成稳定 JSON（``sort_keys=True``、无时间戳），返回路径。"""
    if isinstance(library, MotionLibrary):
        data = library.to_index()
    elif isinstance(library, dict):
        data = dict(library)
    else:
        raise TypeError("save_index 需要 MotionLibrary 或含 clips 的 dict")
    target = str(path)
    parent = os.path.dirname(os.path.abspath(target))
    if parent and not os.path.isdir(parent):
        os.makedirs(parent, exist_ok=True)
    text = json.dumps(data, sort_keys=True, ensure_ascii=False, indent=2)
    with open(target, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text + "\n")
    return target


def scan_directory(path: Any, pattern: Any = DEFAULT_PATTERN) -> List[Dict[str, Any]]:
    """只读扫描目录，为每个匹配文件生成一个候选 clip dict。

    目录不存在 / 权限错误 / 空目录 / 全部被 sidecar 判为非法 —— 一律返回 ``[]``，
    绝不抛异常、绝不解析 FBX 二进制、绝不写回任何文件。
    """
    try:
        root = str(path or "")
        if not root or not os.path.isdir(root):
            return []
        names = sorted(name for name in os.listdir(root)
                       if not name.startswith("."))
    except OSError:
        return []

    candidates: List[Dict[str, Any]] = []
    for name in names:
        full_path = os.path.join(root, name)
        try:
            if not os.path.isfile(full_path):
                continue
        except OSError:
            continue
        if not _matches(name, pattern):
            continue
        try:
            sidecar = _sidecar_for(root, name)
            candidates.append(_candidate(full_path, sidecar))
        except Exception:
            # 单条坏数据不拖垮整次扫描。
            continue
    return candidates
