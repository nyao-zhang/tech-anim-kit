# -*- coding: utf-8 -*-
"""AI provider 抽象与模型分工。

重要边界：本模块只负责「任务该路由到哪个模型」以及调用封装，
**无法切换当前会话的底层模型**，也无法监测某个模型的额度到期。
会话级模型切换由使用者在 WorkBuddy 界面完成；派生子任务时可读取
TASK_ROUTING / 任务的 model 标注来指定模型。

硬约束：确定性判定走 tak.core.rules；AI 只做解释、候选生成、打标、诊断。
"""
from __future__ import annotations

import os
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional

MODEL_DEEPSEEK = "DeepSeek-V4.1-Flash"
MODEL_KIMI = "Kimi-K3.1-Pro"
MODEL_MINIMAX = "MiniMax-M3.1-Flash"

TASK_ROUTING: Dict[str, str] = {
    "default": MODEL_DEEPSEEK,
    "codegen": MODEL_DEEPSEEK,
    "explain": MODEL_DEEPSEEK,
    "complex": MODEL_KIMI,
    "architecture": MODEL_KIMI,
    "cross_dcc": MODEL_KIMI,
    "vision": MODEL_MINIMAX,
    "multimodal": MODEL_MINIMAX,
    "ui_review": MODEL_MINIMAX,
}


class Provider(ABC):
    """统一接口；真实实现放在 P1.5，P0 只交付契约 + 离线 mock。"""

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


class MockProvider(Provider):
    """离线确定性实现：不联网、不随机，保证回归测试可复现。"""

    model = "mock"

    def chat(self, messages: List[Dict[str, str]], **kwargs: Any) -> str:
        last = messages[-1]["content"] if messages else ""
        return "[mock] 已收到 %d 条消息，末条 %d 字符" % (len(messages), len(last))

    def vision(self, images: List[str], prompt: str, **kwargs: Any) -> str:
        return "[mock] %d 张图，提示词 %d 字符" % (len(images), len(prompt))

    def embed(self, texts: List[str], **kwargs: Any) -> List[List[float]]:
        return [[float(len(t) % 97), float(len(t) % 31), 1.0] for t in texts]


def provider_for_task(kind: str = "default") -> str:
    """返回某类任务应使用的模型名（供任务标注 / 子任务派生时读取）。"""
    return TASK_ROUTING.get(kind, TASK_ROUTING["default"])


def get_provider(model: Optional[str] = None, offline: Optional[bool] = None) -> Provider:
    """获取 provider。默认离线；TAK_AI_OFFLINE=0 且未实现真实 provider 时仍回退 mock。"""
    if offline is None:
        offline = os.environ.get("TAK_AI_OFFLINE", "1") != "0"
    if offline:
        return MockProvider()
    # P1.5 在此按 model 构造真实 provider；未实现前明确回退，避免静默失败。
    return MockProvider()
