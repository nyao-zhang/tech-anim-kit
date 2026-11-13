# 绑定知识库 RAG（`rig_kb`）

> 把仓库里的 `knowledge/*.md` 变成可检索的片段库，返回带 `doc_id` / `section` / `path` 的引用，而不是让模型背一段可能记错的规范。

**适用版本** TAK `0.3.2`　·　**DCC** maya / max / mobu / unreal　·　**权限** `scene.read`　·　**UI** `web`

## 1. 它解决什么问题

「蒙皮影响骨到底几根」「FBX 导出帧范围是多少」「UE 里 `EditorDialogLibrary` 到底存不存在」——这些问题在团队里每天被问，答案其实都写在仓库文档里，但没人愿意为了一个数字去读 200 行 Markdown。

这个模块把种子文档切成片段、建好倒排索引，你打一个短查询就拿到最相关的几段原文，每段都带出处。查「蒙皮 权重 上限」，第一条直接给出「`skin.max_influences` 为 4，即每个顶点最多受 4 根骨骼影响」，并告诉你是 `knowledge/skin_weights.md` 的「影响数上限」小节。

对技术动画师来说，它省掉的是「反复问人 + 反复翻文档」；对 Agent Console 来说，它提供的是可注入对话的带引用上下文，让回答有据可查，而不是模型编的。因为它只读本地仓库、不联网、不写缓存，同一句查询在任何机器上、任何时候跑，返回的片段和顺序都一样。

## 2. 怎么打开

| 入口 | 操作 |
|---|---|
| Maya 菜单 | 未挂菜单。`MENU_ITEMS` 里没有本项；从「打开面板」进 Web 面板再切「知识库」页签 |
| Python | 见下 |
| Web 面板 | 页签 `data-tab="kb"`（标题「知识库」）；深链 `?tab=kb` |
| HTTP API | `GET /api/knowledge?q=...&top_k=3` 或 `POST /api/knowledge` |

```python
from tools.rig_kb import core
print(core.search("蒙皮 权重 上限", top_k=1)["results"][0]["section"])
```

```text
GET /api/knowledge?q=命名规范&top_k=2
200 {"ok": true, "query": "命名规范", "top_k": 2, "count": 2,
     "hits": [{"doc_id": "rig_naming", "title": "绑定命名规范与骨架层级",
               "section": "命名正则与 side token", "score": 14.777616, ...}, ...]}
```

## 3. 输入

| 函数 | 参数 | 类型 | 默认值 | 说明 |
|---|---|---|---|---|
| `search` | `query` / `top_k` / `options` | `Any` / `int` / `dict` | 必填 / `3` / `None` | 空 query 直接返回 `[]` 并给 warning；`top_k` 最终由内核夹到 `0..50` |
| `context_for` | `query` / `top_k` / `max_chars` / `options` | `Any` / `int` / `int` / `dict` | 必填 / `3` / `1200` / `None` | 生成带引用的上下文，`<=0` 返回空串 |
| `citations` | `query` / `top_k` | `Any` / `int` | 必填 / `3` | 只返回结构化引用 |
| `run_report` | `ctx` | `Any` | 必填 | query 取 `ctx.options["query"]` → `ctx.snapshot["metadata"]["query"]` → 缺省概览查询 |

`options` 只有一个键有意义：`use_embeddings`。它只在注入**真实** provider 时生效（离线 / mock 的 `embed()` 是占位向量，会被 `_provider_is_real()` 判否）；生效时用 `MAX_RERANK_POOL = 64` 个 BM25 候选做余弦重排，`ranked_by` 变成 `"bm25+embeddings"`。默认不传就是纯 BM25。

种子文档固定是仓库 `knowledge/` 下的 5 篇：

| `doc_id` | 标题 | 文件 |
|---|---|---|
| `rig_naming` | 绑定命名规范与骨架层级 | `knowledge/rig_naming.md` |
| `skin_weights` | 蒙皮权重规范 | `knowledge/skin_weights.md` |
| `fbx_ue_pipeline` | Maya 到 UE 的 FBX 导出与导入校验 | `knowledge/fbx_ue_pipeline.md` |
| `ik_retarget` | IK Rig 与 Retargeter 链映射 | `knowledge/ik_retarget.md` |
| `dcc_compat` | DCC 兼容矩阵与工程约束 | `knowledge/dcc_compat.md` |

`ctx.options["top_k"]` 缺省是 `3`（`run_report` 里 `_top_k(options.get("top_k", 3))`）。HTTP 侧另有收窄：`top_k` 默认 `3`、限 `1..10`，`q` / `query` 为空直接 400。

## 4. 输出

`search()` 返回 `{"ok","tool_id","query","top_k","count","results","citations","context","stats","offline","warning"}`。每条命中：

| 字段 | 类型 | 说明 |
|---|---|---|
| `doc_id` / `title` / `path` | `str` | 文档 id、一级标题、相对仓库路径 |
| `section` | `str` | 最近的 `##` 小标题；首个标题之前的内容记作「概述」 |
| `score` / `bm25_score` | `float` | 最终分 / 原始 BM25 分（重排时二者不同），6 位小数 |
| `excerpt` | `str` | 单行摘要，上限 200 字符，超长以 `…` 结尾 |
| `citation` | `dict` | `doc_id` / `title` / `section` / `excerpt` / `path` 五件套 |
| `tags` | `list` | 来自文档的 `Tags:` 行；缺失时是 `[doc_id]` |
| `rank` / `chunk_index` / `ranked_by` | `int` / `int` / `str` | 名次 / 全库片段序号 / `bm25` 或 `bm25+embeddings` |

真实输出（`search("蒙皮 权重 上限", top_k=3)`，首条 `citation` 原样）：

```json
{"doc_id": "skin_weights", "title": "蒙皮权重规范", "section": "影响数上限",
 "excerpt": "skin.max_influences 为 4，即每个顶点最多受 4 根骨骼影响。 确定性规则 skin.max_influences 读取快照里的 skin.max_influences_found，超过 spec 上限即报 ERROR，修复建议是执行 limit influences 到 4 根并重新归一。 spec_enforcer 与 rig_health_guard 都按同一份 rig…",
 "path": "knowledge/skin_weights.md"}
```

另外两条：`[("影响数上限", "knowledge/skin_weights.md"), ("权重和与归一化", "knowledge/skin_weights.md"), ("采样上限与容差", "knowledge/skin_weights.md")]`——同一文档的三个不同小节。

第一条的完整命中：`score=21.942885`、`bm25_score=21.942885`、`rank=1`、`chunk_index=23`、`ranked_by="bm25"`、`tags=["skin","weights","influences","deformer","normalize"]`。

`stats` 来自 `describe()`，实测：`documents=5`、`chunks=28`、`terms=1593`、`avg_chunk_length=131.9286`、`bm25={"k1":1.5,"b":0.75}`、`embedding={"enabled": false, ...}`、`offline=true`。

`run_report(ctx)` 把每条命中转成一条 `rig_kb.hit` 的 INFO Finding（`node=doc_id`，`fix_hint` 写 `doc_id` + `section`），`metadata` 含 `tool_id` / `query` / `top_k` / `count` / `results` / `citations` / `context` / `stats` / `safety` / `warning`。

## 5. 内部机制

### 5.1 分词：中文单字 + bigram，英文小写词元

`tokenize()` 先把文本转小写，然后逐字符扫描：

- 遇到 CJK 字符（`0x3400–0x4DBF`、`0x4E00–0x9FFF`、`0x3040–0x30FF`、`0xAC00–0xD7AF`），把连续 CJK 串按「每个单字 + 每个相邻 bigram」展开，保序、不去重。`命名` → `命 / 命名 / 名`。
- 遇到 `a-z`、`0-9`、`_`，连续取出作为一个词元。`hand_l` → `hand_l`；`IK Rig` → `ik / rig`。

不需要任何分词器依赖。代价是单字会带来噪声——第 7 节有实测。

### 5.2 切片：按 `##` 分节，再按空行聚合

`chunk_text()` 以 `## ` 行切节，节内按空行切块，逐块累加直到接近 `MAX_CHUNK_CHARS = 400`；单块超长时用 `_hard_split()` 均分，避免出现超短尾片。摘要 `excerpt` 上限 200 字符，先去掉 `#` 前缀再压成单行。

索引时**每个**片段都带 `section` 词元；文档标题与 `Tags:` 只挂在**第一个**片段上——否则按标题检索时所有片段都命中，长度归一反而打乱顺序。

### 5.3 评分与排序

标准 BM25，参数 `_K1 = 1.5`、`_B = 0.75`：

```text
idf    = ln(1 + (N - df + 0.5) / (df + 0.5))
score += idf * (k1 + 1) * tf / (tf + k1 * (1 - b + b * dl / avgdl))
```

`idf <= 0` 的词元跳过（出现在每篇文档里的词不加分）。查询先去重，截断到 `MAX_QUERY_CHARS = 20000` 字符、`MAX_QUERY_TERMS = 256` 个词元。

排序键固定三元组：

```text
(-score, doc_id, chunk_index)
```

即 **分数降序 → `doc_id` 升序 → 片段序号升序**。同分时 `doc_id` 字母序在前；同一文档内同分按切片顺序。没有时间戳、没有随机数。

`use_embeddings` 与真实 provider 同时具备时，取排序后的前 64 个候选算余弦，用余弦分**替换** BM25 分再排一次；任何异常（provider 抛错、返回长度不符、向量非法）都回退纯 BM25，`ranked_by` 保持 `"bm25"`。

### 5.4 哪些是确定性的，AI 在哪

检索、切分、打分、排序全部确定性。AI 的唯一位置是「可选的余弦重排」——而且默认关闭：`KnowledgeBase(provider=None)` 时 `_embedding_enabled=False`；传离线 `MockProvider` 也会被 `_provider_is_real()` 识别为 mock 并禁用，因为它的 `embed()` 只是 `[len%97, len%31, 1.0]` 的占位向量，用它重排会让结果无意义地漂移。

换句话说：不配真实 provider，这个模块里没有任何 AI 成分。

## 6. 典型用法

### 6.1 一次查询拿引用

```python
from tools.rig_kb import core
r = core.search("蒙皮 权重 上限", top_k=3)
for h in r["results"]:
    print(h["rank"], h["doc_id"], h["section"], h["score"])
print(r["citations"][0]["path"])
```

真实输出：

```text
1 skin_weights 影响数上限 21.942885
2 skin_weights 权重和与归一化 13.325016
3 skin_weights 采样上限与容差 8.87797
knowledge/skin_weights.md
```

### 6.2 生成可直接注入对话的上下文

```python
print(core.context_for("IK 重定向", top_k=2, max_chars=600))
```

真实输出：

```text
检索上下文（本地离线知识库；每条含 引用 doc_id / section / path）：
[1] 引用 doc_id=ik_retarget | title=IK Rig 与 Retargeter 链映射 | section=链映射数据契约 | path=knowledge/ik_retarget.md
    ik_retarget_guard 从 snapshot.metadata.retarget 或 options.retarget 读取重定向数据，options 优先。 ik_rig.chains 声明链名与骨骼列表，ik_rig.retarget_root 指定重定向根骨；retargeter.chain_mapping 记录源链到目标链的映射。 内置规则覆盖链映射完整性、骨骼命名不匹配、r…
[2] 引用 doc_id=ik_retarget | title=IK Rig 与 Retargeter 链映射 | section=UE 侧可用 API | path=knowledge/ik_retarget.md
    docs/COMPATIBILITY.md 的实测结论：UE 5.7.2 与 5.8.4 两个版本的 27 项 Python API 探测结果完全一致。 `IKRigDefinition` 与 `IKRetargeter` 均已暴露，`Control
```

### 6.3 同一小节会被切成多段

`GET /api/knowledge?q=命名规范&top_k=2` 实测两条命中同属 `rig_naming` 的「命名正则与 side token」，分数 `14.777616` 与 `7.774714`——因为该小节超过 400 字符被切成两段，各自独立打分。排序仍是「分数降序 → `doc_id` → 片段序号」，所以分高的那段在前。

### 6.4 单字召回的真实表现

```python
r = core.search("不存在的词xyz", top_k=3)
print([(h["doc_id"], h["section"], h["score"]) for h in r["results"]])
```

```text
[('dcc_compat', 'UE 5.7 / 5.8 API 探测结论', 10.785608), ('dcc_compat', 'ML Deformer 未暴露', 9.483083), ('ik_retarget', 'UE 侧可用 API', 7.700381)]
```

这句查询在语义上什么都问不到，但因为分词产出了 `不 / 不存 / 存 / 存在 / 在 / 在的 / 的 / 的词 / 词`，「的」「在」这类高频单字把 `dcc_compat` 顶到了 10.78 分。这是纯词法检索的真实短板，见第 7 节。

## 7. 边界与限制

- **只检索仓库内的 `knowledge/*.md`。** 目录缺失或读不到任何 `.md` 时，回退到 5 篇内置最小文档（`path` 形如 `<builtin>/rig_naming.md`），保证任何环境都能跑；不会去读用户自己的文档目录。
- **没有向量模型，所以「看起来不聪明」。** 纯 BM25 词法匹配：查「权重上限」能命中，因为原文有「影响数上限」里的 `max_influences`；但查「骨骼蒙皮最多几根骨」这种口语化长句，靠的是单字碰运气。同义词、缩写、错别字都不在能力范围内。实测 `不存在的词xyz` 仍返回 3 条 10 分级结果，就是单字噪声——`命名` 会展开成 `命 / 命名 / 名`，而「的」「在」「不」这类高频字几乎每篇都有。这是不用第三方分词器的代价，换来的是零依赖与完全可复现。
- **不联网、不写场景、不写缓存文件。** 每次 `_knowledge_base()` 都新建 `KnowledgeBase()` 重新读盘建索引，不落任何缓存；`offline` 恒为 `true`。离线 `MockProvider.embed()` 的占位向量被 `_provider_is_real()` 显式排除——即使传 `use_embeddings=True`，没有真实 provider 也不会重排。
- **`top_k` 三处口径不同。** 工具默认 `3`；内核夹到 `0..50`；HTTP 限 `1..10`。写脚本按 3 / 50 理解，调接口按 10 理解。
- **空 query 不返回「全部文档」。** 与 `motion_library` 相反，这里空 query 直接返回 `[]`，`warning` 是「query 为空，已返回空结果」。

## 8. 与其它模块的关系

- **Agent Console**：`AgentSession._knowledge_context()` 直接 `from tak.core.knowledge import KnowledgeBase`，优先取结构化 `citations`，取不到就把 `context_for()` 的字符串折成一条 `{"text": ...}`。SEARCH 意图的 `citations` 就是本模块同一份数据。
- **与 `tools/rig_kb/core.py` 的关系**：Web 的 `/api/knowledge` 直接 import `tak.core.knowledge`，不经过这个插件层；插件层是给 `runner` / 脚本用的薄封装（多返回 `citations` / `context` / `stats` / `warning`，并支持 `run_report`）。
- **与 `motion_library` 的分工**：两者结构对称（都是 BM25 + 片段/条目 + 引用字段），但分词器不同（这里保留 `_`，动作库把它当分隔符），索引对象不同（文档片段 vs 动作 clip）。谁给谁注入上下文：知识库注入「规范怎么说」，动作库注入「库里有哪些动作」，互不依赖。
- **规范来源**：`knowledge/rig_naming.md` 与 `schema/rig_spec.default.json` 描述的是同一套规范；知识库给的是解释文本，`tak.core.rules` 给的是确定性判定，两者不互相替代。
