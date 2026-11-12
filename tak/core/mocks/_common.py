# -*- coding: utf-8 -*-
"""mock 模块构造的公共小工具。"""
from __future__ import annotations

import fnmatch
from types import ModuleType

from .scene import MockScene


def new_module(name: str, package: bool = False) -> ModuleType:
    module = ModuleType(name)
    module.__doc__ = "TAK mock module: %s" % name
    if package:
        module.__path__ = []  # type: ignore[attr-defined]
    return module


def attach_bridge(module: ModuleType, scene: MockScene) -> ModuleType:
    """给宿主命名空间挂上统一读口，四套 mock 的桥接方式完全一致。"""
    module.tak_scene = scene  # type: ignore[attr-defined]
    module.tak_snapshot = scene.snapshot  # type: ignore[attr-defined]
    return module


def match_name(name: str, pattern: str) -> bool:
    """Maya 风格的 ls 通配匹配。"""
    if pattern in ("*", ""):
        return True
    return fnmatch.fnmatchcase(name, pattern)


def depth_map(parents: dict) -> dict:
    """按 parent 链计算深度，带环保护。"""
    depths = {}
    for name in parents:
        depth = 0
        current = parents.get(name)
        seen = {name}
        while current is not None and current not in seen:
            seen.add(current)
            depth += 1
            current = parents.get(current)
        depths[name] = depth
    return depths
