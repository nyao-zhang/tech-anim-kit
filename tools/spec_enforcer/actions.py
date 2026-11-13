# -*- coding: utf-8 -*-
"""Spec Enforcer 的受限 DSL 动作：只通过 adapter 改写场景。

约定
----
* 每个动作只做一件事，参数由 ``ACTION_REGISTRY`` 的声明式参数表校验
  （required / type / min / max），越界在 dry-run 阶段就被拒绝；
* 所有写入都走 adapter：适配器没有对应方法时返回 ``skipped`` 结果并写日志，
  **绝不 import maya / pymxs / pyfbsdk**，也绝不直接改场景；
* 注册前用 ``if name not in ACTION_REGISTRY.names`` 守卫，模块被重复加载
  （多次 ``load_plugin_module``）时不会抛 ``DSLError``；
* 动作本身不开启 undo：``ACTION_REGISTRY.execute`` 负责
  begin_undo → 逐条执行 → 失败整体 rollback。
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Tuple

from tak.core.dsl import ACTION_REGISTRY

TOOL_ID = "spec_enforcer"
_PERMISSIONS = ["node.write"]


# --------------------------------------------------------------------------
# adapter 调用与安全降级
# --------------------------------------------------------------------------
def _log(adapter: Any, message: str, level: str = "info") -> None:
    """写宿主日志；日志失败也不能影响动作结果。"""
    logger = getattr(adapter, "log", None)
    if callable(logger):
        try:
            logger("[%s] %s" % (TOOL_ID, message), level)
        except Exception:
            pass


def _skipped(action: str, reason: str, **extra: Any) -> Dict[str, Any]:
    payload: Dict[str, Any] = {"status": "skipped", "action": action, "reason": reason}
    payload.update(extra)
    return payload


def _call(adapter: Any, action: str, candidates: Iterable[str],
          args: Tuple[Any, ...], kwargs: Dict[str, Any]) -> Dict[str, Any]:
    """依次尝试候选 adapter 方法；都不存在时安全 no-op 并记录。

    真实宿主异常不做吞掉处理：让它冒泡到执行器，由执行器整体 rollback。
    """
    names: List[str] = list(candidates)
    for attr in names:
        method = getattr(adapter, attr, None)
        if not callable(method):
            continue
        result = method(*args, **kwargs)
        _log(adapter, "%s 通过 adapter.%s 执行完成" % (action, attr))
        return {"status": "done", "action": action, "method": attr,
                "result": result if isinstance(result, (str, int, float, bool, type(None))) else None}
    reason = "adapter 未实现 %s" % " / ".join(names)
    _log(adapter, "%s 安全跳过：%s" % (action, reason), "warning")
    return _skipped(action, reason)


# --------------------------------------------------------------------------
# 动作实现
# --------------------------------------------------------------------------
def rename_joint(adapter: Any, node: str, name: Optional[str] = None,
                 pattern: Optional[str] = None, reason: str = "") -> Dict[str, Any]:
    """按规范重命名关节。

    ``name`` 是目标名；规范正则无法唯一推导目标名，因此未提供时只记录
    「需人工重命名」，不猜名字、不改场景。
    """
    if not name:
        detail = "缺少目标名，需人工确认重命名（pattern=%s）" % (pattern or "未指定")
        _log(adapter, "rename_joint %r 安全跳过：%s" % (node, detail), "warning")
        return _skipped("rename_joint", detail, node=node, pattern=pattern or "")
    return _call(adapter, "rename_joint", ("rename_node", "rename_joint"),
                 (node, name), {})


def set_control_color(adapter: Any, node: str, color: int) -> Dict[str, Any]:
    """设置控制器的 override color（Maya 颜色索引 0-31）。"""
    return _call(adapter, "set_control_color",
                 ("set_control_color", "set_override_color"), (node, int(color)), {})


def normalize_weights(adapter: Any, max_influences: Optional[int] = None,
                      tolerance: Optional[float] = None) -> Dict[str, Any]:
    """归一化蒙皮权重，可选先把影响骨限制到上限。"""
    kwargs: Dict[str, Any] = {}
    if max_influences is not None:
        kwargs["max_influences"] = int(max_influences)
    if tolerance is not None:
        kwargs["tolerance"] = float(tolerance)
    return _call(adapter, "normalize_weights",
                 ("normalize_weights", "normalize_skin_weights"), (), kwargs)


def set_namespace(adapter: Any, node: str, namespace: str) -> Dict[str, Any]:
    """把节点移入指定命名空间；空字符串表示移回根命名空间。"""
    return _call(adapter, "set_namespace",
                 ("set_namespace", "rename_namespace"), (node, namespace), {})


# --------------------------------------------------------------------------
# 注册（带重复加载守卫）
# --------------------------------------------------------------------------
_ACTIONS = (
    (TOOL_ID + ".rename_joint", rename_joint,
     "按规范重命名关节（目标名必须显式提供）",
     {"node": {"type": "str", "required": True},
      "name": {"type": "str"},
      "pattern": {"type": "str"},
      "reason": {"type": "str"}}),
    (TOOL_ID + ".set_control_color", set_control_color,
     "设置控制器 override color",
     {"node": {"type": "str", "required": True},
      "color": {"type": "int", "required": True, "min": 0, "max": 31}}),
    (TOOL_ID + ".normalize_weights", normalize_weights,
     "归一化蒙皮权重，可限制影响骨上限",
     {"max_influences": {"type": "int", "min": 1, "max": 32},
      "tolerance": {"type": "float", "min": 0.0, "max": 1.0}}),
    (TOOL_ID + ".set_namespace", set_namespace,
     "把节点移入指定命名空间",
     {"node": {"type": "str", "required": True},
      "namespace": {"type": "str", "required": True}}),
)


def register_actions() -> None:
    """注册本插件动作；重复调用（模块多次加载）时静默跳过。"""
    registered = set(ACTION_REGISTRY.names)
    for name, fn, description, parameters in _ACTIONS:
        if name in registered:
            continue
        ACTION_REGISTRY.register(name, fn, permissions=list(_PERMISSIONS),
                                 description=description, parameters=parameters)
        registered.add(name)


register_actions()

ACTION_NAMES = tuple(item[0] for item in _ACTIONS)
