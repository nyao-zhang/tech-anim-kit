# -*- coding: utf-8 -*-
"""AI provider 抽象与模型分工。

重要边界：本模块只负责「任务该路由到哪个模型」以及调用封装，
**无法切换当前会话的底层模型**，也无法监测某个模型的额度到期。
会话级模型切换由使用者在 WorkBuddy 界面完成；派生子任务时可读取
TASK_ROUTING / 任务的 model 标注来指定模型。

硬约束：确定性判定走 tak.core.rules；AI 只做解释、候选生成、打标、诊断、
语义映射。本模块的任何输出都不得直接作为确定性规则判定结果。
"""
from __future__ import annotations

import os
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional

from .budget import BudgetTracker
from .http_client import OpenAIClient
from .offline import OfflineProvider

MODEL_DEEPSEEK = "DeepSeek-V4.1-Flash"
MODEL_KIMI = "Kimi-K3.1-Pro"
MODEL_MINIMAX = "MiniMax-M3.1-Flash"

TASK_ROUTING: Dict[str, str] = {
    "default": MODEL_DEEPSEEK,
    "codegen": MODEL_DEEPSEEK,
    "explain": MODEL_DEEPSEEK,
    "candidate": MODEL_DEEPSEEK,
    "candidates": MODEL_DEEPSEEK,
    "tag": MODEL_DEEPSEEK,
    "tagging": MODEL_DEEPSEEK,
    "diagnose": MODEL_DEEPSEEK,
    "diagnostics": MODEL_DEEPSEEK,
    "complex": MODEL_KIMI,
    "reasoning": MODEL_KIMI,
    "semantic_mapping": MODEL_KIMI,
    "architecture": MODEL_KIMI,
    "cross_dcc": MODEL_KIMI,
    "vision": MODEL_MINIMAX,
    "multimodal": MODEL_MINIMAX,
    "ui_review": MODEL_MINIMAX,
}


class Provider(ABC):
    """统一接口，仅承载解释、候选、打标、诊断和语义映射等 AI 任务。"""

    model: str = ""

    @abstractmethod
    def chat(self, messages: List[Dict[str, str]], **kwargs: Any) -> str:
        """文本对话。"""

    @abstractmethod
    def vision(self, images: List[str], prompt: str, **kwargs: Any) -> str:
        """多模态：图片/抽帧 + 提示词。"""

    @abstractmethod
    def embed(self, texts: List[str], **kwargs: Any) -> List[List[float]]:
        """向量化，供知识库 RAG / 动作检索使用。"""


class HTTPProvider(Provider):
    """真实 OpenAI 兼容 provider；具体传输、重试和预算由客户端实现。"""

    def __init__(self, model: Optional[str] = None,
                 client: Optional[OpenAIClient] = None, **client_kwargs: Any) -> None:
        self.client = client or OpenAIClient(model=model or MODEL_DEEPSEEK, **client_kwargs)
        self.model = self.client.model
        self.budget = self.client.budget

    def chat(self, messages: List[Dict[str, str]], **kwargs: Any) -> str:
        return self.client.chat(messages, **kwargs)

    def vision(self, images: List[str], prompt: str, **kwargs: Any) -> str:
        return self.client.vision(images, prompt, **kwargs)

    def embed(self, texts: List[str], **kwargs: Any) -> List[List[float]]:
        return self.client.embed(texts, **kwargs)


class MockProvider(OfflineProvider, Provider):
    """兼容旧公开名称的离线实现：不联网、不随机，结果可复现。"""

    model = "mock"


def provider_for_task(kind: str = "default") -> str:
    """返回某类任务应使用的模型名（供任务标注 / 子任务派生时读取）。

    任务类型仅限解释、候选、打标、诊断、复杂推理、语义映射和多模态分析；
    确定性判定必须由 tak.core.rules 完成。
    """
    return TASK_ROUTING.get(kind, TASK_ROUTING["default"])


def get_provider(model: Optional[str] = None, offline: Optional[bool] = None,
                 task: str = "default", budget: Optional[BudgetTracker] = None,
                 timeout: float = 30.0, max_retries: int = 3,
                 retry_backoff: float = 0.5) -> Provider:
    """按任务获取 provider；model 显式传入时覆盖任务路由。

    默认离线。TAK_AI_OFFLINE=0 且存在 TAK_AI_API_KEY 时才创建真实 provider；
    缺少密钥或显式离线均自动回退 mock，不因缺配置抛异常。
    """
    # 连接参数统一走 connection.resolve_settings()：环境变量 > ~/.tak/config.json
    # > 默认值。此前只读环境变量，用户在面板里填了 base_url 也不生效 —— 那正是
    # 「对话控制台连不上 WorkBuddy」的根因。
    from .connection import resolve_settings

    settings = resolve_settings()
    # 离线默认值改为「跟着钥匙走」：有密钥就可连，没密钥就离线。
    # 早先的默认是「一律离线，除非显式设 TAK_AI_OFFLINE=0」，结果是用户在面板里
    # 配好地址和密钥后仍然连不上，还得再去找一个没写在界面上的环境变量。
    # 显式设置 TAK_AI_OFFLINE 仍然优先（CI 里固定无网跑要靠它）。
    env_offline = os.environ.get("TAK_AI_OFFLINE", "").strip().lower()
    if offline is None:
        offline = (env_offline != "0") if env_offline else not settings["api_key_present"]
    if offline or not settings["api_key_present"]:
        return MockProvider(budget=budget)
    selected_model = model or settings["model"] or provider_for_task(task)
    return HTTPProvider(
        model=selected_model, budget=budget, timeout=timeout,
        max_retries=max_retries, retry_backoff=retry_backoff,
        base_url=settings["base_url"],
        # 统一走 resolve_settings()：环境变量 > config.json 指定的变量名 >
        # ~/.tak/credential.json。以前这里自己拼环境变量，导致面板里保存的
        # 密钥「测试连接能过、真对话又回落到 mock」—— 两处取值的规则必须同源。
        api_key=settings["api_key"],
    )
