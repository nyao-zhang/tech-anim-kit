# -*- coding: utf-8 -*-
"""``python -m tak.install`` 命令行入口。

返回码：0 = 成功；1 = 存在 error（未知目标、文件不可读、写盘失败等）。
本工具**不做交互输入**，``--apply`` 即最终确认。
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Dict, List, Optional

from . import DEFAULT_REPO_ROOT
from .targets import TARGETS, TARGET_ORDER, detect_targets
from .writer import MARKER_VERSION, apply_plan, plan_install, uninstall

def _build_parser() -> argparse.ArgumentParser:
    """构造参数解析器；所有参数都可用 ``--json`` 输出机器可读结果。"""
    parser = argparse.ArgumentParser(
        prog="python -m tak.install",
        description="TAK 安装器：把九件工具插件装进 Maya / 3ds Max / "
                    "MotionBuilder / Unreal（默认 dry-run）。")
    parser.add_argument("--list", action="store_true",
                        help="列出四端探测结果后退出")
    parser.add_argument("--plan", action="store_true",
                        help="打印安装计划（默认行为，dry-run）")
    parser.add_argument("--apply", action="store_true",
                        help="真正写盘（缺省只预览）")
    parser.add_argument("--uninstall", action="store_true",
                        help="移除 TAK 标记块，与 --apply 组合才真删")
    parser.add_argument("--dcc", default="",
                        help="只处理指定端，逗号分隔，如 maya,max")
    parser.add_argument("--root", action="append", default=[], metavar="DCC=PATH",
                        help="覆盖某端安装根目录，可多次，如 --root maya=C:/tmp/maya")
    parser.add_argument("--repo-root", default="",
                        help="覆盖写进注入块的仓库根目录")
    parser.add_argument("--json", action="store_true",
                        help="输出机器可读 JSON（stdout 只有 JSON）")
    return parser


def _parse_dcc(raw: str, errors: List[str]) -> List[str]:
    """解析 ``--dcc``；空串表示四端全选，未知 id 记为 error。"""
    if not raw.strip():
        return list(TARGET_ORDER)
    selected: List[str] = []
    for item in raw.split(","):
        key = item.strip().lower()
        if not key:
            continue
        if key not in TARGETS:
            errors.append("未知目标：%s（可选：%s）"
                          % (item.strip(), ", ".join(TARGET_ORDER)))
            continue
        if key not in selected:
            selected.append(key)
    return selected


def _parse_roots(raw_roots: List[str], errors: List[str]) -> Dict[str, str]:
    """解析 ``--root dcc=path``；格式错误或未知 dcc 记为 error。"""
    roots: Dict[str, str] = {}
    for raw in raw_roots:
        if "=" not in raw:
            errors.append("--root 格式应为 DCC=PATH：%s" % raw)
            continue
        key, _, value = raw.partition("=")
        key = key.strip().lower()
        value = value.strip()
        if key not in TARGETS:
            errors.append("--root 指定的目标未知：%s" % key)
            continue
        if not value:
            errors.append("--root %s 的路径为空" % key)
            continue
        roots[key] = value
    return roots


def _discover_tools() -> List[str]:
    """列出仓库里可发现的工具插件 id；注册表异常不影响安装器主流程。"""
    try:
        from tak.core.registry import discover

        return [plugin.id for plugin in discover()]
    except Exception:
        return []


def _print_list(detected: Dict[str, Dict[str, Any]]) -> None:
    """人类可读的探测结果。"""
    for target in TARGET_ORDER:
        info = detected[target]
        spec = TARGETS[target]
        state = "已发现" if info["found"] else "未发现"
        where = info["root"] or "-"
        print("%-7s %-24s v%-5s [%s] root=%s"
              % (target, spec["name"], info["version"], state, where))
        print("%-7s 入口：%s" % ("", info["entry"] or "-"))
        print("%-7s 说明：%s" % ("", spec["notes"]))
    tools = _discover_tools()
    print("[tools] 发现 %d 个工具插件：%s"
          % (len(tools), ", ".join(tools) if tools else "-"))


def _print_actions(plan: Any) -> None:
    """逐条打印 ``[mode] 路径  原因``。"""
    for action in plan.actions:
        print("[%s] %s  %s"
              % (action.mode, action.path or "(未探测到路径)", action.reason))


def _print_summary(summary: Dict[str, int], applied: bool) -> None:
    """打印一行统计，便于快速判断有没有实际改动。"""
    label = "已写入" if applied else "dry-run"
    print("[%s] create=%d append=%d replace-block=%d skip=%d total=%d"
          % (label, summary.get("create", 0), summary.get("append", 0),
             summary.get("replace-block", 0), summary.get("skip", 0),
             summary.get("total", 0)))


def _print_errors(errors: List[str]) -> None:
    """错误统一走 stderr，避免污染 ``--json`` 的 stdout。"""
    for message in errors:
        print("[error] %s" % message, file=sys.stderr)


def main(argv: Optional[List[str]] = None) -> int:
    """CLI 主函数；返回进程退出码。"""
    parser = _build_parser()
    args = parser.parse_args(argv)

    errors: List[str] = []
    selected = _parse_dcc(args.dcc, errors)
    roots = _parse_roots(args.root, errors)

    if args.list:
        detected = detect_targets(roots=roots or None)
        if args.json:
            print(json.dumps({"action": "list", "targets": detected,
                              "tools": _discover_tools(),
                              "errors": errors}, ensure_ascii=False, indent=2))
        else:
            _print_list(detected)
            _print_errors(errors)
        return 1 if errors else 0

    repo_root = args.repo_root or DEFAULT_REPO_ROOT

    if args.uninstall:
        picked = selected if args.dcc.strip() else None
        result = uninstall(repo_root, targets=picked, roots=roots or None,
                           dry_run=not args.apply)
        if args.json:
            payload = dict(result)
            payload["action"] = "uninstall"
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            print("[%s] 卸载目标：%s"
                  % ("dry-run" if result["dry_run"] else "apply",
                     ", ".join(selected) or "(全部)"))
            for item in result["skipped"]:
                print("[skip] %s  %s" % (item.get("path") or "(未探测到路径)",
                                         item.get("reason", "")))
            for item in result["removed"]:
                print("[removed] %s  删除 %d 个标记块%s"
                      % (item["path"], item["blocks"],
                         "（文件随之删除）" if item.get("delete_file") else ""))
            for message in result["warnings"]:
                print("[warn] %s" % message)
            _print_errors(result["errors"])
        return 0 if result["ok"] else 1

    picked = selected if args.dcc.strip() else None
    plan = plan_install(repo_root, targets=picked, roots=roots or None,
                        dry_run=not args.apply)
    if errors:
        plan.errors.extend(errors)
    result = apply_plan(plan)

    if args.json:
        payload = plan.to_dict()
        payload["action"] = "apply" if args.apply else "plan"
        payload["result"] = result
        payload["repo_root"] = repo_root
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print("[repo] %s  marker=v%s" % (repo_root, MARKER_VERSION))
        _print_actions(plan)
        _print_summary(plan.summary(), args.apply)
        if not args.apply:
            print("[提示] 以上为 dry-run，加 --apply 才会真正写盘。")
        _print_errors(plan.errors)
    return 0 if result["ok"] else 1


__all__ = ["main"]
