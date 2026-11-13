# -*- coding: utf-8 -*-
"""抽帧缺陷诊断的确定性纯函数层（P5.3）。

严格边界
--------
* 零第三方依赖、零 DCC import：只使用 Python 标准库（json / math / re / typing）；
* 抽帧由调用方提供（list[dict]），本模块不解码视频、不读取本地图片、
  不调用外部进程；image 仅作为 URL / data URI 字符串透传给 provider；
* AI 只产出「候选」，确定性过滤与判定全部在本模块完成；离线 provider
  返回 mock 文本时优雅降级为确定性候选，绝不伪称模型识别结果；
* 输出可复现：无时间戳、无随机数、稳定排序、对超长/非字符串输入设上限。
"""
from __future__ import annotations

import json
import math
import re
from typing import Any, Dict, List, Optional, Set, Tuple

# 类别白名单：模型只能从这组词里选，禁止自由文本类别。
DEFECT_CATEGORIES = frozenset({
    "popping", "jitter", "foot_slide", "penetration",
    "weight_artifact", "twist", "ik_snap", "unknown",
})

MAX_FRAMES = 24
MAX_IMAGE_CHARS = 2_000_000

_SAFE_SCHEMES = ("http://", "https://", "data:")
_CODE_BLOCK_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.IGNORECASE | re.DOTALL)
_INTEGER_RE = re.compile(r"-?\d+")


# --------------------------------------------------------------------------- #
# 通用小工具（全部为纯函数，不把异常抛给上层）
# --------------------------------------------------------------------------- #
def _as_int(value: Any) -> Optional[int]:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and math.isfinite(value) and value.is_integer():
        return int(value)
    if isinstance(value, str):
        text = value.strip()
        if _INTEGER_RE.fullmatch(text):
            return int(text)
    return None


def _frame_value(item: Any) -> Any:
    if not isinstance(item, dict):
        return None
    value = item.get("frame")
    if value is None:
        value = item.get("frame_number")
    return value


def _clean_str_list(values: Any) -> List[str]:
    if isinstance(values, (set, frozenset)):
        values = sorted(values, key=str)
    if not isinstance(values, (list, tuple)):
        return []
    out: List[str] = []
    seen: Set[str] = set()
    for value in values:
        if not isinstance(value, str):
            continue
        text = value.strip()
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
    return out


def _safe_preview(raw: Any) -> Any:
    if isinstance(raw, dict):
        preview: Dict[str, Any] = {}
        for key in ("frame", "frame_number", "category", "bone", "confidence"):
            value = raw.get(key)
            if isinstance(value, float) and not math.isfinite(value):
                preview[key] = "<non-finite>"
            elif isinstance(value, (str, int, float, bool)) or value is None:
                preview[key] = value
        return preview
    return str(raw)[:200]


def _as_confidence(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    if not isinstance(value, (int, float, str)):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number) or number < 0.0 or number > 1.0:
        return None
    return round(number, 4)


def _frame_numbers(frames: Any) -> Set[int]:
    if isinstance(frames, dict):
        frames = frames.get("frames", [])
    if not isinstance(frames, (list, tuple)):
        return set()
    numbers: Set[int] = set()
    for item in frames:
        raw = _frame_value(item) if isinstance(item, dict) else item
        number = _as_int(raw)
        if number is not None:
            numbers.add(number)
    return numbers


def _joint_names(snapshot: Any) -> Set[str]:
    if not isinstance(snapshot, dict):
        return set()
    joints = snapshot.get("joints")
    names: Set[str] = set()
    if isinstance(joints, dict):
        names = {str(key) for key in joints.keys()}
    elif isinstance(joints, (list, tuple, set, frozenset)):
        for item in joints:
            if isinstance(item, str):
                names.add(item)
            elif isinstance(item, dict):
                name = item.get("name") or item.get("bone")
                if isinstance(name, str):
                    names.add(name)
    return names


def _coerce_frame_list(frames: Any) -> List[Dict[str, Any]]:
    """把规整后列表 / normalize 结果 / 原始列表统一成 [{frame, bones}]。"""
    if isinstance(frames, dict):
        frames = frames.get("frames", [])
    if not isinstance(frames, (list, tuple)):
        return []
    out: List[Dict[str, Any]] = []
    for item in frames:
        if isinstance(item, dict):
            number = _as_int(_frame_value(item))
            bones = item.get("bones")
            if not isinstance(bones, (list, tuple, set, frozenset)):
                bones = []
            clean_bones = _clean_str_list(bones)
        else:
            number = _as_int(item)
            clean_bones = []
        if number is None:
            continue
        out.append({"frame": number, "bones": clean_bones})
    return out


# --------------------------------------------------------------------------- #
# 1) 输入规整
# --------------------------------------------------------------------------- #
def _normalize_one(item: Any):
    """返回 (规整后的 dict, None) 或 (None, (reason, message))。"""
    if not isinstance(item, dict):
        return None, ("not_object", "帧项必须是 dict")
    number = _as_int(_frame_value(item))
    if number is None:
        return None, ("invalid_frame", "frame / frame_number 必须是整数")
    image = item.get("image")
    if not isinstance(image, str):
        return None, ("invalid_image", "image 必须是字符串（URL 或 data URI）")
    if len(image) > MAX_IMAGE_CHARS:
        return None, ("image_too_long",
                      "image 超过 %d 字符上限" % MAX_IMAGE_CHARS)
    if not image.strip().lower().startswith(_SAFE_SCHEMES):
        return None, ("invalid_image",
                      "image 只允许 http(s) URL 或 data URI，不读取本地文件")
    bones_raw = item.get("bones", [])
    if isinstance(bones_raw, str):
        bones_raw = [bones_raw]
    bones = _clean_str_list(bones_raw)
    note = item.get("note", "")
    if not isinstance(note, str):
        note = ""
    note = note.strip()[:500]
    if not note and item.get("timestamp") is not None:
        note = "timestamp=%s" % str(item.get("timestamp"))[:100]
    return {"frame": number, "image": image, "bones": bones, "note": note}, None


def normalize_frames(frames: Any) -> Dict[str, Any]:
    """把调用方提供的抽帧列表规整为确定、可复现的结构。

    返回 ``{"ok", "frames", "errors", "truncated", "stats"}``；``frames`` 中
    每项严格为 ``{"frame": int, "image": str, "bones": list[str], "note": str}``。
    非法项进入 ``errors`` 而不抛异常；超过 ``MAX_FRAMES`` 截断并标注。
    重复帧号（保留首次出现）计入 ``stats["duplicates"]``。
    """
    result: Dict[str, Any] = {
        "ok": False,
        "frames": [],
        "errors": [],
        "truncated": False,
        "stats": {"input": 0, "valid": 0, "invalid": 0,
                  "duplicates": 0, "truncated": 0},
    }
    if not isinstance(frames, (list, tuple)):
        result["errors"].append({
            "index": -1, "reason": "frames_not_list",
            "message": "frames 必须是 list[dict]",
        })
        return result
    items = list(frames)
    result["stats"]["input"] = len(items)
    if len(items) > MAX_FRAMES:
        result["truncated"] = True
        result["stats"]["truncated"] = len(items) - MAX_FRAMES
        items = items[:MAX_FRAMES]
    seen: Set[int] = set()
    for index, item in enumerate(items):
        normalized, error = _normalize_one(item)
        if error is not None:
            result["errors"].append({
                "index": index, "reason": error[0], "message": error[1],
            })
            result["stats"]["invalid"] += 1
            continue
        number = normalized["frame"]
        if number in seen:
            result["stats"]["duplicates"] += 1
            result["errors"].append({
                "index": index, "reason": "duplicate_frame",
                "message": "帧号 %d 重复，已保留首次出现" % number,
            })
            continue
        seen.add(number)
        result["frames"].append(normalized)
    result["stats"]["valid"] = len(result["frames"])
    result["ok"] = bool(result["frames"])
    return result


# --------------------------------------------------------------------------- #
# 2) 候选确定性过滤
# --------------------------------------------------------------------------- #
def _check_candidate(raw: Any, frame_numbers: Set[int], joints: Set[str],
                     warnings: List[Dict[str, Any]], index: int):
    if not isinstance(raw, dict):
        return None, "not_object"
    number = _as_int(_frame_value(raw))
    if number is None or number not in frame_numbers:
        return None, "frame_out_of_range"
    category = raw.get("category")
    if not isinstance(category, str) or category not in DEFECT_CATEGORIES:
        return None, "unknown_category"
    confidence = _as_confidence(raw.get("confidence"))
    if confidence is None:
        return None, "invalid_confidence"
    bone = raw.get("bone", "")
    bone = bone.strip() if isinstance(bone, str) else ""
    evidence = raw.get("evidence", "")
    evidence = evidence if isinstance(evidence, str) else str(evidence)
    source = raw.get("source", "")
    source = source if isinstance(source, str) and source else "vision_model"
    bone_known = True
    if bone and joints and bone not in joints:
        bone_known = False
        confidence = round(min(confidence, 0.3), 4)
        warnings.append({
            "type": "unknown_bone",
            "index": index, "frame": number, "bone": bone,
            "message": "骨骼 %s 不在快照 joints 中，候选已降级并降低置信度，需人工复核" % bone,
        })
    candidate = {
        "frame": number,
        "category": category,
        "bone": bone,
        "confidence": confidence,
        "evidence": evidence[:1000],
        "source": source,
        "bone_known": bone_known,
    }
    return candidate, None


def validate_candidates(candidates: Any, frames: Any,
                        snapshot: Any = None) -> Dict[str, Any]:
    """确定性过滤 AI 候选，返回 {"ok","valid","rejected","warnings","stats"}。

    规则：帧号必须存在于输入帧、类别在白名单、confidence 为 0..1 有限数、
    骨骼若提供且 snapshot 有 joints 则必须存在（否则降级并标 warning）、
    重复项按 (frame, category, bone) 去重、结果按 (frame, category, bone) 稳定排序。
    """
    frame_numbers = _frame_numbers(frames)
    joints = _joint_names(snapshot)
    items = list(candidates) if isinstance(candidates, (list, tuple)) else []
    valid: List[Dict[str, Any]] = []
    rejected: List[Dict[str, Any]] = []
    warnings: List[Dict[str, Any]] = []
    seen: Set[Tuple[int, str, str]] = set()
    stats: Dict[str, Any] = {
        "input": len(items), "valid": 0, "rejected": 0, "duplicates": 0,
        "warnings": 0, "frames_checked": len(frame_numbers),
        "joints_checked": len(joints),
    }
    for index, raw in enumerate(items):
        candidate, reason = _check_candidate(raw, frame_numbers, joints,
                                              warnings, index)
        if reason is not None:
            rejected.append({"index": index, "candidate": _safe_preview(raw),
                             "reason": reason})
            stats["rejected"] += 1
            continue
        key = (candidate["frame"], candidate["category"], candidate["bone"])
        if key in seen:
            stats["duplicates"] += 1
            rejected.append({"index": index, "candidate": _safe_preview(raw),
                             "reason": "duplicate"})
            stats["rejected"] += 1
            continue
        seen.add(key)
        valid.append(candidate)
    valid.sort(key=lambda item: (item["frame"], item["category"], item["bone"]))
    stats["valid"] = len(valid)
    stats["warnings"] = len(warnings)
    return {"ok": bool(valid), "valid": valid, "rejected": rejected,
            "warnings": warnings, "stats": stats}


# --------------------------------------------------------------------------- #
# 3) 模型响应解析
# --------------------------------------------------------------------------- #
def _try_loads(value: str):
    if not value:
        return None
    try:
        parsed = json.loads(value)
    except (ValueError, TypeError):
        return None
    return parsed if isinstance(parsed, (dict, list)) else None


def _slice_json(value: str) -> str:
    starts = [pos for pos in (value.find("{"), value.find("[")) if pos >= 0]
    if not starts:
        return ""
    start = min(starts)
    ends = [pos for pos in (value.rfind("}"), value.rfind("]")) if pos >= start]
    if not ends:
        return ""
    return value[start:max(ends) + 1]


def parse_vision_response(text: Any) -> Dict[str, Any]:
    """容忍 ```json 代码块 / {"candidates":[...]} / 纯数组；非法返回空候选+error。"""
    if not isinstance(text, str) or not text.strip():
        return {"candidates": [], "error": "empty_or_non_string"}
    if len(text) > MAX_IMAGE_CHARS:
        return {"candidates": [], "error": "response_too_long"}
    stripped = text.strip()
    payload = None
    match = _CODE_BLOCK_RE.search(stripped)
    if match:
        payload = _try_loads(match.group(1).strip())
    if payload is None:
        payload = _try_loads(stripped)
    if payload is None:
        payload = _try_loads(_slice_json(stripped))
    if payload is None:
        return {"candidates": [], "error": "json_parse_failed"}
    if isinstance(payload, dict):
        candidates = payload.get("candidates")
        if candidates is None and "frame" in payload and "category" in payload:
            candidates = [payload]
    elif isinstance(payload, list):
        candidates = payload
    else:
        return {"candidates": [], "error": "unexpected_json_type"}
    if not isinstance(candidates, list):
        return {"candidates": [], "error": "candidates_not_list"}
    return {"candidates": candidates, "error": ""}


# --------------------------------------------------------------------------- #
# 4) 离线确定性候选（降级用）
# --------------------------------------------------------------------------- #
def _fb(frame: int, confidence: float, evidence: str) -> Dict[str, Any]:
    return {"frame": frame, "category": "unknown", "bone": "",
            "confidence": confidence, "evidence": evidence,
            "source": "deterministic_fallback"}


def _first_unused(numbers: List[int], used: Set[int]) -> Optional[int]:
    for number in numbers:
        if number not in used:
            return number
    return None


def fallback_candidates(frames: Any, snapshot: Any = None,
                        options: Any = None) -> List[Dict[str, Any]]:
    """离线确定性候选：仅基于帧数、帧号间隔、是否提供骨骼等可判定信号。

    不虚构任何具体视觉缺陷（类别一律 unknown、置信度低、evidence 标注
    「需人工复核」）；每条固定带 ``source="deterministic_fallback"``。
    """
    norm = _coerce_frame_list(frames)
    if not norm:
        return []
    numbers = [item["frame"] for item in norm]
    used: Set[int] = set()
    out: List[Dict[str, Any]] = []

    def emit(preferred: int, confidence: float, evidence: str) -> None:
        frame = preferred if preferred not in used else _first_unused(numbers, used)
        if frame is None:
            return
        used.add(frame)
        out.append(_fb(frame, confidence, evidence))

    if len(norm) < 3:
        emit(numbers[0], 0.2,
             "抽帧数量 %d（<3），无法评估运动连续性；离线确定性降级，"
             "未做视觉缺陷识别，需人工复核" % len(norm))
    gaps = [numbers[i + 1] - numbers[i] for i in range(len(numbers) - 1)]
    if gaps and any(gap != gaps[0] for gap in gaps):
        idx = next(i for i, gap in enumerate(gaps) if gap != gaps[0])
        emit(numbers[idx + 1], 0.2,
             "帧号间隔不均匀（首段 %d，第 %d 段 %d），连续性缺陷判读可能失真；"
             "离线确定性降级，需人工复核" % (gaps[0], idx + 2, gaps[idx]))
    missing = [item["frame"] for item in norm if not item["bones"]]
    if missing:
        emit(missing[0], 0.15,
             "该帧未提供骨骼定位信息，候选无法定位到具体关节；"
             "离线确定性降级，需人工复核")
    if not out:
        emit(numbers[0], 0.1,
             "未获得视觉模型有效候选；离线确定性降级，未做缺陷识别，"
             "全部结果需人工复核")
    return out


# --------------------------------------------------------------------------- #
# 5) 提示词
# --------------------------------------------------------------------------- #
def build_prompt(frames: Any, snapshot: Any = None,
                 options: Any = None) -> str:
    """结构化提示词：强制参数化 JSON、字段白名单、帧号/类别/骨骼白名单。"""
    norm = _coerce_frame_list(frames)
    frame_numbers = sorted({item["frame"] for item in norm})
    categories = sorted(DEFECT_CATEGORIES)
    joints = sorted(_joint_names(snapshot))
    bones_line = (json.dumps(joints, ensure_ascii=False) if joints
                  else "[]（未提供快照 joints，bone 请留空字符串）")
    lines = [
        "你是动画抽帧质量诊断助手。输入为按时间顺序排列的抽帧图片（URL 或 data URI 字符串）。",
        "",
        "【硬性输出约束】",
        "1. 只输出一个参数化 JSON 对象，不要输出任何自由文本、解释、Markdown 前缀、注释或脚本。",
        "2. JSON 顶层结构必须且只能为 {\"candidates\": [ ... ]}；不得包裹其它键。",
        "3. 每个候选对象的字段白名单：frame(int)、category(str)、bone(str)、"
        "confidence(0..1 数值)、evidence(str)。不得增加任何其它字段。",
        "4. frame 只能取自【帧号白名单】；category 只能取自【类别白名单】；"
        "bone 只能取自【骨骼白名单】或留空字符串。",
        "5. confidence 必须是 0 到 1 之间的有限数值；evidence 用一句话描述可见现象，不得臆造。",
        "6. 你只产出「候选」，不做确定性判定；无法确定时返回空 candidates 数组。",
        "7. 不得输出可执行代码，不得尝试访问本地文件或执行任何命令。",
        "",
        "【帧号白名单】%s" % json.dumps(frame_numbers, ensure_ascii=False),
        "【类别白名单】%s" % json.dumps(categories, ensure_ascii=False),
        "【骨骼白名单】%s" % bones_line,
        "【输入帧数】%d" % len(frame_numbers),
    ]
    if isinstance(options, dict):
        focus = options.get("focus")
        if isinstance(focus, str) and focus.strip():
            lines.append("【补充关注点】%s" % focus.strip()[:300])
    lines.append("")
    lines.append("仅返回 JSON，例如："
                 '{"candidates": [{"frame": 1, "category": "unknown", "bone": "", '
                 '"confidence": 0.3, "evidence": "需人工复核"}]}。')
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# 6) 统一入口
# --------------------------------------------------------------------------- #
def _default_provider():
    try:
        from tak.core.ai import get_provider
        return get_provider(task="vision")
    except Exception:
        return None


def inspect_frames(frames: Any, prompt: Any = None, provider: Any = None,
                   snapshot: Any = None, options: Any = None) -> Dict[str, Any]:
    """抽帧诊断统一入口：规整 → 提示词 → provider.vision → 解析 → 降级 → 过滤。

    绝不向调用方抛异常：provider 异常、解析失败或离线 mock 文本都会降级为
    ``source="deterministic_fallback"`` 的确定性候选并标注 warning。
    """
    try:
        return _inspect_impl(frames, prompt, provider, snapshot, options)
    except Exception as exc:  # pragma: no cover - 兜底，保证绝不外抛
        return _emergency_result(frames, exc)


def _inspect_impl(frames, prompt, provider, snapshot, options):
    warnings: List[Dict[str, Any]] = []
    norm = normalize_frames(frames)
    good_frames = norm["frames"]
    for error in norm["errors"]:
        warnings.append({
            "type": "frame_rejected",
            "message": "第 %s 项输入被拒绝：%s（%s）" % (
                error.get("index"), error.get("reason"), error.get("message", "")),
        })
    if norm["truncated"]:
        warnings.append({
            "type": "truncated",
            "message": "输入帧数超过上限 %d，已截断，需人工复核" % MAX_FRAMES,
        })

    images = [item["image"] for item in good_frames]
    final_prompt = (prompt if isinstance(prompt, str) and prompt.strip()
                    else build_prompt(good_frames, snapshot=snapshot, options=options))

    if provider is None:
        provider = _default_provider()
    provider_name = type(provider).__name__ if provider is not None else "none"
    model_name = (str(getattr(provider, "model", "") or "")
                  if provider is not None else "")

    parse_result: Dict[str, Any] = {"candidates": [], "error": ""}
    if provider is None:
        warnings.append({"type": "provider_unavailable",
                         "message": "无法获取 vision provider，已降级为确定性候选"})
        parse_result = {"candidates": [], "error": "provider_unavailable"}
    else:
        try:
            raw = provider.vision(images, final_prompt)
            if not isinstance(raw, str):
                raise TypeError("provider.vision 未返回字符串")
            parse_result = parse_vision_response(raw)
            if parse_result.get("error"):
                warnings.append({
                    "type": "parse_error",
                    "message": "模型输出无法解析（%s），已降级" % parse_result["error"],
                })
        except Exception as exc:
            warnings.append({
                "type": "provider_error",
                "message": "vision provider 调用失败（%s），已降级为确定性候选"
                           % type(exc).__name__,
            })
            parse_result = {"candidates": [], "error": "provider_error"}

    candidates = parse_result.get("candidates") or []
    source = "vision_model"
    deterministic = False
    if not candidates or parse_result.get("error"):
        candidates = fallback_candidates(good_frames, snapshot=snapshot,
                                         options=options)
        source = "deterministic_fallback"
        deterministic = True
        warnings.append({
            "type": "deterministic_fallback",
            "message": "未获得有效的视觉模型候选；已切换为离线确定性候选，"
                       "仅标注可判定信号，需人工复核",
        })

    checked = validate_candidates(candidates, good_frames, snapshot=snapshot)
    warnings.extend(checked["warnings"])

    stats = dict(checked["stats"])
    stats.update({
        "frames_checked": len(good_frames),
        "truncated": norm["truncated"],
        "invalid_inputs": len(norm["errors"]),
        "parse_error": parse_result.get("error", ""),
        "source": source,
    })
    ok = bool(good_frames)
    if not ok:
        warnings.append({"type": "no_valid_frames",
                         "message": "没有可诊断的有效帧，结果为空"})
    return {
        "ok": ok,
        "candidates": checked["valid"],
        "rejected": checked["rejected"],
        "warnings": warnings,
        "provider": provider_name,
        "model": model_name,
        "source": source,
        "frames_checked": len(good_frames),
        "stats": stats,
        "deterministic": deterministic,
    }


def _emergency_result(frames, exc):
    numbers: List[int] = []
    try:
        if isinstance(frames, (list, tuple)):
            for item in frames:
                if isinstance(item, dict):
                    number = _as_int(_frame_value(item))
                    if number is not None:
                        numbers.append(number)
    except Exception:
        numbers = []
    candidates: List[Dict[str, Any]] = []
    try:
        seed = [{"frame": number, "bones": []} for number in numbers]
        candidates = validate_candidates(fallback_candidates(seed), numbers)["valid"]
    except Exception:
        candidates = []
    return {
        "ok": False,
        "candidates": candidates,
        "rejected": [],
        "warnings": [{"type": "internal_error",
                      "message": "内部异常已降级为确定性候选：%s" % type(exc).__name__}],
        "provider": "none", "model": "",
        "source": "deterministic_fallback",
        "frames_checked": len(numbers),
        "stats": {}, "deterministic": True,
    }


def diagnose(frames: Any, prompt: Any = None, provider: Any = None,
             snapshot: Any = None, options: Any = None) -> Dict[str, Any]:
    """``inspect_frames`` 的语义化薄封装（别名）。"""
    return inspect_frames(frames, prompt=prompt, provider=provider,
                          snapshot=snapshot, options=options)
