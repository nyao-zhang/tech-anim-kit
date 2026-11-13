# -*- coding: utf-8 -*-
"""抽帧视觉诊断纯函数层（P5.3）。

零第三方依赖、零 DCC import；AI 只产出候选，确定性过滤在本包完成。
输出可复现，离线 provider 返回 mock 文本时优雅降级为确定性候选。
"""
from .frames import (
    DEFECT_CATEGORIES,
    MAX_FRAMES,
    MAX_IMAGE_CHARS,
    build_prompt,
    diagnose,
    fallback_candidates,
    inspect_frames,
    normalize_frames,
    parse_vision_response,
    validate_candidates,
)

__all__ = [
    "DEFECT_CATEGORIES", "MAX_FRAMES", "MAX_IMAGE_CHARS",
    "normalize_frames", "validate_candidates", "parse_vision_response",
    "fallback_candidates", "build_prompt", "inspect_frames", "diagnose",
]
