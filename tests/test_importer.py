# -*- coding: utf-8 -*-
"""第三方工具导入器（tak.core.importer / importer_runtime）测试。

两条主线：

* **看不跑**：``analyze`` 只做 ast 解析。测试里放一个「一 import 就崩」的脚本，
  确认分析阶段不会把它执行起来 —— 这是导入功能的安全边界。
* **不假通过**：没有 TAK 运行契约的脚本，跑完必须是「未判定 / 不通过」，
  不能因为「没抛异常」就显示绿灯。
"""
from __future__ import annotations

import json
import os

import pytest

from tak.core import importer, registry, runner

SIMPLE = '''# -*- coding: utf-8 -*-
"""场景清理小工具。

- 删除空组
- 归一化权重
"""
import maya.cmds as cmds


def find_empty_groups():
    """找出没有子节点的 transform。"""
    return [g for g in (cmds.ls(type="transform") or [])
            if not cmds.listRelatives(g, children=True)]


def main():
    print("cleaned", len(find_empty_groups()))
'''

WITH_UI = '''# -*- coding: utf-8 -*-
"""带界面的小工具：给控制器上色。"""
import maya.cmds as cmds


def show(dcc="maya", omui=None):
    return "window"


def main():
    return show()
'''

WITH_CONTRACT = '''# -*- coding: utf-8 -*-
"""自带 TAK 契约的脚本。"""
from tak.core.report import Report
from tak.core.rules.base import Finding, Severity


def run_report(ctx):
    report = Report(title="imported checker", dcc="maya")
    report.findings.append(Finding(
        rule_id="imported.custom", severity=Severity.WARNING,
        message="脚本自己给的结论"))
    return report
'''

BROKEN_IMPORT = '''# -*- coding: utf-8 -*-
"""这个脚本一 import 就会崩，用来证明分析阶段不会执行它。"""
raise RuntimeError("import 时炸了")
'''

SYNTAX_ERROR = '''def broken(:
    pass
'''


@pytest.fixture()
def sandbox(tmp_path, monkeypatch):
    """每个用例一个独立 TAK_HOME，导入不污染真实配置。"""
    monkeypatch.setenv("TAK_HOME", str(tmp_path))
    source_dir = tmp_path / "src"
    source_dir.mkdir()

    def _write(name, text):
        path = source_dir / name
        path.write_text(text, encoding="utf-8")
        return str(path)

    yield _write
    for item in importer.list_imported():
        importer.uninstall(item["tool_id"])


# --------------------------------------------------------------------------
# 分析：只看不跑
# --------------------------------------------------------------------------
def test_analyze_never_executes_the_target(sandbox):
    path = sandbox("boom.py", BROKEN_IMPORT)
    result = importer.analyze(path)
    # 目标里是顶层 raise：真执行了这里就不可能拿到分析结果。
    assert result["ok"] is True
    assert result["docstring"].startswith("这个脚本一 import 就会崩")
    assert result["has_report_contract"] is False


def test_analyze_reads_docstring_bullets_and_functions(sandbox):
    result = importer.analyze(sandbox("cleanup.py", SIMPLE))
    assert result["ok"] is True
    assert result["dcc"] == ["maya"]
    assert {item["name"] for item in result["functions"]} >= {
        "find_empty_groups", "main"}
    assert result["host_calls"] == ["listRelatives", "ls"]
    assert "删除空组" in result["docstring"]


def test_analyze_does_not_confuse_local_calls_with_host_calls(sandbox):
    """脚本自己定义的函数不是宿主调用 —— 早期实现把 print() 也算进去了。"""
    result = importer.analyze(sandbox("cleanup.py", SIMPLE))
    assert "print" not in result["host_calls"]
    assert "find_empty_groups" not in result["host_calls"]


def test_analyze_detects_ui_and_contract(sandbox):
    assert importer.analyze(sandbox("ui.py", WITH_UI))["has_show"] is True
    contract = importer.analyze(sandbox("contract.py", WITH_CONTRACT))
    assert contract["has_report_contract"] is True


def test_analyze_reports_syntax_error_instead_of_raising(sandbox):
    result = importer.analyze(sandbox("bad.py", SYNTAX_ERROR))
    assert result["ok"] is False
    assert any("语法错误" in item for item in result["errors"])


def test_analyze_rejects_non_python_file(sandbox):
    result = importer.analyze(sandbox("readme.txt", "hello"))
    assert result["ok"] is False
    assert any(".py" in item for item in result["errors"])


def test_analyze_directory_picks_the_most_capable_entry(sandbox, tmp_path):
    sandbox("helper.py", SIMPLE)
    sandbox("tool.py", WITH_UI)
    result = importer.analyze(str(tmp_path / "src"))
    assert result["kind"] == "dir"
    assert os.path.basename(result["entry"]) == "tool.py"


# --------------------------------------------------------------------------
# manifest 草稿
# --------------------------------------------------------------------------
def test_draft_manifest_is_usable_without_any_ai(sandbox):
    draft = importer.draft_manifest(importer.analyze(sandbox("cleanup.py", SIMPLE)))
    assert draft["id"] == "cleanup"
    assert draft["name"] == "Cleanup"
    assert draft["summary"] == "场景清理小工具。"
    assert draft["features"] == ["删除空组", "归一化权重"]
    assert draft["ui"] == "none"
    # 只有读取调用（ls / listRelatives）就不该给写权限
    assert draft["permissions"] == ["scene.read"]
    assert draft["min_version"] == {"maya": "2023"}


def test_draft_manifest_marks_ui_and_usage_for_ui_scripts(sandbox):
    draft = importer.draft_manifest(importer.analyze(sandbox("ui.py", WITH_UI)))
    assert draft["ui"] == "qt"
    assert any("打开工具界面" in item for item in draft["usage"])


def test_draft_manifest_says_honestly_when_there_is_no_contract(sandbox):
    draft = importer.draft_manifest(importer.analyze(sandbox("cleanup.py", SIMPLE)))
    assert any("不会产出判定结论" in item for item in draft["usage"])


# --------------------------------------------------------------------------
# AI 补全（失败必须降级，不能挡住导入）
# --------------------------------------------------------------------------
def test_enrich_without_provider_keeps_the_draft(sandbox):
    draft = importer.draft_manifest(importer.analyze(sandbox("cleanup.py", SIMPLE)))
    result = importer.enrich(draft, {"docstring": ""}, provider=None)
    assert result["ai"] is False
    assert result["manifest"]["summary"] == draft["summary"]


class _FakeProvider:
    def __init__(self, reply):
        self.reply = reply

    def chat(self, messages, **kwargs):
        return self.reply


def test_enrich_applies_ai_fields_and_labels_them(sandbox):
    draft = importer.draft_manifest(importer.analyze(sandbox("cleanup.py", SIMPLE)))
    provider = _FakeProvider(json.dumps({
        "summary": "一键清理空组与权重问题",
        "features": ["删除空组", "归一化权重"],
        "usage": ["选中骨架", "运行清理"],
        "tags": ["清理"],
    }, ensure_ascii=False))
    result = importer.enrich(draft, {"docstring": "x"}, provider=provider)
    assert result["ai"] is True
    assert result["manifest"]["summary"] == "一键清理空组与权重问题"
    assert set(result["fields"]) == {"summary", "features", "usage", "tags"}


def test_enrich_survives_non_json_reply(sandbox):
    draft = importer.draft_manifest(importer.analyze(sandbox("cleanup.py", SIMPLE)))
    result = importer.enrich(draft, {"docstring": "x"},
                             provider=_FakeProvider("抱歉，我不能这么做。"))
    assert result["ai"] is False
    assert "JSON" in result["note"]
    assert result["manifest"]["summary"] == draft["summary"]


def test_enrich_survives_provider_exception(sandbox):
    class Boom:
        def chat(self, messages, **kwargs):
            raise RuntimeError("network down")

    draft = importer.draft_manifest(importer.analyze(sandbox("cleanup.py", SIMPLE)))
    result = importer.enrich(draft, {}, provider=Boom())
    assert result["ai"] is False
    assert "RuntimeError" in result["error"]


# --------------------------------------------------------------------------
# 安装 / 发现 / 运行 / 卸载
# --------------------------------------------------------------------------
def test_install_creates_a_discoverable_plugin(sandbox):
    result = importer.install(sandbox("cleanup.py", SIMPLE))
    assert result["ok"] is True
    tool_id = result["tool_id"]
    assert tool_id == "cleanup"
    for name in ("manifest.json", "core.py", "target.py", "import_meta.json"):
        assert os.path.isfile(os.path.join(result["dir"], name))
    # 没有 show() 就不该生成 ui_qt.py，否则「打开工具界面」会点出一个空窗口
    assert not os.path.isfile(os.path.join(result["dir"], "ui_qt.py"))

    ids = {plugin.id for plugin in registry.discover()}
    assert tool_id in ids
    assert tool_id in {item["id"] for item in runner.list_tools(dcc="maya")}


def test_install_writes_ui_shim_only_when_needed(sandbox):
    result = importer.install(sandbox("ui.py", WITH_UI))
    assert result["ok"] is True
    assert os.path.isfile(os.path.join(result["dir"], "ui_qt.py"))
    assert result["manifest"]["ui"] == "qt"


def test_install_refuses_to_overwrite_by_default(sandbox):
    first = importer.install(sandbox("cleanup.py", SIMPLE))
    again = importer.install(sandbox("cleanup.py", SIMPLE))
    assert first["ok"] is True and again["ok"] is False
    assert "覆盖" in again["error"]
    forced = importer.install(sandbox("cleanup.py", SIMPLE), overwrite=True)
    assert forced["ok"] is True


def test_install_keeps_user_edited_manifest_fields(sandbox):
    analysis = importer.analyze(sandbox("cleanup.py", SIMPLE))
    draft = importer.draft_manifest(analysis)
    draft["summary"] = "我手改过的说明"
    draft["category"] = ["rigging"]
    result = importer.install(sandbox("cleanup.py", SIMPLE),
                              manifest=draft, analysis=analysis)
    with open(os.path.join(result["dir"], "manifest.json"), encoding="utf-8") as fh:
        stored = json.load(fh)
    assert stored["summary"] == "我手改过的说明"
    assert stored["category"] == ["rigging"]


def test_installed_script_without_contract_does_not_fake_a_pass(sandbox):
    result = importer.install(sandbox("cleanup.py", SIMPLE))
    # 目标脚本 import maya.cmds，不在 Maya 里 —— 必须报错而不是「通过」
    run = runner.run_tool(result["tool_id"], {"dcc": "maya", "joints": {}},
                          dcc="maya", adapter=runner.adapter_for("maya"))
    assert run.report.passed is False
    assert [item.rule_id for item in run.report.findings] == ["imported.load_error"]


def test_installed_script_with_contract_is_wrapped(sandbox):
    result = importer.install(sandbox("contract.py", WITH_CONTRACT))
    run = runner.run_tool(result["tool_id"], {"dcc": "maya", "joints": {}},
                          dcc="maya", adapter=runner.adapter_for("maya"))
    assert [item.rule_id for item in run.report.findings] == ["imported.custom"]
    assert run.report.metadata["imported"] is True


def test_uninstall_removes_only_imported_tools(sandbox):
    result = importer.install(sandbox("cleanup.py", SIMPLE))
    assert importer.uninstall(result["tool_id"])["ok"] is True
    assert importer.list_imported() == []
    # 内置工具目录绝不能被动到
    assert importer.uninstall("qa_inspector")["ok"] is False


def test_install_result_is_listed_with_capabilities(sandbox):
    importer.install(sandbox("ui.py", WITH_UI))
    listed = importer.list_imported()
    assert len(listed) == 1
    assert listed[0]["has_show"] is True
    assert listed[0]["has_report_contract"] is False
    from tak.core import importer_runtime
    caps = importer_runtime.capabilities(listed[0]["dir"])
    assert caps["has_show"] is True
    assert caps["entrypoints"] == ["show", "main"]


def test_executed_but_undetermined_never_counts_as_a_pass(sandbox, monkeypatch):
    """跑完但没有契约 → passed 必须是 False。

    真机探针抓到过反例：severity 用 WARNING 时 passed 仍为 True，结论里却写着
    「未判定」，于是面板给一片绿灯 —— 这正是本项目一直在修的假通过。
    """
    import sys
    import types

    # 造一个不依赖宿主的脚本，让它在纯 Python 里也能跑起来
    plain = sandbox("plain_tool.py", (
        '"""纯工具：打印一句话。"""\n'
        "def main():\n"
        '    print("done")\n'
    ))
    result = importer.install(plain)
    assert result["ok"] is True
    run = runner.run_tool(result["tool_id"], {"dcc": "maya", "joints": {}},
                          dcc="maya", adapter=runner.adapter_for("maya"))
    ids = [item.rule_id for item in run.report.findings]
    assert ids == ["imported.no_contract"]
    assert run.report.passed is False
    assert run.report.metadata.get("undetermined") is True
