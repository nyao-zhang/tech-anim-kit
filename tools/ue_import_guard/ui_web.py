# -*- coding: utf-8 -*-
"""UE Import Guard 的 Web 面板片段（不含 <html>，由 Web 服务端嵌入）。

约束
----
* 只使用 ``tak/ui/theme.css`` 暴露的类名（tak-panel / tak-header / tak-meta /
  tak-state / tak-table / tak-row / tak-badge / tak-sev-* / tak-hint）；
* 不写任何字面颜色（十六进制 / rgb() / hsl() / 具名颜色），不引用外链
  （无 <link> / <script> / http(s)://）；
* 所有动态文本都经过 HTML 转义，面板可安全渲染资产路径与 diff 内容。

``render_panel_html`` 接收 ``ToolResult`` / ``Report`` / ``ToolResult.to_dict()``
三种形态，返回 HTML **片段**。
"""
from __future__ import annotations

import html
from typing import Any, Dict, List, Tuple

TOOL_ID = "ue_import_guard"
_SEVERITY_LABEL = {"blocker": "BLOCKER", "error": "ERROR",
                   "warning": "WARNING", "info": "INFO"}
_SEVERITY_CLASS = {"blocker": "tak-sev-blocker", "error": "tak-sev-error",
                   "warning": "tak-sev-warning", "info": "tak-sev-info"}


# --------------------------------------------------------------------------
# 片段渲染
# --------------------------------------------------------------------------
def render_panel_html(result: Any) -> str:
    """渲染完整面板片段：校验结论 + diff 表 + 规则结果 + api_notes。"""
    report, view = _split(result)
    metadata = _metadata(report, view)
    counts = _counts(report, view)
    passed = _passed(report, view)
    title = str(_get(report, "title") or "UE Import Guard 导入校验")
    dcc = str(_get(report, "dcc") or "unreal")
    spec_version = str(_get(report, "spec_version") or "1.0.0")

    state_class = "tak-state-pass" if passed else "tak-state-fail"
    state_label = "通过" if passed else "不通过"
    parts: List[str] = [
        '<section class="tak-panel" data-tool="%s">' % html.escape(TOOL_ID),
        '<header class="tak-header"><h1>%s</h1>'
        '<span class="tak-state %s">%s</span></header>' % (
            html.escape(title), state_class, state_label),
        '<p class="tak-meta">DCC %s · 规范 %s · 规则 %d · %s</p>' % (
            html.escape(dcc), html.escape(spec_version),
            int(metadata.get("rules_count") or 0), _count_text(counts)),
    ]
    parts.append(_validate_section(metadata.get("validate")))
    parts.append(_diff_section(_findings(report, view)))
    parts.append(_findings_section(_findings(report, view)))
    parts.append(_api_notes_section(metadata.get("api_notes")))
    parts.append("</section>")
    return "".join(part for part in parts if part)


def render_report_html(report: Any) -> str:
    """只渲染规则结果表（复用 ``Report.to_html`` 的类名与结构约定）。"""
    report, view = _split(report)
    counts = _counts(report, view)
    passed = _passed(report, view)
    title = str(_get(report, "title") or "UE Import Guard 导入校验")
    state_class = "tak-state-pass" if passed else "tak-state-fail"
    state_label = "通过" if passed else "不通过"
    return (
        '<section class="tak-panel" data-tool="%s">'
        '<header class="tak-header"><h1>%s</h1>'
        '<span class="tak-state %s">%s</span></header>'
        '<p class="tak-meta">DCC %s · 规范 %s · %s</p>%s</section>' % (
            html.escape(TOOL_ID), html.escape(title), state_class, state_label,
            html.escape(str(_get(report, "dcc") or "unreal")),
            html.escape(str(_get(report, "spec_version") or "1.0.0")),
            _count_text(counts),
            _findings_section(_findings(report, view))))


# --------------------------------------------------------------------------
# 各区块
# --------------------------------------------------------------------------
def _validate_section(validate: Any) -> str:
    data = validate if isinstance(validate, dict) else {}
    if not data:
        return ('<section><h2>导入校验</h2><p class="tak-meta">'
                '未提供 sidecar 与 UE 快照，本次跳过导入校验。</p></section>')
    state = "tak-state-pass" if data.get("passed") else "tak-state-fail"
    label = "通过" if data.get("passed") else "不通过"
    if data.get("compared") is False:
        state, label = "tak-state-pass", "已跳过"
    return (
        '<section><h2>导入校验</h2>'
        '<p class="tak-meta">%s<span class="tak-state %s">%s</span></p>'
        '<p class="tak-hint">%s</p></section>' % (
            html.escape(_reason(data)), state, label,
            html.escape("阻断 %d 处 · 差异 %d 处 · 忽略 %s" % (
                int(data.get("blocking_count") or 0),
                int(data.get("diff_count") or 0),
                "、".join(str(item) for item in (data.get("ignored") or [])) or "无"))))


def _diff_section(findings: List[Any]) -> str:
    """从导入校验 Finding 的 data["diff"] 还原 diff 明细表。"""
    rows: List[str] = []
    for item in findings:
        data = _field(item, "data")
        diff = data.get("diff") if isinstance(data, dict) else None
        if not isinstance(diff, dict) or not diff.get("field"):
            continue
        blocking = bool(diff.get("blocking"))
        severity = "tak-sev-error" if blocking else "tak-sev-info"
        rows.append(
            '<tr class="tak-row %s"><td class="tak-badge">%s</td>'
            '<td><code>%s</code></td><td><code>%s</code></td><td>%s</td></tr>' % (
                severity, "BLOCKER" if blocking else "INFO",
                html.escape(str(diff.get("field") or "")),
                html.escape(str(diff.get("joint") or diff.get("before") or "")),
                html.escape(str(diff.get("message") or ""))))
    if not rows:
        return ""
    return (
        '<section><h2>快照 diff</h2>'
        '<table class="tak-table"><thead><tr><th>级别</th><th>字段</th>'
        '<th>对象</th><th>说明</th></tr></thead><tbody>%s</tbody></table></section>'
        % "".join(rows))


def _api_notes_section(notes: Any) -> str:
    if not isinstance(notes, (list, tuple)) or not notes:
        return ""
    rows: List[str] = []
    for item in notes:
        if not isinstance(item, dict):
            continue
        rows.append(
            '<tr class="tak-row tak-sev-info"><td class="tak-badge">%s</td>'
            '<td><code>%s</code></td><td>%s<span class="tak-hint">来源：%s</span></td></tr>' % (
                html.escape(str(item.get("status") or "note")),
                html.escape(str(item.get("symbol") or "")),
                html.escape(str(item.get("note") or "")),
                html.escape(str(item.get("source") or ""))))
    if not rows:
        return ""
    return (
        '<section><h2>API 说明</h2>'
        '<table class="tak-table"><thead><tr><th>状态</th><th>符号</th>'
        '<th>说明</th></tr></thead><tbody>%s</tbody></table></section>' % "".join(rows))


def _findings_section(findings: List[Any]) -> str:
    rows: List[str] = []
    for item in findings:
        severity = _severity(item)
        hint = _text(_field(item, "fix_hint"))
        rows.append(
            '<tr class="tak-row %s"><td class="tak-badge">%s</td>'
            '<td><code>%s</code></td><td><code>%s</code></td><td>%s%s</td></tr>' % (
                _SEVERITY_CLASS.get(severity, "tak-sev-info"),
                html.escape(_SEVERITY_LABEL.get(severity, severity.upper())),
                html.escape(_text(_field(item, "rule_id"))),
                html.escape(_text(_field(item, "node"))),
                html.escape(_text(_field(item, "message"))),
                ('<span class="tak-hint">%s</span>' % html.escape(hint)) if hint else ""))
    body = "".join(rows) or ('<tr><td colspan="4">无异常</td></tr>')
    return (
        '<section><h2>规则结果</h2>'
        '<table class="tak-table"><thead><tr><th>级别</th><th>规则</th>'
        '<th>节点</th><th>说明</th></tr></thead><tbody>%s</tbody></table></section>' % body)


# --------------------------------------------------------------------------
# 取值与转义
# --------------------------------------------------------------------------
def _split(result: Any) -> Tuple[Any, Dict[str, Any]]:
    """返回 (report 对象或原始 dict, ToolResult.to_dict() 视图)。"""
    if isinstance(result, dict):
        return result, result
    report = getattr(result, "report", None)
    view: Dict[str, Any] = {}
    if hasattr(result, "to_dict"):
        try:
            data = result.to_dict()
            view = data if isinstance(data, dict) else {}
        except Exception:
            view = {}
    return report if report is not None else result, view


def _metadata(report: Any, view: Dict[str, Any]) -> Dict[str, Any]:
    meta = view.get("metadata")
    if isinstance(meta, dict):
        return meta
    meta = _get(report, "metadata")
    return meta if isinstance(meta, dict) else {}


def _findings(report: Any, view: Dict[str, Any]) -> List[Any]:
    value = view.get("findings")
    if isinstance(value, list):
        return value
    value = _get(report, "findings")
    return list(value) if isinstance(value, list) else []


def _counts(report: Any, view: Dict[str, Any]) -> Dict[str, int]:
    value = view.get("counts")
    if isinstance(value, dict):
        return {str(key): int(number or 0) for key, number in value.items()}
    value = _get(report, "counts")
    if callable(value):
        try:
            value = value()
        except Exception:
            value = None
    if isinstance(value, dict):
        return {str(key): int(number or 0) for key, number in value.items()}
    counts = {"blocker": 0, "error": 0, "warning": 0, "info": 0}
    for item in _findings(report, view):
        severity = _severity(item)
        if severity in counts:
            counts[severity] += 1
    return counts


def _passed(report: Any, view: Dict[str, Any]) -> bool:
    value = view.get("passed")
    if isinstance(value, bool):
        return value
    value = _get(report, "passed")
    return bool(value)


def _get(obj: Any, name: str) -> Any:
    if isinstance(obj, dict):
        return obj.get(name)
    return getattr(obj, name, None)


def _field(item: Any, name: str) -> Any:
    if isinstance(item, dict):
        return item.get(name)
    return getattr(item, name, None)


def _severity(item: Any) -> str:
    value = _field(item, "severity")
    return value.value if hasattr(value, "value") else str(value or "")


def _text(value: Any) -> str:
    return "" if value is None else str(value)


def _reason(validate: Dict[str, Any]) -> str:
    return _text(validate.get("reason")) or "未提供结论"


def _count_text(counts: Dict[str, int]) -> str:
    return " · ".join("%s=%d" % (name, counts.get(name, 0))
                      for name in ("blocker", "error", "warning", "info"))
