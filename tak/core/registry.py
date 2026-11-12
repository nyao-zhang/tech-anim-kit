# -*- coding: utf-8 -*-
"""工具插件发现（P0.7 扩展机制的核心）。

约定：每个工具是 tools/<id>/ 下的一个目录，含 manifest.json。
外部插件目录通过环境变量 TAK_PLUGIN_PATH（os.pathsep 分隔）追加，
新增工具无需修改框架任何代码。
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

MANIFEST_NAME = "manifest.json"
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DEFAULT_TOOLS_DIR = os.path.join(_REPO_ROOT, "tools")

_REQUIRED_FIELDS = ("id", "name", "dcc", "min_version", "entry")
_VALID_DCC = {"maya", "max", "mobu", "unreal"}
_VALID_UI = {"qt", "web", "both", "none"}


class ManifestError(ValueError):
    """manifest 不合法。"""


@dataclass
class Plugin:
    id: str
    name: str
    dcc: List[str]
    min_version: Dict[str, str]
    entry: str
    ui: str = "qt"
    permissions: List[str] = field(default_factory=list)
    description: str = ""
    path: str = ""
    raw: Dict[str, Any] = field(default_factory=dict)

    def supports(self, dcc: str) -> bool:
        return dcc in self.dcc

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id, "name": self.name, "dcc": self.dcc,
            "min_version": self.min_version, "entry": self.entry,
            "ui": self.ui, "permissions": self.permissions,
            "description": self.description, "path": self.path,
        }


def validate_manifest(data: Dict[str, Any]) -> List[str]:
    """返回错误列表，空列表代表合法。"""
    errors: List[str] = []
    for key in _REQUIRED_FIELDS:
        if key not in data:
            errors.append("缺少必填字段 %s" % key)
    if errors:
        return errors
    if not isinstance(data["id"], str) or not data["id"].replace("_", "").isalnum():
        errors.append("id 必须是字母/数字/下划线")
    if not isinstance(data["dcc"], list) or not data["dcc"]:
        errors.append("dcc 必须是非空列表")
    else:
        unknown = set(data["dcc"]) - _VALID_DCC
        if unknown:
            errors.append("未知 DCC: %s" % sorted(unknown))
    if not isinstance(data["min_version"], dict):
        errors.append("min_version 必须是对象")
    if data.get("ui", "qt") not in _VALID_UI:
        errors.append("ui 必须是 qt/web/both/none 之一")
    return errors


def load_manifest(path: str) -> Plugin:
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    errors = validate_manifest(data)
    if errors:
        raise ManifestError("%s: %s" % (path, "; ".join(errors)))
    return Plugin(
        id=data["id"], name=data["name"], dcc=list(data["dcc"]),
        min_version=dict(data["min_version"]), entry=data["entry"],
        ui=data.get("ui", "qt"), permissions=list(data.get("permissions", [])),
        description=data.get("description", ""),
        path=os.path.dirname(os.path.abspath(path)), raw=data,
    )


def plugin_dirs(tools_dir: Optional[str] = None,
                extra_dirs: Optional[List[str]] = None) -> List[str]:
    """收集候选插件目录；以下划线开头的目录（如 _template）跳过。"""
    roots = [tools_dir or DEFAULT_TOOLS_DIR]
    env = os.environ.get("TAK_PLUGIN_PATH", "")
    if env:
        roots.extend(p for p in env.split(os.pathsep) if p)
    if extra_dirs:
        roots.extend(extra_dirs)
    found: List[str] = []
    for root in roots:
        if not os.path.isdir(root):
            continue
        for name in sorted(os.listdir(root)):
            if name.startswith("_") or name.startswith("."):
                continue
            sub = os.path.join(root, name)
            if os.path.isfile(os.path.join(sub, MANIFEST_NAME)):
                found.append(sub)
    return found


def discover(tools_dir: Optional[str] = None,
             extra_dirs: Optional[List[str]] = None,
             dcc: Optional[str] = None) -> List[Plugin]:
    """发现全部合法插件；单个坏 manifest 不阻断其余插件。"""
    plugins: List[Plugin] = []
    for sub in plugin_dirs(tools_dir, extra_dirs):
        try:
            plugin = load_manifest(os.path.join(sub, MANIFEST_NAME))
        except (ManifestError, OSError, ValueError):
            continue
        if dcc and not plugin.supports(dcc):
            continue
        plugins.append(plugin)
    return plugins


def load_plugin_module(plugin: Plugin, submodule: str = "core"):
    """按文件路径加载插件模块，模块名带插件 id 前缀，避免多个插件同名冲突。"""
    filename = os.path.join(plugin.path, "%s.py" % submodule)
    if not os.path.isfile(filename):
        raise FileNotFoundError("插件 %s 缺少 %s.py" % (plugin.id, submodule))
    module_name = "tak_plugin_%s_%s" % (plugin.id, submodule)
    spec = importlib.util.spec_from_file_location(module_name, filename)
    if spec is None or spec.loader is None:
        raise ImportError("无法加载 %s" % filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module
