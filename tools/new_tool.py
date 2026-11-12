# -*- coding: utf-8 -*-
"""工具插件脚手架：后续补充新工具只需一条命令。

    python tools/new_tool.py rig_health_guard --name "场景体检" --dcc maya,max,mobu,unreal
    python tools/new_tool.py --list

生成后的插件立即被 tak.core.registry 发现，并自带可跑的单测。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(_HERE)
_TEMPLATE = os.path.join(_HERE, "_template")
_VALID_DCC = {"maya", "max", "mobu", "unreal"}
_ID_RE = re.compile(r"^[a-z][a-z0-9_]*$")

sys.path.insert(0, _REPO_ROOT)


def scaffold(tool_id: str, name: str, dcc: list, description: str = "",
             tools_dir: str = _HERE, force: bool = False) -> str:
    """从模板生成插件目录，返回目录路径。"""
    if not _ID_RE.match(tool_id):
        raise ValueError("工具 id 必须是小写字母/数字/下划线，且以字母开头")
    unknown = set(dcc) - _VALID_DCC
    if unknown:
        raise ValueError("未知 DCC: %s" % sorted(unknown))
    target = os.path.join(tools_dir, tool_id)
    if os.path.exists(target) and not force:
        raise FileExistsError("插件已存在: %s（加 --force 覆盖）" % target)
    if os.path.exists(target):
        shutil.rmtree(target)
    shutil.copytree(_TEMPLATE, target)

    manifest_path = os.path.join(target, "manifest.json")
    with open(manifest_path, "r", encoding="utf-8") as fh:
        manifest = json.load(fh)
    manifest["id"] = tool_id
    manifest["name"] = name or tool_id
    manifest["dcc"] = list(dcc)
    manifest["entry"] = tool_id
    manifest["description"] = description or ("%s 工具插件" % (name or tool_id))
    with open(manifest_path, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, ensure_ascii=False, indent=2)

    for filename in os.listdir(target):
        if not filename.endswith(".py"):
            continue
        path = os.path.join(target, filename)
        with open(path, "r", encoding="utf-8") as fh:
            text = fh.read()
        text = text.replace("__TEMPLATE_ID__", tool_id).replace("__TEMPLATE_NAME__", name or tool_id)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
    return target


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="生成 TAK 工具插件骨架")
    parser.add_argument("tool_id", nargs="?", help="插件 id，如 rig_health_guard")
    parser.add_argument("--name", default="", help="显示名称")
    parser.add_argument("--dcc", default="maya,max,mobu,unreal", help="逗号分隔的 DCC 列表")
    parser.add_argument("--description", default="")
    parser.add_argument("--tools-dir", default=_HERE)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--list", action="store_true", help="列出已发现的插件")
    args = parser.parse_args(argv)

    if args.list:
        from tak.core.registry import discover
        for plugin in discover(args.tools_dir):
            print("%-28s %-20s %s" % (plugin.id, ",".join(plugin.dcc), plugin.name))
        return 0
    if not args.tool_id:
        parser.error("需要 tool_id，或使用 --list")
    dcc = [item.strip() for item in args.dcc.split(",") if item.strip()]
    try:
        target = scaffold(args.tool_id, args.name, dcc, args.description,
                          args.tools_dir, args.force)
    except (ValueError, FileExistsError) as exc:
        print("ERROR: %s" % exc)
        return 1
    print("created %s" % target)
    print("next: python -m pytest %s" % os.path.join(target, "test_core.py"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
