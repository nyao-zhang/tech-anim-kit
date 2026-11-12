# -*- coding: utf-8 -*-
"""报告模型：同一份结果渲染为 JSON / Markdown / HTML。

HTML 使用 tak/ui/theme.css 的类名，与 Qt 端共用一套设计 token。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List

from tak.core.rules.base import Finding, Severity
from tak.version import __version__

_BLOCKING = {Severity.ERROR.value, Severity.BLOCKER.value}


@dataclass
class Report:
    title: str
    dcc: str
    findings: List[Finding] = field(default_factory=list)
    spec_version: str = "1.0.0"
    metadata: Dict[str, Any] = field(default_factory=dict)

    def add(self, finding: Finding) -> "Report":
        self.findings.append(finding)
        return self

    def extend(self, findings: List[Finding]) -> "Report":
        self.findings.extend(findings)
        return self

    def filter_by_severity(self, severity: Any = None) -> List[Finding]:
        """按一个 severity 或 severity 集合过滤；None 表示全部。"""
        if severity is None:
            return list(self.findings)
        if isinstance(severity, (str, Severity)):
            values = [severity]
        else:
            try:
                values = list(severity)
            except TypeError:
                values = [severity]
        requested = {_severity_value(value) for value in values}
        valid = {item.value for item in Severity}
        unknown = sorted(requested - valid)
        if unknown:
            raise ValueError("未知 severity: %s" % unknown)
        return [item for item in self.findings if item.severity.value in requested]

    def counts(self, severity: Any = None) -> Dict[str, int]:
        result = {s.value: 0 for s in Severity}
        for item in self.filter_by_severity(severity):
            result[item.severity.value] += 1
        return result

    def summary(self, severity: Any = None) -> Dict[str, Any]:
        """返回可 JSON 序列化的统计摘要，可与过滤条件配合使用。"""
        findings = self.filter_by_severity(severity)
        return {
            "total": len(findings),
            "counts": self.counts(severity),
            "passed": all(item.severity.value not in _BLOCKING for item in findings),
        }

    @property
    def passed(self) -> bool:
        return all(f.severity.value not in _BLOCKING for f in self.findings)

    def to_dict(self, severity: Any = None) -> Dict[str, Any]:
        findings = self.filter_by_severity(severity)
        summary = self.summary(severity)
        return {
            "title": self.title,
            "dcc": self.dcc,
            "toolkit_version": __version__,
            "spec_version": self.spec_version,
            "passed": summary["passed"],
            "counts": summary["counts"],
            "metadata": self.metadata,
            "findings": [f.to_dict() for f in findings],
        }

    def to_json(self, indent: int = 2, severity: Any = None) -> str:
        return json.dumps(self.to_dict(severity), ensure_ascii=False, indent=indent)

    def to_markdown(self, severity: Any = None) -> str:
        findings = self.filter_by_severity(severity)
        summary = self.summary(severity)
        lines = ["# %s" % _md(self.title), "",
                 "- DCC: %s" % _md_code(self.dcc),
                 "- 规范版本: %s" % _md_code(self.spec_version),
                 "- 结论: **%s**" % ("通过" if summary["passed"] else "不通过"),
                 "- 统计: %s" % ", ".join(
                     "%s=%d" % kv for kv in summary["counts"].items()), ""]
        if not findings:
            lines.append("无异常。" if severity is None else "无匹配项。")
        for item in findings:
            node = (" %s" % _md_code(item.node)) if item.node else ""
            hint = ("（修复建议：%s）" % _md(item.fix_hint)) if item.fix_hint else ""
            lines.append("- **[%s]**%s %s — %s%s" % (
                item.severity.value.upper(), node, _md_code(item.rule_id),
                _md(item.message), hint))
        return "\n".join(lines) + "\n"

    def to_html(self, theme_href: str = "tak/ui/theme.css",
                severity: Any = None) -> str:
        findings = self.filter_by_severity(severity)
        summary = self.summary(severity)
        rows = []
        for item in findings:
            hint = ('<span class="tak-hint">%s</span>' % _escape(item.fix_hint)) if item.fix_hint else ""
            rows.append(
                '<tr class="tak-row tak-sev-%s"><td class="tak-badge">%s</td>'
                '<td><code>%s</code></td><td><code>%s</code></td><td>%s%s</td></tr>' % (
                    item.severity.value, item.severity.value.upper(),
                    _escape(item.rule_id), _escape(item.node),
                    _escape(item.message), hint))
        state = "tak-state-pass" if summary["passed"] else "tak-state-fail"
        label = "通过" if summary["passed"] else "不通过"
        empty = "无异常" if severity is None else "无匹配项"
        return (
            '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
            '<link rel="stylesheet" href="%s"><title>%s</title></head><body>'
            '<main class="tak-panel"><header class="tak-header"><h1>%s</h1>'
            '<span class="tak-state %s">%s</span></header>'
            '<section class="tak-meta">DCC %s · 规范 %s · %s</section>'
            '<table class="tak-table"><thead><tr><th>级别</th><th>规则</th>'
            '<th>节点</th><th>说明</th></tr></thead><tbody>%s</tbody></table>'
            '</main></body></html>' % (
                _escape(theme_href), _escape(self.title), _escape(self.title), state, label,
                _escape(self.dcc), _escape(self.spec_version),
                ", ".join("%s=%d" % kv for kv in summary["counts"].items()),
                "".join(rows) or '<tr><td colspan="4">%s</td></tr>' % empty))


def _severity_value(value: Any) -> str:
    return value.value if isinstance(value, Severity) else str(value)


def _text(text: Any) -> str:
    return "" if text is None else str(text)


def _escape(text: Any) -> str:
    return (_text(text).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;").replace("'", "&#x27;"))


def _one_line(text: Any) -> str:
    return _text(text).replace("\r\n", " ").replace("\r", " ").replace("\n", " ")


def _md(text: Any) -> str:
    value = _one_line(text).replace("\\", "\\\\")
    for char in "`*_{}[]()#+-.!|":
        value = value.replace(char, "\\" + char)
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _md_code(text: Any) -> str:
    value = _one_line(text)
    if not value:
        return ""
    value = value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    runs = re.findall(r"`+", value)
    delimiter = "`" * (max((len(run) for run in runs), default=0) + 1)
    padding = " " if value.startswith(("`", " ")) or value.endswith(("`", " ")) else ""
    return "%s%s%s%s%s" % (delimiter, padding, value, padding, delimiter)
