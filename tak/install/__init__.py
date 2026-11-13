# -*- coding: utf-8 -*-
"""TAK 一条命令安装器：把九件工具插件装进四大 DCC。

用法::

    python -m tak.install --list              # 探测四端安装目录
    python -m tak.install --plan              # 打印安装计划（默认 dry-run）
    python -m tak.install --apply             # 真正写入
    python -m tak.install --uninstall         # 预览卸载
    python -m tak.install --uninstall --apply # 真正移除

安全默认：任何写盘动作都必须显式 ``--apply``；写前自动备份为
``<file>.tak-backup-<N>``，标记块之外的内容逐字节不动。
"""
from __future__ import annotations

import os

from .targets import TARGETS, TARGET_ORDER, detect_targets
from .writer import (
    MARKER_VERSION,
    InstallAction,
    InstallPlan,
    apply_plan,
    plan_install,
    uninstall,
)

#: 默认仓库根：由 ``tak/install/__init__.py`` 位置向上两级推断
DEFAULT_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))

__all__ = [
    "DEFAULT_REPO_ROOT",
    "MARKER_VERSION",
    "TARGETS",
    "TARGET_ORDER",
    "InstallAction",
    "InstallPlan",
    "apply_plan",
    "detect_targets",
    "plan_install",
    "uninstall",
]
