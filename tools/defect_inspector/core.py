# -*- coding: utf-8 -*-
"""VLM Defect Inspector 插件编排层（P5.3）。

只做编排：从 ``ctx.options["frames"]`` 取抽帧、``ctx.snapshot`` 取骨骼白名单，
调用 ``tak.core.vision.inspect_frames`` 得到「AI 候选 + 确定性过滤结果」，
再把每条有效候选转成 INFO/WARNING Finding。**绝不写场景**；AI 只产出候选，
所有结果都需人工复核（对应 docs/VALIDATION.md B9）。
"""
from __future__ import annotations

from typing import Any, Dict, List

from tak.core.vision import (
    DEFECT_CATEGORIES, MAX_FRAMES, MAX_IMAGE_CHARS,
    build_prompt, fallback_candidates, inspect_frames,
    normalize_frames, parse_vision_response, validate_candidates,
)

TOOL_ID = "defect_inspector"
TITLE = "VLM Defect Inspector 抽帧缺陷诊断"
VERSION = "0.1.0"
_HIGH_CONFIDENCE = 0.75
_DCCS = ["maya", "max", "mobu", "unreal"]


def info() -> Dict[str, Any]:
    """插件自描述，供工具面板、菜单与 Web /api/tools 使用。"""
    return {
        "id": TOOL_ID, "name": TITLE, "version": VERSION,
        "dcc": list(_DCCS),
        "min_version": {"maya": "2023", "max": "2023",
                        "mobu": "2023", "unreal": "5.8"},
        "capabilities": ["vision.frames", "defect.candidates", "human_review",
                         "scene.read", "report.render", "offline"],
        "permissions": ["scene.read"],
        "description": "对 Playblast / UE 录屏抽帧做缺陷候选诊断：AI 仅产出候选，"
                       "确定性过滤后输出需人工复核的帧号/类别/骨骼/置信度；"
                       "离线可跑（确定性降级），不写场景。",
        "safety": "输出候选、需人工复核、不写场景、离线可跑",
        "web_api": "inspect(frames, snapshot=None, options=None, prompt=None, "
                   "provider=None, **kwargs) -> dict",
        "limits": {"max_frames": MAX_FRAMES, "max_image_chars": MAX_IMAGE_CHARS,
                   "categories": sorted(DEFECT_CATEGORIES)},
    }


def inspect(frames: Any = None, snapshot: Any = None, options: Any = None,
            prompt: Any = None, provider: Any = None, **kwargs: Any) -> Dict[str, Any]:
    """Web ``/api/vision`` 入口：返回 ``inspect_frames`` 的严格可序列化 dict。

    签名 ``inspect(frames, snapshot=None, options=None, prompt=None,
    provider=None, **kwargs)``；额外键（如 ``dcc``）被忽略，便于 Web 层
    直接 ``inspect(**body)``。绝不抛异常、绝不写场景。
    """
    return inspect_frames(
        frames if frames is not None else [],
        prompt=prompt, provider=provider, snapshot=snapshot,
        options=options if isinstance(options, dict) else None,
    )


def _severity_for(candidate: Dict[str, Any]) -> str:
    """确定性映射：高置信且类别可操作时 WARNING，否则 INFO；不交给 AI 判定。"""
    confidence = candidate.get("confidence", 0.0)
    high = isinstance(confidence, (int, float)) and not isinstance(confidence, bool) \
        and float(confidence) >= _HIGH_CONFIDENCE
    category = candidate.get("category", "unknown")
    actionable = isinstance(category, str) and category in DEFECT_CATEGORIES \
        and category != "unknown"
    return "warning" if (high and actionable) else "info"


def _conf_text(value: Any) -> str:
    return "%.2f" % value if isinstance(value, (int, float)) \
        and not isinstance(value, bool) else "0.00"


def run_report(ctx: Any) -> Any:
    """把 ``inspect_frames`` 候选转换为统一 Report；绝不写场景。"""
    from tak.core.report import Report
    from tak.core.rules import Finding, Severity

    options = getattr(ctx, "options", None)
    options = options if isinstance(options, dict) else {}
    frames = options.get("frames", [])

    snapshot = getattr(ctx, "snapshot", None)
    if hasattr(snapshot, "to_dict") and not isinstance(snapshot, dict):
        try:
            snapshot = snapshot.to_dict()
        except Exception:
            snapshot = {}
    snapshot = snapshot if isinstance(snapshot, dict) else {}

    result = inspect_frames(frames, snapshot=snapshot, options=options)
    candidates: List[Dict[str, Any]] = list(result.get("candidates") or [])
    findings: List[Any] = []
    for item in candidates:
        if not isinstance(item, dict):
            continue
        message = "帧 %s · 类别 %s · 置信度 %s：%s（候选，需人工复核）" % (
            item.get("frame"), item.get("category"),
            _conf_text(item.get("confidence")), item.get("evidence", ""))
        findings.append(Finding(
            rule_id="%s.candidate.%s" % (TOOL_ID, item.get("category", "unknown")),
            severity=Severity(_severity_for(item)),
            message=message[:500],
            node=str(item.get("bone") or ""),
            fix_hint="需人工复核（AI 候选，非确定性判定；见 docs/VALIDATION.md B9）",
            data={
                "frame": item.get("frame"),
                "category": item.get("category"),
                "bone": item.get("bone"),
                "confidence": item.get("confidence"),
                "evidence": item.get("evidence"),
                "source": item.get("source"),
                "bone_known": item.get("bone_known", True),
            },
        ))

    spec = getattr(ctx, "spec", None)
    spec_version = str(spec.get("version", "1.0.0")) if isinstance(spec, dict) \
        else "1.0.0"
    report = Report(
        title=TITLE,
        dcc=str(getattr(ctx, "dcc", "unreal") or "unreal"),
        findings=findings, spec_version=spec_version,
    )
    report.metadata.update({
        "tool_id": TOOL_ID,
        "candidates": candidates,
        "rejected": list(result.get("rejected") or []),
        "warnings": list(result.get("warnings") or []),
        "stats": dict(result.get("stats") or {}),
        "source": result.get("source", ""),
        "provider": result.get("provider", ""),
        "model": result.get("model", ""),
        "frames_checked": result.get("frames_checked", 0),
        "deterministic": bool(result.get("deterministic")),
        "safety": {
            "ai_role": "candidates_only",
            "deterministic_filter": "tak.core.vision.validate_candidates",
            "needs_human_review": True,
            "writes_scene": False,
            "offline_capable": True,
            "image_policy": "url_or_data_uri_only",
            "validation_ref": "docs/VALIDATION.md B9",
            "note": "AI 仅产出候选，非确定性判定；帧号/骨骼/置信度需人工复核；不写场景。",
        },
    })
    return report
