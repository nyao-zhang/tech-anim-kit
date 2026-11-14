# -*- coding: utf-8 -*-
"""TAK MCP 的自包含启动器：让 MCP 客户端**不依赖 env** 也能连上桥。

用法（由 ``config_snippet()`` 生成，不要手写）::

    C:\\...\\python.exe C:\\...\\tech-anim-kit\\scripts\\tak_mcp_entry.py

为什么不用 ``python -m tak.mcp``
--------------------------------
Windows 上的 MCP 客户端常把 ``mcpServers.<name>.env`` 当成**全量替换**：只把
配置里写的几个键传给子进程，父进程的 ``USERPROFILE`` / ``SystemRoot`` / ``PATH``
全丢掉。这时会同时缺三样东西：

* ``PYTHONPATH`` → ``-m tak.mcp`` 直接 ``ModuleNotFoundError: No module named 'tak'``；
* ``USERPROFILE`` → ``os.path.expanduser("~")`` 返回字面量 ``~``，桥发现文件
  ``bridge.json`` 与 token 被读写到错误目录，所有工具回「桥未启动」；
* ``SystemRoot`` / ``TEMP`` → Python 部分标准库调用失败。

更糟的是 ``env`` 一旦被清空，``-m`` 连报错都来不及写进 stdout，客户端只看到
30 秒的 ``initialize timed out``。本脚本在 import 任何 ``tak`` 代码**之前**补齐
这些值，因此 ``env`` 写错、写少、甚至整段删掉，server 都照样能起来。

零第三方依赖，只用标准库；不 import 任何 DCC 模块。
"""
from __future__ import annotations

import os
import sys
from typing import List, Optional

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

#: CSIDL_PROFILE，取当前用户配置目录，不依赖环境变量。
_CSIDL_PROFILE = 0x0028


def profile_dir() -> str:
    """拿当前用户主目录，按可靠性逐级兜底。

    1. ``USERPROFILE``（绝大多数情况）；
    2. ``HOMEDRIVE`` + ``HOMEPATH``；
    3. Windows ``SHGetFolderPathW(CSIDL_PROFILE)`` —— env 被全量替换时靠它；
    4. 从仓库位置反推 ``.../<用户>/WorkBuddy/<项目>/scripts`` 的上两级。
    """
    value = os.environ.get("USERPROFILE", "").strip()
    if value and os.path.isdir(value):
        return value

    drive = os.environ.get("HOMEDRIVE", "").strip()
    path = os.environ.get("HOMEPATH", "").strip()
    if drive and path:
        joined = os.path.join(drive, path)
        if os.path.isdir(joined):
            return joined

    if os.name == "nt":
        try:
            import ctypes
            from ctypes import wintypes

            shell32 = ctypes.WinDLL("shell32", use_last_error=True)
            get_folder = shell32.SHGetFolderPathW
            get_folder.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.HANDLE,
                                  wintypes.DWORD, wintypes.LPWSTR]
            get_folder.restype = ctypes.c_long
            buffer = ctypes.create_unicode_buffer(1024)
            if get_folder(None, _CSIDL_PROFILE, None, 0, buffer) == 0 and buffer.value:
                if os.path.isdir(buffer.value):
                    return buffer.value
        except Exception:  # pragma: no cover - 非 Windows / API 不可用
            pass

    # ROOT = .../<user>/WorkBuddy/<project>，再上两级就是 <user>
    candidate = os.path.dirname(os.path.dirname(ROOT))
    if os.path.isdir(candidate):
        return candidate
    return ""


def bootstrap() -> None:
    """补齐 import 路径与 Windows 环境变量；已有且合理的值一律不覆盖。"""
    if ROOT not in sys.path:
        sys.path.insert(0, ROOT)
    os.environ.setdefault("PYTHONPATH", ROOT)
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")

    profile = profile_dir()
    if profile:
        os.environ.setdefault("USERPROFILE", profile)
        drive, tail = os.path.splitdrive(profile)
        os.environ.setdefault("HOMEDRIVE", drive)
        os.environ.setdefault("HOMEPATH", tail)
        roaming = os.path.join(profile, "AppData", "Roaming")
        local = os.path.join(profile, "AppData", "Local")
        if os.path.isdir(roaming):
            os.environ.setdefault("APPDATA", roaming)
        if os.path.isdir(local):
            os.environ.setdefault("LOCALAPPDATA", local)

    # 客户端只传 env 里的键时 TAK_HOME 一定缺；写了字面 "~" 同样要纠正，
    # 否则 config_home() 会返回用户主目录而不是 ~/.tak，桥发现文件全错位。
    configured = os.environ.get("TAK_HOME", "").strip().strip('"')
    if not configured or configured == "~" or not os.path.isabs(configured):
        if profile:
            os.environ["TAK_HOME"] = os.path.join(profile, ".tak")
        else:  # pragma: no cover - 极端环境，仍保证是绝对路径
            os.environ["TAK_HOME"] = os.path.join(ROOT, ".tak-home")

    if os.name == "nt":
        os.environ.setdefault("SystemRoot", r"C:\Windows")
        temp_root = os.environ.get("LOCALAPPDATA") or os.environ.get("SystemRoot")
        if temp_root:
            os.environ.setdefault("TEMP", os.path.join(temp_root, "Temp"))
            os.environ.setdefault("TMP", os.path.join(temp_root, "Temp"))


def main(argv: Optional[List[str]] = None) -> int:
    bootstrap()
    from tak.mcp.__main__ import main as mcp_main

    args = list(sys.argv[1:] if argv is None else argv)
    return int(mcp_main(args))


if __name__ == "__main__":
    raise SystemExit(main())
