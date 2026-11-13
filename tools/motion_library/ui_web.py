# -*- coding: utf-8 -*-
"""动作库语义检索的 Web 面板片段。

约束（与其它插件 ui_web 保持一致）
--------------------------------
* 只使用 ``tak/ui/theme.css`` 的 ``tak-*`` 类名与 ``var(--tak-*)`` token，
  不写字面颜色、不写行内样式、不引用 CDN 或任何外链；
* 所有动态文本经 ``html.escape(..., quote=True)`` 完整转义；
* 返回 HTML **片段**（不含 ``<html>`` / ``<body>``），由 Web 服务端嵌入。

``render_panel_html`` 接受 ``core.search()`` 的 dict、``Report`` / ``ToolResult``
及其 ``to_dict()``；``render_report_html`` 复用同一片段。另外提供
``panel_data()``：同样的内容以纯数据结构返回，便于非 HTML 前端渲染。
"""
from __future__ import annotations

import html
from typing import Any, Dict, List

TOOL_ID = "motion_library"
TITLE = "动作库语义检索"


# --------------------------------------------------------------------------- #
# 取值工具
# --------------------------------------------------------------------------- #
def _text(value: Any) -> str:
    return "" if value is None else str(value)


def _escape(value: Any) -> str:
    return html.escape(_text(value), quote=True)


def _escape_multiline(value: Any) -> str:
    return _escape(value).replace("\n", "<br>")


def _score(value: Any) -> str:
    try:
        return "%.4f" % float(value)
    except (TypeError, ValueError):
        return "0.0000"


def _to_dict(result: Any) -> Dict[str, Any]:
    if isinstance(result, dict):
        return dict(result)
    if hasattr(result, "to_dict"):
        try:
            value = result.to_dict()
            if isinstance(value, dict):
                return dict(value)
        except Exception:
            pass
    return {}


def _view(result: Any) -> Dict[str, Any]:
    """把多种结果形态归一成面板视图（不修改入参）。"""
    data = _to_dict(result)
    metadata = data.get("metadata")
    metadata = metadata if isinstance(metadata, dict) else {}

    def pick(key: str, default: Any = None) -> Any:
        for source in (data, metadata):
            value = source.get(key)
            if value is not None:
                return value
        return default

    results = pick("results")
    results = results if isinstance(results, list) else []
    stats = pick("stats")
    stats = stats if isinstance(stats, dict) else {}
    count = pick("count")
    if not isinstance(count, int):
        count = len(results)
    try:
        clip_count = int(stats.get("clip_count", 0))
    except (TypeError, ValueError):
        clip_count = 0
    bone_count = 0
    try:
        bone_count = int(stats.get("bone_count", 0))
    except (TypeError, ValueError):
        bone_count = 0
    return {
        "title": _text(pick("title") or TITLE),
        "dcc": _text(pick("dcc") or "maya"),
        "query": _text(pick("query") or ""),
        "count": count,
        "results": [item for item in results if isinstance(item, dict)],
        "clip_count": clip_count,
        "bone_count": bone_count,
        "warning": _text(pick("warning") or ""),
        "offline": bool(pick("offline", True)),
    }


# --------------------------------------------------------------------------- #
# 片段渲染
# --------------------------------------------------------------------------- #
def _duration_text(value: Any) -> str:
    try:
        return "%.2fs" % float(value)
    except (TypeError, ValueError):
        return "—"


def _context_from_results(view: Dict[str, Any]) -> str:
    """由命中列表生成与 ``core.context_for`` 同构的上下文（最多 3 条）。"""
    if not view["results"]:
        return ""
    lines = ["动作库上下文（本地离线索引；只读，未解析 FBX 二进制）："]
    for number, hit in enumerate(view["results"][:3], 1):
        lines.append("[%d] clip_id=%s | %s | 时长 %s | 类型 %s | 循环=%s" % (
            number, _text(hit.get("clip_id")), _text(hit.get("name")),
            _duration_text(hit.get("duration")),
            _text(hit.get("motion_type")), "是" if hit.get("loopable") else "否"))
    return "\n".join(lines)


def _results_section(results: List[Dict[str, Any]]) -> str:
    rows: List[str] = []
    for number, item in enumerate(results, 1):
        clip_id = _text(item.get("clip_id"))
        name = _text(item.get("name"))
        motion_type = _text(item.get("motion_type") or "other")
        why = _text(item.get("why"))
        tags = item.get("tags") or []
        tag_text = "、".join(_text(tag) for tag in tags) if isinstance(tags, list) else ""
        path = _text(item.get("path"))
        rows.append(
            '<tr class="tak-row tak-sev-info">'
            '<td class="tak-badge">%d</td>'
            '<td><strong>%s</strong><br><code>%s</code></td>'
            '<td><code>%s</code><br><span class="tak-hint">时长 %s · 循环 %s · 根运动 %s</span></td>'
            '<td class="tak-badge">%s</td>'
            '<td>%s</td>'
            '<td><span class="tak-hint">标签 %s<br>路径 %s</span></td>'
            '</tr>' % (
                number, _escape(name), _escape(clip_id),
                _escape(motion_type), _escape(_duration_text(item.get("duration"))),
                "是" if item.get("loopable") else "否",
                "是" if item.get("root_motion") else "否",
                _escape(_score(item.get("score"))), _escape(why),
                _escape(tag_text or "—"), _escape(path or "—")))
    body = "".join(rows) or (
        '<tr class="tak-row tak-sev-info"><td colspan="6">'
        '无命中动作（可换用 走路 / 奔跑 / 攻击 / 循环 等关键词，或清空查询列出全部）'
        '</td></tr>')
    return (
        '<section><h2>检索结果</h2><table class="tak-table"><thead><tr>'
        '<th>#</th><th>动作</th><th>类型 / 时长</th><th>分数</th>'
        '<th>匹配理由</th><th>标签 / 路径</th></tr></thead><tbody>%s</tbody>'
        '</table></section>' % body)


def _context_section(context: str) -> str:
    if not context:
        return ('<section><h2>上下文注入提示</h2>'
                '<p class="tak-hint">当前无命中动作，无上下文可注入。</p>'
                '</section>')
    return (
        '<section><h2>上下文注入提示</h2>'
        '<p class="tak-hint">Agent Console 可调用 '
        '<code>context_for(query, top_k=3, max_chars=800)</code> '
        '把下面带 clip_id 的上下文注入对话；内容离线、可复现、长度受限。</p>'
        '<div class="tak-hint">%s</div></section>'
        % _escape_multiline(context[:800]))


def _offline_section(view: Dict[str, Any]) -> str:
    label = "离线可用" if view["offline"] else "已启用真实 provider"
    return (
        '<section><h2>离线与安全说明</h2>'
        '<p class="tak-hint">默认 %s：只读动作库元数据与数值特征，'
        '文本分用 BM25（名称 2 倍权重 + 标签 + 骨骼），类型分按动作关键词加权，'
        '过滤不满足即排除，排序固定为分数降序 → clip_id 升序。'
        '全程不解析 FBX 二进制、不写场景、不联网、不写缓存文件；'
        'AI 只做语义重排候选，结果完全可复现。</p></section>' % _escape(label))


def render_panel_html(result: Any) -> str:
    """渲染「命中卡片 + 上下文提示 + 离线说明」，返回无外链的 HTML 片段。"""
    view = _view(result)
    context = ""
    if isinstance(result, dict):
        context = _text(result.get("context") or "")
    if not context:
        context = _context_from_results(view)
    parts = [
        '<section class="tak-panel" data-tool="%s">' % _escape(TOOL_ID),
        '<header class="tak-header"><h1>%s</h1>'
        '<span class="tak-state tak-state-pass">%s</span></header>' % (
            _escape(view["title"]),
            _escape("离线" if view["offline"] else "在线")),
        '<p class="tak-meta">查询 %s · 命中 %d · 库内动作 %d · 骨骼 %d · DCC %s</p>' % (
            _escape(view["query"] or "（空查询：列出全部）"), view["count"],
            view["clip_count"], view["bone_count"], _escape(view["dcc"])),
    ]
    if view["warning"]:
        parts.append('<p class="tak-hint">%s</p>' % _escape(view["warning"]))
    parts.extend([
        _results_section(view["results"]),
        _context_section(context),
        _offline_section(view),
        '</section>',
    ])
    return "".join(parts)


def render_report_html(result: Any) -> str:
    """保留与 Report 面板一致的入口；返回同一份 HTML 片段。"""
    return render_panel_html(result)


def panel_data(result: Any) -> Dict[str, Any]:
    """同样的面板内容以纯数据返回，便于非 HTML 前端渲染。

    结构：``{"section_id", "title", "items": [{"label", "value", "severity"}]}``。
    """
    view = _view(result)
    items: List[Dict[str, str]] = [{
        "label": "命中概览",
        "value": "查询「%s」命中 %d / 库内 %d 个动作，骨骼 %d 根" % (
            view["query"] or "（空查询）", view["count"],
            view["clip_count"], view["bone_count"]),
        "severity": "info",
    }]
    for item in view["results"]:
        items.append({
            "label": _text(item.get("clip_id")),
            "value": "%s · 类型 %s · 时长 %s · 分数 %s · %s" % (
                _text(item.get("name")), _text(item.get("motion_type")),
                _duration_text(item.get("duration")),
                _score(item.get("score")), _text(item.get("why"))),
            "severity": "info",
        })
    if view["warning"]:
        items.append({"label": "提示", "value": view["warning"],
                      "severity": "warning"})
    return {"section_id": TOOL_ID, "title": view["title"], "items": items}
