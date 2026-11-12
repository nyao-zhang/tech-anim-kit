# -*- coding: utf-8 -*-
"""确定性离线 provider：不读取网络、时间或随机数来生成结果。"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from .budget import BudgetTracker, estimate_tokens


class OfflineProvider:
    """与真实 provider 相同的 chat/vision/embed 接口，输出可复现。"""

    model = "offline"

    def __init__(self, model: Optional[str] = None,
                 budget: Optional[BudgetTracker] = None) -> None:
        if model is not None:
            self.model = model
        self.budget = budget or BudgetTracker()

    def chat(self, messages: List[Dict[str, Any]], **kwargs: Any) -> str:
        started = time.monotonic()
        try:
            last = messages[-1]["content"] if messages else ""
            result = "[mock] 已收到 %d 条消息，末条 %d 字符" % (len(messages), len(last))
        except Exception:
            self.budget.record_call(estimate_tokens(messages),
                                    time.monotonic() - started, failed=True)
            raise
        self.budget.record_call(estimate_tokens(messages) + estimate_tokens(result),
                                time.monotonic() - started)
        return result

    def vision(self, images: List[str], prompt: str, **kwargs: Any) -> str:
        started = time.monotonic()
        result = "[mock] %d 张图，提示词 %d 字符" % (len(images), len(prompt))
        self.budget.record_call(
            estimate_tokens(images) + estimate_tokens(prompt) + estimate_tokens(result),
            time.monotonic() - started,
        )
        return result

    def embed(self, texts: List[str], **kwargs: Any) -> List[List[float]]:
        started = time.monotonic()
        result = [[float(len(text) % 97), float(len(text) % 31), 1.0] for text in texts]
        self.budget.record_call(estimate_tokens(texts) + estimate_tokens(result),
                                time.monotonic() - started)
        return result
