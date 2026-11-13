# -*- coding: utf-8 -*-
"""TAK 的 UE 编辑器菜单入口（沿用 ``acc_menu.py`` 的 ToolMenus 写法）。

关键约束
--------
* **import 时零副作用**：模块顶层不 import unreal，也不注册任何菜单；
  只有显式调用 :func:`register_menus` 才会碰 ``ToolMenus``。
* 任何一步失败（无 unreal、5.9 改名、ToolMenus 缺失、menu 不存在）都返回
  ``False`` 并打印一行降级日志，绝不抛异常打断编辑器启动。
* ``ToolMenuEntryScript`` 子类必须动态创建（``unreal.uclass()(type(...))``），
  等价于 ``acc_menu.py`` 在类体里写 ``@unreal.uclass()`` 的写法；因为类体装饰
  需要 import 时就存在 unreal，与「纯 Python 可 import」冲突。
* 菜单项清单由 :func:`menu_items` 暴露成纯数据，P3.2 的 Web 面板可以直接复用，
  不需要再抄一份文案。
"""
from __future__ import annotations

import os
import webbrowser
from typing import Any, Callable, Dict, List, Optional

from tak.compat.unreal import dialog, safe_attr
from tak.adapters.unreal.adapter import create_adapter, is_available

MENU_OWNER = "TAKOwner"
MENU_NAME = "TAK"
MENU_LABEL = "TAK"
MENU_SECTION = "TAK"
MENU_MAIN = "LevelEditor.MainMenu"
MENU_ASSET_CONTEXT = "ContentBrowser.AssetContextMenu"

MENU_ITEMS: List[Dict[str, str]] = [
    {"id": "TAK_RunHealthGuard", "label": "Run Health Guard",
     "action": "run_health_guard", "tooltip": "跑全部内置规则并输出体检报告"},
    {"id": "TAK_OpenWebPanel", "label": "Open Web Panel",
     "action": "open_web_panel", "tooltip": "在浏览器打开 TAK 面板 / 体检报告"},
    {"id": "TAK_ValidateImport", "label": "Validate Import",
     "action": "validate_import", "tooltip": "把 Export Bridge sidecar 与 UE Skeleton 做 diff"},
]

# 动态生成的 ToolMenuEntryScript 子类缓存：重复注册不再生成新 UClass。
_ENTRY_CLASSES: Dict[str, Any] = {}


# --------------------------------------------------------------------------- #
# 宿主访问（全部降级）
# --------------------------------------------------------------------------- #
def _unreal() -> Optional[Any]:
    try:
        import importlib
        return importlib.import_module("unreal")
    except Exception:
        return None


def _log(message: str, level: str = "info") -> None:
    module = _unreal()
    if module is not None:
        name = {"error": "log_error", "critical": "log_error",
                "warning": "log_warning", "warn": "log_warning"}.get(
                    str(level or "info").lower(), "log")
        func = safe_attr(module, name) or safe_attr(module, "log")
        if callable(func):
            try:
                func("[TAK] %s" % message)
                return
            except Exception:
                pass
    print("[TAK][%s] %s" % (level, message))


def _call(obj: Any, name: str, *args: Any) -> Any:
    func = safe_attr(obj, name)
    if not callable(func):
        return None
    try:
        return func(*args)
    except Exception:
        return None


def menus_available() -> bool:
    """``ToolMenus`` 与 ``ToolMenuEntryScript`` 是否都存在（5.8/5.9 均应存在）。"""
    module = _unreal()
    if module is None:
        return False
    return (safe_attr(module, "ToolMenus") is not None
            and safe_attr(module, "ToolMenuEntryScript") is not None)


def menu_items() -> List[Dict[str, str]]:
    """菜单项清单（纯数据，UI 与文档共用）。"""
    return [dict(item) for item in MENU_ITEMS]


# --------------------------------------------------------------------------- #
# 菜单动作
# --------------------------------------------------------------------------- #
def run_health_guard() -> Dict[str, Any]:
    """跑一遍内置规则，把 HTML 报告写到 Saved/TAK 下并返回报告字典。"""
    from tak.core.runner import evaluate

    adapter = create_adapter()
    report = evaluate(dcc="unreal", adapter=adapter, title="TAK UE Health Guard")
    data = report.to_dict()
    target = ""
    try:
        target = _write_report("health_guard.html", report.to_html())
    except Exception as exc:
        _log("体检报告写入失败: %s: %s" % (type(exc).__name__, exc), "warning")
    data["out"] = target
    _log("体检%s：%d 项发现%s" % (
        "通过" if report.passed else "不通过", len(report.findings),
        "，报告 %s" % target if target else ""),
        "info" if report.passed else "warning")
    return data


def open_web_panel(url: str = "") -> bool:
    """打开 TAK 面板。

    P3.2 的完整 Web 面板尚未合入，因此未显式给 URL 时退化为「生成并打开
    体检报告 HTML」——同一条 ``launch_url`` 通道，行为可验证。
    """
    target = str(url or os.environ.get("TAK_WEB_PANEL_URL", "") or "")
    if not target:
        info = run_health_guard()
        target = str(info.get("out") or "")
    if not target:
        _log("没有可打开的 Web 面板 / 报告，已跳过", "warning")
        return False
    module = _unreal()
    system_library = safe_attr(module, "SystemLibrary") if module is not None else None
    if system_library is not None:
        launched = _call(system_library, "launch_url", target)
        if launched is not None:
            return True
    try:
        return bool(webbrowser.open(target))
    except Exception:
        return False


def validate_import(path: str = "", spec: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """校验一次导入：sidecar 快照 vs UE Skeleton，结果用对话框回显。"""
    from tak.core.spec import load_spec

    adapter = create_adapter()
    effective_spec = spec if isinstance(spec, dict) else load_spec()
    targets = [str(path)] if path else _sidecar_candidates()
    if not targets:
        message = ("未指定 sidecar 快照：请传入路径，或在内容浏览器选中已导出的资产后重试"
                   "（约定路径 <资产>.snapshot.json）。")
        dialog(_unreal(), "TAK Validate Import", message)
        _log(message, "warning")
        return {"passed": False, "diffs": [], "reason": message, "results": []}

    results = []
    for target in targets:
        results.append({"path": target,
                        "result": adapter.import_validate(target, effective_spec)})
    passed = all(item["result"].get("passed") for item in results)
    lines = ["%s: %s" % (item["path"], item["result"].get("reason")) for item in results]
    message = ("校验通过。\n" if passed else "校验未通过。\n") + "\n".join(lines)
    dialog(_unreal(), "TAK Validate Import", message)
    _log("导入校验%s：%s" % ("通过" if passed else "未通过", lines[0] if lines else ""),
         "info" if passed else "warning")
    return {"passed": passed, "diffs": list(results[0]["result"].get("diffs") or []),
            "reason": message, "results": results}


def _sidecar_candidates() -> List[str]:
    """把选中的资产转换成磁盘路径，交给适配器再补 ``.snapshot.json``。"""
    module = _unreal()
    if module is None:
        return []
    assets = _call(safe_attr(module, "EditorUtilityLibrary"), "get_selected_assets") or []
    paths_api = safe_attr(module, "Paths")
    candidates: List[str] = []
    for asset in list(assets)[:8]:
        package = safe_attr(asset, "package_name") or _call(asset, "get_path_name")
        if not package:
            continue
        disk = _call(paths_api, "convert_relative_path_to_full", str(package))
        if disk:
            candidates.append(str(disk))
    return candidates


# --------------------------------------------------------------------------- #
# 报告落盘
# --------------------------------------------------------------------------- #
def _report_dir() -> str:
    module = _unreal()
    saved = _call(safe_attr(module, "Paths"), "project_saved_dir") if module is not None else None
    base = str(saved or os.environ.get("TAK_REPORT_DIR") or os.getcwd())
    target = os.path.join(base, "TAK")
    try:
        os.makedirs(target, exist_ok=True)
    except OSError:
        return base
    return target


def _write_report(name: str, text: str) -> str:
    target = os.path.join(_report_dir(), name)
    with open(target, "w", encoding="utf-8") as fh:
        fh.write(text)
    return target


# --------------------------------------------------------------------------- #
# 注册 / 反注册
# --------------------------------------------------------------------------- #
def _entry_class(action_id: str, action: Callable[[], Any]) -> Optional[Any]:
    """动态生成 ToolMenuEntryScript 子类（等价于 acc_menu.py 的类体装饰器）。"""
    cached = _ENTRY_CLASSES.get(action_id)
    if cached is not None:
        return cached
    module = _unreal()
    if module is None:
        return None
    base = safe_attr(module, "ToolMenuEntryScript")
    uclass = safe_attr(module, "uclass")
    if base is None or not callable(uclass):
        return None

    def execute(self: Any, context: Any = None) -> None:
        try:
            action()
        except Exception as exc:  # 菜单回调不能把异常抛回引擎
            _log("菜单动作 %s 失败: %s: %s" % (action_id, type(exc).__name__, exc), "error")

    function = execute
    ufunction = safe_attr(module, "ufunction")
    if callable(ufunction):
        try:
            function = ufunction(override=True)(execute)
        except Exception:
            function = execute
    namespace = {"execute": function, "__doc__": "TAK 菜单项 %s" % action_id}
    try:
        cls = type("TakMenuEntry_%s" % action_id, (base,), namespace)
    except Exception:
        return None
    try:
        cls = uclass()(cls)
    except Exception:
        pass
    _ENTRY_CLASSES[action_id] = cls
    return cls


def _action_table() -> Dict[str, Callable[[], Any]]:
    return {"run_health_guard": run_health_guard,
            "open_web_panel": open_web_panel,
            "validate_import": validate_import}


def _create_entry(action_id: str, action: Callable[[], Any]) -> Optional[Any]:
    cls = _entry_class(action_id, action)
    if cls is None:
        return None
    try:
        return cls()
    except Exception:
        return None


def unregister_menus(menu_owner: str = MENU_OWNER) -> bool:
    """移除本插件注册过的菜单所有权；无 unreal 时安全返回 False。"""
    module = _unreal()
    menus = _call(safe_attr(module, "ToolMenus"), "get") if module is not None else None
    if menus is None:
        return False
    return _call(menus, "unregister_owner_by_name", menu_owner) is not None


def register_menus(menu_owner: str = MENU_OWNER) -> bool:
    """在 LevelEditor 主菜单下注册 TAK 子菜单，并在资产右键加 Validate Import。

    返回是否至少成功挂上一项；任何缺失（无 unreal / 无 ToolMenus / menu 找不到）
    都返回 False，不抛异常。
    """
    if not is_available() or not menus_available():
        _log("未检测到 unreal / ToolMenus，TAK 菜单未注册（commandlet 与 Web 入口仍可用）",
             "warning")
        return False
    module = _unreal()
    menus = _call(safe_attr(module, "ToolMenus"), "get")
    if menus is None:
        _log("ToolMenus.get() 不可用，跳过菜单注册", "warning")
        return False

    # 先清理旧所有权，避免热重载或语言切换后菜单堆叠（与 acc_menu.py 一致）。
    _call(menus, "unregister_owner_by_name", menu_owner)

    actions = _action_table()
    attached = 0
    main = _call(menus, "find_menu", MENU_MAIN)
    if main is not None:
        sub = _call(main, "add_sub_menu", menu_owner, "Help", MENU_NAME, MENU_LABEL)
        sub_name = str(safe_attr(sub, "menu_name") or MENU_NAME)
        sub_menu = _call(menus, "find_menu", sub_name) or sub
        if sub_menu is not None:
            _call(sub_menu, "add_section", MENU_SECTION, MENU_SECTION)
            for item in MENU_ITEMS:
                entry = _create_entry(item["id"], actions.get(item["action"], (lambda: None)))
                if entry is None:
                    continue
                _call(entry, "init_entry", menu_owner, sub_name, MENU_SECTION,
                      item["id"], item["label"])
                if _call(sub_menu, "add_menu_entry_object", entry) is not None:
                    attached += 1
                elif hasattr(sub_menu, "add_menu_entry_object"):
                    # 某些版本成功时返回 None：以方法存在且未抛异常为准
                    attached += 1

    asset_menu = _call(menus, "find_menu", MENU_ASSET_CONTEXT)
    if asset_menu is not None:
        _call(asset_menu, "add_section", MENU_SECTION, MENU_SECTION)
        entry = _create_entry("TAK_ValidateImportContext",
                             actions.get("validate_import", (lambda: None)))
        if entry is not None:
            _call(entry, "init_entry", menu_owner,
                  str(safe_attr(asset_menu, "menu_name") or MENU_ASSET_CONTEXT),
                  MENU_SECTION, "TAK_ValidateImportContext", "TAK: Validate Import")
            _call(asset_menu, "add_menu_entry_object", entry)

    _call(menus, "refresh_all_widgets")
    if attached:
        _log("TAK 菜单已注册（%d 项）" % attached)
    else:
        _log("未找到可挂载的编辑器菜单，TAK 菜单未注册", "warning")
    return attached > 0
