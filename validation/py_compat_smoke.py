# -*- coding: utf-8 -*-
"""跨 Python 版本冒烟验证：不依赖 pytest、不依赖第三方库、不依赖 DCC GUI。

用法::

    <解释器> validation/py_compat_smoke.py

用于验证 ``tak`` 核心层在下列解释器下都能导入并跑通关键路径：

- Maya 2023 → Python 3.9
- Maya 2024 → Python 3.10
- UE 5.8 内置 Python / Maya 2026 → Python 3.11
- 开发机 Python → 3.13

与 CI 的 3.9/3.10/3.11/3.13 矩阵一一对应；CI 跑 pytest，本脚本跑真机解释器。
"""
from __future__ import annotations

import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

_MODULE_FOR = {
    "maya": "maya",
    "max": "pymxs",
    "mobu": "pyfbsdk",
    "unreal": "unreal",
}
_BLOCKING = {"error", "blocker"}


def _fixture(kind, name):
    path = os.path.join(ROOT, "fixtures", kind, name)
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def _severity(finding):
    value = finding.severity
    return value.value if hasattr(value, "value") else str(value)


def main():
    checks = []

    def record(name, detail=""):
        checks.append((name, detail))
        print("  [ok] %-22s %s" % (name, detail))

    import tak
    record("tak.version", tak.__version__)

    from tak.core.spec import load_spec, validate
    assert validate(load_spec()) == [], "默认规范校验失败"
    record("spec.validate")

    # 核心模块整体可导入（无 DCC、无第三方依赖）
    import tak.core.diff  # noqa: F401
    import tak.core.dsl  # noqa: F401
    import tak.core.registry  # noqa: F401
    import tak.core.report  # noqa: F401
    record("core.modules", "spec/rules/diff/dsl/report/registry")

    from tak.core.rules import evaluate_snapshot, list_rules
    rules = list_rules()
    assert len(rules) >= 10, "内置规则不足"
    record("rules.count", str(len(rules)))

    good = _fixture("good", "good_001.json")
    bad = _fixture("bad", "bad_001.json")
    good_report = evaluate_snapshot(good["snapshot"], dcc="maya")
    bad_report = evaluate_snapshot(bad["snapshot"], dcc="maya")
    assert not [f for f in good_report.findings if _severity(f) in _BLOCKING], "好场景误报"
    assert [f for f in bad_report.findings if _severity(f) in _BLOCKING], "坏场景漏报"
    assert good_report.to_markdown() and good_report.to_json()
    record("rules.fixtures", "good=pass bad=blocked")

    from tak.adapters import NullAdapter
    from tak.core.dsl import (ACTION_REGISTRY, ActionRegistry, DSLError,
                              ExecutionPlan)  # noqa: F401

    # 框架不预置动作，函数白名单由各工具插件注册；用独立实例验证安全链路。
    assert isinstance(ACTION_REGISTRY.names, list)
    calls = []

    def _echo(adapter, value=0):
        calls.append(value)

    registry = ActionRegistry()
    registry.register("smoke.echo", _echo, permissions=[], description="冒烟动作",
                      parameters={"value": {"type": int, "required": True,
                                            "min": 0, "max": 10}})
    plan = registry.build_plan(
        {"actions": [{"tool": "smoke.echo", "params": {"value": 3}}]})
    preview = registry.execute(plan, NullAdapter(), dry_run=True)
    assert preview["dry_run"] and not calls, "dry-run 产生了副作用"
    result = registry.execute(plan, NullAdapter(), dry_run=False, confirm=True)
    assert result["committed"] and calls == [3], "DSL 安全执行失败"
    try:
        registry.build_plan({"actions": [{"tool": "not.registered", "params": {}}]})
    except DSLError:
        pass
    else:
        raise AssertionError("未注册动作未被拒绝")
    record("dsl.safe_exec", "register/preview/execute/reject")

    from tak.core.mocks import available_dccs, install_mock, uninstall_mock
    for dcc in available_dccs():
        module_name = _MODULE_FOR[dcc]
        before = module_name in sys.modules
        handle = install_mock(dcc, snapshot=good["snapshot"])
        assert handle.installed(), "mock 安装失败: %s" % dcc
        assert module_name in sys.modules
        assert uninstall_mock(), "mock 卸载失败: %s" % dcc
        assert (module_name in sys.modules) == before, "sys.modules 未还原: %s" % dcc
    record("mocks.install", ",".join(available_dccs()))

    from tak.core.ai import get_provider, provider_for_task
    assert provider_for_task("vision") == "MiniMax-M3.1-Flash"
    provider = get_provider(task="explain", offline=True)
    messages = [{"role": "user", "content": "ping"}]
    first = provider.chat(messages)
    assert first and first == provider.chat(messages), "离线 provider 输出不可复现"
    record("ai.offline", provider.model)

    print("RESULT: PASS python=%s checks=%d" % (sys.version.split()[0], len(checks)))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print("RESULT: FAIL python=%s error=%r" % (sys.version.split()[0], exc))
        raise
