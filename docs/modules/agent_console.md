# 对话式控制台（`agent_console`）

> 用一句中文查当前场景、骨架与蒙皮，或创建、改名、删除节点；写操作先出 dry-run 预览，你点「执行」才落地。同一套动作经 MCP 暴露给外部 AI。旧的规则问答与候选计划仍然保留。

**适用版本** TAK `0.7.0`　·　**DCC** maya / max / mobu / unreal（真实宿主动作目前只有 Maya）　·　**权限** `scene.read` / `node.write`　·　**UI** `both`

## 1. 它解决什么问题

**v0.7.0 之前这里答非所问**：旧控制台只有正则匹配的五种问法、4 个规范修复动作，仓里没有 MCP 代码，用户想要的「直接提需求 → 查场景 → 改场景 → 建资产」一条都做不到。v0.7.0 补上了 18 个受限场景动作、真实的 Maya 宿主实现、面板内的人工确认执行，以及一个 stdio MCP server。详见 [MCP 接入文档](../mcp.md)。

现在输入框能干四件事：

| 你说 | 它做 |
|---|---|
| 「场景里有多少关节」「列出所有控制器」「查 body 的蒙皮」「找 foot 相关的节点」 | 查询当前场景、节点、选择集、骨架层级、skinCluster 影响数与权重偏差、连接关系 |
| 「查 root 的层级」「root 影响了哪些网格」 | 查绑定逻辑 |
| 「创建一个 locator 叫 foot_ctrl」「把 pCube1 改名为 body」「删除 pCube1」 | 生成写操作预览，你点「执行」后真的改场景，可「撤销上一次」 |
| 「解释一下命名问题」「搜索蒙皮权重上限」 | 旧的确定性规则问答与知识库检索（保留，不再冒充「对话式 Agent」） |

配了 AI 端点时走 OpenAI 兼容 function calling，任意说法都能解析；没配时走本地关键词解析，覆盖上表的高频句式，解析不出来会明确提示而不是瞎猜。

体检报告列出十条问题之后，真正费时的是回想「这条规则什么意思」「改哪个节点」「会不会把导出搞坏」。旧的规则问答把这段接过来：你把问题原话打进去，它回一段人话，规则 id 和节点名都带在里面，不用回头翻报告。

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
| `允许写入（面板 + MCP）` | 默认**关**。关着时只能查询与预览；打开后写操作仍需 dry-run + 点「执行」 |
| `[执行]` / `[撤销上一次]` | 执行上一次预览的写操作 / 撤销最近一次 TAK 写入 |
| `[MCP 配置]` | 输出并复制可直接粘进 WorkBuddy 的 `mcpServers` 配置片段 |

两者互不依赖：不开 AI 也能让外部调工具（本地关键词解析照常可用）；不开桥也能在面板里查改本机场景。桥开着 + 打开写入 + 配好 MCP，外部 AI 才能改场景，见 [MCP 文档](../mcp.md)。

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

`tools/agent_console/core.py` 的入口，默认值与代码签名一致。前四个是 v0.6 及以前的规则问答/计划链路，后五个是 v0.7.0 的场景动作链路：

| 函数 | 签名 | 说明 |
|---|---|---|
| `create_session` | `(session_id="default", provider=None, adapter=None, spec=None, permissions=None, max_turns=50)` | 建会话并返回 `describe()`。`provider` 不传则 `get_provider(task="default")`（默认离线 mock），`adapter` 不传则 `NullAdapter()`；`permissions` 是会话默认已授予权限；`max_turns` 限制历史条数（user / assistant 各计一条） |
| `chat` | `(message, snapshot=None, session_id="default", spec=None, **kwargs)` | `message` 可换成整个 JSON body（dict）；会话不存在时自动新建，`spec` 只在新建时生效 |
| `confirm` | `(plan_id, confirm=False, granted=None, dry_run=True, session_id="default")` | `plan_id` 也可传整个 body；`granted=None` 时用会话 `permissions`；会话不存在时返回 `ok=false` |
| `run_report` | `(ctx)` | 读 `ctx.snapshot` / `ctx.spec` / `ctx.dcc`，返回只读 `Report` |
| `command` | `(message="", *, calls=None, dcc="maya", allow_write=False, execute=False, use_model=True, provider=None)` | **自然语言主入口**。`calls` 为空时解析句子并返回动作结果（写操作只给预览 + `confirm_token`）；`calls` 非空时执行带 token 的批次（UI 点「执行」） |
| `actions` | `(dcc="maya")` | 列出 18 个场景动作、参数、权限、是否已实现 |
| `undo` | `(dcc="maya")` | 撤销最近一次 TAK 写入 |
| `mcp_config` | `()` | 返回 `mcpServers` 配置片段 |
| `write_enabled` / `set_write_enabled` | `()` / `(bool)` | 读 / 写「允许写入」开关（持久化到 `config.json` 的 `bridge.allow_write`） |

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

`command()` 的返回（v0.7.0）：`ok` / `message` / `mode`（`model` / `local` / `local-fallback` / `preview` / `execute`）/ `reply` / `hint` / `model_error` / `results` / `calls` / `need_confirm` / `allow_write` / `dcc`。`results[]` 每项含 `ok` / `action` / `params` / `read_only` / `status`（`ok` / `dry_run` / `committed` / `error`）/ `result` 或 `preview` 或 `error` / `committed`。

```json
{"ok": true, "mode": "local", "need_confirm": true,
 "results": [{"ok": true, "action": "node.create",
              "status": "dry_run", "committed": false,
              "preview": {"operation": "CREATE", "name": "foot_ctrl"},
              "confirm_token": "Xgd..."}],
 "calls": [{"action": "node.create",
            "params": {"kind": "locator", "name": "foot_ctrl"},
            "confirm_token": "Xgd..."}]}
```

把 `calls` 原样回传并置 `execute=True`、`allow_write=True` 才会真正写入。查询类动作在第一次调用就直接返回 `status=ok` 的数据，不需要确认。

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

上表描述的是**旧的规则问答链路**（`chat` / `confirm`）。新的场景动作链路是另一条路，两者共用同一个输入框但不共用执行器。

### 5.7 场景动作链路（v0.7.0）

```text
自然语言 / MCP tools/call
  -> tak.core.ai.commands.resolve()     有 key 走 function calling，失败降级本地关键词
  -> tak.core.scene_actions.REGISTRY   18 个动作白名单 + 参数 schema 校验
  -> 写操作：dry-run 预览 + 一次性 confirm_token（绑定动作与参数）
  -> 人工点「执行」/ 带 token 二次调用
  -> 权限 granted + undo chunk
  -> tak.adapters.maya.scene_ops       真正的 maya.cmds 调用
```

三处硬约束：

1. `tak/core` 零 DCC import，宿主 handler 由适配器在导入时注册；没实现的动作调用时明确报错，不静默假装成功。
2. 模型只能输出动作名 + 参数 JSON，不能输出代码；参数还要过类型 / 枚举 / 范围 / 未知参数校验。
3. 写操作两阶段且 token 一次性；`bridge.allow_write` 默认关，是外部调用的总闸。`undo` 只在「TAK 写入后没有其它宿主操作」时生效，避免误撤用户自己的改动。

`tools/agent_console/core.py` 里的 `command` / `actions` / `undo` 只是薄封装；同一张注册表同时被桥的 `/bridge/act` 和 MCP server 使用，所以面板、HTTP、MCP 三处看到的动作清单与安全边界完全一致。

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

- **旧的 `chat` / `confirm` 链路绝不写场景。** `chat()` 全程不调 `ACTION_REGISTRY.execute`；`safety.chat_writes_scene` 明写 `false`。那条链路的唯一写入口是 `confirm(plan_id, confirm=True, dry_run=False, granted=[...])`。
- **新的 `command` 链路会写场景，但只能由人触发。** 写操作必须先 dry-run 拿 token，再由人在面板点「执行」（或 MCP 客户端带 token 二次调用），且 `bridge.allow_write` 必须为 `true`。AI 无法自己生成有效 token。
- **宿主动作目前只有 Maya 实现。** 能力表对四端都可见，但 Max / MoBu / UE 调用时返回「当前 DCC 没有宿主实现」，不假装成功。
- **被锁定的属性拒绝修改。** 常见于被蒙皮或约束连接的变换通道，TAK 不擅自解锁；Maya 真机探针验证过这条。
- **面板里的模型调用是同步的。** 端点超时设 12 秒并只重试一次，超时自动降级为本地关键词解析，避免 Maya 界面长时间假死。
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
- **MCP 与桥**：`tak/mcp/server.py` 把 `tak.core.scene_actions` 的动作表暴露为 `tools/list`，`tools/call` 经 `tak.core.bridge.call()` 转发到 `/bridge/act`。配置与安全边界见 [MCP 文档](../mcp.md)。
- **真机验证**：`validation/maya_scene_actions_probe.py` 用 Maya 2026 standalone 跑过 18 个动作、撤销、桥与 MCP 写入，结果 `ok=true`（63 步）。
