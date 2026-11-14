# -*- coding: utf-8 -*-
"""一键把正确的 ``tak-scene`` 配置写进 MCP 客户端，并做真实握手自检。

专治两类反复出现的故障：

* ``MCP initialize timed out for "custom-mcp:tak-scene"`` —— 在客户端 UI 里
  删除并重新添加 / 重新 Trust 时，表单把 v0.7.0 生成的旧配置（``maya.exe``）
  又写回了 ``mcp.json``；
* 握手成功但工具一律回「桥未启动」—— ``env`` 被客户端全量替换。

用法::

    python scripts/install_mcp_config.py            # 修 + 自检
    python scripts/install_mcp_config.py --check   # 只体检，不改文件
    python scripts/install_mcp_config.py --python C:\\Path\\python.exe
    python scripts/install_mcp_config.py --config <别的 mcp.json>

只改 ``mcpServers.tak-scene`` 一个条目，其它 server 原样保留；写之前自动备份成
``mcp.json.bak-<时间戳>``。真正的逻辑在 ``tak/mcp/install.py``，Maya 面板的
「修复 MCP」按钮用的是同一份。零第三方依赖。
"""
from __future__ import annotations

import argparse
import io
import json
import os
import subprocess
import sys
from typing import List, Optional

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from tak.mcp import install as mcp_install  # noqa: E402

DEFAULT_CHECK = os.path.join(ROOT, "validation", "mcp_handshake_check.py")


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="修复并自检 TAK 的 MCP 客户端配置")
    parser.add_argument("--config", default=mcp_install.default_config_path(),
                        help="目标 mcp.json 路径")
    parser.add_argument("--check", action="store_true", help="只体检，不写文件")
    parser.add_argument("--python", default="", help="指定独立 python.exe 的绝对路径")
    parser.add_argument("--no-handshake", action="store_true",
                        help="写完不跑子进程握手自检")
    args = parser.parse_args(argv)

    config_path = os.path.abspath(args.config)
    report = mcp_install.diagnose(config_path)
    old = report["entry"]
    print("目标配置: %s" % config_path)
    print("现有 command: %s" % (old.get("command") or "(无)"))
    print("现有 args   : %r" % (old.get("args") or []))
    if report["problems"]:
        print("体检发现问题:")
        for item in report["problems"]:
            print("  - %s" % item)
    else:
        print("体检: 没有发现问题")

    if args.check:
        return 1 if report["problems"] else 0

    fixed = mcp_install.repair(config_path, python_override=args.python)
    if not fixed["ok"]:
        print("修复失败: %s" % fixed["error"])
        for item in fixed["after"]:
            print("  - %s" % item)
        return 2
    if fixed["backup"]:
        print("已备份旧配置: %s" % fixed["backup"])
    print("已写入正确配置:")
    print(json.dumps({"mcpServers": {mcp_install.SERVER_NAME: fixed["server"]}},
                     ensure_ascii=False, indent=2))

    if args.no_handshake or not os.path.isfile(DEFAULT_CHECK):
        print("跳过握手自检。")
        return 0

    print("")
    print("按客户端实际配置启动子进程做握手自检…")
    result = subprocess.run(
        [sys.executable, DEFAULT_CHECK, "--config", config_path,
         "--server", mcp_install.SERVER_NAME], cwd=ROOT)
    if result.returncode != 0:
        print("握手自检未通过（退出码 %s）。常见原因：Maya 里的桥没启动，"
              "或 command 仍指向 DCC 宿主。" % result.returncode)
        return result.returncode
    print("")
    print("配置已就绪。请在 WorkBuddy 里**重启客户端**（或重新 Trust tak-scene），"
          "让它重新读取 mcp.json；不要在添加表单里沿用旧的 maya.exe 片段。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
