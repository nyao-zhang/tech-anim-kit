# -*- coding: utf-8 -*-
"""安装计划与写盘核心（纯函数化，便于测试）。

安全模型
--------
1. **默认 dry-run**：:func:`plan_install` 只读文件、只算 diff，绝不写盘；
   真实写盘必须 ``apply_plan`` 且 ``plan.dry_run is False``。
2. **块外逐字节不动**：文件按 ``surrogateescape`` 解码/编码，任意编码的
   用户文件都能原样保留；替换只发生在 ``BEGIN … END`` 标记块区间内。
3. **写前备份**：任何已存在文件在覆盖前都会生成
   ``<file>.tak-backup-<N>``，N 递增且绝不覆盖旧备份。
4. **幂等**：连续两次 apply，第二次全部 ``skip``（内容一致）或
   ``replace-block``（内容变化但只换块），不会产生重复标记块。

标记块格式（``hash`` 风格，MaxScript 用 ``--`` 前缀）
-----------------------------------------------------
``# >>> TAK BEGIN maya v<版本>``
…块内代码…
``# >>> TAK END maya``
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Tuple

from tak.version import __version__

from .targets import TARGETS, TARGET_ORDER, detect_targets

#: 标记块版本。**直接取包版本，不在这里重复写字面量** —— 早期版本两处各写一份，
#: 结果 README 是 0.2.0、`tak/version.py` 还是 0.1.0，没人发现。
#: 块内容一旦变化，``apply`` 会因为内容不等而走 ``replace-block``，不需要靠这个
#: 数字判断，它只是给人看的溯源信息。``tests/test_release_meta.py`` 守着这条。
MARKER_VERSION = __version__

#: 四种安装动作
MODES = ("create", "append", "replace-block", "skip")

_BEGIN_RE = re.compile(
    r"^(?P<prefix>#|--)\s*>>>\s*TAK BEGIN\s+(?P<target>[A-Za-z0-9_]+)"
    r"(?:\s+v(?P<version>\S+))?\s*$")
_END_RE = re.compile(
    r"^(?P<prefix>#|--)\s*>>>\s*TAK END\s+(?P<target>[A-Za-z0-9_]+)\s*$")


# --------------------------------------------------------------------- 数据结构
@dataclass
class InstallAction:
    """单个安装动作（一个 target 对应一个入口文件）。"""

    target: str
    path: str
    mode: str
    before: str = ""
    after: str = ""
    backup: str = ""
    reason: str = ""

    @property
    def changed(self) -> bool:
        """块内容是否真的发生变化（``skip`` 恒为 False）。"""
        return self.mode != "skip" and self.before != self.after

    def to_dict(self) -> Dict[str, Any]:
        """可 JSON 序列化；不携带完整文件内容，只给长度与备份路径。"""
        return {
            "target": self.target,
            "path": self.path,
            "mode": self.mode,
            "reason": self.reason,
            "backup": self.backup,
            "changed": self.changed,
            "before_length": len(self.before),
            "after_length": len(self.after),
        }


@dataclass
class InstallPlan:
    """一次安装计划：默认 dry-run，只有显式设为 False 才允许写盘。"""

    actions: List[InstallAction] = field(default_factory=list)
    dry_run: bool = True
    errors: List[str] = field(default_factory=list)

    def summary(self) -> Dict[str, int]:
        """按 mode 统计，另附 ``total`` 与 ``errors``。"""
        counts = {mode: 0 for mode in MODES}
        for action in self.actions:
            counts[action.mode] = counts.get(action.mode, 0) + 1
        counts["total"] = len(self.actions)
        counts["errors"] = len(self.errors)
        return counts

    def to_dict(self) -> Dict[str, Any]:
        """给 ``--json`` 用的完整计划。"""
        return {
            "dry_run": self.dry_run,
            "summary": self.summary(),
            "actions": [action.to_dict() for action in self.actions],
            "errors": list(self.errors),
        }


# --------------------------------------------------------------------- 块文本
def marker_lines(comment_style: str, target: str, version: str) -> Tuple[str, str]:
    """返回 (BEGIN 行, END 行)；``dash`` 用于 MaxScript 的 ``--`` 注释。"""
    prefix = "--" if comment_style == "dash" else "#"
    return ("%s >>> TAK BEGIN %s v%s" % (prefix, target, version),
            "%s >>> TAK END %s" % (prefix, target))


def build_block(target: str, repo_root: str, version: str = MARKER_VERSION,
                newline: str = "\n") -> str:
    """生成带标记的注入块（不含块外换行）。"""
    spec = TARGETS[target]
    payload = spec["block_template"]
    payload = payload.replace("{repo_root}", str(repo_root))
    payload = payload.replace("{version}", str(version))
    payload = payload.replace("{target}", str(target))
    payload = payload.replace("\r\n", "\n").rstrip("\n")
    begin, end = marker_lines(spec["comment_style"], target, version)
    lines = [begin] + payload.split("\n") + [end]
    return newline.join(lines)


def detect_newline(text: str, default: str = "\n") -> str:
    """按出现次数判断文件主换行符，避免把 CRLF 文件改成 LF。"""
    if not text:
        return default
    crlf = text.count("\r\n")
    lf = text.count("\n") - crlf
    if crlf == 0 and lf == 0:
        return default
    return "\r\n" if crlf > lf else "\n"


def _normalized(text: str) -> str:
    """比较标记块内容时忽略换行风格差异。"""
    return text.replace("\r\n", "\n")


def _iter_lines(text: str) -> Iterable[Tuple[int, int, str]]:
    """逐行产出 ``(start, end, body)``，``end`` 不含行尾换行符。"""
    position = 0
    for line in text.splitlines(True):
        body = line.rstrip("\r\n")
        yield position, position + len(body), body
        position += len(line)


def scan_blocks(text: str) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """扫描全部标记块。

    :return: ``(blocks, unclosed)``；``blocks`` 每项含
        ``target`` / ``start`` / ``end`` / ``version`` / ``style``。
        未被 ``END`` 闭合的 ``BEGIN`` 进入 ``unclosed``，调用方据此保护文件。
    """
    blocks: List[Dict[str, Any]] = []
    unclosed: List[Dict[str, Any]] = []
    opened: Optional[Dict[str, Any]] = None
    for start, end, body in _iter_lines(text):
        begin_match = _BEGIN_RE.match(body)
        if begin_match:
            if opened is not None:
                unclosed.append(opened)
            opened = {
                "target": begin_match.group("target"),
                "style": "dash" if begin_match.group("prefix") == "--" else "hash",
                "start": start,
                "version": begin_match.group("version") or "",
            }
            continue
        end_match = _END_RE.match(body)
        if end_match and opened is not None and end_match.group("target") == opened["target"]:
            opened["end"] = end
            blocks.append(opened)
            opened = None
    if opened is not None:
        unclosed.append(opened)
    return blocks, unclosed


def _append_block(text: str, block: str, newline: str) -> str:
    """把块追加到文件末尾：与用户内容之间恰好留一个空行。"""
    if not text:
        return block + newline
    separator = newline if text.endswith(newline) else newline + newline
    return text + separator + block + newline


def _remove_span(text: str, start: int, end: int) -> str:
    """删除 ``[start, end)``，并把块前后各一个空行归一化掉。

    规则：先吃掉 END 行自身的换行；再吃掉块前面的一个空行；块在文件开头
    时改为吃掉块后面的一个空行，避免卸载后留下首行空白。
    """
    prefix, suffix = text[:start], text[end:]
    if suffix.startswith("\r\n"):
        suffix = suffix[2:]
    elif suffix.startswith("\n"):
        suffix = suffix[1:]
    if prefix.endswith("\r\n\r\n"):
        prefix = prefix[:-2]
    elif prefix.endswith("\n\n"):
        prefix = prefix[:-1]
    elif not prefix:
        if suffix.startswith("\r\n"):
            suffix = suffix[2:]
        elif suffix.startswith("\n"):
            suffix = suffix[1:]
    return prefix + suffix


def _replace_blocks(text: str, blocks: List[Dict[str, Any]], block: str) -> str:
    """用新块替换第一个标记块，多余的同类块直接删除（保证一 target 一块）。"""
    result = text
    for extra in sorted(blocks[1:], key=lambda item: item["start"], reverse=True):
        result = _remove_span(result, extra["start"], extra["end"])
    first = blocks[0]
    return result[:first["start"]] + block + result[first["end"]:]


def _remove_blocks(text: str, blocks: List[Dict[str, Any]]) -> str:
    """从后往前删除所有给定块，前面的偏移量因此保持有效。"""
    result = text
    for item in sorted(blocks, key=lambda entry: entry["start"], reverse=True):
        result = _remove_span(result, item["start"], item["end"])
    return result


# --------------------------------------------------------------------- 文件读写
def read_text(path: str) -> str:
    """按字节读取再 ``surrogateescape`` 解码，保证任意编码都能原样回写。"""
    with open(path, "rb") as handle:
        return handle.read().decode("utf-8", "surrogateescape")


def write_text(path: str, text: str) -> None:
    """先写同目录临时文件再原子替换，避免中断留下半个文件。"""
    data = text.encode("utf-8", "surrogateescape")
    temp_path = path + ".tak-tmp"
    try:
        with open(temp_path, "wb") as handle:
            handle.write(data)
        os.replace(temp_path, path)
    except OSError:
        if os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except OSError:
                pass
        raise


def make_backup(path: str) -> str:
    """生成 ``<file>.tak-backup-<N>``，N 递增且绝不覆盖已有备份。"""
    with open(path, "rb") as handle:
        payload = handle.read()
    counter = 1
    while True:
        candidate = "%s.tak-backup-%d" % (path, counter)
        if not os.path.exists(candidate):
            with open(candidate, "wb") as handle:
                handle.write(payload)
            return candidate
        counter += 1


def _ensure_parent(path: str) -> None:
    """确保入口文件的父目录存在（显式 ``--root`` 时可能需要新建）。"""
    parent = os.path.dirname(os.path.abspath(path))
    if parent and not os.path.isdir(parent):
        os.makedirs(parent, exist_ok=True)


# --------------------------------------------------------------------- 计划
def _normalize_targets(targets: Optional[Iterable[str]],
                       errors: List[str]) -> List[str]:
    """规范化 target 列表：去重、保序，未知 id 记为 error。"""
    if targets is None:
        return list(TARGET_ORDER)
    if isinstance(targets, str):
        targets = targets.split(",")
    result: List[str] = []
    for raw in targets:
        key = str(raw).strip().lower()
        if not key:
            continue
        if key not in TARGETS:
            errors.append("未知目标：%s（可选：%s）"
                          % (raw, ", ".join(TARGET_ORDER)))
            continue
        if key not in result:
            result.append(key)
    return result


def _plan_one(target: str, path: str, before: str, exists: bool,
              repo_root: str) -> InstallAction:
    """计算单个入口文件的动作。"""
    spec = TARGETS[target]
    newline = detect_newline(before)
    block = build_block(target, repo_root, newline=newline)

    if not exists:
        return InstallAction(target, path, "create", before, block + newline,
                             reason="文件不存在，将新建并写入标记块")

    blocks, unclosed = scan_blocks(before)
    if unclosed:
        return InstallAction(target, path, "skip", before, before,
                             reason="存在未闭合的 TAK BEGIN（缺少 TAK END），"
                                    "为保护文件已跳过，请手动修复")

    mine = [item for item in blocks if item["target"] == target]
    if not mine:
        return InstallAction(target, path, "append", before,
                             _append_block(before, block, newline),
                             reason="文件已存在但无 TAK 标记块，将在末尾追加"
                                    "（块外内容逐字节不动）")

    if len(mine) == 1:
        current = before[mine[0]["start"]:mine[0]["end"]]
        if _normalized(current) == _normalized(block):
            return InstallAction(target, path, "skip", before, before,
                                 reason="标记块已是 v%s，无需变更" % MARKER_VERSION)
        return InstallAction(target, path, "replace-block", before,
                             _replace_blocks(before, mine, block),
                             reason="标记块内容与 v%s 不一致，将整块替换"
                                    "（块外内容逐字节不动）" % MARKER_VERSION)

    return InstallAction(target, path, "replace-block", before,
                         _replace_blocks(before, mine, block),
                         reason="发现 %d 个 TAK 标记块，将合并为一个" % len(mine))


def plan_install(repo_root: str, targets: Optional[Iterable[str]] = None,
                 roots: Optional[Dict[str, str]] = None,
                 dry_run: bool = True) -> InstallPlan:
    """计算安装计划，**不写盘**。

    :param repo_root: 要写进注入块的仓库根目录
    :param targets: 目标子集，缺省四端全装
    :param roots: 显式根目录覆盖（``{"maya": "C:/tmp/maya"}``）
    :param dry_run: 默认 True；仅作为计划标记，本函数永不写盘
    """
    plan = InstallPlan(dry_run=bool(dry_run))
    selected = _normalize_targets(targets, plan.errors)
    detected = detect_targets(roots=roots)

    for target in selected:
        info = detected.get(target, {})
        path = str(info.get("entry") or "")
        if not info.get("found") or not path:
            plan.actions.append(InstallAction(
                target, "", "skip",
                reason="未检测到 %s 安装目录（可用 --root %s=PATH 指定）"
                       % (TARGETS[target]["name"], target)))
            continue

        exists = os.path.exists(path)
        try:
            before = read_text(path) if exists else ""
        except OSError as exc:
            plan.errors.append("%s 读取失败：%s" % (path, exc))
            plan.actions.append(InstallAction(
                target, path, "skip", reason="文件不可读：%s" % exc))
            continue

        plan.actions.append(_plan_one(target, path, before, exists, repo_root))
    return plan


# --------------------------------------------------------------------- 执行
def _apply_action(action: InstallAction) -> None:
    """真正落盘：建目录 → 备份 → 原子写入。"""
    _ensure_parent(action.path)
    if os.path.exists(action.path):
        action.backup = make_backup(action.path)
    write_text(action.path, action.after)


def apply_plan(plan: InstallPlan) -> Dict[str, Any]:
    """执行计划；``plan.dry_run`` 为 True 时只回放动作，绝不写盘。"""
    result: Dict[str, Any] = {
        "ok": not plan.errors,
        "dry_run": bool(plan.dry_run),
        "applied": [],
        "planned": [],
        "skipped": [],
        "backups": [],
        "errors": list(plan.errors),
    }
    for action in plan.actions:
        record = {"target": action.target, "path": action.path, "mode": action.mode}
        if action.mode == "skip":
            result["skipped"].append(record)
            continue
        if plan.dry_run:
            result["planned"].append(record)
            continue
        try:
            _apply_action(action)
        except OSError as exc:
            result["ok"] = False
            result["errors"].append("%s 写入失败：%s" % (action.path, exc))
            record["error"] = str(exc)
            continue
        result["applied"].append(record)
        if action.backup:
            result["backups"].append(action.backup)
    return result


def uninstall(repo_root: Optional[str] = None,
              targets: Optional[Iterable[str]] = None,
              roots: Optional[Dict[str, str]] = None,
              dry_run: bool = True) -> Dict[str, Any]:
    """精确删除标记块，恢复到安装前内容。

    * 块外内容逐字节保留（含其它 target 的块）；
    * 删完只剩空白时删除该入口文件（说明它只因 TAK 存在），但备份保留；
    * 写前同样备份，删文件失败只记 error，不影响其它 target。

    :param repo_root: 仅为签名一致性保留（卸载不需要仓库路径）
    """
    result: Dict[str, Any] = {
        "ok": True,
        "dry_run": bool(dry_run),
        "removed": [],
        "deleted": [],
        "skipped": [],
        "backups": [],
        "warnings": [],
        "errors": [],
    }
    selected = _normalize_targets(targets, result["errors"])
    if result["errors"]:
        result["ok"] = False
    detected = detect_targets(roots=roots)

    for target in selected:
        info = detected.get(target, {})
        path = str(info.get("entry") or "")
        if not path or not os.path.exists(path):
            result["skipped"].append({
                "target": target,
                "path": path,
                "reason": "未安装（%s 不存在）" % (path or "入口文件"),
            })
            continue

        try:
            before = read_text(path)
        except OSError as exc:
            result["ok"] = False
            result["errors"].append("%s 读取失败：%s" % (path, exc))
            continue

        blocks, unclosed = scan_blocks(before)
        mine = [item for item in blocks if item["target"] == target]
        if not mine:
            reason = "未发现 %s 的 TAK 标记块" % target
            if unclosed:
                reason = "存在未闭合的 TAK BEGIN，未做任何修改"
                result["warnings"].append("%s：%s" % (path, reason))
            result["skipped"].append({"target": target, "path": path, "reason": reason})
            continue

        after = _remove_blocks(before, mine)
        became_empty = after.strip() == ""
        if dry_run:
            result["removed"].append({
                "target": target, "path": path, "blocks": len(mine),
                "delete_file": became_empty,
            })
            continue

        try:
            if os.path.exists(path):
                result["backups"].append(make_backup(path))
            if became_empty:
                os.remove(path)
                result["deleted"].append(path)
            else:
                write_text(path, after)
        except OSError as exc:
            result["ok"] = False
            result["errors"].append("%s 卸载失败：%s" % (path, exc))
            continue

        result["removed"].append({
            "target": target, "path": path, "blocks": len(mine),
            "delete_file": became_empty,
        })
    return result


__all__ = [
    "MARKER_VERSION",
    "MODES",
    "InstallAction",
    "InstallPlan",
    "apply_plan",
    "build_block",
    "detect_newline",
    "make_backup",
    "marker_lines",
    "plan_install",
    "read_text",
    "scan_blocks",
    "uninstall",
    "write_text",
]
