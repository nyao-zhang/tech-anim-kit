# -*- coding: utf-8 -*-
"""Agent Console 的 Web 面板 HTML 片段。

约定：仅使用 ``tak/ui/theme.css`` 的 ``tak-*`` 类名与 ``var(--tak-*)`` token，
不写任何行内样式、字面颜色、CDN / 外链；所有动态文本用 ``html.escape`` 完整转义。

``render_panel_html`` / ``render_report_html`` 都接受：

* ``AgentSession.chat()`` 的返回 dict（对话 / 候选 / dry-run / 安全边界）；
* ``Report`` / ``ToolResult`` / ``*.to_dict()``（规则结果与 metadata）。
"""
from __future__ import annotations

import html
import json
from typing import Any, Dict, List

TOOL_ID = "agent_console"


def _text(value: Any) -> str:
    return "" if value is None else str(value)


def _e(value: Any) -> str:
    """完整 HTML 转义（含引号）。"""
    return html.escape(_text(value), quote=True)


def _json(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    except (TypeError, ValueError):
        return _text(value)


def _view(result: Any) -> Dict[str, Any]:
    """把任意结果归一成 dict 视图（不修改入参）。"""
    if isinstance(result, dict):
        return dict(result)
    if hasattr(result, "to_dict"):
        try:
            data = result.to_dict()
            if isinstance(data, dict):
                return data
        except Exception:
            pass
    view: Dict[str, Any] = {}
    for key in ("title", "dcc", "metadata", "findings", "passed"):
        if hasattr(result, key):
            view[key] = getattr(result, key)
    return view


def _metadata(view: Dict[str, Any]) -> Dict[str, Any]:
    meta = view.get("metadata")
    return dict(meta) if isinstance(meta, dict) else {}


def _as_list(value: Any) -> List[Any]:
    return list(value) if isinstance(value, (list, tuple)) else []


def _safety_lines(view: Dict[str, Any], meta: Dict[str, Any]) -> List[str]:
    safety = view.get("safety")
    if isinstance(safety, dict):
        notes = safety.get("notes") or []
    elif isinstance(safety, (list, tuple)):
        notes = safety
    else:
        notes = meta.get("safety") or []
    return [_text(item) for item in _as_list(notes) if item]


def _candidates(view: Dict[str, Any], meta: Dict[str, Any]) -> List[Any]:
    value = view.get("candidates")
    if isinstance(value, list):
        return value
    value = meta.get("candidates")
    if isinstance(value, dict):
        return _as_list(value.get("actions"))
    return _as_list(value)


def _preview(view: Dict[str, Any], meta: Dict[str, Any]) -> List[Any]:
    value = view.get("preview")
    if isinstance(value, list):
        return value
    return _as_list(meta.get("preview"))


def _citations(view: Dict[str, Any], meta: Dict[str, Any]) -> List[Any]:
    value = view.get("citations")
    if isinstance(value, list):
        return value
    return _as_list(meta.get("citations"))


def _reply(view: Dict[str, Any], meta: Dict[str, Any]) -> str:
    reply = _text(view.get("reply") or "")
    if reply:
        return reply
    explanations = _as_list(meta.get("explanations"))
    if explanations:
        return "\n".join(_text(item.get("explanation")) if isinstance(item, dict)
                         else _text(item) for item in explanations)
    return "对话式控制台就绪：可解释问题、生成修复计划、检索知识与查看状态。"


def _candidates_table(rows: List[Any]) -> str:
    body: List[str] = []
    for index, item in enumerate(rows, 1):
        if isinstance(item, dict):
            tool = _text(item.get("tool"))
            params = item.get("params")
        else:
            tool, params = _text(item), {}
        body.append(
            '<tr class="tak-row tak-sev-info"><td class="tak-badge">%d</td>'
            '<td><code>%s</code></td><td><code>%s</code></td></tr>' % (
                index, _e(tool), _e(_json(params))))
    if not body:
        body.append('<tr class="tak-row tak-sev-info"><td colspan="3">无候选动作</td></tr>')
    return (
        '<section><h2>候选动作</h2><table class="tak-table"><thead><tr>'
        '<th>#</th><th>动作</th><th>参数</th></tr></thead><tbody>%s'
        '</tbody></table></section>' % "".join(body))


def _preview_table(rows: List[Any]) -> str:
    body: List[str] = []
    for index, item in enumerate(rows, 1):
        if not isinstance(item, dict):
            continue
        tool = _text(item.get("tool"))
        params = item.get("params")
        permissions = item.get("permissions")
        status = _text(item.get("status") or "pending")
        body.append(
            '<tr class="tak-row tak-sev-warning"><td class="tak-badge">%d</td>'
            '<td><code>%s</code></td><td><code>%s</code></td>'
            '<td><code>%s</code></td><td class="tak-badge">%s</td></tr>' % (
                index, _e(tool), _e(_json(params)), _e(_json(permissions)),
                _e(status)))
    if not body:
        body.append('<tr class="tak-row tak-sev-info"><td colspan="5">'
                    '无 dry-run 预览（尚未生成计划）</td></tr>')
    return (
        '<section><h2>Dry-run diff（预览，未执行）</h2><table class="tak-table">'
        '<thead><tr><th>#</th><th>动作</th><th>参数</th><th>权限</th><th>状态</th>'
        '</tr></thead><tbody>%s</tbody></table></section>' % "".join(body))


def _citations_block(rows: List[Any]) -> str:
    if not rows:
        return ""
    lines: List[str] = []
    for item in rows:
        if isinstance(item, dict):
            label = _text(item.get("title") or item.get("doc_id") or "引用")
            detail = _text(item.get("excerpt") or item.get("text") or "")
            section = _text(item.get("section") or "")
            path = _text(item.get("path") or "")
            lines.append('<p class="tak-hint"><code>%s</code> %s <code>%s</code> %s</p>'
                         % (_e(label), _e(section), _e(path), _e(detail)))
        else:
            lines.append('<p class="tak-hint">%s</p>' % _e(item))
    return '<section><h2>知识库引用</h2>%s</section>' % "".join(lines)


def _confirm_block(plan_id: str) -> str:
    call = ("confirm(plan_id, confirm=True, dry_run=False, "
            "granted=['node.write'])")
    return (
        '<section><h2>确认与执行</h2>'
        '<p class="tak-hint">执行是唯一可能写场景的操作：必须白名单校验 + dry-run '
        '预览 + 权限授予 + 人工二次确认，执行器开启 undo 并在失败时整体回滚；'
        'chat 绝不写场景。</p>'
        '<button type="button" disabled data-plan-id="%s">确认执行（需二次确认与授权）</button>'
        '<p class="tak-hint"><code>%s</code></p>'
        '</section>' % (_e(plan_id), _e(call)))


def _safety_block(lines: List[str]) -> str:
    if not lines:
        return ""
    items = "".join('<li class="tak-hint">%s</li>' % _e(line) for line in lines)
    return '<section><h2>安全边界</h2><ul>%s</ul></section>' % items


def _findings_block(view: Dict[str, Any]) -> str:
    findings = _as_list(view.get("findings"))
    if not findings:
        return ""
    rows: List[str] = []
    for item in findings:
        if isinstance(item, dict):
            severity = _text(item.get("severity") or "info")
            rule_id = _text(item.get("rule_id"))
            node = _text(item.get("node"))
            message = _text(item.get("message"))
        else:
            severity = _text(getattr(getattr(item, "severity", "info"), "value", "info"))
            rule_id = _text(getattr(item, "rule_id", ""))
            node = _text(getattr(item, "node", ""))
            message = _text(getattr(item, "message", ""))
        rows.append(
            '<tr class="tak-row tak-sev-%s"><td class="tak-badge">%s</td>'
            '<td><code>%s</code></td><td><code>%s</code></td><td>%s</td></tr>' % (
                _e(severity), _e(severity).upper(), _e(rule_id), _e(node),
                _e(message)))
    return (
        '<section><h2>规则结果</h2><table class="tak-table"><thead><tr>'
        '<th>级别</th><th>规则</th><th>节点</th><th>说明</th></tr></thead>'
        '<tbody>%s</tbody></table></section>' % "".join(rows))


def render_panel_html(result: Any) -> str:
    """渲染对话、候选动作、dry-run diff、确认说明与安全边界（无外链 HTML 片段）。"""
    view = _view(result)
    meta = _metadata(view)
    status = _text(view.get("status") or "status")
    intent = _text(view.get("intent") or "")
    turn = _text(view.get("turn") or "")
    plan_id = _text(view.get("plan_id") or "")
    if not plan_id:
        for item in _as_list(meta.get("chat_turns")):
            if isinstance(item, dict) and item.get("plan_id"):
                plan_id = _text(item.get("plan_id"))
    dcc = _text(view.get("dcc") or meta.get("dcc") or "maya")
    state = "tak-state-fail" if status == "rejected" else "tak-state-pass"

    reply_html = _e(_reply(view, meta)).replace("\n", "<br>")
    parts: List[str] = [
        '<section class="tak-panel" data-tool="%s">' % TOOL_ID,
        '<header class="tak-header"><h1>Agent Console 对话式控制台</h1>'
        '<span class="tak-state %s">%s</span></header>' % (state, _e(status)),
        '<p class="tak-meta">意图 %s · 轮次 %s · 计划 %s · DCC %s</p>' % (
            _e(intent or "—"), _e(turn or "—"), _e(plan_id or "—"), _e(dcc)),
        '<section><h2>对话</h2><p class="tak-hint">状态：%s</p><p>%s</p></section>' % (
            _e(status), reply_html),
        _citations_block(_citations(view, meta)),
        _candidates_table(_candidates(view, meta)),
        _preview_table(_preview(view, meta)),
        _confirm_block(plan_id),
        _safety_block(_safety_lines(view, meta)),
        _findings_block(view),
        '</section>',
    ]
    return "".join(parts)


def render_report_html(result: Any) -> str:
    """与完整面板一致的入口（Report / ToolResult 同样只返回 HTML 片段）。"""
    return render_panel_html(result)
