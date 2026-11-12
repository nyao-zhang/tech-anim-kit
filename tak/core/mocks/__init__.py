# -*- coding: utf-8 -*-
"""DCC mock 注入（P1.4）：离线跑通 Maya / Max / MoBu / Unreal 适配层。

用法::

    from tak.core.mocks import install_mock, uninstall_mock

    handle = install_mock("maya", "fixtures/good/good_001.json")
    handle.adapter.get_skeleton_snapshot()
    uninstall_mock()

约束：
* 只替换 ``sys.modules`` 中约定好的模块名，不 import 任何真实 DCC 模块；
* ``uninstall_mock`` 精确还原替换前的 ``sys.modules``（含原本不存在的键）；
* 重复 install 会先还原再安装，异常路径不会留下半装状态。
"""
from __future__ import annotations

import os
import sys
from types import ModuleType
from typing import Any, Dict, Optional, Tuple, Union

from .adapter import MockAdapter
from .max_mock import build as build_max
from .maya_mock import build as build_maya
from .mobu_mock import build as build_mobu
from .scene import MockJoint, MockScene, load_fixture
from .unreal_mock import build as build_unreal

__all__ = [
    "MockAdapter",
    "MockHandle",
    "MockJoint",
    "MockScene",
    "available_dccs",
    "install_mock",
    "load_fixture",
    "uninstall_mock",
]

_BUILDERS = {
    "maya": build_maya,
    "max": build_max,
    "mobu": build_mobu,
    "unreal": build_unreal,
}

_ALIASES = {
    "maya": "maya",
    "autodeskmaya": "maya",
    "max": "max",
    "3dsmax": "max",
    "3ds": "max",
    "3dsmax2023": "max",
    "mobu": "mobu",
    "motionbuilder": "mobu",
    "mb": "mobu",
    "unreal": "unreal",
    "unrealengine": "unreal",
    "ue": "unreal",
    "ue5": "unreal",
    "ue58": "unreal",
}

_MISSING = object()

Snapshots = Union[MockScene, Dict[str, Any], str, os.PathLike, None]


class MockHandle:
    """一次安装的句柄：场景、适配器与注入的模块。"""

    def __init__(self, dcc: str, scene: MockScene, adapter: MockAdapter,
                 modules: Dict[str, ModuleType], originals: Dict[str, Any]):
        self.dcc = dcc
        self.scene = scene
        self.adapter = adapter
        self.modules = modules
        self._originals = originals

    def installed(self) -> bool:
        return all(sys.modules.get(name) is module for name, module in self.modules.items())

    def __repr__(self) -> str:
        return "<MockHandle %s modules=%s>" % (self.dcc, sorted(self.modules))


_state: Optional[MockHandle] = None


def available_dccs() -> Tuple[str, ...]:
    """可注入的 DCC 规范名，顺序固定便于断言。"""
    return tuple(_BUILDERS)


def install_mock(dcc: Any, snapshot: Snapshots = None) -> MockHandle:
    """注入指定 DCC 的假模块。

    :param dcc: ``"maya"`` / ``"max"`` / ``"mobu"`` / ``"unreal"`` 或 ``DCC`` 枚举
    :param snapshot: ``MockScene``、fixture 路径、fixture dict 或 snapshot dict；
                     缺省时使用最小内置骨架。
    """
    global _state
    canonical = _canonical_dcc(dcc)
    scene = _coerce_scene(canonical, snapshot)
    modules = _BUILDERS[canonical](scene)

    if any(not isinstance(module, ModuleType) for module in modules.values()):
        raise TypeError("mock 构造器必须返回 ModuleType")
    for name, module in modules.items():
        if module.__name__ != name:
            raise ValueError("mock 模块名不一致：%s != %s" % (module.__name__, name))

    if _state is not None:
        uninstall_mock()

    originals = {name: sys.modules.get(name, _MISSING) for name in modules}
    try:
        _apply(modules)
    except BaseException:
        _restore(originals)
        raise

    handle = MockHandle(canonical, scene, MockAdapter(canonical, scene), modules, originals)
    _state = handle
    return handle


def uninstall_mock() -> bool:
    """还原注入前的 ``sys.modules``；未安装时安全返回 False。"""
    global _state
    if _state is None:
        return False
    handle = _state
    _state = None
    _restore(handle._originals)
    handle._originals = {}
    return True


def _canonical_dcc(dcc: Any) -> str:
    from tak.adapters.base import DCC  # 延迟导入，避免包初始化期循环

    if isinstance(dcc, DCC):
        return dcc.value
    key = str(dcc).strip().lower().replace(" ", "").replace("_", "").replace("-", "")
    canonical = _ALIASES.get(key)
    if canonical is None:
        raise ValueError("未知 DCC %r，可用：%s" % (dcc, ", ".join(available_dccs())))
    return canonical


def _coerce_scene(canonical: str, snapshot: Snapshots) -> MockScene:
    if snapshot is None:
        return MockScene.default(canonical)
    if isinstance(snapshot, MockScene):
        if snapshot.dcc == canonical:
            return snapshot
        return MockScene.from_snapshot(snapshot.snapshot(), dcc=canonical)
    if isinstance(snapshot, dict):
        return MockScene.from_snapshot(snapshot, dcc=canonical)
    if isinstance(snapshot, (str, os.PathLike)):
        return MockScene.from_fixture(os.fspath(snapshot), dcc=canonical)
    raise TypeError("无法从 %s 构造 mock 场景" % type(snapshot).__name__)


def _apply(modules: Dict[str, ModuleType]) -> None:
    for name in sorted(modules, key=lambda item: item.count(".")):
        sys.modules[name] = modules[name]
    for name, module in modules.items():
        if "." not in name:
            continue
        parent_name, _, child = name.rpartition(".")
        parent = sys.modules.get(parent_name)
        if parent is not None:
            setattr(parent, child, module)


def _restore(originals: Dict[str, Any]) -> None:
    for name, original in originals.items():
        if original is _MISSING:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = original
