# -*- coding: utf-8 -*-
"""把 ``tak-scene`` 配置写进 MCP 客户端：诊断、备份、原子写。

为什么单独一层：同一份逻辑既被命令行脚本 ``scripts/install_mcp_config.py``
用，也被 Maya 面板的「修复 MCP」按钮用。客户端 UI 在重新 Trust 时可能把
v0.7.0 的旧片段（``maya.exe``）写回 ``mcp.json``，用户不该为此去翻 JSON。

零 DCC import、零第三方依赖；只碰 ``mcpServers.tak-scene`` 一个条目。
"""
from __future__ import annotations

import datetime
import io
import json
import os
from typing import Any, Dict, List, Optional

from .server import config_snippet, diagnose_config

SERVER_NAME = "tak-scene"
ENV_HOME = "TAK_HOME"


def default_config_path() -> str:
    """WorkBuddy 自定义 MCP 的配置文件；``TAK_MCP_CONFIG`` 可覆盖（测试用）。"""
    override = os.environ.get("TAK_MCP_CONFIG", "").strip()
    if override:
        return os.path.abspath(override)
    return os.path.join(os.path.expanduser("~"), ".workbuddy", "mcp.json")


def load(path: str) -> Dict[str, Any]:
    if not os.path.isfile(path):
        return {"mcpServers": {}}
    with io.open(path, "r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError("mcp.json 顶层必须是对象")
    servers = data.setdefault("mcpServers", {})
    if not isinstance(servers, dict):
        raise ValueError("mcpServers 必须是对象")
    return data


def build_server(python_override: str = "") -> Dict[str, Any]:
    """生成一条正确的 server 配置；可强制指定独立 Python 的绝对路径。"""
    server = dict(config_snippet()["mcpServers"][SERVER_NAME])
    if python_override:
        server["command"] = os.path.abspath(python_override)
    return server


def diagnose(path: Optional[str] = None) -> Dict[str, Any]:
    """只读体检，返回 ``{path, exists, entry, problems, ok, recommendation}``。"""
    config_path = os.path.abspath(path or default_config_path())
    result: Dict[str, Any] = {"path": config_path, "exists": False,
                              "entry": {}, "problems": [], "ok": False,
                              "recommendation": ""}
    try:
        data = load(config_path)
    except (OSError, ValueError) as exc:
        result["problems"] = ["读取配置失败: %s: %s" % (type(exc).__name__, exc)]
        result["recommendation"] = "检查文件是否是合法 JSON"
        return result

    result["exists"] = os.path.isfile(config_path)
    entry = data["mcpServers"].get(SERVER_NAME) or {}
    if not isinstance(entry, dict):
        result["problems"] = ["tak-scene 条目不是对象"]
        return result
    result["entry"] = entry
    result["problems"] = diagnose_config(entry) if entry else ["缺少 tak-scene 条目"]
    result["ok"] = not result["problems"]
    if not result["ok"]:
        result["recommendation"] = (
            "运行 python scripts/install_mcp_config.py，或在 TAK 面板点「修复 MCP」；"
            "之后重启客户端让它重新读取 mcp.json，不要在添加表单里沿用旧片段。")
    return result


def repair(path: Optional[str] = None, server_name: str = SERVER_NAME,
           python_override: str = "", backup: bool = True) -> Dict[str, Any]:
    """修一条配置并原子写回；返回诊断前后对比与备份路径。

    其它 ``mcpServers`` 条目原样保留。写之前备份为 ``<path>.bak-<时间戳>``。
    """
    config_path = os.path.abspath(path or default_config_path())
    before = diagnose(config_path)
    server = build_server(python_override)
    problems = diagnose_config(server)
    if problems:
        return {"ok": False, "path": config_path, "before": before["problems"],
                "after": problems, "backup": "",
                "error": "生成的新配置本身有问题，未写入"}

    try:
        data = load(config_path)
    except (OSError, ValueError) as exc:
        return {"ok": False, "path": config_path, "before": before["problems"],
                "after": [], "backup": "",
                "error": "读取配置失败: %s: %s" % (type(exc).__name__, exc)}

    backup_path = ""
    parent = os.path.dirname(config_path)
    if parent and not os.path.isdir(parent):
        os.makedirs(parent, exist_ok=True)
    if backup and os.path.isfile(config_path):
        stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        backup_path = config_path + ".bak-" + stamp
        with io.open(config_path, "r", encoding="utf-8") as source:
            with io.open(backup_path, "w", encoding="utf-8") as handle:
                handle.write(source.read())

    data["mcpServers"][server_name] = server
    tmp = config_path + ".tmp"
    with io.open(tmp, "w", encoding="utf-8") as handle:
        handle.write(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    os.replace(tmp, config_path)

    after = diagnose(config_path)
    return {"ok": after["ok"], "path": config_path,
            "before": before["problems"], "after": after["problems"],
            "backup": backup_path, "server": server,
            "error": "" if after["ok"] else "写入后复检仍有问题"}


__all__ = ["SERVER_NAME", "build_server", "default_config_path", "diagnose",
           "load", "repair"]
