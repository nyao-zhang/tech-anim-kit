# -*- coding: utf-8 -*-
"""UE Python 启动引导脚本（供复制到 Documents/UnrealEngine/Python/ 或项目 Content/Python/）。

它做三件事，全部幂等、全部可降级：

1. 把仓库根加入 ``sys.path``（候选顺序：``TAK_REPO_ROOT`` 环境变量 →
   ``REPO_ROOT_HINT`` → 从本文件向上找 ``tak/adapters/base.py`` → 当前工作目录）；
2. 注册 TAK 的编辑器菜单（:func:`tak.adapters.unreal.menu.register_menus`）；
3. 暴露 :func:`tak_reload`，改完 ``tak/`` 代码后无需重启编辑器即可热重载。

在纯 Python 下 ``import tak.adapters.unreal.init_unreal`` 不会报错也没有副作用，
只有执行 :func:`main`（或作为脚本运行）时才打印降级说明。
"""
from __future__ import annotations

import os
import sys
from typing import Any, Dict, List

# 若把本文件复制到 Documents/UnrealEngine/Python/（不在仓库内），
# 请把仓库根填到这里，或设置环境变量 TAK_REPO_ROOT。
REPO_ROOT_HINT = ""

_MARKER = os.path.join("tak", "adapters", "base.py")

# 热重载顺序：依赖方在前，被依赖方在后，避免 reload 到旧引用。
_RELOAD_ORDER = [
    "tak.version",
    "tak.compat.unreal",
    "tak.core.diff.snapshot",
    "tak.core.diff",
    "tak.core.spec.schema",
    "tak.core.spec",
    "tak.core.rules.base",
    "tak.core.rules.builtin",
    "tak.core.rules.engine",
    "tak.core.rules",
    "tak.core.report.model",
    "tak.core.report",
    "tak.core.registry",
    "tak.core.runner",
    "tak.adapters.base",
    "tak.adapters.maya.adapter",
    "tak.adapters.unreal.adapter",
    "tak.adapters.unreal.menu",
    "tak.adapters.unreal.commandlet",
    "tak.adapters.unreal.init_unreal",
]


def _looks_like_repo(path: str) -> bool:
    return bool(path) and os.path.isfile(os.path.join(path, _MARKER))


def _walk_up(start: str) -> List[str]:
    """从 start 向上收集候选根目录（最多 8 层）。"""
    found: List[str] = []
    current = os.path.abspath(start)
    for _ in range(8):
        found.append(current)
        parent = os.path.dirname(current)
        if parent == current:
            break
        current = parent
    return found


def _candidates() -> List[str]:
    items: List[str] = []
    env_root = os.environ.get("TAK_REPO_ROOT", "").strip()
    if env_root:
        items.append(env_root)
    if REPO_ROOT_HINT:
        items.append(REPO_ROOT_HINT)
    here = globals().get("__file__")
    if here:
        items.extend(_walk_up(os.path.dirname(os.path.abspath(str(here)))))
    items.extend(_walk_up(os.getcwd()))
    # 去重但保持顺序
    seen = set()
    unique = []
    for item in items:
        key = os.path.normcase(os.path.abspath(item))
        if key not in seen:
            seen.add(key)
            unique.append(item)
    return unique


def ensure_repo_on_path() -> str:
    """确保仓库根在 ``sys.path`` 上，返回解析到的根目录（失败返回 ""）。"""
    try:
        import tak  # noqa: F401  # 已经能导入说明路径就绪
        return os.path.dirname(os.path.dirname(os.path.abspath(tak.__file__)))
    except Exception:
        pass
    for candidate in _candidates():
        if _looks_like_repo(candidate):
            absolute = os.path.abspath(candidate)
            if absolute not in sys.path:
                sys.path.insert(0, absolute)
            return absolute
    return ""


def tak_reload() -> List[str]:
    """按依赖顺序 ``importlib.reload`` 已加载的 tak 模块，返回成功列表。"""
    import importlib

    reloaded: List[str] = []
    for name in _RELOAD_ORDER:
        module = sys.modules.get(name)
        if module is None:
            continue
        try:
            importlib.reload(module)
            reloaded.append(name)
        except Exception as exc:
            print("[TAK] 热重载 %s 失败: %s: %s" % (name, type(exc).__name__, exc))
    return reloaded


def main(register: bool = True) -> Dict[str, Any]:
    """启动引导：加入 sys.path → 导入 tak → 注册菜单。返回可序列化的状态字典。"""
    info: Dict[str, Any] = {
        "repo_root": "",
        "tak_import": False,
        "unreal": False,
        "menu_registered": False,
        "degraded": [],
    }
    try:
        import unreal  # noqa: F401  # 仅用于判断是否在编辑器内
        info["unreal"] = True
    except Exception:
        info["unreal"] = False

    root = ensure_repo_on_path()
    info["repo_root"] = root
    if not root:
        message = ("[TAK] 降级：未找到仓库根，请设置 TAK_REPO_ROOT 环境变量或在 "
                   "REPO_ROOT_HINT 中填写仓库路径")
        info["degraded"].append(message)
        print(message)
        return info

    try:
        import tak  # noqa: F401
        from tak.adapters.unreal.adapter import create_adapter
        info["tak_import"] = True
    except Exception as exc:
        message = "[TAK] 降级：import tak 失败 %s: %s" % (type(exc).__name__, exc)
        info["degraded"].append(message)
        print(message)
        return info

    info["engine_version"] = create_adapter().describe().get("engine_version") or ""
    info["version"] = getattr(tak, "__version__", "")

    if not info["unreal"]:
        message = "[TAK] 降级：当前不是 Unreal 编辑器环境，跳过菜单注册（commandlet 仍可用）"
        info["degraded"].append(message)
        print(message)
        return info

    if register:
        try:
            from tak.adapters.unreal.menu import register_menus
            info["menu_registered"] = bool(register_menus())
        except Exception as exc:
            message = "[TAK] 菜单注册失败 %s: %s" % (type(exc).__name__, exc)
            info["degraded"].append(message)
            print(message)
    print("[TAK] 初始化完成：engine=%s menu=%s" % (
        info.get("engine_version") or "unknown", info["menu_registered"]))
    return info


if __name__ == "__main__":
    main()
