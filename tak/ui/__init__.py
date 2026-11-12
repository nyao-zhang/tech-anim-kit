# -*- coding: utf-8 -*-
"""统一设计系统：tokens.json 是唯一事实源，theme.qss / theme.css 由生成器产出。"""
from __future__ import annotations

import json
import os

TOKENS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tokens.json")


def load_tokens() -> dict:
    with open(TOKENS_PATH, "r", encoding="utf-8") as fh:
        return json.load(fh)


__all__ = ["TOKENS_PATH", "load_tokens"]
