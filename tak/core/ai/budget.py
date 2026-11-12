# -*- coding: utf-8 -*-
"""线程安全的 AI 调用预算统计；token 数为离线估算值。"""
from __future__ import annotations

import json
import threading
from typing import Any, Dict, Optional


def estimate_tokens(value: Any) -> int:
    """按 UTF-8 字节数约 4 字节/token 估算，不依赖模型分词器。"""
    if value is None:
        return 0
    if isinstance(value, bytes):
        size = len(value)
    else:
        if isinstance(value, str):
            text = value
        else:
            text = json.dumps(value, ensure_ascii=False, default=str,
                              separators=(",", ":"))
        size = len(text.encode("utf-8"))
    return (size + 3) // 4 if size else 0


class BudgetTracker:
    """聚合调用次数、估算 token、耗时与失败次数；所有读写均加锁。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._calls = 0
        self._tokens = 0
        self._elapsed_seconds = 0.0
        self._failures = 0

    def record_call(self, tokens: int = 0, elapsed_seconds: float = 0.0,
                    failed: bool = False) -> None:
        """记录一次 provider 方法调用；HTTP 内部重试不重复计数。"""
        with self._lock:
            self._calls += 1
            self._tokens += max(0, int(tokens))
            self._elapsed_seconds += max(0.0, float(elapsed_seconds))
            if failed:
                self._failures += 1

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "calls": self._calls,
                "tokens": self._tokens,
                "elapsed_seconds": self._elapsed_seconds,
                "failures": self._failures,
            }

    @property
    def calls(self) -> int:
        return self.snapshot()["calls"]

    @property
    def tokens(self) -> int:
        return self.snapshot()["tokens"]

    @property
    def elapsed_seconds(self) -> float:
        return self.snapshot()["elapsed_seconds"]

    @property
    def failures(self) -> int:
        return self.snapshot()["failures"]

    def reset(self) -> None:
        with self._lock:
            self._calls = 0
            self._tokens = 0
            self._elapsed_seconds = 0.0
            self._failures = 0

    def to_json(self) -> str:
        return json.dumps(self.snapshot(), ensure_ascii=False, sort_keys=True)

    def export_json(self, path: Optional[str] = None) -> str:
        """返回 JSON；传入路径时同时以 UTF-8 写入该文件。"""
        text = self.to_json()
        if path is not None:
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(text)
        return text
