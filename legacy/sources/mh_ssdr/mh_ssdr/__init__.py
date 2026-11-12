# -*- coding: utf-8 -*-
"""
mh_ssdr —— MetaHuman 面部骨骼 SSDR 烘焙工具（Maya 2024 / Python 3）

模块
----
* ``ssdr_core``   纯 NumPy 的 SSDR 反求核心（可离线单测）
* ``maya_bridge`` Maya 数据层（读权重 / 提取雕刻网格 / 写回骨骼）
* ``mirror``      把反求结果镜像到另一侧（左右命名配对 / 反射共轭）
* ``tool_ui``     界面与控制器

快速启动
--------
::

    from mh_ssdr.tool_ui import show
    show()
"""

__version__ = "1.1.0"
__all__ = ["ssdr_core", "maya_bridge", "mirror", "tool_ui", "show"]


def show():
    """打开工具窗口（只应在 Maya 内部调用）"""
    from . import tool_ui
    return tool_ui.show()
