# -*- coding: utf-8 -*-
"""MCP 接入层：stdio JSON-RPC 2.0 server，把 TAK 的场景动作暴露给外部 AI。

启动：``python -m tak.mcp``（普通 Python 即可，不需要 Maya 解释器）。
配置片段：``python -m tak.mcp --print-config``。
"""
from __future__ import annotations

from .server import (PROTOCOL_VERSION, bridge_tools, config_snippet,
                     diagnose_config, entry_script, handle, python_executable,
                     repo_root, serve)

__all__ = ["PROTOCOL_VERSION", "bridge_tools", "config_snippet",
           "diagnose_config", "entry_script", "handle", "python_executable",
           "repo_root", "serve"]
