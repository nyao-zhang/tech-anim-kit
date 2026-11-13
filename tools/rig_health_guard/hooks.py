# -*- coding: utf-8 -*-
"""保存 / 导出前的体检钩子（Maya scriptJob）。

边界
----
* 钩子**只做检查**：不修改变换、不删除节点、不自动修复；
* 宿主不可用（maya.cmds 导入失败）时 ``install_hooks()`` 安全返回 False，
  原因写入 ``hook_status()``，绝不抛异常；
* ``on_before_save`` / ``on_before_export`` 是纯函数，可脱离 Maya 单测；
  内部通过 ``tak.core.runner.run_tool`` 复用统一契约，不直接 import 本插件
  的其他模块（插件目录不在 sys.path）。

``on_before_save(ctx)`` 与 ``on_before_export(ctx)`` 返回::

    {"passed": bool, "report": dict, "block": bool}

``block`` 仅在存在 ERROR/BLOCKER 时为 True；工具自身异常时保持
``passed=True, block=False``，避免因为体检工具故障阻断用户保存。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

TOOL_ID = "rig_health_guard"
HOOK_VERSION = "0.1.0"

_SAVE_EVENTS = ("BeforeSave", "SceneSaved")
_EXPORT_EVENTS = ("SceneExported", "BeforeExport")

_JOBS: Dict[str, int] = {}
_STATE: Dict[str, Any] = {
    "installed": False,
    "jobs": {},
    "events": [],
    "reason": "尚未安装",
    "export_hooked": False,
}


# --------------------------------------------------------------------------
# 纯函数：保存 / 导出前检查
# --------------------------------------------------------------------------
def on_before_save(ctx: Any) -> Dict[str, Any]:
    """保存前体检：返回 passed / report / block，不修改场景。"""
    return _envelope(ctx, "before_save")


def on_before_export(ctx: Any) -> Dict[str, Any]:
    """导出前体检：返回 passed / report / block，不修改场景。"""
    return _envelope(ctx, "before_export")


def _envelope(ctx: Any, hook: str) -> Dict[str, Any]:
    try:
        result = _run(ctx)
    except Exception as exc:  # 体检工具故障不得阻断保存/导出
        return {
            "passed": True,
            "report": {
                "hook": hook,
                "tool_id": TOOL_ID,
                "skipped": True,
                "error": "%s: %s" % (type(exc).__name__, exc),
            },
            "block": False,
        }
    payload = result.to_dict()
    payload["hook"] = hook
    return {"passed": bool(result.passed), "report": payload,
            "block": not bool(result.passed)}


def _run(ctx: Any) -> Any:
    """通过统一 runner 执行体检；ctx 可以是 ToolContext、dict 或任意对象。"""
    from tak.core.runner import run_tool

    snapshot = _field(ctx, "snapshot")
    data = snapshot if isinstance(snapshot, dict) and snapshot else None
    kwargs: Dict[str, Any] = {"dcc": _field(ctx, "dcc") or "maya"}
    adapter = _field(ctx, "adapter")
    if adapter is not None:
        kwargs["adapter"] = adapter
    spec = _field(ctx, "spec")
    if isinstance(spec, dict) and spec:
        kwargs["spec"] = spec
    options = _field(ctx, "options")
    if isinstance(options, dict) and options:
        kwargs["options"] = options
    return run_tool(TOOL_ID, data, **kwargs)


def _field(ctx: Any, name: str) -> Any:
    if isinstance(ctx, dict):
        return ctx.get(name)
    return getattr(ctx, name, None)


# --------------------------------------------------------------------------
# Maya scriptJob 安装 / 卸载
# --------------------------------------------------------------------------
def install_hooks() -> bool:
    """安装保存 / 导出前钩子；无 Maya 时返回 False 并记录原因。"""
    global _STATE
    cmds = _maya_cmds()
    if cmds is None:
        _STATE = _state(False, {}, [], "Maya 不可用（import maya.cmds 失败），钩子未安装", False)
        return False
    if _JOBS:
        _STATE = _state(True, dict(_JOBS), _STATE.get("events", []),
                        "已安装，重复调用忽略", _STATE.get("export_hooked", False))
        return True

    jobs: Dict[str, int] = {}
    events: List[str] = []
    errors: List[str] = []

    save = _install_event(cmds, _SAVE_EVENTS, _on_before_save_event, errors)
    if save is not None:
        jobs["save"] = save[1]
        events.append(save[0])
    export = _install_event(cmds, _EXPORT_EVENTS, _on_before_export_event, errors)
    if export is not None:
        jobs["export"] = export[1]
        events.append(export[0])

    if "save" not in jobs:
        # 保存钩子是主路径；只注册到导出事件时回滚，避免留下无人管理的 scriptJob。
        for _name, job in jobs.items():
            try:
                cmds.scriptJob(kill=job, force=True)
            except Exception:
                pass
        _STATE = _state(False, {}, events,
                        "Maya scriptJob 注册失败: %s" % ("; ".join(errors) or "未知原因"),
                        False)
        return False

    _JOBS.clear()
    _JOBS.update(jobs)
    reason = "已安装"
    if "export" not in jobs:
        reason = "保存钩子已安装；宿主未提供导出事件，导出检查需由导出脚本显式调用 on_before_export"
    _STATE = _state(True, dict(_JOBS), events, reason, "export" in jobs)
    return True


def uninstall_hooks() -> bool:
    """卸载已安装的 scriptJob；无 Maya 时清理本地状态并返回 False。"""
    global _STATE
    cmds = _maya_cmds()
    had_jobs = bool(_JOBS)
    if cmds is None:
        _JOBS.clear()
        _STATE = _state(False, {}, [], "Maya 不可用，仅清理本地安装状态", False)
        return not had_jobs
    errors = []
    for name, job in list(_JOBS.items()):
        try:
            cmds.scriptJob(kill=job, force=True)
        except Exception as exc:
            errors.append("%s: %s" % (name, exc))
    _JOBS.clear()
    _STATE = _state(False, {}, [], "已卸载" if not errors else
                    "部分 scriptJob 取消失败: %s" % "; ".join(errors), False)
    return not errors


def hook_status() -> Dict[str, Any]:
    """查询钩子安装状态（纯查询，不触碰 Maya）。"""
    status = dict(_STATE)
    status["installed"] = bool(_JOBS)
    status["jobs"] = dict(_JOBS)
    status["events"] = list(_STATE.get("events", []))
    status["tool_id"] = TOOL_ID
    status["version"] = HOOK_VERSION
    status["only_checks"] = True
    return status


# --------------------------------------------------------------------------
# 内部实现
# --------------------------------------------------------------------------
def _install_event(cmds: Any, candidates: Tuple[str, ...], callback: Any,
                   errors: List[str]) -> Optional[Tuple[str, int]]:
    for event in candidates:
        for kwargs in ({"protected": True}, {}):
            try:
                job = cmds.scriptJob(event=[event, callback], **kwargs)
                return event, int(job)
            except Exception as exc:
                errors.append("%s%s: %s" % (event, " protected" if kwargs else "",
                                            exc))
    return None


def _maya_cmds() -> Any:
    """延迟导入 maya.cmds；宿主不可用返回 None。"""
    try:
        import maya.cmds as cmds  # noqa: F401  # 仅 Maya 内可用
        return cmds
    except Exception:
        return None


def _on_before_save_event() -> None:
    """scriptJob 回调：只打印检查结果，绝不自动修复。"""
    _report_event("保存前", "before_save")


def _on_before_export_event() -> None:
    """scriptJob 回调：只打印检查结果，绝不自动修复。"""
    _report_event("导出前", "before_export")


def _report_event(label: str, hook: str) -> None:
    try:
        from tak.core.runner import run_tool

        result = run_tool(TOOL_ID, dcc="maya",
                          options={"include_builtin": True})
        counts = result.report.counts()
        if result.passed:
            print("[TAK][RigHealthGuard] %s体检通过（warning %d / info %d）"
                  % (label, counts.get("warning", 0), counts.get("info", 0)))
        else:
            print("[TAK][RigHealthGuard] %s体检发现阻断问题：error %d / blocker %d，"
                  "请先运行体检面板确认（钩子不会自动修复）"
                  % (label, counts.get("error", 0), counts.get("blocker", 0)))
    except Exception as exc:
        print("[TAK][RigHealthGuard] %s体检跳过: %s: %s"
              % (label, type(exc).__name__, exc))


def _state(installed: bool, jobs: Dict[str, int], events: List[str],
           reason: str, export_hooked: bool) -> Dict[str, Any]:
    return {
        "installed": installed,
        "jobs": dict(jobs),
        "events": list(events),
        "reason": reason,
        "export_hooked": bool(export_hooked),
    }
