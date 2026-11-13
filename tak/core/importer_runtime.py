# -*- coding: utf-8 -*-
"""导入插件的运行时适配层：把「谁的脚本」接进 TAK 的统一运行契约。

一份第三方脚本可能长成三种样子，这里分别兜住：

===============  ==========================================================
有 TAK 契约       目标里已有 ``run_report(ctx)`` / ``run(ctx)``：直接转调，
                  TAK 的判定、报告渲染、结果表全部照旧生效。
只有 UI           目标里有 ``show()`` / ``main()``：不假装能出报告，报告里
                  明确写「该脚本没有 TAK 契约，已打开其界面」，避免用户以为
                  「跑完了没问题」。
两者都没有         通用执行：捕获 stdout/stderr 与异常，原样回报。脚本报错
                  就是 ERROR，不是「通过」。
===============  ==========================================================

刻意不做的事
------------
* **不猜判定**：脚本没有返回值就没有结论，绝不根据「没抛异常」推断「检查通过」；
* **不改脚本**：目标文件只读执行，TAK 自己的元信息写在 ``import_meta.json``；
* **不在导入时执行**：导入只做 ``ast`` 解析与文件复制，运行是用户显式触发的。
"""
from __future__ import annotations

import importlib.util
import io
import json
import os
import sys
import traceback
from typing import Any, Dict, Optional

META_NAME = "import_meta.json"
TARGET_NAME = "target.py"


# --------------------------------------------------------------------------- #
# 元信息与目标加载
# --------------------------------------------------------------------------- #
def read_meta(plugin_dir: str) -> Dict[str, Any]:
    path = os.path.join(plugin_dir, META_NAME)
    if not os.path.isfile(path):
        return {}
    try:
        with io.open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def target_path(plugin_dir: str) -> str:
    meta = read_meta(plugin_dir)
    name = str(meta.get("target") or TARGET_NAME)
    return os.path.join(plugin_dir, name)


def load_target(plugin_dir: str, module_tag: str = "") -> Any:
    """按文件路径加载目标脚本（只执行一次，之后复用 sys.modules）。"""
    path = target_path(plugin_dir)
    if not os.path.isfile(path):
        raise FileNotFoundError("导入的脚本不存在: %s" % path)
    name = "tak_imported_%s" % (module_tag or os.path.basename(plugin_dir) or "tool")
    cached = sys.modules.get(name)
    if cached is not None:
        return cached
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError("无法加载 %s" % path)
    module = importlib.util.module_from_spec(spec)
    # 让脚本能 import 同目录的兄弟模块（很多脚本习惯相对目录）
    directory = os.path.dirname(path)
    if directory not in sys.path:
        sys.path.insert(0, directory)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def capabilities(plugin_dir: str) -> Dict[str, Any]:
    """这个导入插件到底能干什么 —— UI 与报告都要说清楚，不能含糊。"""
    meta = read_meta(plugin_dir)
    return {
        "tool_id": os.path.basename(plugin_dir),
        "has_report_contract": bool(meta.get("has_report_contract")),
        "has_show": bool(meta.get("has_show")),
        "entrypoints": list(meta.get("entrypoints") or []),
        "source": str(meta.get("source") or ""),
        "imported_at": str(meta.get("imported_at") or ""),
        "ai_fields": list(meta.get("ai_fields") or []),
    }


# --------------------------------------------------------------------------- #
# 运行契约
# --------------------------------------------------------------------------- #
def run_report(ctx: Any, plugin_dir: str) -> Any:
    """统一入口：优先转调目标契约，否则通用执行。永不抛异常给调用方。"""
    from tak.core.report import Report
    from tak.core.rules.base import Finding, Severity

    meta = read_meta(plugin_dir)
    title = str(meta.get("name") or os.path.basename(plugin_dir))
    report = Report(title=title, dcc=str(getattr(ctx, "dcc", "maya") or "maya"),
                    findings=[])
    report.metadata["imported"] = True
    report.metadata["import_meta"] = capabilities(plugin_dir)

    try:
        module = load_target(plugin_dir, module_tag=str(meta.get("id") or ""))
    except Exception as exc:
        report.findings.append(Finding(
            rule_id="imported.load_error", severity=Severity.ERROR,
            message="导入的脚本无法加载：%s: %s" % (type(exc).__name__, exc),
            fix_hint="确认脚本没有依赖未安装的库；依赖需要在宿主 Python 里能 import。",
        ))
        return report

    # 1) 目标自带契约 —— 直接转调，TAK 的判定链路完整生效。
    for name in ("run_report", "run"):
        func = getattr(module, name, None)
        if not callable(func):
            continue
        try:
            if name == "run_report":
                produced = func(ctx)
            else:
                produced = func(ctx)
        except Exception as exc:
            report.findings.append(Finding(
                rule_id="imported.run_error", severity=Severity.ERROR,
                message="脚本执行失败：%s: %s" % (type(exc).__name__, exc),
                fix_hint="查看日志台的完整 traceback；脚本自身的报错不会被 TAK 吞掉。",
            ))
            report.metadata["traceback"] = traceback.format_exc()[-2000:]
            return report
        if isinstance(produced, Report):
            produced.metadata.setdefault("imported", True)
            produced.metadata.setdefault("import_meta", capabilities(plugin_dir))
            return produced
        wrapped = _report_from_findings(produced, report)
        if wrapped is not None:
            return wrapped
        report.findings.append(Finding(
            rule_id="imported.contract_mismatch", severity=Severity.WARNING,
            message="脚本的 %s() 返回了 %r，既不是 Report 也不是 Finding 列表。"
                    % (name, type(produced).__name__),
            fix_hint="返回 Report 或 Finding 列表才能进结果表；否则请改用界面运行。",
        ))
        return report

    # 2) 只有界面 —— 明确说明「这不是一次检查」，不伪造结论。
    if callable(getattr(module, "show", None)):
        # 必须是 ERROR 级。Report.passed 只看 ERROR / BLOCKER 是否出现
        # （见 tak/core/report/model.py 的 _BLOCKING），用 INFO 或 WARNING 都会
        # 让结论显示「通过」—— 而这次**一次检查都没跑**。真机探针抓到过这一点：
        # 导入的工具跑完 passed=True，结论里却写着「未判定」，自相矛盾。
        report.findings.append(Finding(
            rule_id="imported.ui_only", severity=Severity.ERROR,
            message="未判定：该脚本只有界面（show()），没有 TAK 运行契约，"
                    "本次未执行任何检查。请点「打开工具界面」使用它。",
            fix_hint="如需进结果表，脚本需暴露 run_report(ctx) 或 run(ctx)。",
        ))
        report.metadata["undetermined"] = True
        return report

    # 3) 通用执行：捕获输出与异常，老老实实回报发生了什么。
    output, error, ok = execute_entry(module, meta)
    report.metadata["stdout"] = output[-4000:]
    if not ok:
        report.findings.append(Finding(
            rule_id="imported.execute_error", severity=Severity.ERROR,
            message="脚本执行抛错：%s" % error.splitlines()[-1] if error else
                    "脚本执行失败",
            fix_hint="查看日志台；脚本没有 TAK 契约，报错细节只能原样转述。",
            data={"traceback": error[-4000:]},
        ))
        return report
    # 同上：执行完毕 ≠ 检查通过。用 ERROR 才能让 passed 变成 False，
    # 否则一个纯工具脚本跑完会得到一片绿灯，而实际上没有任何判定发生。
    report.findings.append(Finding(
        rule_id="imported.no_contract", severity=Severity.ERROR,
        message="未判定：脚本已按原样执行完毕，但它没有 TAK 运行契约，"
                "本次没有产生可判定的检查结论（执行完毕 ≠ 检查通过）。",
        fix_hint="如需判定结果，给脚本加上 run_report(ctx) 或 run(ctx)。",
        data={"stdout_tail": output[-400:]},
    ))
    report.metadata["undetermined"] = True
    return report


def execute_entry(module: Any, meta: Optional[Dict[str, Any]] = None) -> Any:
    """调用目标的 main()/run()；返回 ``(stdout, traceback, ok)``。"""
    data = meta if isinstance(meta, dict) else {}
    func = None
    for name in ("main", "run", "execute"):
        candidate = getattr(module, name, None)
        if callable(candidate):
            func = candidate
            break
    buffer = io.StringIO()
    if func is None:
        return "", "", True   # 模块顶层代码已经执行过，没有可调用的入口
    try:
        from contextlib import redirect_stderr, redirect_stdout
        with redirect_stdout(buffer), redirect_stderr(buffer):
            func()
    except Exception:
        return buffer.getvalue(), traceback.format_exc(), False
    return buffer.getvalue(), "", True


def _report_from_findings(produced: Any, template: Any) -> Any:
    """把 Finding 列表包成 Report；不是列表就返回 None（交给上层报不匹配）。"""
    if not isinstance(produced, (list, tuple)):
        return None
    from tak.core.report import Report
    findings = [item for item in produced
                if hasattr(item, "rule_id") and hasattr(item, "severity")]
    if not findings and produced:
        return None
    report = Report(title=template.title, dcc=template.dcc,
                    findings=list(findings))
    report.metadata.update(template.metadata)
    return report


def show(plugin_dir: str, dcc: str = "maya", omui: Any = None) -> Any:
    """调用目标的 show()/main() 打开它自己的界面；没有就返回 None。"""
    meta = read_meta(plugin_dir)
    try:
        module = load_target(plugin_dir, module_tag=str(meta.get("id") or ""))
    except Exception as exc:
        print("[TAK][imported] 加载失败: %s: %s" % (type(exc).__name__, exc))
        return None
    for name in ("show", "main"):
        func = getattr(module, name, None)
        if not callable(func):
            continue
        try:
            return func()
        except TypeError:
            # 有些脚本的 show 需要 dcc 参数；只在这一种情况下补参重试，
            # 其它异常必须冒出来，否则会把脚本自身的 bug 当成「参数不匹配」。
            try:
                return func(dcc)
            except Exception as exc:
                print("[TAK][imported] 界面打开失败: %s: %s"
                      % (type(exc).__name__, exc))
                return None
        except Exception as exc:
            print("[TAK][imported] 界面打开失败: %s: %s"
                  % (type(exc).__name__, exc))
            return None
    return None
