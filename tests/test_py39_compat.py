# -*- coding: utf-8 -*-
"""P1.4：Python 3.9 语法自检与 tak/core 的 DCC 隔离静态检查。

只做静态分析（ast.parse / ast.walk），不 import 被检查的文件，
因此注释、文档字符串里的关键词不会被误判。
"""
import ast
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CORE_DIR = os.path.join(ROOT, "tak", "core")
MOCKS_DIR = os.path.join(CORE_DIR, "mocks")
SCAN_DIRS = (os.path.join(ROOT, "tak"), os.path.join(ROOT, "tools"))
FORBIDDEN_ROOTS = ("maya", "pymxs", "pyfbsdk", "unreal")
PY39 = (3, 9)


def _python_files(root):
    found = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(name for name in dirnames
                             if name != "__pycache__" and not name.startswith("."))
        for name in sorted(filenames):
            if name.endswith(".py"):
                found.append(os.path.join(dirpath, name))
    return found


def _rel(path):
    return os.path.relpath(path, ROOT).replace("\\", "/")


def _read(path):
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


def _parse_py39(source, filename="<source>"):
    """按 3.9 语法解析；返回 AST 或抛出 SyntaxError。"""
    return ast.parse(source, filename=filename, feature_version=PY39)


def _syntax_errors(paths):
    errors = []
    for path in paths:
        try:
            _parse_py39(_read(path), filename=path)
        except SyntaxError as exc:
            errors.append("%s:%s: %s" % (_rel(path), exc.lineno, exc.msg))
    return errors


def _import_roots(source, filename="<source>"):
    """返回顶层 import 的模块名（不含相对导入）。"""
    tree = _parse_py39(source, filename=filename)
    roots = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.extend(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module:
                roots.append(node.module.split(".")[0])
    return roots


def _dcc_imports(source, filename="<source>"):
    """返回 source 中真实 DCC 导入的 (行号, 模块名)。"""
    tree = _parse_py39(source, filename=filename)
    hits = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] in FORBIDDEN_ROOTS:
                    hits.append((node.lineno, alias.name))
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module:
                if node.module.split(".")[0] in FORBIDDEN_ROOTS:
                    hits.append((node.lineno, node.module))
    return hits


def _core_files_excluding_mocks():
    mocks_prefix = os.path.abspath(MOCKS_DIR) + os.sep
    return [path for path in _python_files(CORE_DIR)
            if not os.path.abspath(path).startswith(mocks_prefix)]


# --------------------------------------------------------------------- 扫描面
def test_scan_covers_tak_tools_and_mocks():
    for directory in SCAN_DIRS:
        assert os.path.isdir(directory)
        assert len(_python_files(directory)) >= 5
    assert os.path.isdir(MOCKS_DIR)
    assert _python_files(MOCKS_DIR)
    assert len(_core_files_excluding_mocks()) >= 5


def test_core_scan_excludes_mocks_package():
    scanned = {os.path.abspath(path) for path in _core_files_excluding_mocks()}
    assert not any(path.startswith(os.path.abspath(MOCKS_DIR) + os.sep) for path in scanned)
    assert any(path.startswith(os.path.abspath(CORE_DIR)) for path in scanned)


# --------------------------------------------------------------------- 3.9 语法
def test_all_sources_parse_as_python39():
    errors = []
    for directory in SCAN_DIRS:
        errors.extend(_syntax_errors(_python_files(directory)))
    assert errors == [], "存在非 Python 3.9 语法：\n" + "\n".join(errors)


def test_scanner_accepts_pep585_annotations():
    source = "def f(items: list[str], mapping: dict[str, int]) -> list[str]:\n    return items\n"
    assert _syntax_errors_from_source(source) == []
    assert _import_roots(source) == []


def _syntax_errors_from_source(source):
    try:
        _parse_py39(source)
    except SyntaxError as exc:
        return ["%s: %s" % (exc.lineno, exc.msg)]
    return []


def test_scanner_rejects_newer_than_py39_syntax():
    assert _syntax_errors_from_source("match value:\n    case 1:\n        pass\n")
    assert _syntax_errors_from_source("try:\n    pass\nexcept* ValueError:\n    pass\n")


# --------------------------------------------------------------------- DCC 隔离
def test_core_has_no_real_dcc_imports():
    errors = []
    for path in _core_files_excluding_mocks():
        for lineno, module in _dcc_imports(_read(path), filename=path):
            errors.append("%s:%s: import %s" % (_rel(path), lineno, module))
    assert errors == [], "tak/core 出现真实 DCC 导入：\n" + "\n".join(errors)


def test_detector_ignores_comments_and_docstrings():
    source = (
        '"""示例：import maya.cmds 只出现在文档里。"""\n'
        "# import pymxs\n"
        "VALUE = 'from unreal import EditorAssetLibrary'\n"
    )
    assert _dcc_imports(source) == []


def test_detector_finds_each_dcc_import_form():
    source = (
        "import maya.cmds\n"
        "from pymxs import runtime\n"
        "import pyfbsdk as fb\n"
        "from unreal import EditorAssetLibrary\n"
        "import os\n"
        "from . import unreal_mock\n"
    )
    found = _dcc_imports(source)
    assert [module for _, module in found] == ["maya.cmds", "pymxs", "pyfbsdk", "unreal"]
