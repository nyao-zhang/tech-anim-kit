# -*- coding: utf-8 -*-
"""
参数审计：校验源码里每个 ``cmds.xxx(**kwargs)`` 的关键字参数是否真的是该命令的 flag。

为什么需要它
------------
Maya 的 UI 命令在**批处理模式下会静默返回 False，连 flag 都不校验**，
所以 ``cmds.shelfButton(name="x")`` 这类拼错/不存在的参数只有真正开界面点下去
才会炸。这个测试用 MEL 的 ``help "<command>"`` 取出真实参数表，静态比对一遍。

跑法::

    mayapy tests/test_cmds_flags.py
"""

import ast
import io
import os
import re
import sys

import maya.standalone
maya.standalone.initialize(name="python")

import maya.cmds as cmds          # noqa: E402
import maya.mel as mel            # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKIP_DIRS = {"__pycache__", ".git", "tests"}   # tests 里有探针式的临时调用，不强制

# help 输出的两行式：  -iol -imageOverlayLabel          String
_FLAG_RE = re.compile(r"^\s*-([A-Za-z0-9]+)\s+-([A-Za-z0-9]+)")


def _iter_sources(extra=()):
    for base, dirs, files in os.walk(ROOT):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for fn in files:
            if fn.endswith(".py"):
                yield os.path.join(base, fn)
    for p in extra:
        if os.path.isfile(p):
            yield p


def _dict_key_names(node):
    """``{...}`` 字面量或 ``dict(a=1, b=2)``，返回键名列表"""
    if isinstance(node, ast.Dict):
        return [k.value for k in node.keys
                if isinstance(k, ast.Constant) and isinstance(k.value, str)]
    if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            and node.func.id == "dict"):
        return [k.arg for k in node.keywords if k.arg]
    return []


def _collect_calls(path):
    """返回 [(行号, 命令名, [关键字参数名, ...]), ...]

    除了普通 ``cmds.foo(a=1)``，也解析 ``cmds.foo(**kwargs)``：把同文件里
    ``kwargs = {...}`` 的键静态收集进来（这类写法最容易藏非法参数）。
    """
    with io.open(path, "r", encoding="utf-8") as f:
        src = f.read()
    try:
        tree = ast.parse(src, filename=path)
    except SyntaxError as exc:
        print(u"  ! 语法错误，跳过 %s: %s" % (path, exc))
        return []

    # name -> [键名,...]（同一名字多次赋值时合并）
    dicts = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            keys = _dict_key_names(node.value)
            if not keys:
                continue
            for tgt in node.targets:
                if isinstance(tgt, ast.Name):
                    dicts.setdefault(tgt.id, []).extend(keys)

    out = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        if not (isinstance(fn, ast.Attribute) and isinstance(fn.value, ast.Name)
                and fn.value.id == "cmds"):
            continue
        names = [k.arg for k in node.keywords if k.arg]
        for k in node.keywords:
            if k.arg:                     # 普通 kwargs
                continue
            v = k.value
            if isinstance(v, ast.Dict):   # cmds.foo(**{"name": 1})
                names.extend(_dict_key_names(v))
            elif isinstance(v, ast.Name) and v.id in dicts:
                names.extend(dicts[v.id])  # cmds.foo(**kwargs)，kwargs 在上文定义
        if names:
            out.append((node.lineno, fn.attr, names))
    return out


_HELP_CACHE = {}


def command_flags(cmd):
    """该命令支持的 flag 名集合；命令不存在 / 无帮助时返回 None（跳过校验）"""
    if cmd in _HELP_CACHE:
        return _HELP_CACHE[cmd]
    flags = None
    try:
        text = mel.eval('help "%s"' % cmd) or ""
    except Exception:
        text = ""
    if "Flags:" in text or "-" in text:
        found = set()
        for line in text.splitlines():
            m = _FLAG_RE.match(line)
            if m:
                found.add(m.group(1))
                found.add(m.group(2))
        flags = found or None
    _HELP_CACHE[cmd] = flags
    return flags


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    print(u"== 参数审计：cmds 关键字参数 vs MEL help 参数表 ==")
    if not hasattr(cmds, "shelfButton"):          # 确认 cmds 可用
        print(u"cmds 不可用，无法审计")
        return 1

    checked = bad = skipped = 0
    for path in sorted(_iter_sources(argv)):
        rel = os.path.relpath(path, ROOT)
        for lineno, cmd, kwargs in _collect_calls(path):
            flags = command_flags(cmd)
            if flags is None:
                skipped += 1
                continue
            for kw in kwargs:
                checked += 1
                if kw in flags:
                    continue
                bad += 1
                if bad <= 25:
                    print(u"  ✗ %s:%d  cmds.%s(%s=...)  -> 该命令没有这个参数"
                          % (rel, lineno, cmd, kw))
                    near = sorted(f for f in flags if kw.lower()[:4] in f.lower())
                    if near:
                        print(u"        相近的合法参数：%s" % ", ".join(near[:6]))
    print(u"   已校验 %d 个关键字参数，跳过 %d 个无帮助信息的命令" % (checked, skipped))
    if bad:
        print(u"结果: %d 个参数不存在 ✗" % bad)
        return 1
    print(u"结果: 全部通过 ✓")
    return 0


if __name__ == "__main__":
    rc = main()
    print(u"退出码 %d" % rc)
    sys.exit(rc)
