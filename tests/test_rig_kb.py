# -*- coding: utf-8 -*-
"""P5.4 绑定知识库 RAG 离线回归测试。

覆盖：tokenize 中英文与 bigram、种子文档与缺目录回退、chunk 边界与长度、
KnowledgeBase 默认加载、中英文与混合 query 命中、top_k 上限、空/超长/未知 query
不抛异常、结果可复现、context_for 引用与长度、add_document、describe、
use_embeddings 离线不漂移、manifest discover、run_report、ui_web 转义/无外链，
以及 Python 3.9 语法与 tak/core 零 DCC / 零第三方依赖的静态检查。
全部使用纯标准库与 NullAdapter，不接触真实 DCC 或网络。
"""
from __future__ import annotations

import ast
import os
import re
import subprocess
import sys

import pytest

from tak.adapters.base import NullAdapter
from tak.core import knowledge, registry, runner
from tak.core.knowledge import (
    MAX_CHUNK_CHARS, Document, KnowledgeBase, chunk_text,
    load_seed_documents, tokenize,
)
from tak.core.report import Report

TOOL_ID = "rig_kb"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KNOWLEDGE_DIR = os.path.join(ROOT, "knowledge")
NEW_PY_FILES = (
    os.path.join(ROOT, "tak", "core", "knowledge", "__init__.py"),
    os.path.join(ROOT, "tak", "core", "knowledge", "index.py"),
    os.path.join(ROOT, "tools", TOOL_ID, "core.py"),
    os.path.join(ROOT, "tools", TOOL_ID, "ui_web.py"),
    os.path.abspath(__file__),
)
REQUIRED_HIT_KEYS = {"doc_id", "title", "score", "section", "excerpt",
                     "citation", "tags"}


def _load(submodule):
    return registry.load_plugin_module(runner.find_plugin(TOOL_ID), submodule)


@pytest.fixture(scope="module")
def core():
    return _load("core")


@pytest.fixture(scope="module")
def ui():
    return _load("ui_web")


@pytest.fixture(scope="module")
def kb():
    return KnowledgeBase()


# --------------------------------------------------------------------------- #
# tokenize
# --------------------------------------------------------------------------- #
def test_tokenize_english_lower_words_and_mixed():
    tokens = tokenize("FBX UE IK_Rig skinCluster 5.8")
    assert "fbx" in tokens and "ue" in tokens
    assert "ik_rig" in tokens and "skincluster" in tokens
    assert tokens == [item.lower() for item in tokens]
    assert tokenize("IK Rig") == ["ik", "rig"]


def test_tokenize_chinese_single_char_and_bigram():
    assert tokenize("命名") == ["命", "命名", "名"]
    tokens = tokenize("命名规范")
    assert "命" in tokens and "名" in tokens and "规" in tokens and "范" in tokens
    assert "命名" in tokens and "名规" in tokens and "规范" in tokens
    mixed = tokenize("IK Rig 命名规范")
    assert "ik" in mixed and "命名" in mixed and "规范" in mixed


def test_tokenize_is_deterministic_and_handles_empty():
    text = "Python 3.9 绑定命名规范 skin_weights.md"
    assert tokenize(text) == tokenize(text)
    assert tokenize("") == []
    assert tokenize(None) == []
    assert tokenize("   !!!   ") == []


# --------------------------------------------------------------------------- #
# 种子文档
# --------------------------------------------------------------------------- #
def test_load_seed_documents_has_five_titled_docs():
    docs = load_seed_documents()
    assert len(docs) >= 5
    assert all(isinstance(item, Document) for item in docs)
    assert all(item.title.strip() for item in docs)
    assert all(item.doc_id.strip() for item in docs)
    assert all(item.tags for item in docs)
    ids = {item.doc_id for item in docs}
    assert {"rig_naming", "skin_weights", "fbx_ue_pipeline",
            "ik_retarget", "dcc_compat"} <= ids
    assert all(item.to_dict()["text"] == item.text for item in docs)


def test_load_seed_documents_accepts_directory_and_single_file(tmp_path):
    single = tmp_path / "custom.md"
    single.write_text("# 自定义标题\n\nTags: alpha, beta\n\n## 小节\n\n自定义内容\n",
                      encoding="utf-8")
    from_dir = load_seed_documents(str(tmp_path))
    assert len(from_dir) == 1
    assert from_dir[0].doc_id == "custom"
    assert from_dir[0].title == "自定义标题"
    assert from_dir[0].tags == ["alpha", "beta"]
    from_file = load_seed_documents(str(single))
    assert len(from_file) == 1 and from_file[0].title == "自定义标题"


def test_load_seed_documents_falls_back_when_dir_missing(tmp_path):
    docs = load_seed_documents(str(tmp_path / "does_not_exist"))
    assert len(docs) >= 5
    assert all(item.title.strip() for item in docs)
    # 回退文档同样能被检索，保证任何环境都能跑。
    fallback_kb = KnowledgeBase(documents=docs)
    assert fallback_kb.search("命名规范", top_k=2)


# --------------------------------------------------------------------------- #
# chunk
# --------------------------------------------------------------------------- #
def test_chunk_text_respects_section_and_blank_line_boundaries():
    text = ("## 甲节\n\n" + "词A " * 100 + "\n\n" + "词B " * 100
            + "\n\n## 乙节\n\n" + "词C " * 100)
    chunks = chunk_text(text)
    assert len(chunks) >= 3
    assert all(isinstance(item, dict) for item in chunks)
    assert all("text" in item and "section" in item for item in chunks)
    assert all(len(item["text"]) <= MAX_CHUNK_CHARS for item in chunks)
    assert {item["section"] for item in chunks} == {"甲节", "乙节"}
    assert all(not ("词A" in item["text"] and "词C" in item["text"])
               for item in chunks)


def test_chunk_text_hard_splits_long_block_without_tiny_tail():
    text = "## 长节\n\n" + "权重" * 700
    chunks = chunk_text(text)
    assert len(chunks) >= 3
    assert all(len(item["text"]) <= MAX_CHUNK_CHARS for item in chunks)
    assert all(item["section"] == "长节" for item in chunks)
    assert min(len(item["text"]) for item in chunks) >= 100
    assert chunk_text("") == []


def test_chunk_text_uses_section_hint_for_preamble():
    chunks = chunk_text("开头概述内容\n\n## 后续\n\n后续内容", section_hint="导言")
    assert chunks[0]["section"] == "导言"
    assert chunks[-1]["section"] == "后续"


# --------------------------------------------------------------------------- #
# KnowledgeBase / search
# --------------------------------------------------------------------------- #
def test_knowledge_base_default_loads_seed_documents(kb):
    assert len(kb.documents) >= 5
    assert kb.describe()["chunks"] > 0
    assert kb.describe()["built"] is True
    assert kb.describe()["offline"] is True


def test_search_chinese_query_hits_expected_document(kb):
    hits = kb.search("命名规范 joint_pattern 命名空间", top_k=3)
    assert hits
    assert hits[0]["doc_id"] == "rig_naming"


def test_search_english_query_hits_expected_document(kb):
    hits = kb.search("max_influences weight_sum deformer", top_k=3)
    assert hits
    assert hits[0]["doc_id"] == "skin_weights"


def test_search_mixed_query_hits_expected_document(kb):
    hits = kb.search("FBX sidecar 根运动 compression", top_k=3)
    assert hits
    assert hits[0]["doc_id"] == "fbx_ue_pipeline"


def test_search_result_fields_and_citation(kb):
    hits = kb.search("IKRetargeter chain mapping conflicts", top_k=3)
    assert hits
    for hit in hits:
        assert REQUIRED_HIT_KEYS <= set(hit)
        assert isinstance(hit["score"], float)
        assert isinstance(hit["tags"], list)
        citation = hit["citation"]
        assert {"doc_id", "title", "section", "excerpt"} <= set(citation)
        assert citation["doc_id"] == hit["doc_id"]
        assert hit["excerpt"] and hit["section"]


def test_search_top_k_upper_bound(kb):
    assert len(kb.search("规范 权重 FBX IK 兼容", top_k=2)) <= 2
    assert len(kb.search("规范", top_k=1)) == 1
    assert kb.search("规范", top_k=0) == []
    assert len(kb.search("规范", top_k=99)) <= 50


def test_search_empty_long_and_unknown_queries_never_raise(kb):
    for query in ("", "   ", None, "zzzz_unknown_token_xyz", "🍀🍀🍀"):
        assert kb.search(query, top_k=3) == []
    huge = "权重 " * 30000
    result = kb.search(huge, top_k=3)
    assert isinstance(result, list)
    assert len(result) <= 3


def test_search_is_fully_reproducible(kb):
    first = kb.search("蒙皮权重 归一化 skinCluster", top_k=3)
    second = kb.search("蒙皮权重 归一化 skinCluster", top_k=3)
    assert first == second
    assert [item["rank"] for item in first] == list(range(1, len(first) + 1))
    for left, right in zip(first, first[1:]):
        assert left["score"] >= right["score"]


def test_search_sort_is_score_then_doc_id(kb):
    hits = kb.search("规范 权重 导出 兼容 映射", top_k=5)
    assert len(hits) >= 2
    for left, right in zip(hits, hits[1:]):
        assert (-left["score"], left["doc_id"], left["chunk_index"]) <= \
               (-right["score"], right["doc_id"], right["chunk_index"])


# --------------------------------------------------------------------------- #
# context_for / citations / add_document / describe
# --------------------------------------------------------------------------- #
def test_context_for_has_citations_and_respects_max_chars(kb):
    for limit in (1200, 400, 120):
        text = kb.context_for("蒙皮权重", top_k=3, max_chars=limit)
        assert len(text) <= limit
        assert "引用" in text and "doc_id" in text
    assert kb.context_for("zzzz_unknown_token_xyz", top_k=3) == ""
    assert kb.context_for("权重", top_k=3, max_chars=0) == ""


def test_citations_return_structured_references(kb):
    refs = kb.citations("命名规范", top_k=2)
    assert refs and all(isinstance(item, dict) for item in refs)
    assert all("doc_id" in item and "section" in item for item in refs)
    assert refs == [hit["citation"] for hit in kb.search("命名规范", top_k=2)]


def test_add_document_is_immediately_searchable():
    custom = KnowledgeBase(documents=[])
    assert custom.search("量子纠缠标记", top_k=3) == []
    doc = custom.add_document("custom_doc", "自定义文档",
                              "## 唯一分节\n\n量子纠缠标记 qmark_token 说明文本",
                              tags=["custom"], path="knowledge/custom.md")
    assert isinstance(doc, Document)
    hits = custom.search("量子纠缠标记 qmark_token", top_k=3)
    assert hits and hits[0]["doc_id"] == "custom_doc"
    assert hits[0]["section"] == "唯一分节"
    assert len(custom.documents) == 1


def test_describe_fields_complete(kb):
    data = kb.describe()
    for key in ("documents", "doc_count", "chunks", "terms", "term_count",
                "docs_dir", "seed_dir", "embedding", "embedding_enabled",
                "provider", "offline", "bm25", "built", "version"):
        assert key in data, key
    assert data["documents"] >= 5
    assert data["terms"] > 0
    assert data["chunks"] > 0
    assert data["embedding_enabled"] is False
    assert data["embedding"]["offline"] is True


# --------------------------------------------------------------------------- #
# embeddings（离线不漂移）
# --------------------------------------------------------------------------- #
def test_use_embeddings_offline_disabled_and_unchanged():
    from tak.core.ai import get_provider

    query = "蒙皮权重 归一化"
    plain = KnowledgeBase()
    baseline = plain.search(query, top_k=3)
    enabled = plain.search(query, top_k=3, options={"use_embeddings": True})
    assert enabled == baseline
    assert all(hit["ranked_by"] == "bm25" for hit in enabled)
    assert plain.describe()["embedding_enabled"] is False

    # get_provider 默认离线：MockProvider.embed 是确定性占位向量，不参与重排。
    mock_kb = KnowledgeBase(provider=get_provider(offline=True))
    assert mock_kb.describe()["embedding_enabled"] is False
    assert mock_kb.search(query, top_k=3,
                          options={"use_embeddings": True}) == baseline


def test_use_embeddings_with_real_provider_enables_rerank():
    class _RealProvider(object):
        model = "fake-embed"

        def embed(self, texts, **kwargs):
            vectors = []
            for text in texts:
                flag = 1.0 if "权重" in text else 0.0
                vectors.append([flag, 1.0 - flag, 1.0])
            return vectors

    kb = KnowledgeBase(provider=_RealProvider())
    assert kb.describe()["embedding_enabled"] is True
    hits = kb.search("权重", top_k=3, options={"use_embeddings": True})
    assert hits
    assert all(hit["ranked_by"] == "bm25+embeddings" for hit in hits)
    assert all(isinstance(hit["score"], float) for hit in hits)


# --------------------------------------------------------------------------- #
# 模块级便捷函数
# --------------------------------------------------------------------------- #
def test_module_level_convenience_functions():
    hits = knowledge.search("命名规范", top_k=2)
    assert hits and hits[0]["doc_id"] == "rig_naming"
    assert "doc_id" in knowledge.context_for("权重", top_k=2, max_chars=300)
    assert knowledge.citations("权重", top_k=2)
    assert knowledge.describe()["documents"] >= 5


# --------------------------------------------------------------------------- #
# 插件 / manifest / run_report
# --------------------------------------------------------------------------- #
def test_manifest_is_discoverable():
    plugins = registry.discover()
    match = [item for item in plugins if item.id == TOOL_ID]
    assert len(match) == 1
    plugin = match[0]
    assert plugin.name == "绑定知识库 RAG"
    assert plugin.ui == "web"
    assert set(plugin.dcc) == {"maya", "max", "mobu", "unreal"}
    assert plugin.permissions == ["scene.read"]
    assert "离线" in plugin.description or "引用" in plugin.description


def test_new_tool_list_includes_rig_kb():
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    completed = subprocess.run(
        [sys.executable, "tools/new_tool.py", "--list"],
        cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        env=env, timeout=60)
    output = completed.stdout.decode("utf-8", "replace")
    assert completed.returncode == 0
    assert TOOL_ID in output


def test_plugin_info_and_search_api(core):
    info = core.info()
    assert info["id"] == TOOL_ID
    assert info["offline"] is True and info["read_only"] is True
    assert "search(" in info["search_api"] and "context_for(" in info["context_api"]
    result = core.search("skinCluster 权重", top_k=2)
    assert result["ok"] is True
    assert result["count"] == len(result["results"])
    assert len(result["citations"]) == result["count"]
    assert isinstance(result["context"], str)
    assert result["stats"]["documents"] >= 5
    assert core.context_for("权重", top_k=2, max_chars=200)


def test_run_report_returns_report_with_citations_metadata(core):
    ctx = runner.ToolContext(
        dcc="unreal", snapshot={"metadata": {"query": "FBX 导出 UE 导入"}},
        options={"top_k": 2}, tool_id=TOOL_ID)
    report = core.run_report(ctx)
    assert isinstance(report, Report)
    assert report.passed is True
    assert report.findings
    assert all(item.severity.value == "info" for item in report.findings)
    for key in ("citations", "context", "stats", "safety", "results", "query"):
        assert key in report.metadata, key
    assert len(report.metadata["citations"]) == len(report.findings)
    assert report.metadata["safety"]["scene_write"] is False
    assert report.metadata["safety"]["network"] is False


def test_run_report_uses_default_query_and_runner_integration():
    result = runner.run_tool(TOOL_ID, {}, dcc="maya", adapter=NullAdapter())
    assert isinstance(result.report, Report)
    assert result.report.metadata["query"]
    assert result.report.metadata["citations"]
    assert result.passed is True


def test_run_report_query_from_options_takes_priority(core):
    ctx = runner.ToolContext(
        dcc="maya", snapshot={"metadata": {"query": "命名规范"}},
        options={"query": "IKRetargeter 链映射", "top_k": 1})
    report = core.run_report(ctx)
    assert report.metadata["query"] == "IKRetargeter 链映射"
    assert report.findings
    assert report.findings[0].node == "ik_retarget"


# --------------------------------------------------------------------------- #
# ui_web
# --------------------------------------------------------------------------- #
def test_ui_web_escapes_dynamic_text_and_has_no_external_links(core, ui):
    payload = core.search("权重", top_k=2)
    payload["query"] = '<script>alert("x")</script> http://evil.example/a'
    payload["results"][0]["excerpt"] = '<img src=x onerror=alert(1)> & <b>粗体</b>'
    markup = ui.render_panel_html(payload)
    assert "<script>" not in markup
    assert "<img" not in markup
    assert "&lt;script&gt;" in markup
    assert "&lt;img" in markup
    assert "http://" not in markup and "https://" not in markup
    assert "<link" not in markup.lower()
    assert "var(--tak-" in markup
    assert ui.render_report_html(payload) == markup


def test_ui_web_has_no_literal_colors_and_no_cdn(ui):
    markup = ui.render_panel_html({"results": [], "query": "test",
                                   "stats": {"documents": 5, "terms": 10}})
    color_patterns = (
        re.compile(r"#[0-9a-fA-F]{3,8}\b"),
        re.compile(r"\brgba?\s*\("),
        re.compile(r"\bhsla?\s*\("),
        re.compile(r"\b(?:white|black|red|blue|green|gray|grey|orange|"
                   r"yellow|purple|pink|cyan|magenta|silver|transparent)"
                   r"(?![-\w])", re.IGNORECASE),
    )
    for pattern in color_patterns:
        assert pattern.search(markup) is None, pattern.pattern
    for token in ("<link", "<script", "src=", "href=", "cdn", "https://",
                  "http://"):
        assert token not in markup.lower()


# --------------------------------------------------------------------------- #
# 静态工程约束
# --------------------------------------------------------------------------- #
def test_all_new_python_files_parse_as_python39():
    for path in NEW_PY_FILES:
        with open(path, "r", encoding="utf-8") as handle:
            ast.parse(handle.read(), filename=path, feature_version=(3, 9))


def test_knowledge_core_has_no_dcc_or_third_party_imports():
    allowed = {"__future__", "math", "os", "re", "dataclasses", "typing",
               "html"}
    forbidden_dcc = {"maya", "pymxs", "pyfbsdk", "unreal"}
    forbidden_third_party = {"sklearn", "jieba", "faiss", "numpy", "scipy",
                             "requests", "sentence_transformers"}
    for path in NEW_PY_FILES[:2]:  # tak/core/knowledge 的两个模块
        with open(path, "r", encoding="utf-8") as handle:
            tree = ast.parse(handle.read(), filename=path,
                             feature_version=(3, 9))
        roots = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                roots.extend(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0 \
                    and node.module:
                roots.append(node.module.split(".")[0])
        assert not (set(roots) & forbidden_dcc), roots
        assert not (set(roots) & forbidden_third_party), roots
        assert set(roots) <= allowed, roots
