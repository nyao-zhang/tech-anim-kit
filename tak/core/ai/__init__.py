# -*- coding: utf-8 -*-
"""可插拔 AI provider：真实 HTTP 与确定性离线实现共用统一接口。"""
from .budget import BudgetTracker, estimate_tokens
from .http_client import (
    AIClientError, AIError, AINetworkError, AIParseError, AIServerError,
    AITimeoutError, OpenAIClient,
)
from .offline import OfflineProvider
from .providers import (
    MODEL_DEEPSEEK, MODEL_KIMI, MODEL_MINIMAX, TASK_ROUTING,
    HTTPProvider, MockProvider, Provider, get_provider, provider_for_task,
)

__all__ = [
    "MODEL_DEEPSEEK", "MODEL_KIMI", "MODEL_MINIMAX", "TASK_ROUTING",
    "AIClientError", "AIError", "AINetworkError", "AIParseError", "AIServerError",
    "AITimeoutError", "BudgetTracker", "HTTPProvider", "MockProvider",
    "OfflineProvider", "OpenAIClient", "Provider", "estimate_tokens", "get_provider",
    "provider_for_task",
]
