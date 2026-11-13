# -*- coding: utf-8 -*-
"""用户配置层（``~/.tak/config.json``）。

工具分类、工具归类、隐藏项、检查项勾选、主题偏好都落在这一个文件里。
设计约束：

* 纯 Python 3.9+，零第三方依赖，零 DCC import —— 与 ``tak.core`` 其余部分一致；
* **读永不失败**：文件缺失 / JSON 损坏 / 字段类型不对，一律回退到内置默认值，
  并把问题写进 ``load()["_warnings"]``，而不是抛异常。面板是给人用的，
  一个手改坏的配置文件不该让整个工具集打不开；
* **写是原子的**：先写 ``config.json.tmp`` 再 ``os.replace``，避免 DCC 崩溃
  留下半截文件；
* 默认值只在这里定义一份，插件 manifest 的 ``category`` 只是**初值**，
  用户在设置面板改过的归类以本文件为准（``tool_categories`` 覆盖 manifest）。

路径可用 ``TAK_HOME`` 环境变量覆盖，测试与便携安装都靠它。
"""
from __future__ import annotations

import copy
import json
import os
from typing import Any, Dict, Iterable, List, Optional, Tuple

CONFIG_SCHEMA = 1
CONFIG_NAME = "config.json"
ENV_HOME = "TAK_HOME"

#: 内置分类（id, 显示名, 说明）。顺序即面板左栏顺序，「全部」是视图不是分类。
DEFAULT_CATEGORIES: Tuple[Tuple[str, str, str], ...] = (
    ("general", "通用", "跨流程的检查、设置与杂项"),
    ("rigging", "绑定", "骨架、蒙皮、控制器、形变"),
    ("animation", "动画", "动作、重定向、动画缓存"),
    ("pipeline", "管线", "资产导出导入、DCC 之间传递"),
    ("ai", "AI", "模型训练、视觉判读、对话式控制"),
)

ALL_VIEW = "all"
ALL_VIEW_NAME = "全部"


def config_home() -> str:
    """配置目录：``TAK_HOME`` 优先，否则用户主目录下的 ``.tak``。"""
    override = os.environ.get(ENV_HOME, "").strip()
    if override:
        return os.path.abspath(os.path.expanduser(override))
    return os.path.join(os.path.expanduser("~"), ".tak")


def config_path() -> str:
    return os.path.join(config_home(), CONFIG_NAME)


def default_config() -> Dict[str, Any]:
    """返回一份全新的默认配置（深拷贝，调用方可随意改）。"""
    return {
        "schema": CONFIG_SCHEMA,
        "categories": [{"id": cid, "name": name, "description": desc}
                       for cid, name, desc in DEFAULT_CATEGORIES],
        # 用户对 manifest 默认归类的覆盖：{"tool_id": ["rigging", "animation"]}
        "tool_categories": {},
        # 用户显式隐藏的工具 id（不删插件，只是不在面板列出）
        "hidden_tools": [],
        # 分类 -> 工具顺序偏好（面板拖拽排序用，未列出的按 manifest 名字排）
        "tool_order": {},
        "check_state": {},
        "export_profiles": {},
        "bridge": {"port": 0, "token": "", "autostart": False},
        "theme": "dark",
        "ai": {"provider": "", "model": "", "vision_model": ""},
    }


# --------------------------------------------------------------------------- #
# 读
# --------------------------------------------------------------------------- #
def _coerce_categories(value: Any) -> Optional[List[Dict[str, str]]]:
    """把外部数据整理成合法分类列表；无效返回 None 表示「用默认」。"""
    if not isinstance(value, list):
        return None
    out: List[Dict[str, str]] = []
    seen = set()
    for item in value:
        if not isinstance(item, dict):
            continue
        cid = str(item.get("id") or "").strip()
        name = str(item.get("name") or "").strip()
        if not cid or not name or cid in seen:
            continue
        # 「全部」是视图，不允许被注册成一个真实分类，否则会出现同名空分类。
        if cid == ALL_VIEW:
            continue
        seen.add(cid)
        out.append({"id": cid, "name": name,
                    "description": str(item.get("description") or "")})
    return out or None


def _coerce_tool_map(value: Any) -> Dict[str, List[str]]:
    if not isinstance(value, dict):
        return {}
    out: Dict[str, List[str]] = {}
    for tid, cats in value.items():
        if isinstance(cats, str):
            cats = [cats]
        if not isinstance(cats, (list, tuple)):
            continue
        cleaned = [str(c) for c in cats if isinstance(c, str) and c.strip()]
        if cleaned:
            out[str(tid)] = cleaned
    return out


def _normalize(raw: Dict[str, Any]) -> Tuple[Dict[str, Any], List[str]]:
    """把读到的原始 dict 归一化，返回 (配置, 警告列表)。"""
    data = default_config()
    warnings: List[str] = []
    if not isinstance(raw, dict):
        return data, ["配置文件根节点不是对象，已回退默认配置"]

    categories = _coerce_categories(raw.get("categories"))
    if categories is None and "categories" in raw:
        warnings.append("categories 字段无效，已回退内置分类")
    if categories:
        data["categories"] = categories

    data["tool_categories"] = _coerce_tool_map(raw.get("tool_categories"))

    hidden = raw.get("hidden_tools")
    if isinstance(hidden, list):
        data["hidden_tools"] = [str(h) for h in hidden if isinstance(h, str)]
    elif hidden is not None:
        warnings.append("hidden_tools 不是列表，已忽略")

    order = raw.get("tool_order")
    if isinstance(order, dict):
        data["tool_order"] = {
            str(cat): [str(t) for t in ids if isinstance(t, str)]
            for cat, ids in order.items() if isinstance(ids, list)
        }

    checks = raw.get("check_state")
    if isinstance(checks, dict):
        data["check_state"] = {
            str(tid): [str(c) for c in ids if isinstance(c, str)]
            for tid, ids in checks.items() if isinstance(ids, list)
        }

    profiles = raw.get("export_profiles")
    if isinstance(profiles, dict):
        data["export_profiles"] = {str(k): v for k, v in profiles.items()
                                   if isinstance(v, dict)}

    bridge = raw.get("bridge")
    if isinstance(bridge, dict):
        try:
            data["bridge"]["port"] = int(bridge.get("port") or 0)
        except (TypeError, ValueError):
            warnings.append("bridge.port 不是整数，已回退 0")
        data["bridge"]["token"] = str(bridge.get("token") or "")
        data["bridge"]["autostart"] = bool(bridge.get("autostart", False))

    theme = str(raw.get("theme") or "dark").lower()
    data["theme"] = theme if theme in ("dark", "light") else "dark"

    ai = raw.get("ai")
    if isinstance(ai, dict):
        for key in ("provider", "model", "vision_model"):
            data["ai"][key] = str(ai.get(key) or "")

    schema = raw.get("schema")
    if isinstance(schema, int) and schema > CONFIG_SCHEMA:
        warnings.append("配置 schema %d 高于本版本支持的 %d，未知字段已忽略"
                        % (schema, CONFIG_SCHEMA))
    return data, warnings


def load() -> Dict[str, Any]:
    """读配置；任何失败都返回默认值并附 ``_warnings``，绝不抛异常。"""
    path = config_path()
    if not os.path.isfile(path):
        data = default_config()
        data["_warnings"] = []
        data["_path"] = path
        return data
    try:
        with open(path, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
    except (OSError, ValueError) as exc:
        data = default_config()
        data["_warnings"] = ["读取 %s 失败（%s: %s），已回退默认配置"
                             % (path, type(exc).__name__, exc)]
        data["_path"] = path
        return data
    data, warnings = _normalize(raw)
    data["_warnings"] = warnings
    data["_path"] = path
    return data


def _public(data: Dict[str, Any]) -> Dict[str, Any]:
    """剥掉 ``_warnings`` / ``_path`` 这两个仅供运行时读的键。"""
    return {k: copy.deepcopy(v) for k, v in data.items()
            if not k.startswith("_")}


# --------------------------------------------------------------------------- #
# 写
# --------------------------------------------------------------------------- #
def save(data: Dict[str, Any]) -> Dict[str, Any]:
    """原子写回配置；返回 ``{"ok", "path", "reason"}``，不抛异常。"""
    path = config_path()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        payload = _public(data)
        payload["schema"] = CONFIG_SCHEMA
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2, sort_keys=True)
            fh.write("\n")
        os.replace(tmp, path)
        return {"ok": True, "path": path, "reason": ""}
    except OSError as exc:
        return {"ok": False, "path": path,
                "reason": "%s: %s" % (type(exc).__name__, exc)}


def update(**changes: Any) -> Dict[str, Any]:
    """读-改-写一步完成；只覆盖传入的顶层键。"""
    data = load()
    for key, value in changes.items():
        if not key.startswith("_"):
            data[key] = value
    return save(data)


# --------------------------------------------------------------------------- #
# 分类
# --------------------------------------------------------------------------- #
def categories(data: Optional[Dict[str, Any]] = None) -> List[Dict[str, str]]:
    """当前分类列表（含「全部」视图项，排在第一位）。"""
    cfg = data if data is not None else load()
    items = [{"id": ALL_VIEW, "name": ALL_VIEW_NAME,
              "description": "不分类，显示全部工具"}]
    items.extend(dict(c) for c in cfg.get("categories") or [])
    return items


def category_names(data: Optional[Dict[str, Any]] = None) -> Dict[str, str]:
    """``{category_id: 显示名}``，用于面板渲染。"""
    return {c["id"]: c["name"] for c in categories(data)}


def resolve_categories(manifest_categories: Iterable[str],
                       tool_id: str,
                       cfg: Optional[Dict[str, Any]] = None) -> List[str]:
    """工具最终归类：用户覆盖 > manifest 声明 > general。

    未在配置里登记的 category id 会原样保留 —— 插件可以自带分类，
    用户删掉分类后残留的归类不至于让工具从「全部」里消失。
    """
    data = cfg if cfg is not None else load()
    override = (data.get("tool_categories") or {}).get(tool_id)
    if override:
        return list(override)
    declared = [str(c) for c in manifest_categories if c]
    return declared or ["general"]


def set_tool_categories(tool_id: str, cats: Iterable[str]) -> Dict[str, Any]:
    data = load()
    mapping = dict(data.get("tool_categories") or {})
    cleaned = [str(c) for c in cats if c]
    if cleaned:
        mapping[str(tool_id)] = cleaned
    else:
        mapping.pop(str(tool_id), None)
    data["tool_categories"] = mapping
    return save(data)


def add_category(cat_id: str, name: str, description: str = "") -> Dict[str, Any]:
    """新增自定义分类；id 非法或重复时返回 ok=False。"""
    cat_id = str(cat_id or "").strip()
    name = str(name or "").strip()
    if not cat_id.replace("_", "").isalnum():
        return {"ok": False, "reason": "分类 id 只能用字母、数字、下划线"}
    if not name:
        return {"ok": False, "reason": "分类名不能为空"}
    if cat_id == ALL_VIEW:
        return {"ok": False, "reason": "「%s」是内置视图，不能用作分类 id" % ALL_VIEW}
    data = load()
    existing = list(data.get("categories") or [])
    if any(c["id"] == cat_id for c in existing):
        return {"ok": False, "reason": "分类 %s 已存在" % cat_id}
    existing.append({"id": cat_id, "name": name, "description": str(description or "")})
    data["categories"] = existing
    return save(data)


def remove_category(cat_id: str) -> Dict[str, Any]:
    """删分类；受影响的工具归类一并清理（回落到 general，不会丢工具）。"""
    cat_id = str(cat_id or "")
    data = load()
    remaining = [c for c in (data.get("categories") or []) if c["id"] != cat_id]
    data["categories"] = remaining
    mapping = {}
    for tid, cats in (data.get("tool_categories") or {}).items():
        kept = [c for c in cats if c != cat_id]
        if kept:
            mapping[tid] = kept
    data["tool_categories"] = mapping
    return save(data)


def rename_category(cat_id: str, name: str) -> Dict[str, Any]:
    name = str(name or "").strip()
    if not name:
        return {"ok": False, "reason": "分类名不能为空"}
    data = load()
    for item in data.get("categories") or []:
        if item["id"] == cat_id:
            item["name"] = name
            return save(data)
    return {"ok": False, "reason": "分类 %s 不存在" % cat_id}


# --------------------------------------------------------------------------- #
# 隐藏工具
# --------------------------------------------------------------------------- #
def hidden_tools(cfg: Optional[Dict[str, Any]] = None) -> List[str]:
    data = cfg if cfg is not None else load()
    return list(data.get("hidden_tools") or [])


def set_hidden(tool_ids: Iterable[str]) -> Dict[str, Any]:
    data = load()
    data["hidden_tools"] = sorted({str(t) for t in tool_ids if t})
    return save(data)


# --------------------------------------------------------------------------- #
# 检查项勾选（qa_inspector 等按项开关的工具共用）
# --------------------------------------------------------------------------- #
def check_state(tool_id: str, cfg: Optional[Dict[str, Any]] = None) -> Optional[List[str]]:
    """返回用户勾选的检查项；``None`` 表示用户从没存过（用默认全开）。"""
    data = cfg if cfg is not None else load()
    value = (data.get("check_state") or {}).get(str(tool_id))
    return list(value) if isinstance(value, list) else None


def set_check_state(tool_id: str, enabled: Iterable[str]) -> Dict[str, Any]:
    data = load()
    state = dict(data.get("check_state") or {})
    state[str(tool_id)] = [str(c) for c in enabled if c]
    data["check_state"] = state
    return save(data)


# --------------------------------------------------------------------------- #
# 导出 profile
# --------------------------------------------------------------------------- #
def export_profile(name: str, cfg: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    data = cfg if cfg is not None else load()
    value = (data.get("export_profiles") or {}).get(str(name))
    return copy.deepcopy(value) if isinstance(value, dict) else None


def export_profiles(cfg: Optional[Dict[str, Any]] = None) -> List[str]:
    data = cfg if cfg is not None else load()
    return sorted((data.get("export_profiles") or {}).keys())


def save_export_profile(name: str, profile: Dict[str, Any]) -> Dict[str, Any]:
    name = str(name or "").strip()
    if not name:
        return {"ok": False, "reason": "profile 名不能为空"}
    if not isinstance(profile, dict):
        return {"ok": False, "reason": "profile 必须是对象"}
    data = load()
    profiles = dict(data.get("export_profiles") or {})
    profiles[name] = copy.deepcopy(profile)
    data["export_profiles"] = profiles
    return save(data)


def delete_export_profile(name: str) -> Dict[str, Any]:
    data = load()
    profiles = dict(data.get("export_profiles") or {})
    profiles.pop(str(name), None)
    data["export_profiles"] = profiles
    return save(data)


# --------------------------------------------------------------------------- #
# 重置
# --------------------------------------------------------------------------- #
def reset() -> Dict[str, Any]:
    return save(default_config())


def open_in_explorer() -> Dict[str, Any]:
    """在系统文件管理器里定位配置文件（供设置面板的「打开配置目录」按钮）。"""
    path = config_path()
    folder = os.path.dirname(path)
    try:
        os.makedirs(folder, exist_ok=True)
        if os.name == "nt":
            os.startfile(folder)  # type: ignore[attr-defined]  # noqa: S606
            return {"ok": True, "path": folder}
        import subprocess
        opener = "open" if os.sys.platform == "darwin" else "xdg-open"
        subprocess.Popen([opener, folder])  # noqa: S603,S607
        return {"ok": True, "path": folder}
    except Exception as exc:
        return {"ok": False, "path": folder,
                "reason": "%s: %s" % (type(exc).__name__, exc)}
