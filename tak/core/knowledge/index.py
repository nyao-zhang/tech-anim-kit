# -*- coding: utf-8 -*-
"""本地离线绑定知识库：确定性 BM25 片段检索（零第三方 / 零 DCC / 零网络）。

设计要点
--------
* 只依赖标准库：``os`` / ``re`` / ``math`` / ``dataclasses`` / ``typing``；
  不 import 任何向量数据库、分词器、embedding 库，也不联网。
* 中文不需要分词器：按「单字 + 相邻 bigram」切分；英文按小写词元。
* 检索在**片段级**做 BM25，命中后回填 ``section``，并给出结构化
  ``citation``（doc_id / title / section / excerpt / path）。
* 结果严格可复现：排序 ``score 降序 → doc_id 升序 → 片段序号升序``；
  不写时间戳、随机数或缓存文件。
* ``options["use_embeddings"]`` 仅在注入了**真实** provider 时启用余弦重排；
  离线 / mock provider 的 ``embed()`` 是确定性占位向量，绝不用它改变排序。
"""
from __future__ import annotations

import math
import os
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

# --------------------------------------------------------------------------- #
# 常量
# --------------------------------------------------------------------------- #
_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(_HERE)))
SEED_DIR = os.path.join(_REPO_ROOT, "knowledge")

MAX_CHUNK_CHARS = 400       # 片段目标上限
MAX_EXCERPT_CHARS = 200     # 返回给调用方的摘要上限
MAX_QUERY_CHARS = 20000     # 超长 query 截断，防止病态输入
MAX_QUERY_TERMS = 256       # 参与打分的去重词元上限
MAX_RERANK_POOL = 64        # 余弦重排的最大候选数
MAX_TOP_K = 50
_K1 = 1.5
_B = 0.75
_CJK_RANGES = ((0x3400, 0x4DBF), (0x4E00, 0x9FFF),
               (0x3040, 0x30FF), (0xAC00, 0xD7AF))
_TAGS_RE = re.compile(r"^tags?\s*[:：]\s*(.+)$", re.IGNORECASE)
_TAG_SPLIT_RE = re.compile(r"[,，、;；|]")


# --------------------------------------------------------------------------- #
# 数据结构
# --------------------------------------------------------------------------- #
@dataclass
class Document:
    """一篇种子知识文档；纯数据，可 JSON 序列化。"""

    doc_id: str
    title: str
    text: str
    tags: List[str] = field(default_factory=list)
    path: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "doc_id": self.doc_id,
            "title": self.title,
            "text": self.text,
            "tags": list(self.tags),
            "path": self.path,
        }


# --------------------------------------------------------------------------- #
# 分词（无需第三方分词器）
# --------------------------------------------------------------------------- #
def _is_cjk(ch: str) -> bool:
    code = ord(ch)
    for low, high in _CJK_RANGES:
        if low <= code <= high:
            return True
    return False


def _is_ascii_word(ch: str) -> bool:
    return ("a" <= ch <= "z") or ("0" <= ch <= "9") or ch == "_"


def tokenize(text: Any) -> List[str]:
    """英文小写词元 + 中文单字与相邻 bigram；纯确定性、保序、不去重。

    >>> tokenize("IK Rig")
    ['ik', 'rig']
    >>> tokenize("命名")
    ['命', '命名', '名']
    """
    if text is None:
        return []
    raw = str(text).lower()
    tokens: List[str] = []
    index = 0
    size = len(raw)
    while index < size:
        ch = raw[index]
        if _is_cjk(ch):
            end = index
            while end < size and _is_cjk(raw[end]):
                end += 1
            run = raw[index:end]
            for offset in range(len(run)):
                tokens.append(run[offset])
                if offset + 1 < len(run):
                    tokens.append(run[offset:offset + 2])
            index = end
        elif _is_ascii_word(ch):
            end = index
            while end < size and _is_ascii_word(raw[end]):
                end += 1
            tokens.append(raw[index:end])
            index = end
        else:
            index += 1
    return tokens


# --------------------------------------------------------------------------- #
# 文本切分
# --------------------------------------------------------------------------- #
def _hard_split(block: str, limit: int = MAX_CHUNK_CHARS) -> List[str]:
    """把单个超长块均分成若干 <= ``limit`` 的片，避免末尾出现超短尾片。"""
    length = len(block)
    count = max(1, (length + limit - 1) // limit)
    size = max(1, (length + count - 1) // count)
    pieces = [block[start:start + size].strip()
              for start in range(0, length, size)]
    return [piece for piece in pieces if piece] or [block[:limit]]


def _excerpt(text: Any, limit: int = MAX_EXCERPT_CHARS) -> str:
    """把片段折叠成单行摘要，超过上限时截断并加省略号。"""
    lines: List[str] = []
    for line in str(text or "").splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            stripped = stripped.lstrip("#").strip()
        lines.append(stripped)
    cleaned = re.sub(r"\s+", " ", " ".join(lines)).strip()
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[:limit - 1].rstrip() + "…"


def chunk_text(text: Any, section_hint: str = "") -> List[Dict[str, str]]:
    """把长文档切成不超过 ``MAX_CHUNK_CHARS`` 的片段。

    优先在 ``##`` 小标题与空行边界切分；返回
    ``[{"text": ..., "section": ...}, ...]``，``section`` 取自最近的 ``##``
    标题，首个标题之前的内容用 ``section_hint`` 或 ``概述``。
    """
    content = str(text or "")
    sections: List[Dict[str, str]] = []
    current_section = str(section_hint or "").strip() or "概述"
    current_lines: List[str] = []
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("## ") or stripped == "##":
            if current_lines:
                sections.append({"section": current_section,
                                 "body": "\n".join(current_lines).strip()})
                current_lines = []
            current_section = stripped.lstrip("#").strip() or current_section
            continue
        current_lines.append(line)
    if current_lines:
        sections.append({"section": current_section,
                         "body": "\n".join(current_lines).strip()})

    chunks: List[Dict[str, str]] = []
    for part in sections:
        body = part["body"]
        if not body:
            continue
        blocks = [block.strip() for block in re.split(r"\n\s*\n", body)
                  if block.strip()]
        if not blocks:
            blocks = [body]
        buf = ""
        for block in blocks:
            candidate = (buf + "\n\n" + block).strip() if buf else block
            if len(candidate) <= MAX_CHUNK_CHARS:
                buf = candidate
                continue
            if buf:
                chunks.append({"text": buf, "section": part["section"]})
                buf = ""
            if len(block) <= MAX_CHUNK_CHARS:
                buf = block
                continue
            for piece in _hard_split(block):
                chunks.append({"text": piece, "section": part["section"]})
        if buf:
            chunks.append({"text": buf, "section": part["section"]})
    if not chunks and content.strip():
        chunks.append({"text": content.strip()[:MAX_CHUNK_CHARS],
                       "section": str(section_hint or "").strip() or "概述"})
    return chunks


# --------------------------------------------------------------------------- #
# 种子文档加载
# --------------------------------------------------------------------------- #
def _parse_seed_markdown(text: Any, doc_id: str, path: str) -> Document:
    """解析一级标题为 title、``Tags:`` 行为标签，其余正文保留（含 ``##``）。"""
    title = ""
    tags: List[str] = []
    kept: List[str] = []
    for line in str(text or "").splitlines():
        stripped = line.strip()
        if not title and stripped.startswith("# "):
            title = stripped[2:].strip()
            continue
        match = _TAGS_RE.match(stripped)
        if match:
            tags = [item.strip() for item in _TAG_SPLIT_RE.split(match.group(1))
                    if item.strip()]
            continue
        kept.append(line)
    return Document(doc_id=doc_id, title=title or doc_id,
                    text="\n".join(kept).strip(),
                    tags=tags or [doc_id], path=path)


def _display_path(filename: str) -> str:
    normalized = str(filename).replace(os.sep, "/")
    try:
        rel = os.path.relpath(filename, _REPO_ROOT).replace(os.sep, "/")
        if not rel.startswith(".."):
            return rel
    except ValueError:
        pass
    return normalized


# 目录缺失时的内置最小文档：保证任何环境（含纯 Python / 打包后）都能检索。
_FALLBACK_SPECS = (
    ("rig_naming", "绑定命名规范与骨架层级", ["naming", "joint", "schema"],
     "## 命名规范\n"
     "默认规范 schema/rig_spec.default.json：naming.joint_pattern 约束 root / pelvis / "
     "spine_01 / clavicle_L 等关节名；side_tokens 为 _L 与 _R；control_namespace 为 CTRL；"
     "case_sensitive 为 true；forbidden_tokens 禁止空格、点、竖线、冒号与 copy。\n\n"
     "## 骨架层级\n"
     "skeleton.required_joints 共 19 个，max_depth 为 12，allow_negative_scale 为 false，"
     "allow_frozen_transforms_only 为 true，joint_orient_tolerance_deg 为 0.5。"),
    ("skin_weights", "蒙皮权重规范", ["skin", "weights", "deformer"],
     "## 影响数与归一化\n"
     "skin.max_influences 为 4；weight_sum_tolerance 为 0.001；normalize 为 true。\n\n"
     "## 允许 deformer 与采样\n"
     "allowed_deformers 为 skinCluster / blendShape / cluster / wrap；"
     "适配器快照包含 max_influences_found / weight_sum_deviation / deformers / "
     "sampled_vertices / truncated，Maya 采样上限 max_sample_vertices 为 512。"),
    ("fbx_ue_pipeline", "Maya 到 UE 的 FBX 导出与导入校验", ["fbx", "ue", "export"],
     "## FBX 导出\n"
     "export.fbx：up_axis 为 Y、unit 为 cm、bake_animation 为 true、include_constraints "
     "为 false、include_meshes 为 true、frame_range 为 [1, 120]。\n\n"
     "## sidecar 与 UE 导入\n"
     "Export Bridge 写出 .tak_snapshot.json（sidecar_version 1），导出前后做快照 diff；"
     "UE 侧由 ue_import_guard 校验 compression 为 Default、root_motion 为 false、"
     "LOD allow_deformer 为 false。"),
    ("ik_retarget", "IK Rig 与 Retargeter 链映射", ["ik", "retarget", "unreal"],
     "## 链映射\n"
     "UE 5.7 / 5.8 已实测暴露 IKRigDefinition 与 IKRetargeter；"
     "ik_retarget_guard 离线校验 chain_mapping、root/pelvis 偏移与姿态偏差。\n\n"
     "## mapping asset v1.0\n"
     "mapping.py 写出 version 1.0 的资产，含 entries / unmapped_source / "
     "unmapped_target / conflicts；conflicts 与未映射项必须人工确认，绝不自动选择。"),
    ("dcc_compat", "DCC 兼容矩阵与工程约束", ["compat", "python", "unreal"],
     "## Python 与 Qt 基线\n"
     "tak/core 必须兼容 Python 3.9；Qt 绑定由 tak.compat.qt 统一处理 PySide2 / PySide6，"
     "无 Qt 时降级为 None 而不抛异常。\n\n"
     "## UE API 探测结论\n"
     "UE 5.7.2 与 5.8.4 的 27 项 API 探测结果完全一致；EditorDialogLibrary 不存在，"
     "应改用 EditorDialog；MLDeformerAsset 等类型在 Python 侧未暴露。"),
)


def _fallback_documents() -> List[Document]:
    docs: List[Document] = []
    for doc_id, title, tags, text in _FALLBACK_SPECS:
        docs.append(Document(doc_id=doc_id, title=title, text=text,
                             tags=list(tags), path="<builtin>/%s.md" % doc_id))
    return docs


def load_seed_documents(path: Optional[str] = None) -> List[Document]:
    """从仓库 ``knowledge/*.md`` 读取种子文档；目录缺失时回退内置最小文档。

    ``path`` 可为目录、单个 ``.md`` 文件或 ``None``（默认用 ``__file__``
    相对定位到仓库根下的 ``knowledge/``）。读取失败不抛异常。
    """
    target = path if path is not None else SEED_DIR
    candidates: List[str] = []
    try:
        if os.path.isdir(target):
            candidates = [os.path.join(target, name)
                          for name in sorted(os.listdir(target))
                          if name.lower().endswith(".md")
                          and not name.startswith(".")]
        elif os.path.isfile(target) and str(target).lower().endswith(".md"):
            candidates = [str(target)]
    except OSError:
        candidates = []
    docs: List[Document] = []
    for filename in candidates:
        try:
            with open(filename, "r", encoding="utf-8") as handle:
                raw = handle.read()
        except (OSError, UnicodeError):
            continue
        doc_id = os.path.splitext(os.path.basename(filename))[0]
        docs.append(_parse_seed_markdown(raw, doc_id, _display_path(filename)))
    if docs:
        return docs
    return _fallback_documents()


# --------------------------------------------------------------------------- #
# 向量工具（仅在真实 provider 且显式开启时使用）
# --------------------------------------------------------------------------- #
def _provider_is_real(provider: Any) -> bool:
    """判断 provider 是否可能产生真实语义向量。

    默认离线 provider（model=mock / offline，或 MockProvider / OfflineProvider）
    一律视为不可用：其 ``embed()`` 只是按文本长度生成的确定性占位向量，
    用它重排会让结果无意义地漂移。
    """
    if provider is None:
        return False
    model = str(getattr(provider, "model", "") or "").strip().lower()
    if model in ("", "mock", "offline"):
        return False
    names = set()
    for klass in type(provider).__mro__:
        names.add(klass.__name__.lower())
    if "mockprovider" in names or "offlineprovider" in names:
        return False
    return callable(getattr(provider, "embed", None))


def _as_vector(value: Any) -> Optional[List[float]]:
    if not isinstance(value, (list, tuple)):
        return None
    vector: List[float] = []
    for item in value:
        try:
            vector.append(float(item))
        except (TypeError, ValueError):
            return None
    return vector or None


def _cosine(left: List[float], right: Optional[List[float]]) -> float:
    if not right:
        return 0.0
    size = min(len(left), len(right))
    if size == 0:
        return 0.0
    dot = norm_left = norm_right = 0.0
    for position in range(size):
        dot += left[position] * right[position]
        norm_left += left[position] * left[position]
        norm_right += right[position] * right[position]
    if norm_left <= 0.0 or norm_right <= 0.0:
        return 0.0
    return dot / math.sqrt(norm_left * norm_right)


def _normalize_top_k(value: Any) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = 3
    return min(max(number, 0), MAX_TOP_K)


# --------------------------------------------------------------------------- #
# 知识库
# --------------------------------------------------------------------------- #
class KnowledgeBase:
    """确定性 BM25 片段检索器。

    ``documents`` 为 ``None`` 时加载种子文档；``docs_dir`` 可覆盖种子目录；
    ``provider`` 缺省为 ``None``（纯离线，不创建 provider、不联网）。
    """

    def __init__(self, documents: Optional[List[Any]] = None,
                 docs_dir: Optional[str] = None,
                 provider: Any = None) -> None:
        self.provider = provider
        self._docs_dir = docs_dir
        self._embedding_enabled = _provider_is_real(provider)
        self._provider_model = ("none" if provider is None
                                else str(getattr(provider, "model", "") or "unknown"))
        if documents is None:
            self.documents = load_seed_documents(docs_dir)
        else:
            self.documents = []
            for item in documents:
                if isinstance(item, Document):
                    self.documents.append(item)
                elif isinstance(item, dict):
                    self.documents.append(Document(
                        doc_id=str(item.get("doc_id") or ""),
                        title=str(item.get("title") or ""),
                        text=str(item.get("text") or ""),
                        tags=[str(tag) for tag in (item.get("tags") or [])],
                        path=str(item.get("path") or "")))
        self._chunks: List[Dict[str, Any]] = []
        self._postings: Dict[str, Dict[int, int]] = {}
        self._lengths: List[int] = []
        self._idf: Dict[str, float] = {}
        self._avgdl = 0.0
        self._built = False
        self.build()

    # -- 索引 -------------------------------------------------------------- #
    def build(self) -> "KnowledgeBase":
        """建倒排索引 + 片段长度 + IDF；可重复调用（add_document 后重建）。"""
        self._chunks = []
        self._postings = {}
        self._lengths = []
        self._idf = {}
        self._avgdl = 0.0
        for doc in self.documents:
            parts = chunk_text(doc.text)
            if not parts:
                parts = [{"text": doc.title or doc.doc_id, "section": "概述"}]
            for part_index, part in enumerate(parts):
                index = len(self._chunks)
                text = str(part.get("text") or "")
                section = str(part.get("section") or "概述")
                # 分节名每个片段都参与；标题 / 标签只挂在文档首个片段，
                # 避免「按文档标题检索」时所有片段都命中、被长度归一打乱顺序。
                tokens = tokenize(text) + tokenize(section)
                if part_index == 0:
                    tokens += tokenize(doc.title) + tokenize(" ".join(doc.tags))
                self._chunks.append({
                    "index": index,
                    "doc_id": doc.doc_id,
                    "title": doc.title,
                    "path": doc.path,
                    "tags": list(doc.tags),
                    "section": section,
                    "text": text,
                    "excerpt": _excerpt(text),
                })
                tf: Dict[str, int] = {}
                for term in tokens:
                    tf[term] = tf.get(term, 0) + 1
                self._lengths.append(len(tokens))
                for term, count in tf.items():
                    self._postings.setdefault(term, {})[index] = count
        total = len(self._chunks)
        if total:
            for term, bucket in self._postings.items():
                count = len(bucket)
                self._idf[term] = math.log(
                    1.0 + (total - count + 0.5) / (count + 0.5))
            self._avgdl = sum(self._lengths) / float(total)
        self._built = True
        return self

    def add_document(self, doc_id: Any, title: Any, text: Any,
                     tags: Optional[List[Any]] = None,
                     path: Any = "") -> Document:
        """追加一篇文档并重建索引，返回新建的 ``Document``。"""
        doc = Document(doc_id=str(doc_id), title=str(title), text=str(text),
                       tags=[str(tag) for tag in (tags or [])],
                       path=str(path or ""))
        self.documents.append(doc)
        self.build()
        return doc

    # -- 检索 -------------------------------------------------------------- #
    @staticmethod
    def _query_terms(query: Any) -> List[str]:
        raw = str(query or "")[:MAX_QUERY_CHARS]
        seen = set()
        terms: List[str] = []
        for term in tokenize(raw):
            if term not in seen:
                seen.add(term)
                terms.append(term)
                if len(terms) >= MAX_QUERY_TERMS:
                    break
        return terms

    def _bm25_scores(self, terms: List[str]) -> Dict[int, float]:
        scores: Dict[int, float] = {}
        avgdl = self._avgdl or 1.0
        for term in terms:
            bucket = self._postings.get(term)
            if not bucket:
                continue
            idf = self._idf.get(term, 0.0)
            if idf <= 0.0:
                continue
            weight = idf * (_K1 + 1.0)
            for index, tf in bucket.items():
                length = self._lengths[index] or 1
                denominator = tf + _K1 * (1.0 - _B + _B * length / avgdl)
                if denominator <= 0.0:
                    continue
                scores[index] = scores.get(index, 0.0) + weight * tf / denominator
        return scores

    def _rerank_scores(self, ordered: List[int], query: Any,
                       options: Optional[Dict[str, Any]]) -> Optional[Dict[int, float]]:
        """仅在「显式开启 + 真实 provider」时用余弦分数替换 BM25 分数。

        任何异常 / 非法返回都回退到纯 BM25，保证离线结果永不漂移。
        """
        opts = options if isinstance(options, dict) else {}
        if not opts.get("use_embeddings") or not self._embedding_enabled:
            return None
        pool = ordered[:MAX_RERANK_POOL]
        if not pool:
            return None
        texts = [str(query or "")[:MAX_QUERY_CHARS]]
        texts.extend(self._chunks[index]["text"] for index in pool)
        try:
            vectors = self.provider.embed(texts)
        except Exception:
            return None
        if not isinstance(vectors, (list, tuple)) or len(vectors) != len(texts):
            return None
        query_vector = _as_vector(vectors[0])
        if query_vector is None:
            return None
        scores: Dict[int, float] = {}
        for position, index in enumerate(pool):
            scores[index] = _cosine(query_vector,
                                    _as_vector(vectors[position + 1]))
        return scores

    def search(self, query: Any, top_k: int = 3,
               options: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        """BM25 片段检索；空 query / 超长 query / 未知词都稳定返回空列表，绝不抛异常。

        每项含 ``doc_id / title / score / section / excerpt / citation / tags``，
        另附 ``path / bm25_score / rank / chunk_index / ranked_by``。
        """
        try:
            limit = _normalize_top_k(top_k)
            if limit == 0:
                return []
            terms = self._query_terms(query)
            if not terms:
                return []
            bm25 = self._bm25_scores(terms)
            if not bm25:
                return []
            scores = bm25
            ranked_by = "bm25"
            reranked = self._rerank_scores(
                sorted(bm25, key=lambda i: (-bm25[i],
                                            self._chunks[i]["doc_id"], i)),
                query, options)
            if reranked is not None:
                scores = reranked
                ranked_by = "bm25+embeddings"
            ordered = sorted(
                scores,
                key=lambda i: (-scores[i], self._chunks[i]["doc_id"], i))
            results: List[Dict[str, Any]] = []
            for rank, index in enumerate(ordered[:limit], 1):
                chunk = self._chunks[index]
                citation = {
                    "doc_id": chunk["doc_id"],
                    "title": chunk["title"],
                    "section": chunk["section"],
                    "excerpt": chunk["excerpt"],
                    "path": chunk["path"],
                }
                results.append({
                    "doc_id": chunk["doc_id"],
                    "title": chunk["title"],
                    "score": round(float(scores[index]), 6),
                    "section": chunk["section"],
                    "excerpt": chunk["excerpt"],
                    "citation": citation,
                    "tags": list(chunk["tags"]),
                    "path": chunk["path"],
                    "bm25_score": round(float(bm25.get(index, 0.0)), 6),
                    "rank": rank,
                    "chunk_index": chunk["index"],
                    "ranked_by": ranked_by,
                })
            return results
        except Exception:
            return []

    def context_for(self, query: Any, top_k: int = 3, max_chars: int = 1200,
                    options: Optional[Dict[str, Any]] = None) -> str:
        """拼接检索片段并带引用，供 Agent Console 注入上下文；严格不超过 max_chars。"""
        try:
            limit = int(max_chars)
        except (TypeError, ValueError):
            limit = 1200
        if limit <= 0:
            return ""
        hits = self.search(query, top_k=top_k, options=options)
        if not hits:
            return ""
        lines = ["检索上下文（本地离线知识库；每条含 引用 doc_id / section / path）："]
        for number, hit in enumerate(hits, 1):
            lines.append("[%d] 引用 doc_id=%s | title=%s | section=%s | path=%s"
                         % (number, hit["doc_id"], hit["title"],
                            hit["section"], hit["path"]))
            lines.append("    %s" % hit["excerpt"])
        return "\n".join(lines)[:limit]

    def citations(self, query: Any, top_k: int = 3) -> List[Dict[str, Any]]:
        """返回结构化的引用列表（doc_id / title / section / excerpt / path）。"""
        return [dict(hit["citation"])
                for hit in self.search(query, top_k=top_k)]

    def describe(self) -> Dict[str, Any]:
        """文档数、片段数、词条数、种子目录与 embedding 状态。"""
        resolved = self._docs_dir if self._docs_dir is not None else SEED_DIR
        seed_dir = str(resolved).replace(os.sep, "/")
        offline = not self._embedding_enabled
        return {
            "documents": len(self.documents),
            "doc_count": len(self.documents),
            "chunks": len(self._chunks),
            "chunk_count": len(self._chunks),
            "terms": len(self._idf),
            "term_count": len(self._idf),
            "vocab_size": len(self._idf),
            "avg_chunk_length": round(self._avgdl, 4),
            "docs_dir": seed_dir,
            "seed_dir": seed_dir,
            "provider": self._provider_model,
            "offline": offline,
            "embedding": {
                "enabled": self._embedding_enabled,
                "offline": offline,
                "provider": self._provider_model,
                "note": ("真实 provider 可用，显式开启 use_embeddings 才做余弦重排"
                         if self._embedding_enabled
                         else "禁用：离线占位向量不改变排序"),
            },
            "embedding_enabled": self._embedding_enabled,
            "bm25": {"k1": _K1, "b": _B},
            "built": self._built,
            "version": "1.0.0",
        }
