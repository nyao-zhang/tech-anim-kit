# -*- coding: utf-8 -*-
"""模块文档的护栏。

文档与代码最容易发生的漂移有两种：**漏写**（加了插件没写文档、加了规则没写
进表）和**编造**（文档里写了一个代码里不存在的标识）。两种都会让使用者按
文档操作却对不上实际行为，所以放进 CI。

``rule_id`` 的判定不能简单 grep 源码：多数插件是用
``TOOL_ID + ".orphan_nodes"`` 或者 ``"%s.candidate.%s" % (TOOL_ID, ...)``
在运行时拼出来的，源码里没有完整字面量。因此这里按「插件自身前缀 + 源码里
存在的拼接形式」判定，既容得下动态构造，又拦得住凭空写出来的名字。
"""
from __future__ import annotations

import io
import os
import re
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DOCS = os.path.join(_ROOT, "docs")
_MODULE_DOCS = os.path.join(_DOCS, "modules")
_INDEX = os.path.join(_DOCS, "MODULES.md")

_VERSION_RE = re.compile(r"TAK\s+`(\d+\.\d+\.\d+)`")
_EMOJI_RE = re.compile("[\U0001F300-\U0001FAFF\u2600-\u27BF]")

#: ``TOOL_ID + ".orphan_nodes"`` 这类静态拼接
_LITERAL_RE = re.compile(r'(?:_?TOOL_ID)\s*\+\s*"\.([a-z0-9_]+(?:\.[a-z0-9_]+)*)"')
#: ``"%s.candidate.%s" % (TOOL_ID, ...)`` 这类动态拼接，取出中间那一段
_DYNAMIC_RE = re.compile(r'"%s\.([a-z0-9_]+)\.%s"')


def _plugin_ids():
    from tak.core.registry import discover

    return sorted(plugin.id for plugin in discover())


def _doc_path(plugin_id):
    return os.path.join(_MODULE_DOCS, plugin_id + ".md")


def _read(path):
    with io.open(path, encoding="utf-8") as handle:
        return handle.read()


def _python_sources(*folders):
    chunks = []
    for folder in folders:
        for root, _dirs, files in os.walk(folder):
            if "__pycache__" in root:
                continue
            for name in files:
                if name.endswith(".py"):
                    chunks.append(_read(os.path.join(root, name)))
    return "\n".join(chunks)


@pytest.fixture(scope="module")
def plugin_ids():
    return _plugin_ids()


@pytest.fixture(scope="module")
def plugin_sources():
    """``tools/<id>/`` 的源码文本；agent_console 的动作名在 ``tak/core/agent``。"""
    cache = {}
    for plugin_id in _plugin_ids():
        folders = [os.path.join(_ROOT, "tools", plugin_id)]
        if plugin_id == "agent_console":
            folders.append(os.path.join(_ROOT, "tak", "core", "agent"))
        cache[plugin_id] = _python_sources(*folders)
    return cache


@pytest.fixture(scope="module")
def registered_rules():
    """加载全部插件模块，让 ``@rule`` 完成注册。"""
    from tak.core.registry import discover
    from tak.core.rules.base import RULE_REGISTRY
    from tak.core import runner

    for plugin in discover():
        try:
            runner.load_tool_modules(plugin)
        except Exception:  # 加载失败的插件由它自己的测试负责
            continue
    return list(RULE_REGISTRY)


# --------------------------------------------------------------------------- #
# 覆盖度
# --------------------------------------------------------------------------- #
def test_every_plugin_has_a_doc(plugin_ids):
    missing = [pid for pid in plugin_ids if not os.path.isfile(_doc_path(pid))]
    assert missing == [], "以下插件缺文档: %s" % missing


def test_no_orphan_docs(plugin_ids):
    """反向检查：文档目录里不该有已经删掉的插件留下的文件。"""
    known = set(plugin_ids)
    orphans = sorted(
        name[:-3] for name in os.listdir(_MODULE_DOCS)
        if name.endswith(".md") and name[:-3] not in known
    )
    assert orphans == [], "以下文档没有对应插件: %s" % orphans


def test_index_covers_every_plugin(plugin_ids):
    text = _read(_INDEX)
    missing = [pid for pid in plugin_ids if "modules/%s.md" % pid not in text]
    assert missing == [], "docs/MODULES.md 没有链接到: %s" % missing


def test_index_explains_the_menu(plugin_ids):
    """索引必须解释「菜单为什么只有六项」——使用者最先问的就是这个。"""
    text = _read(_INDEX)
    assert "打开面板" in text
    assert "菜单" in text


# --------------------------------------------------------------------------- #
# 结构与排版
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("plugin_id", _plugin_ids())
def test_doc_has_eight_sections(plugin_id):
    text = _read(_doc_path(plugin_id))
    sections = [line for line in text.splitlines() if line.startswith("## ")]
    assert len(sections) == 8, "%s 的二级标题应为 8 个，实际 %d 个: %s" % (
        plugin_id, len(sections), sections)
    joined = "\n".join(sections)
    for keyword in ("解决什么问题", "打开", "输入", "输出", "关系"):
        assert keyword in joined, "%s 缺少含「%s」的小节" % (plugin_id, keyword)


@pytest.mark.parametrize("plugin_id", _plugin_ids())
def test_doc_identifies_its_plugin(plugin_id):
    text = _read(_doc_path(plugin_id))
    assert "`%s`" % plugin_id in text, "%s 文档没有写出自己的 tool_id" % plugin_id


@pytest.mark.parametrize("plugin_id", _plugin_ids())
def test_doc_code_fences_are_balanced(plugin_id):
    text = _read(_doc_path(plugin_id))
    fences = [line for line in text.splitlines() if line.startswith("```")]
    assert len(fences) % 2 == 0, "%s 的代码块围栏数量为奇数，说明有未闭合的块" % plugin_id


@pytest.mark.parametrize("plugin_id", _plugin_ids())
def test_doc_has_no_emoji(plugin_id):
    found = _EMOJI_RE.findall(_read(_doc_path(plugin_id)))
    assert found == [], "%s 含 emoji: %s" % (plugin_id, found)


@pytest.mark.parametrize("plugin_id", _plugin_ids())
def test_doc_version_is_not_from_the_future(plugin_id):
    """文档标注的版本是「它对照的代码版本」，不该比当前版本新。"""
    from tak.version import __version__

    match = _VERSION_RE.search(_read(_doc_path(plugin_id)))
    assert match is not None, "%s 文档头部缺少 `TAK \\`x.y.z\\`` 版本标注" % plugin_id

    def _tuple(value):
        return tuple(int(part) for part in value.split("."))

    assert _tuple(match.group(1)) <= _tuple(__version__), (
        "%s 标注的版本 %s 比当前 %s 还新" % (plugin_id, match.group(1), __version__))


# --------------------------------------------------------------------------- #
# 与代码一致性
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("plugin_id", _plugin_ids())
def test_rule_ids_in_doc_exist_in_source(plugin_id, plugin_sources):
    """文档里以本插件前缀出现的标识，必须是源码真能产出的。"""
    source = plugin_sources[plugin_id]
    literal = set(_LITERAL_RE.findall(source))
    dynamic = set(_DYNAMIC_RE.findall(source))
    # 也有直接写全的：rule_id="rig_kb.hit"
    literal |= set(re.findall(
        r'rule_id\s*=\s*"%s\.([a-z0-9_]+(?:\.[a-z0-9_]+)*)"' % re.escape(plugin_id),
        source))

    text = _read(_doc_path(plugin_id))
    # 前面是 / 或 . 的是文件路径（tools/<id>/ui_qt.py），不是标识符
    pattern = re.compile(r"(?<![/\w.])%s\.[a-z][a-z0-9_.]*" % re.escape(plugin_id))
    mentioned = sorted(set(pattern.findall(text)))

    invented = []
    for item in mentioned:
        rest = item[len(plugin_id) + 1:]
        if rest in literal:
            continue
        if rest.split(".")[0] in dynamic:
            continue
        invented.append(item)
    assert invented == [], (
        "%s 文档里出现源码中不存在的标识: %s（源码里可产出的: %s）"
        % (plugin_id, invented, sorted(literal) or "无"))


@pytest.mark.parametrize("plugin_id", _plugin_ids())
def test_registered_rules_are_documented(plugin_id, registered_rules):
    """反向：凡是注册到该插件的规则，文档里都得出现。

    防的是「加了规则忘了写文档」——文档看着完整，实际漏了新增的检查项。
    """
    text = _read(_doc_path(plugin_id))
    ids = sorted({rule.id for rule in registered_rules
                  if getattr(rule, "plugin_id", "") == plugin_id})
    missing = [item for item in ids if item not in text]
    assert missing == [], "%s 文档漏写了这些已注册规则: %s" % (plugin_id, missing)
