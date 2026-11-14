# 对话式控制台（`agent_console`）

> 用一句中文问出「哪里不对、怎么修、规范怎么写、现在什么状态」，回答里带结构化候选与 dry-run 预览；写场景永远要你亲手确认。

**适用版本** TAK `0.3.2`　·　**DCC** maya / max / mobu / unreal　·　**权限** `scene.read` / `node.write`　·　**UI** `both`

## 1. 它解决什么问题

体检报告列出十条问题之后，真正费时的是回想「这条规则什么意思」「改哪个节点」「会不会把导出搞坏」。控制台把这段接过来：你把问题原话打进去，它回一段人话，规则 id 和节点名都带在里面，不用回头翻报告。

第二种场景是「知道要改，但不想手敲」。它从当前快照确定性推导出参数化修复候选，并把每个动作要对哪个节点做什么、需要什么权限摊成一份 dry-run 预览。不满意就丢掉，满意再走确认流程。

第三种是「规范到底怎么写的」。检索意图直接查仓库里的 `knowledge/*.md`，返回带 `doc_id` / `section` / `path` 的引用片段，而不是让模型背一段可能记错的规范。三种场景共用一个输入框，底下却是三条确定性路径——「省掉反复问人」靠的是这个：判定部分不依赖模型，只依赖你的快照和仓库文本。

## 2. 怎么打开

| 入口 | 操作 |
|---|---|
| Maya 菜单 | TAK → 打开面板 → 分类「AI」→「对话式控制台」（菜单本身只有「打开面板 / 设置 / 关于 TAK」三项） |
| Python | 见下 |
| Web 面板 | 页签 `data-tab="chat"`（标题「对话候选」）；深链 `?tab=chat` |
| HTTP API | `POST /api/agent`。仓里**没有** `/api/chat` 路由，聊天是 `/api/agent` 的 `action=chat` |

窗口底部有一行「连接区」，两条通道各自独立：

| 控件 | 作用 |
|---|---|
| `桥接：未启动（外部调不进来）　[启动桥接]` | 点一下起本地桥，再点是停；地址直接显示在标签上。要 token、发现文件路径和 curl 示例时点「详情」 |
| `[AI 连接]` | 三步向导：选服务商 → 粘密钥 → 测试连接。密钥存 `~/.tak/credential.json`，不进 `config.json` |

两者互不依赖：不开 AI 也能让外部调工具；不开桥也能本地用 mock 对话。

```python
from tools.agent_console import core
core.create_session("demo")
print(core.chat("查看状态", session_id="demo")["reply"])
```

```text
POST /api/agent
{"action": "chat", "session_id": "web", "message": "解释当前快照存在的问题",
 "snapshot": {"skin": {"weight_sum_deviation": 0.02, "max_influences_found": 6}}}
-> 200 {"ok": true, "action": "chat", "session_id": "webdoc"}
   {"intent": "explain", "status": "explained", "turn": 1,
    "reply": "确定性规则解释（判定来自规则引擎，不来自 AI）：\n- [ERROR] 缺少规范要求的关键关节，绑定与导出链路可能不完整。 定位节点：calf_L。\n（其余 4 条同类解释略）\nAI 补充解释：[mock] 已收到 2 条消息，末条 145 字符"}
```

## 3. 输入

`tools/agent_console/core.py` 的四个入口，默认值与代码签名一致：

| 函数 | 签名 | 说明 |
|---|---|---|
| `create_session` | `(session_id="default", provider=None, adapter=None, spec=None, permissions=None, max_turns=50)` | 建会话并返回 `describe()`。`provider` 不传则 `get_provider(task="default")`（默认离线 mock），`adapter` 不传则 `NullAdapter()`；`permissions` 是会话默认已授予权限；`max_turns` 限制历史条数（user / assistant 各计一条） |
| `chat` | `(message, snapshot=None, session_id="default", spec=None, **kwargs)` | `message` 可换成整个 JSON body（dict）；会话不存在时自动新建，`spec` 只在新建时生效 |
| `confirm` | `(plan_id, confirm=False, granted=None, dry_run=True, session_id="default")` | `plan_id` 也可传整个 body；`granted=None` 时用会话 `permissions`；会话不存在时返回 `ok=false` |
| `run_report` | `(ctx)` | 读 `ctx.snapshot` / `ctx.spec` / `ctx.dcc`，返回只读 `Report` |

`AgentSession` 类签名与 `create_session` 一致，另有 `propose(payload, snapshot=None)`、`history()`、`audit()`、`pending_plans()`、`describe()`、`reset()`。

## 4. 输出

`chat()` 返回固定键（`session.py::_blank`）：

| 字段 | 类型 | 说明 |
|---|---|---|
| `session_id` / `turn` | `str` / `int` | 会话 id 与轮次（从 1 递增） |
| `intent` | `str` | `explain` / `plan` / `run` / `search` / `status` / `unknown` |
| `reply` | `str` | 人话回复，多行用 `\n` |
| `candidates` | `list` | 仅 PLAN 非空，元素 `{"tool","params"}` |
| `plan` / `plan_id` | `dict` / `str` | 仅 PLAN，`plan_id` 形如 `plan-1` |
| `status` | `str` | `explained` / `plan_ready` / `rejected` / `status` / `searched` |
| `citations` | `list` | 仅 SEARCH，元素 `doc_id/title/section/excerpt/path` |
| `safety` | `dict` | `chat_writes_scene=false`、`only_write_entry`、`chain`、`notes` |
| `preview` | `list` | dry-run 行：`index/tool/params/permissions/status` |
| `reason` | `str` | 计划被拒原因（通过时空串） |

真实返回（`chat("生成修复计划", snapshot={"skin": {"weight_sum_deviation": 0.02, "max_influences_found": 6}})`）：

```json
{"turn": 1, "intent": "plan", "status": "plan_ready", "plan_id": "plan-1",
 "reply": "已生成待确认计划 plan_id=plan-1（3 个候选动作）。以下为 dry-run diff，chat 未对场景做任何修改；如要执行请调用 confirm(...) 并二次确认。",
 "reason": ""}
```

`preview[0]` 原样：

```json
{"index": 0, "tool": "spec_enforcer.normalize_weights",
 "params": {"tolerance": 0.001, "max_influences": 4},
 "permissions": ["node.write"], "status": "pending"}
```

`confirm()` 返回 `ok` / `status`（`committed` / `dry_run` / `failed`）/ `plan_id` / `session_id` / `dry_run` / `committed` / `preview` / `required_permissions` / `granted` / `result`；出错时 `ok=false` + `error`。

`run_report(ctx)` 的 `metadata` 键：`tool_id`、`session`、`capabilities`、`safety`、`candidates`、`candidate_count`、`preview`、`plan`、`plan_error`、`explanations`、`chat_turns`、`dry_run_only`。

## 5. 内部机制

### 5.1 意图分类：纯正则，无模型

`intents.py::_ROUTE` 是 first-match-wins 的有序表：

| 意图 | 关键词（正则原文，英文大小写不敏感） |
|---|---|
| `run` | 执行、应用、写入、提交、确认执行、确认应用、真的改、跑一下、`\bapply\b`、`\bexecute\b`、`\bcommit\b`、`\brun\b` |
| `plan` | 计划、方案、修复、候选、建议、怎么办、如何处理、怎么处理、预览、出方案、生成计划、`plan`、`propose`、`candidate`、`suggest`、`fix`、`preview`、`dry.?run` |
| `search` | 搜索、检索、查询、查找、查一下、查文档、知识库、文档、`search`、`find`、`query`、`lookup`、`docs?`、`knowledge` |
| `explain` | 解释、说明、为什么、什么原因、原因、分析、是怎么回事、啥意思、含义、解读、什么意思、`explain`、`why`、`what`、`describe`、`meaning`、`reason` |
| `status` | 状态、历史、记录、审计、帮助、能做什么、能力、会话信息、信息、`status`、`history`、`audit`、`help`、`capabilit`、`info` |

置信度 `round(min(0.99, 0.7 + 0.1 * 命中片段数), 2)`，一条不中就 `unknown` / `0.0`。`search` 命中时额外写 `args["top_k"]=3`。`snapshot` 只为 API 兼容，不参与分类。

实测：`解释一下命名问题 → explain 0.8 ["解释"]`；`生成修复计划 → plan 0.9 ["计划","修复"]`；`执行 → run 0.8`；`今天天气 → unknown 0.0 []`。

### 5.2 每条意图做什么

- `explain`：跑 `plugin_id="builtin"` 内置规则取前 5 条，用 `_PLAIN` 词表翻人话，再让 provider 补一句。
- `plan`：`local_candidates()` 推导 → `propose()` 白名单 + dry-run → 存 `plan-N`，**从不执行**。
- `run`：**不执行**，只回「请走 confirm(...)」并报待确认计划数与最近 `plan_id`。
- `search`：`KnowledgeBase().context_for(query, top_k=3)`，优先结构化 `citations`，退化时把字符串折成 `{"text": ...}`。
- `status`：把 `describe()` 拼成一句（轮次 / 审计数 / 待确认数 / provider / adapter / 已注册动作数）。

### 5.3 AI 只在哪一步给建议

唯一调模型的是 `AgentSession._provider_chat()`，且只被 EXPLAIN 调用。它把「用户问题 + 前 3 条确定性规则结论」发给 provider，返回只拼进 `reply` 的「AI 补充解释：」后缀。规则判定、候选生成、计划校验、排序全在 AI 之外。

provider 异常时返回 `（AI 解释层不可用，已降级为确定性规则解释：<异常类名>）`，链路不中断。默认离线 provider 回 `[mock] 已收到 N 条消息，末条 M 字符`——这不是「想不出答案」，这个位置本来就不承担判定。

### 5.4 候选推导：白名单是硬闸

阈值全部来自 spec，缺省值写在 `intents.py`：

| 信号 | 条件 | 动作 |
|---|---|---|
| 权重 | `abs(weight_sum_deviation) > skin.weight_sum_tolerance`（缺省 0.001）或 `max_influences_found > skin.max_influences`（缺省 4） | `spec_enforcer.normalize_weights`，仅当 `0<=tolerance<=1` 且 `1<=max_influences<=32` 时带参 |
| 命名空间 | 关节名含 `:` 或 `namespace` 非空 | `spec_enforcer.set_namespace`，`namespace=""` |
| 负缩放 | `scale` 任一轴 < 0 | `spec_enforcer.rename_joint`，只带 `node/pattern/reason`，**不猜目标名** |

节点类最多 5 条（`_MAX_NODE_CANDIDATES`），按关节名排序遍历；产出后按 `(tool, params)` 稳定键去重，再用 `ACTION_REGISTRY.names` 过滤，未注册动作一律丢弃。

注册表当前只有 4 个动作，全部属于 `spec_enforcer`，权限都是 `node.write`：

| 动作 | 参数声明 |
|---|---|
| `spec_enforcer.rename_joint` | `node` str 必填、`name` str、`pattern` str、`reason` str |
| `spec_enforcer.set_control_color` | `node` str 必填、`color` int 必填 0–31 |
| `spec_enforcer.normalize_weights` | `max_influences` int 1–32、`tolerance` float 0.0–1.0 |
| `spec_enforcer.set_namespace` | `node` str 必填、`namespace` str 必填 |

参数表非空时出现未声明参数也被拒。这就是「模型只能输出 tool + params」的落地：编一个动作名或多余参数，`build_plan()` 直接抛 `DSLError`。

### 5.5 dry-run → 授权 → confirm，以及 undo

`ACTION_REGISTRY.execute()` 的顺序写死：

1. `_validate_plan(plan)` 整批先校验（避免跑一半才发现后续参数非法）；
2. 算 `required`（权限并集）；
3. `preview(plan)` 生成 diff；
4. `dry_run=True` → 立即返回 `{"dry_run": true, "committed": false}`，**连只读宿主查询都不发生**；
5. 非 dry-run：`granted` 必须覆盖 `required`，否则 `DSLError: 缺少权限: [...]`；
6. `confirm` 必须 `True`，否则 `DSLError: 非 dry-run 执行必须显式 confirm=True（人工确认）`；
7. `adapter.begin_undo(...)` → 逐条执行 → `commit_undo(token)`。

`begin_undo` 本身失败则一条都不跑。执行中任一条抛异常 → `adapter.rollback(token)`，`changes[].status` 全置 `rolled_back`；回滚再失败则 `rollback_failed` + `rollback_error`。`confirm()` 把任何异常转成 `{"ok": false, "error": ...}`，不往上抛。

审计由 `finish()` 写入：`seq` / `event` / `plan_id` / `confirm` / `dry_run` / `ok` / `committed` / `status` / `required_permissions` / `granted`，`seq` 单调递增、无时间戳。

三段实测（同一 `plan-1`）：

```text
confirm(confirm=True, dry_run=False)                 -> ok=false  "DSLError: 缺少权限: ['node.write']"
confirm(confirm=False)                               -> ok=true   status=dry_run    committed=false
confirm(confirm=True, dry_run=False, granted=[...])  -> ok=true   status=committed  committed=true
```

### 5.6 确定性 vs AI

| 环节 | 由谁决定 |
|---|---|
| 意图分类 | 正则表 |
| 规则判定（存在哪些问题） | `tak.core.rules` 内置规则 |
| 候选动作与参数 | `intents.local_candidates` |
| 计划合法性 | `ACTION_REGISTRY.build_plan` |
| dry-run 预览内容 | `ACTION_REGISTRY.preview` |
| 是否执行 | 你的 `confirm` / `granted` / `dry_run` |
| 多出来的那段话 | AI，只影响 `reply` 末尾一句 |

## 6. 典型用法

### 6.1 只看确定性部分：`run_report`

```python
from tak.core.runner import ToolContext
from tak.core.spec import load_spec
from tools.agent_console import core as ac

snap = {
    "dcc": "maya", "version": "2023",
    "joints": {
        "ns:root": {"namespace": "ns", "scale": [1, 1, 1]},
        "hand_L_mirror": {"scale": [-1, 1, 1]},
        "pelvis": {"scale": [1, 1, 1]},
    },
    "skin": {"weight_sum_deviation": 0.02, "max_influences_found": 6},
}
report = ac.run_report(ToolContext(dcc="maya", snapshot=snap, spec=load_spec()))
print(report.passed, len(report.findings))
print(report.metadata["candidates"]["actions"])
```

真实输出（`passed` 只判有无 `error` / `blocker`；`run_report` 只产 INFO / WARNING，所以恒为 True。长字典按可读性折行，值未改动）：

```text
True 9
[{'tool': 'spec_enforcer.normalize_weights', 'params': {'tolerance': 0.001, 'max_influences': 4}},
 {'tool': 'spec_enforcer.rename_joint',
  'params': {'node': 'hand_L_mirror',
             'pattern': '^(root|pelvis|spine_[0-9]{2}|neck|head|clavicle_[LR]|upperarm_[LR]|lowerarm_[LR]|hand_[LR]|thigh_[LR]|calf_[LR]|foot_[LR]|toe_[LR]|FACIAL_[A-Za-z0-9_]+)$',
             'reason': '负缩放疑似镜像关节，需人工复核命名（不自动猜目标名）'}},
 {'tool': 'spec_enforcer.set_namespace', 'params': {'node': 'ns:root', 'namespace': ''}}]
```

9 条 Finding = 1 条 `agent_console.session` + 5 条 `agent_console.finding`（`explain_findings(limit=5)`）+ 3 条 `agent_console.candidate`。

### 6.2 对话 → 计划 → 确认

```python
from tools.agent_console import core as ac

snap = {"skin": {"weight_sum_deviation": 0.02, "max_influences_found": 6}}
ac.create_session("s3")
turn = ac.chat("生成修复计划", snapshot=snap, session_id="s3")
print(turn["plan_id"], turn["status"])
print(ac.chat("执行一下", snapshot=snap, session_id="s3")["reply"])
print(ac.confirm("plan-1", session_id="s3")["status"])
```

真实输出：

```text
plan-1 plan_ready
chat 不写场景：执行必须走 confirm(plan_id, confirm=True, dry_run=False, granted=['node.write']) 并经人工二次确认，执行器会开启 undo 并在失败时整体回滚。 当前有 1 个待确认计划，最近一个 plan_id=plan-1。
dry_run
```

`confirm("plan-1")` 关键字段：`ok=true status=dry_run dry_run=true committed=false required_permissions=['node.write'] granted=[]`。

检索意图也真的会查仓库文档：另建会话 `ac.create_session("s2")` 后打 `ac.chat("搜索 蒙皮权重上限", session_id="s2")`，实测返回 `intent=search`、`status=searched`、3 条 `citations`，首条是 `doc_id=skin_weights`、`section=影响数上限`、`path=knowledge/skin_weights.md`。

## 7. 边界与限制

- **chat 绝不写场景。** `chat()` 全程不调 `ACTION_REGISTRY.execute`；`safety.chat_writes_scene` 明写 `false`。唯一写入口是 `confirm(plan_id, confirm=True, dry_run=False, granted=[...])`。
- **LLM 绝不生成 DCC 脚本。** 模型输出只拼进 `reply`；没有路径把模型字符串当代码或计划解析。`propose()` 是唯一能把外部 JSON 变计划的入口，第一站就是 `build_plan`。
- **候选面很窄。** 只有 4 个动作、只覆盖权重 / 命名空间 / 负缩放三类信号。没问题就是空候选，不凑数。
- **`rename_joint` 永远缺目标名。** 推导器不写 `name`，所以这条在真实 adapter 上走 `skipped`。猜错名字比不改更危险，这是刻意的。
- **NullAdapter 下 `committed=true` 不等于改了场景。** 动作找不到 adapter 方法就写 warning 返回 `skipped`，但执行器仍把整批标 `committed`（链路本身跑完了）。判断是否真动场景要看 `result.changes[].status`。
- **默认离线，不写缓存文件。** `TAK_AI_OFFLINE` 非 `"0"` 或没有 `TAK_AI_API_KEY` 时 `get_provider()` 返回 `MockProvider`，不联网；会话只存在进程内 `_SESSIONS`，进程结束即消失，`history` / `audit` / `pending_plans` 都是内存列表。
- **Web 端 confirm 需三条件同时满足。** `_api_agent` 只在 `confirm is True`、`dry_run is False`、`granted` 含 `node.write` 时才真执行，其余一律退化 dry-run；可授予权限硬编码为 `frozenset({"node.write"})`。
- **没有 `/api/chat`。** 聊天是 `POST /api/agent` + `action=chat`。

## 8. 与其它模块的关系

- **规则引擎**：`explain` 依赖 `tak.core.rules.engine.run_rules(..., plugin_id="builtin")`，只跑内置规则，不受插件加载状态影响。
- **受限 DSL**：计划 / 预览 / 执行 / 回滚全在 `tak.core.dsl.executor`。本模块**不注册任何动作**，只用 `ensure_actions_registered()` 触发 `spec_enforcer/actions.py` 的幂等注册，且只加载 `actions` 不加载 `rules`，避免污染全局规则表。
- **spec_enforcer**：4 个候选动作的执行体都在 `tools/spec_enforcer/actions.py`，经 adapter 改场景；动作自身不开 undo，undo 由执行器统一管。
- **知识库与 AI 层**：SEARCH 直接 `from tak.core.knowledge import KnowledgeBase`，与 `rig_kb` 共用同一内核；`get_provider()` 默认 mock，`TASK_ROUTING` 里 `explain` / `candidate` 指向 `DeepSeek-V4.1-Flash`，只在真实 provider 生效时才有意义。
- **Web 面板**：`/api/agent` 直接持有 `AgentSession`，不经过 `tools/agent_console/core.py`；后者是给 `runner` / 脚本 / Qt 面板用的薄封装。两条路复用同一份 `tak.core.agent`。
- **未调用 `motion_library`**：`AgentSession._knowledge_context` 只查知识库，动作上下文需要调用方自己把 `motion_library.context_for(...)` 的结果塞进 `message` 或 `snapshot`。
