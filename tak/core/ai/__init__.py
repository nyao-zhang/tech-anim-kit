# -*- coding: utf-8 -*-
"""可插拔 AI provider：离线 mock 可跑通全部测试。"""
from .providers import (
    MODEL_DEEPSEEK, MODEL_KIMI, MODEL_MINIMAX, TASK_ROUTING,
    MockProvider, Provider, get_provider, provider_for_task,
)

__all__ = ["MODEL_DEEPSEEK", "MODEL_KIMI", "MODEL_MINIMAX", "TASK_ROUTING",
           "MockProvider", "Provider", "get_provider", "provider_for_task"]
