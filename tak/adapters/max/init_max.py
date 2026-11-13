# -*- coding: utf-8 -*-
"""3ds Max 启动引导入口。

约束：

* import 时**零副作用**（不注册菜单、不打印、不触碰宿主）；
* ``initialize()`` 调用 ``register_menu()`` 并打印使用说明，无宿主时同样安全；
* 只有 ``if __name__ == "__main__"`` 才真正执行，便于被 Max 监听器
  通过 ``python.Execute`` 显式调用，也便于 headless 下被测试导入。
"""
from __future__ import annotations

from typing import Any, Dict

from .menu import menu_script, register_menu


def initialize() -> Dict[str, Any]:
    """注册菜单并打印说明；返回可序列化的启动结果。"""
    menu_result = register_menu()
    print("[TAK] 3ds Max 适配器初始化：menu.ok=%s" % bool(menu_result.get("ok")))
    if not menu_result.get("ok"):
        print("[TAK] 未检测到 3ds Max 宿主，菜单注册已安全降级：%s"
              % menu_result.get("reason", ""))
    print("[TAK] 可将 menu_script() 输出的代码粘贴到 MAXScript 监听器以注册菜单。")
    return {"dcc": "max", "menu": menu_result, "startup_script": menu_script()}


if __name__ == "__main__":
    initialize()
