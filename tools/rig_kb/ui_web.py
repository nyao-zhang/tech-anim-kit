# -*- coding: utf-8 -*-
"""绑定知识库 RAG 的 Web 面板 HTML 片段。

约束
----
* 只使用 ``tak/ui/theme.css`` 的 tak-* 类名；颜色 / 间距一律取自
  ``var(--tak-*)`` 设计 token，不写任何字面颜色（十六进制 / rgb() / 具名颜色）；
* 不引用 CDN / 外链（无 <link> / <script> / href / src / http(s)://）；
* 所有动态文本经过 ``html.escape`` 完整转义；URL 字面量在转义前先打散，
  避免把外部链接文本带进面板。

``render_panel_html`` 接受 ``core.search()`` 的 dict、``ToolResult``、
``Report`` 及各自的 ``to_dict()``；``render_report_html`` 复用同一片段。
"""
from __future__ import annotations

import html
from typing import Any, Dict, List

TOOL_ID = "rig_kb"
TITLE = "绑定知识库 RAG"


def _text(value: Any) -> str:
    return "" if value is None else str(value)


def _escape(value: Any) -> str:
    """完整转义；顺带打散 URL 方案，保证面板不出现外链文本。"""
    raw = _text(value).replace("https://", "https:").replace("http://", "http:")
    return html.escape(raw, quote=True)


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
    citations = pick("citations")
    citations = citations if isinstance(citations, list) else []
    stats = pick("stats")
    stats = stats if isinstance(stats, dict) else {}
    count = pick("count")
    if not isinstance(count, int):
        count = len(results)
    try:
        documents = int(stats.get("documents", 0))
    except (TypeError, ValueError):
        documents = 0
    try:
        terms = int(stats.get("terms", 0))
    except (TypeError, ValueError):
        terms = 0
    return {
        "title": _text(pick("title") or TITLE),
        "dcc": _text(pick("dcc") or "maya"),
        "query": _text(pick("query") or ""),
        "count": count,
        "results": results,
        "citations": citations,
        "context": _text(pick("context") or ""),
        "documents": documents,
        "terms": terms,
        "offline": bool(pick("offline", True)),
    }


def _results_section(results: List[Any]) -> str:
    rows: List[str] = []
    for number, item in enumerate(results, 1):
        if not isinstance(item, dict):
            continue
        citation = item.get("citation")
        citation = citation if isinstance(citation, dict) else {}
        doc_id = _text(item.get("doc_id") or citation.get("doc_id") or "")
        title = _text(item.get("title") or citation.get("title") or "")
        section = _text(item.get("section") or citation.get("section") or "")
        path = _text(item.get("path") or citation.get("path") or "")
        excerpt = _text(item.get("excerpt") or citation.get("excerpt") or "")
        tags = item.get("tags") or []
        tag_text = "、".join(_text(tag) for tag in tags) if isinstance(tags, list) else ""
        rows.append(
            '<tr class="tak-row tak-sev-info">'
            '<td class="tak-badge">%d</td>'
            '<td><strong>%s</strong><br><code>%s</code></td>'
            '<td><code>%s</code></td>'
            '<td class="tak-badge">%s</td>'
            '<td>%s</td>'
            '<td><code>%s</code><br><span class="tak-hint">分节 %s · 路径 %s · 标签 %s</span></td>'
            '</tr>' % (
                number, _escape(title), _escape(doc_id), _escape(section),
                _escape(_score(item.get("score"))), _escape(excerpt),
                _escape(doc_id), _escape(section), _escape(path),
                _escape(tag_text)))
    body = "".join(rows) or (
        '<tr class="tak-row tak-sev-info"><td colspan="6">'
        '无命中片段（可换用 命名 / 权重 / FBX / IK / 兼容 等规范关键词）'
        '</td></tr>')
    return (
        '<section><h2>检索结果</h2><table class="tak-table"><thead><tr>'
        '<th>#</th><th>文档</th><th>分节</th><th>分数</th><th>片段</th>'
        '<th>引用</th></tr></thead><tbody>%s</tbody></table></section>'
        % body)


def _context_section(context: str) -> str:
    if not context:
        return ('<section><h2>上下文注入提示</h2>'
                '<p class="tak-hint">当前无命中片段，无上下文可注入。</p></section>')
    shown = context[:800]
    return (
        '<section><h2>上下文注入提示</h2>'
        '<p class="tak-hint">Agent Console 可调用 '
        '<code>context_for(query, top_k=3, max_chars=1200)</code> '
        '把下面带引用的片段注入对话上下文；结果离线、可复现、长度受限。</p>'
        '<div class="tak-hint" style="color: var(--tak-text-muted); '
        'padding-left: var(--tak-space-sm)">%s</div></section>'
        % _escape_multiline(shown))


def _offline_section(view: Dict[str, Any]) -> str:
    label = "离线可用" if view["offline"] else "已启用真实 provider"
    return (
        '<section><h2>离线说明</h2>'
        '<p class="tak-hint">本工具只检索仓库内 <code>knowledge/*.md</code> '
        '种子文档：中文按「单字 + 相邻 bigram」、英文按小写词元切分，'
        'BM25 在片段级打分，排序固定为分数降序 → doc_id 升序。'
        '默认 %s、不联网、不写场景；离线 provider 的确定性占位向量'
        '绝不参与排序，命中结果均带 doc_id / title / section / excerpt 引用。'
        '</p></section>' % _escape(label))


def render_panel_html(result: Any) -> str:
    """渲染检索结果卡片、上下文注入提示与离线说明，返回无外链的 HTML 片段。"""
    view = _view(result)
    parts = [
        '<section class="tak-panel" data-tool="%s">' % _escape(TOOL_ID),
        '<header class="tak-header"><h1>%s</h1>'
        '<span class="tak-state tak-state-pass">%s</span></header>' % (
            _escape(view["title"]),
            _escape("离线" if view["offline"] else "在线")),
        '<p class="tak-meta">查询 %s · 命中 %d · 文档 %d · 词条 %d · DCC %s</p>' % (
            _escape(view["query"] or "（缺省规范概览）"), view["count"],
            view["documents"], view["terms"], _escape(view["dcc"])),
        _results_section(view["results"]),
        _context_section(view["context"]),
        _offline_section(view),
        '</section>',
    ]
    return "".join(parts)


def render_report_html(result: Any) -> str:
    """保留与 Report 面板一致的入口；完整面板同样只返回 HTML 片段。"""
    return render_panel_html(result)
