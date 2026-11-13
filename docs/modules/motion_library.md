# 动作库语义检索（`motion_library`）

> 在本机动作资产里用一句中文找到「那个跑步循环」，返回带 `clip_id`、时长、类型和中文匹配理由的命中列表，全程只读、离线、可复现。

**适用版本** TAK `0.3.2`　·　**DCC** maya / max / mobu / unreal　·　**权限** `scene.read`　·　**UI** `web`

## 1. 它解决什么问题

整理动作库最烦的不是没有素材，是「我记得有一版跑步循环，但它在哪个文件夹」。目录名可能是 `run_fwd_01`、可能是 `LF_Run_Loop`、也可能被同事改成拼音。翻十分钟找到的，往往还不是你想要的那一版。

这个模块把这些元数据编成一份可检索的索引：名称、标签、骨骼名、时长、循环标记、是否有根运动，全都可以作为查询条件。你打「跑步 循环 一秒内」，它按文本相关度 + 动作类型加权排好序给你，每条命中还附一句中文理由——「命中关键词：跑、循环；类型查询命中：奔跑」。

要接管线时它给的是 `clip_id`，不是文件名。`clip_id` 稳定、唯一、跨 DCC 一致，可以直接写进动画状态机的配置或 UE 的资产引用。

对技术动画师来说，它省掉的是「反复翻文件夹 + 靠肉眼比对缩略图」；对下游工具来说，它提供的是一个纯数据接口，`search()` 返回的 dict 可以直接序列化成 JSON。

## 2. 怎么打开

| 入口 | 操作 |
|---|---|
| Maya 菜单 | 「TAK / Tech Anim Kit → 动作检索」。菜单项 `tak_motion_library` 会启动本地 Web 面板并直接跳到动作检索页签 |
| Python | 见下 |
| Web 面板 | 页签 `data-tab="motion"`（标题「动作检索」）；深链 `?tab=motion` |
| HTTP API | `POST /api/motion` |

```python
from tools.motion_library import core
print(core.search("跑步 循环", top_k=3)["results"][0]["clip_id"])
```

```text
POST /api/motion
{"query": "走路", "top_k": 2, "options": {"filters": {"loopable": true}}}
200 {"ok": true, "query": "走路", "top_k": 2, "count": 2,
     "hits": [{"clip_id": "walk_back_01", "score": 11.219565,
               "why": "命中关键词：走路；类型查询命中：行走；仅循环动作"}, ...],
     "stats": {...}, "warning": ""}      # hits 只列首条，stats 略
```

## 3. 输入

| 函数 | 参数 | 类型 | 默认值 | 说明 |
|---|---|---|---|---|
| `search` | `query` / `top_k` / `options` | `Any` / `int` / `dict` | 必填 / `5` / `None` | 空串 query 表示「列出全部」并按 `clip_id` 升序；`top_k` 归一化到 `0..50`，传 0 返回空列表；`options` 也接受 `options["motion"]` 嵌套写法 |
| `context_for` | `query` / `top_k` / `max_chars` | `Any` / `int` / `int` | 必填 / `3` / `800` | 生成对话用上下文，`max_chars` 硬截断，`<=0` 返回空串 |
| `similar_to` | `clip_id` / `top_k` | `Any` / `int` | 必填 / `5` | 目标 clip 找不到返回 `[]` |
| `run_report` | `ctx` | `Any` | 必填 | query 取 `ctx.options["query"]`，回退 `ctx.snapshot["metadata"]["query"]`；`top_k` 只读 `ctx.options` |

`options` 只有两件事：`motion_type`（12 类之一 `idle` / `walk` / `run` / `jump` / `attack` / `hit` / `turn` / `crouch` / `climb` / `dance` / `death` / `other`，非法值忽略）和 `filters`（硬过滤：`min_duration` / `max_duration` / `loopable` / `root_motion` / `has_bone` / `min_fps`，不满足**直接排除**，不是降权）。

HTTP 侧 `POST /api/motion` 有自己的收窄：`top_k` 默认 `8` 且限 `1..20`，`filters` 里的 `min_duration` / `max_duration` / `min_fps` 会被强转 `float`（失败则丢弃），`loopable` / `root_motion` 强转 `bool`。

## 4. 输出

`search()` 返回 `{"ok","tool_id","query","top_k","count","results","clips","stats","offline","warning"}`。每条命中：

| 字段 | 类型 | 说明 |
|---|---|---|
| `clip_id` / `name` / `path` | `str` | 稳定 id（下游引用键）/ clip 名 / 来源路径 |
| `score` / `text_score` / `type_score` | `float` | 总分 / BM25 分 / 类型加权分，6 位小数 |
| `motion_type` | `str` | 推断出的 12 类英文 key |
| `duration` / `loopable` / `root_motion` | `float` / `bool` / `bool` | 秒（`frame_count / fps`）/ 是否循环 / 是否含根运动 |
| `bones` / `tags` | `list` | 骨骼与标签原文 |
| `rank` / `matched_terms` / `why` | `int` / `list` / `str` | 从 1 开始 / 展平后的命中词（最多 4 个）/ 中文匹配理由 |

真实输出（`search("跑步 循环", top_k=3)`，取关键字段打印，值未改动）：

```json
[{"clip_id": "run_fwd_01", "name": "Run_Fwd_01 奔跑 前进 循环",
  "score": 9.375138, "text_score": 6.375138, "type_score": 3.0,
  "motion_type": "run", "duration": 0.8, "loopable": true, "root_motion": true,
  "rank": 1, "matched_terms": ["跑", "循环"],
  "why": "命中关键词：跑、循环；类型查询命中：奔跑"},
 {"clip_id": "climb_ladder_01", "score": 2.742315, "text_score": 2.742315,
  "type_score": 0.0, "motion_type": "climb", "duration": 1.333333,
  "rank": 2, "why": "命中关键词：循环"},
 {"clip_id": "crouch_idle_01", "score": 2.742315, "text_score": 2.742315,
  "type_score": 0.0, "motion_type": "crouch", "duration": 2.0,
  "rank": 3, "why": "命中关键词：循环"}]
```

注意第 2、3 名同分 `2.742315`——按 `clip_id` 升序，`climb_ladder_01` 排在 `crouch_idle_01` 前面。`stats` 来自 `library.describe()`：`contract`、`clip_count`、`types`（12 类各自数量）、`bone_count`、`bones`（最多 40 个，升序）、`has_audio`、`offline`。

## 5. 内部机制

### 5.1 分词：下划线当分隔符

`features.tokenize()`：英文转小写后按「字母 + 数字」切词元，`_` **不算**词内字符；中文按 `单字 + 相邻 bigram` 展开。所以 `Walk_Fwd_01` → `walk / fwd / 01`，`hand_l` → `hand / l`——这样查询「walk」「hand」能直接命中 clip 名与骨骼名。中文「走路」会同时产出 `走 / 走路 / 路`，所以「走」也能命中。（对照：`tak.core.knowledge` 的分词把 `_` 算作词内字符，`hand_l` 会整体保留；两个模块的分词器不同，别混用。）

### 5.2 BM25：语料怎么组、分怎么算

每篇「文档」是一个 clip，词元序列是 `tokenize(name) * 2 + tokenize(tags) + tokenize(bones)`——`name` 重复 2 次（`_NAME_REPEAT = 2`），因为 clip 名比标签更能代表动作语义。

参数：`_K1 = 1.5`、`_B = 0.75`。IDF 用 `ln(1 + (N - df + 0.5) / (df + 0.5))`，`idf <= 0` 的词元直接跳过。逐词累加：

```text
score += idf * (k1 + 1) * tf / (tf + k1 * (1 - b + b * dl / avgdl))
```

`dl` 是该 clip 的词元总数，`avgdl` 是库内平均值。查询词先去重、截断到 2000 字符 / 64 个词元（`MAX_QUERY_CHARS` / `MAX_QUERY_TERMS`）。

### 5.3 类型加权

| 情形 | 加分 |
|---|---|
| `options["motion_type"]` 显式指定且命中该 clip 类型 | `type_score += 6.0`（`_FORCED_TYPE_WEIGHT`） |
| 未显式指定，但查询词元命中了某个动作类型关键词 | `type_score += 3.0`（`_TYPE_WEIGHT`） |

类型关键词表 `MOTION_KEYWORDS` 每类是一组中英文词，例如 `run`: `run / running / sprint / jog / 奔跑 / 跑步 / 冲刺 / 跑`。加分是**一次性**的，不随命中类型个数累加。

clip 的类型先看 `metadata["motion_type"]`（人工标注最可信），再看名称 + 标签关键词，推断优先级为 `crouch → climb → death → hit → attack → jump → turn → dance → run → walk → idle → other`——所以 `Crouch_Idle_01` 判 `crouch` 而不是 `idle`。

### 5.4 硬过滤与排序键

`filters` 在打分**之前**就把候选集缩小（`allowed` 集合），不满足的 clip 根本不参与 BM25。`has_bone` 忽略大小写做子集判断；`min_fps` 遇到非法 `fps` 直接判不通过。

排序键固定为三元组，从主到次：

```text
(-score, clip_id, 建库序号)
```

即：**分数降序 → clip_id 升序 → 建库顺序升序**。同分时 `clip_id` 小的在前；`clip_id` 也相同（理论上不会，构造时已去重加后缀 `_2`）才看建库顺序。这就是「同分怎么排」的全部答案，没有时间戳、没有随机数。

空 query 走另一条分支：不参与 BM25，直接按 `(clip_id, 建库序号)` 升序返回前 `top_k` 条，`score` / `text_score` / `type_score` 全是 `0.0`，`why` 是「空查询：按名称列出全部动作」。

`similar_to()` 另算一套：`SIMILARITY_WEIGHTS` 里的 15 个数值特征逐个在库内做 min-max 归一化后乘权重（`root_speed_mean` 1.2、`joint_speed_mean` 1.1 最高，`fps` / `frame_count` 0.3 最低），做加权余弦；全库同值（无区分度）的特征自动跳过——内置库里 `fps` 全是 30，所以实测是「共同特征 14 项」；排除自身；排序同样是 `(-score, clip_id, index)`。

### 5.5 哪些是确定性的，AI 在哪

全部确定性。`MotionLibrary.__init__` 收一个 `provider` 参数，但整个 `library.py` 里只有一行 `self.provider = provider`，**没有任何调用点**——它是留给未来语义重排的接口位。当前版本没有向量模型、没有重排阈值，所以不存在「AI 把结果顺序改了」这种情况。

## 6. 典型用法

### 6.1 关键词 + 过滤

```python
from tools.motion_library import core
hits = core.search("循环", top_k=4,
                   options={"motion_type": "walk",
                            "filters": {"loopable": True}})["results"]
for h in hits:
    print(h["clip_id"], h["score"], h["why"])
```

真实输出：

```text
walk_back_01 8.742315 命中关键词：循环；类型显式指定：行走；仅循环动作
walk_fwd_01 8.742315 命中关键词：循环；类型显式指定：行走；仅循环动作
run_fwd_01 2.761392 命中关键词：循环；仅循环动作
climb_ladder_01 2.742315 命中关键词：循环；仅循环动作
```

前两名同为 `8.742315`（`text_score 2.742315 + type_score 6.0`），按 `clip_id` 升序：`walk_back_01` 在 `walk_fwd_01` 之前。

### 6.2 列出全部 + 生成上下文

```python
print([h["clip_id"] for h in core.search("", top_k=12)["results"]])
print(core.context_for("走路 循环", top_k=3))
```

真实输出：

```text
['attack_slash_01', 'climb_ladder_01', 'crouch_idle_01', 'dance_wave_01', 'death_fall_01', 'hit_front_01', 'idle_loop_01', 'jump_start_01', 'run_fwd_01', 'turn_left_90_01', 'walk_back_01', 'walk_fwd_01']
动作库上下文（本地离线索引；只读，未解析 FBX 二进制）：
[1] clip_id=walk_back_01 | Walk_Back_01 走路 后退 循环 | 时长 1.33s | 类型 行走 | 循环=是 | 骨骼 20
[2] clip_id=walk_fwd_01 | Walk_Fwd_01 走路 前进 循环 | 时长 1.20s | 类型 行走 | 循环=是 | 骨骼 20
[3] clip_id=run_fwd_01 | Run_Fwd_01 奔跑 前进 循环 | 时长 0.80s | 类型 奔跑 | 循环=是 | 骨骼 20
```

### 6.3 找相近动作

```python
for h in core.similar_to("walk_fwd_01", top_k=3):
    print(h["clip_id"], h["score"], h["why"])
```

```text
walk_back_01 0.998797 特征向量余弦相似度 0.9988（共同特征 14 项）
climb_ladder_01 0.948723 特征向量余弦相似度 0.9487（共同特征 14 项）
run_fwd_01 0.871736 特征向量余弦相似度 0.8717（共同特征 14 项）
```

## 7. 边界与限制

- **不解析 FBX 二进制。** 只读目录元数据与 JSON sidecar（`<stem>.motion.json` / `motion_index.json`）；文件名启发式也不 `open()` 任何 `.fbx`。真实的速度、循环误差应由上游采样阶段写进 sidecar 的 `samples` 或 `features`。
- **内置 12 个 clip 是演示数据。** 数值特征标注 `estimated = 1.0`（非实测），用来让你在没导入任何资产时也能把链路跑通；别拿它的 `root_speed_mean` 当真实数据用。
- **空 query 时 `motion_type` 不生效。** 实测：`search("", options={"filters": {...}, "motion_type": "walk"})` 返回 4 条（`climb_ladder_01`、`run_fwd_01`、`walk_back_01`、`walk_fwd_01`），按 `clip_id` 升序，`type_score` 全为 `0.0`——空查询走的是「列出全部」分支，压根没有打分环节，所以类型加权无从谈起。
- **没有向量模型，所以「看起来不聪明」。** 纯词法匹配：查「飞行」不会命中 `jump`，查「打斗」不会命中 `attack`（词表里是「攻击 / 挥砍 / 连击」）。同义词、近义句、模糊拼写都不在能力范围内。类型加权能补偿一部分（查「跑」也能召回 `run` 类），但补偿不了语义鸿沟。实测 `search("蜘蛛侠 飞行")` 的 `warning` 是「未命中任何动作（可换用 走路 / 奔跑 / 攻击 / 循环 等关键词，或清空 query 列出全部）」。
- **只读、不联网、不写缓存文件。** `_library()` 每次新建 `MotionLibrary`，不缓存文件、不共享可变状态；`offline` 恒为 `true`。`top_k` 有两套上限：工具层 `MAX_TOP_K = 50`，HTTP 层收窄到 `1..20`、默认 `8`。

## 8. 与其它模块的关系

- **Agent Console**：`core.context_for(query, top_k=3, max_chars=800)` 是给对话注入动作上下文的稳定接口，输出带 `clip_id` 的纯文本、长度硬截断。注意 `AgentSession` 目前只自动注入知识库（`rig_kb` 那个内核），动作上下文需要调用方自己把这段文本塞进 `message` 或 `snapshot`。
- **Web 面板**：`/api/motion` 直接 `from tak.core.motion import MotionLibrary`，与 `tools/motion_library/core.py` 共用同一个内核；服务端只做类型收窄（`float` / `bool` 转换、`top_k` 夹取），排序与过滤仍在核心层。
- **导出链路**：`clip_id` 是跨模块引用键。`path` 只是展示用，不要拿文件名当 id。
- **数据入口**：`tak.core.motion.loader` 提供 `scan_directory()` / `load_index()` / `save_index()`；索引文件缺失或损坏时 `load_index()` 回退内置库而不是抛异常，调用方可用 `describe()["clip_count"]` 判断拿到的是真索引还是演示数据。
