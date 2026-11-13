# -*- coding: utf-8 -*-
"""TAK Web 面板：给 UE 内嵌浏览器 / 本地浏览器用的单页工具面板。

对外只暴露三件事::

    from tak.ui.web import create_server, serve, main
    server = create_server(port=0)   # 只绑定，不进入消息循环
    server.server_close()
    serve(host="127.0.0.1", port=8765)

纯 Python 环境（CI、mayapy、无 DCC 的开发机）可直接 import，
不依赖任何第三方库，也不要求 Qt 或浏览器存在。

注意：这里用 PEP 562 的模块级 ``__getattr__`` 做**惰性导入**，而不是在
模块顶层直接 ``from .server import ...``。原因是
``python -m tak.ui.web.server`` 会先把父包 ``tak.ui.web`` 导入一遍，
顶层急切导入会让 ``tak.ui.web.server`` 提前出现在 ``sys.modules``，
runpy 因此打印一条 "found in sys.modules" 的 RuntimeWarning。
"""
from __future__ import annotations

from typing import Any

__all__ = [
    "create_server", "serve", "main", "handle_api", "panel_url",
    "PANEL_PATH", "APP_JS_PATH", "APP_CSS_PATH", "THEME_CSS_PATH",
    "WEB_DIR", "UI_DIR", "DCC_LIST",
]

_LAZY = {
    "create_server": "server",
    "serve": "server",
    "main": "server",
    "handle_api": "server",
    "panel_url": "server",
    "PANEL_PATH": "server",
    "APP_JS_PATH": "server",
    "APP_CSS_PATH": "server",
    "THEME_CSS_PATH": "server",
    "WEB_DIR": "server",
    "UI_DIR": "server",
    "DCC_LIST": "server",
}


def __getattr__(name: str) -> Any:
    """按需从 :mod:`tak.ui.web.server` 取名字，避免 runpy 警告。"""
    module_name = _LAZY.get(name)
    if module_name is None:
        raise AttributeError("module %r has no attribute %r" % (__name__, name))
    from importlib import import_module
    module = import_module("%s.%s" % (__name__, module_name))
    value = getattr(module, name)
    globals()[name] = value
    return value


def __dir__():
    return sorted(set(list(globals().keys()) + list(_LAZY.keys())))
