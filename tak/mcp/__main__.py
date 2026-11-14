# -*- coding: utf-8 -*-
"""``python -m tak.mcp`` 入口。

* 无参数            → 启动 stdio MCP server
* ``--print-config`` → 打印可直接粘进 WorkBuddy / Claude Desktop 的 mcpServers 配置
* ``--status``       → 只看桥状态，不进入协议循环
"""
from __future__ import annotations

import json
import sys
from typing import List, Optional

from .server import config_snippet, serve


def main(argv: Optional[List[str]] = None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])
    if "--print-config" in args:
        print(json.dumps(config_snippet(), ensure_ascii=False, indent=2))
        return 0
    if "--status" in args:
        from tak.core import bridge
        print(json.dumps(bridge.bridge_status(), ensure_ascii=False, indent=2))
        return 0
    return serve()


if __name__ == "__main__":
    raise SystemExit(main())
