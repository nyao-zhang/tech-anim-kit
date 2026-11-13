# -*- coding: utf-8 -*-
"""发布元数据一致性护栏（P6.4）。

这类测试不验证功能，只验证**版本与清单没有撒谎**。它们存在的理由很具体：
批次 3 结束时 README 写着 `0.2.0`，而 `tak/version.py` 还停在 `0.1.0`，
`/api/health` 也一直在报旧版本号 —— 没人发现，因为没有任何东西拦得住它。

同一条链路上还有几个更容易出问题的地方，一并锁住：
- 插件目录名与 ``manifest.json`` 的 ``id`` 不一致（发现器按 id 注册，改名就会静默失联）；
- ``min_version`` 里出现 ``dcc`` 列表之外的键（拼写错误会让版本门槛永远不生效）；
- README 里指向 ``docs/*.md`` 的链接指向不存在的文件。
"""
from __future__ import annotations

import io
import json
import os
import re

import tak
from tak.version import SCHEMA_VERSION, __version__

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLUGIN_ROOT = os.path.join(ROOT, "tools")
EXPECTED_PLUGIN_COUNT = 10
KNOWN_DCCS = {"maya", "max", "mobu", "unreal"}


def _read(*parts: str) -> str:
    with io.open(os.path.join(ROOT, *parts), encoding="utf-8") as fh:
        return fh.read()


def _plugin_dirs():
    for name in sorted(os.listdir(PLUGIN_ROOT)):
        path = os.path.join(PLUGIN_ROOT, name)
        if name.startswith("_") or not os.path.isdir(path):
            continue
        if os.path.isfile(os.path.join(path, "manifest.json")):
            yield name, path


def _manifest(path: str):
    with io.open(os.path.join(path, "manifest.json"), encoding="utf-8") as fh:
        return json.load(fh)


# --------------------------------------------------------------------------- #
# 版本
# --------------------------------------------------------------------------- #
def test_version_is_semver():
    assert re.match(r"^\d+\.\d+\.\d+$", __version__), __version__
    assert re.match(r"^\d+\.\d+\.\d+$", SCHEMA_VERSION), SCHEMA_VERSION


def test_package_exposes_same_version():
    assert tak.__version__ == __version__


def test_readme_declares_current_version():
    head = "\n".join(_read("README.md").splitlines()[:10])
    assert "`%s`" % __version__ in head, "README 首段版本号与 tak/version.py 不一致"


def test_changelog_has_current_version_section():
    changelog = _read("CHANGELOG.md")
    assert "## [%s]" % __version__ in changelog
    # 历史版本必须保留（版本日志是减法容易出事的文档）
    for older in ("0.2.0", "0.1.0"):
        assert "## [%s]" % older in changelog


def test_version_is_not_hardcoded_outside_version_module():
    """除 ``tak/version.py`` 外，tak 包内不得出现具体版本号字面量。"""
    offenders = []
    for base, dirs, files in os.walk(os.path.join(ROOT, "tak")):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        for name in files:
            if not name.endswith(".py") or name == "version.py":
                continue
            path = os.path.join(base, name)
            source = io.open(path, encoding="utf-8").read()
            for literal in ('"%s"' % __version__, "'%s'" % __version__):
                if literal in source:
                    offenders.append((os.path.relpath(path, ROOT), literal))
    assert not offenders, offenders


def test_web_health_reports_current_version():
    from tak.ui.web import server
    status, payload = server.handle_api("GET", "/api/health")
    assert status == 200
    assert payload["version"] == __version__


# --------------------------------------------------------------------------- #
# 插件清单
# --------------------------------------------------------------------------- #
def test_expected_plugin_count():
    found = [name for name, _path in _plugin_dirs()]
    assert len(found) == EXPECTED_PLUGIN_COUNT, found


def test_every_manifest_has_required_fields():
    required = ("id", "name", "description", "dcc", "entry")
    for name, path in _plugin_dirs():
        manifest = _manifest(path)
        for key in required:
            assert manifest.get(key), "%s 缺字段 %s" % (name, key)
        assert isinstance(manifest["dcc"], list) and manifest["dcc"], name


def test_manifest_id_matches_directory():
    for name, path in _plugin_dirs():
        assert _manifest(path)["id"] == name, name


def test_manifest_entry_matches_id():
    for name, path in _plugin_dirs():
        assert _manifest(path)["entry"] == name, name


def test_manifest_dcc_values_are_known():
    for name, path in _plugin_dirs():
        unknown = set(_manifest(path)["dcc"]) - KNOWN_DCCS
        assert not unknown, "%s 含未知 DCC: %s" % (name, unknown)


def test_min_version_keys_are_subset_of_dcc():
    for name, path in _plugin_dirs():
        manifest = _manifest(path)
        min_version = manifest.get("min_version") or {}
        assert isinstance(min_version, dict), name
        extra = set(min_version) - set(manifest["dcc"])
        assert not extra, "%s 的 min_version 含 dcc 之外的键: %s" % (name, extra)


def test_ai_plugins_do_not_register_global_rules():
    """AI 类插件不得带 rules 字段：否则会污染 40 个 fixtures 的判定基线。"""
    ai_plugins = ("agent_console", "defect_inspector", "mldeformer_factory")
    for name, path in _plugin_dirs():
        manifest = _manifest(path)
        if name in ai_plugins:
            assert not manifest.get("rules"), "%s 不应声明 rules" % name


def test_core_tool_id_matches_manifest():
    for name, path in _plugin_dirs():
        core = os.path.join(path, "core.py")
        assert os.path.isfile(core), "%s 缺 core.py" % name
        match = re.search(r'^TOOL_ID\s*=\s*[\'"]([^\'"]+)[\'"]',
                          io.open(core, encoding="utf-8").read(), re.M)
        assert match, "%s/core.py 未定义 TOOL_ID" % name
        assert match.group(1) == name


def test_plugins_are_discoverable_by_registry():
    from tak.core.registry import discover
    found = {plugin.id for plugin in discover()}
    for name, _path in _plugin_dirs():
        assert name in found, "%s 未被 registry 发现" % name


# --------------------------------------------------------------------------- #
# 文档链接
# --------------------------------------------------------------------------- #
def test_readme_doc_links_exist():
    readme = _read("README.md")
    missing = []
    for target in re.findall(r"\]\((docs/[A-Za-z0-9_./-]+\.md)\)", readme):
        if not os.path.isfile(os.path.join(ROOT, target)):
            missing.append(target)
    assert not missing, missing


def test_docs_referenced_reports_exist():
    for name in ("BATCH2_REPORT.md", "BATCH3_REPORT.md", "BATCH4_REPORT.md",
                 "RELEASE.md",
                 "VALIDATION.md", "WEB_PANEL.md", "PLUGIN_GUIDE.md",
                 "COMPATIBILITY.md", "LEGACY_MAPPING.md"):
        assert os.path.isfile(os.path.join(ROOT, "docs", name)), name
