# 让外部 AI 直接操作 DCC 场景（MCP）

> 一句需求 → AI 选出受限动作 → 本地桥 → Maya 主线程 → 真实查改场景。
> 适用 TAK `0.8.0`　·　当前已实现宿主：**Maya 2023+**（Max / MoBu / UE 的能力表会列出，但没有宿主 handler，调用会明确报「未实现」）。

## 1. 它和 v0.6 的「对话控制台」不是一回事

v0.6 及以前的对话控制台是**规则问答 + 候选计划预览器**：正则匹配五种问法，回答来自规则引擎，只有 4 个 `spec_enforcer` 动作，仓里没有任何 MCP 代码。所以「提需求改场景」做不到。

v0.7.0 补上了完整链路：

```text
WorkBuddy / Claude Desktop / 任何 MCP 客户端
        │  stdio · JSON-RPC 2.0
        ▼
python scripts/tak_mcp_entry.py  ← 普通 Python，零第三方依赖，不 import Maya
        │  HTTP 127.0.0.1 · X-TAK-Token
        ▼
DCC 面板里的 TAK 桥         ← 只绑回环，请求切回 Maya 主线程
        │  动作白名单 + 参数校验 + dry-run + 权限 + undo
        ▼
maya.cmds                  ← 真正查/改场景
```

同一次能力表也被**面板内的输入框**复用：你在 TAK 面板里打字，走的是同一个注册表与同一套确认流程，只是不经过 HTTP。

## 2. 三步接上

### 第 1 步：在 Maya 里打开桥

Maya → TAK → 打开面板 → 分类「AI」→ 对话式控制台 → 底部点 **「启动桥接」**。标签会变成 `桥接：http://127.0.0.1:64074`（端口随机）。这一步会写 `~/.tak/bridge.json` 发现文件和 token。

不想点界面也行：

```python
import tak.core.bridge as bridge
bridge.start_bridge(dcc="maya")
print(bridge.bridge_status()["url"])
```

### 第 2 步：生成 MCP 配置

面板里点 **「MCP 配置」**（会复制到剪贴板），或命令行：

```bash
python -m tak.mcp --print-config
```

输出形如（`args` 指向自包含启动器，不是 `-m`）：

```json
{
  "mcpServers": {
    "tak-scene": {
      "command": "C:\\...\\python.exe",
      "args": ["C:\\...\\tech-anim-kit\\scripts\\tak_mcp_entry.py"],
      "env": {
        "PYTHONPATH": "<仓库根目录>",
        "PYTHONIOENCODING": "utf-8",
        "TAK_HOME": "C:\\Users\\<你>\\.tak"
      }
    }
  }
}
```

把这段加进 WorkBuddy 的 MCP 配置（或 Claude Desktop 的 `claude_desktop_config.json`），然后重启客户端让它重新加载 MCP server。

**更省事的做法**：直接跑 `python scripts/install_mcp_config.py`，它会读你正在用的 `~/.workbuddy/mcp.json`，备份旧文件、只改 `tak-scene` 这一条、复检并做一次真实握手；在 Maya 面板里点 **「修复 MCP」** 效果相同（同样自动备份）。以后只要客户端 UI 重新 Trust 后又把旧片段写回来，重跑一次即可。

> **`command` 必须是普通 `python.exe`，绝不能是 `maya.exe` / `3dsmax.exe` 之类的 DCC 宿主。**
> v0.7.0 在面板里点「MCP 配置」时误用了 `sys.executable`，而它在 Maya 进程内等于
> `maya.exe`；客户端会去启动 Maya GUI，永远等不到 `initialize`，报
> `MCP initialize timed out for "custom-mcp:tak-scene"`。v0.7.1 起会自动探测独立
> Python（`TAK_MCP_PYTHON` → 当前解释器 → PATH → `py -3` → `mayapy`），并把来源
> 写进日志台。仍然不对时，设 `TAK_MCP_PYTHON` 指向一个 `python.exe` 再点一次。
>
> **v0.7.2 补了一个真实的坑**：客户端 UI 里删除并重新添加 / 重新 Trust
> `tak-scene` 时，表单可能把 v0.7.0 的旧片段（含 `maya.exe`）**写回** `mcp.json`，
> 于是「昨天刚修好今天又超时」。本机 `daemon.log` 的 `McpAuth` 诊断能直接看到
> 它拉起的 `command` 与 30 秒超时。遇到这种情况不要手动照旧片段重填，跑
> `python scripts/install_mcp_config.py` 或在面板点「修复 MCP」，然后**重启客户端**。

三个 `env` 键的用途（v0.7.2 起**删了也能跑**：`tak_mcp_entry.py` 会自己补齐，但建议保留，正常路径更直白）：

| 键 | 为什么有它 |
|---|---|
| `PYTHONPATH` | 客户端的工作目录不是仓库根；不设且没用启动器时 `ModuleNotFoundError: tak` |
| `PYTHONIOENCODING` | 固定 UTF-8，中文日志在 Windows 管道里不会编码失败 |
| `TAK_HOME` | 配置目录的**绝对路径**。有些客户端把 `env` 当全量替换，少了 `USERPROFILE` 时 `os.path.expanduser("~")` 会返回字面 `~`，桥发现文件与 token 全找不到，工具会一律回「桥未启动」 |

`TAK_HOME` 指向的是 `~/.tak` 目录本身（里面放 `config.json` / `bridge.json`），不是它的父目录。启动器在 `USERPROFILE` 缺失时会用 `SHGetFolderPathW(CSIDL_PROFILE)` 拿真实主目录，再把 `TAK_HOME` 补成绝对路径；所以 `env` 被整段清空、`PYTHONPATH` 也没了，它照样能 import tak 并连上桥。

自检（不进 MCP 客户端也能做）：

```bash
python -m tak.mcp --status                 # 看桥有没有开
python -m tak.mcp --print-config          # 看当前生成的 command / args / env 对不对
python scripts/install_mcp_config.py --check   # 只体检本机 mcp.json，不写文件
python scripts/install_mcp_config.py         # 体检 + 备份 + 修 + 完整握手
echo '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"check","version":"1"}}}' | python -m tak.mcp
python validation/mcp_handshake_check.py   # 按当前 mcp.json 完整握手 + 真读场景
python validation/mcp_handshake_check.py --minimal-env  # 把 env 全丢掉再握一次
```

最后一条应该**立刻**回一行 `{"jsonrpc":"2.0","id":1,"result":{...}}`。没回或卡住，先查 `command` 是不是被写成了 DCC 宿主 exe。

`mcp_handshake_check.py` 会读你正在用的 `~/.workbuddy/mcp.json`，按里面的 `command` / `args` / `env` 真起一个子进程（故意不继承 `USERPROFILE`，模拟最严苛的客户端；`--minimal-env` 时连配置里的 `env` 也丢掉），依次跑 `initialize`、`tools/list`、`tak_status`，桥在跑时再只读一次 `tak_scene_summary`。输出每一步耗时与结论，结果写 `validation/mcp_handshake_result.json`，退出码 0 才是全通。换机器、换客户端、换了 Python 路径之后先跑它，比看日志快。它还会直接把「`command` 不是 Python 解释器」「启动器文件不存在」判成 fatal，不用等 30 秒超时再猜。

### 第 3 步：要不要允许写入

**默认只能查询和预览，不能改场景。** 要真改，必须同时满足：

1. 面板里勾选 **「允许写入（面板 + MCP）」**（或 `python -m tak.core.bridge write on`）；
2. 第一次调用拿到 `confirm_token`，把 dry-run 预览给人看过；
3. 第二次调用带同一个 token 且 `confirm: true`。

token 绑定动作名与参数，改一个字就失效，5 分钟过期。面板里还有 **「撤销上一次」** 按钮，对应 MCP 工具 `tak_undo`。

## 3. 可用工具（22 个 = 19 个动作 + 3 个元工具）

| 分类 | MCP 工具 | 作用 |
|---|---|---|
| 元 | `tak_status` | 桥是否启动、当前 DCC、写开关状态 |
| 元 | `tak_list_actions` | 列出全部动作、参数、权限、是否已实现 |
| 元 | `tak_undo` | 撤销最近一次 TAK 写入 |
| 查询 | `tak_scene_summary` | 场景概况：文件名、帧范围、单位、节点计数、选择集 |
| 查询 | `tak_scene_list_nodes` | 按类型 / 通配符列节点 |
| 查询 | `tak_scene_find` | 名字模糊查找（不知道确切名字时先用这个） |
| 查询 | `tak_scene_get_node` | 节点类型、父子、变换、自定义属性 |
| 查询 | `tak_scene_get_attr` | 读属性当前值 |
| 查询 | `tak_scene_frame_range` | 帧范围与时间单位 |
| 查询 | `tak_selection_get` | 当前选择集 |
| 绑定 | `tak_rig_hierarchy` | 关节树与深度 |
| 绑定 | `tak_rig_skin_info` | skinCluster、影响关节、最大影响数、权重和偏差抽样 |
| 绑定 | `tak_rig_influences` | 反查某个关节影响了哪些网格 |
| 绑定 | `tak_rig_connections` | 节点的驱动 / 被驱动连接 |
| 写入 | `tak_node_create` | 创建 group / locator / joint / 控制器圆圈 / 立方体 |
| 写入 | `tak_node_rename` | 重命名 |
| 写入 | `tak_node_delete` | 删除（连带子节点） |
| 写入 | `tak_node_parent` | 改父子关系 |
| 写入 | `tak_attr_set` | 设置属性 |
| 写入 | `tak_selection_set` | 设置选择集 |
| 写入 | `tak_scene_create_cube_chain` | **v0.8.0**：一次建 N 个随机大小立方体，按体积降序连成父子链（最大为最父级），一次确认、一次撤销 |
| 写入 | `tak_scene_undo_tak` | 撤销最近一次 TAK 写入 |

写工具的参数 schema 里额外有 `confirm` / `confirm_token` 两个字段，这是两阶段确认用的，不属于动作参数。

### 3.1 批量动作：`tak_scene_create_cube_chain`

对着「创建十个随机大小在 10~100 立方厘米的正方块，以大小体积做父子关系，最大的作为最父级」这种需求，**不要**拆成 10 次 `tak_node_create` + 9 次 `tak_node_parent`：每个写操作都要单独 dry-run + 人肉确认，拆开根本不可操作。一次调用即可：

```json
{
  "count": 10, "min_size": 10, "max_size": 100,
  "size_by": "volume", "seed": 2024,
  "prefix": "takCube", "link": true
}
```

| 参数 | 含义 |
|---|---|
| `count` | 1~50，默认 10 |
| `min_size` / `max_size` | 尺寸范围，默认 10 / 100 |
| `size_by` | `volume`（默认）= 按体积 cm³ 解释，边长 = 体积^(1/3)，所以 10~100 cm³ 对应边长约 2.15~4.64 cm；`edge` = 按边长 cm 解释。预览里会写明用的是哪种，避免「立方厘米」这个歧义闷在心里 |
| `seed` | 随机种子。**相同 seed 的尺寸、名字、层级完全一致**，dry-run 与带 token 第二次执行靠它对齐，预览绝不会和落地结果不一样 |
| `link` | 是否按体积降序连成父子链（最大 = 最父级） |
| `spacing` | `-1`（默认）= 自动按最大边长 ×1.6 沿本地 X 轴逐级排开，避免同心立方体被最大的包住看不见；`0` = 真的全部在原点；`>0` = 指定间距 cm |
| `prefix` | 命名前缀，实际名字 `prefix_01 …`，编号即层级顺序 |

执行前整体查重：任何一个名字已被占用就整体失败、不留半成品。Maya 侧整批在一个 undo chunk 里，`tak_undo` 一次全撤。

## 4. 一次典型的写入对话

用户说「给脚踝建一个控制器，叫 `foot_ctrl`」。AI 先查再改：

```text
→ tools/call tak_scene_find {"query":"foot"}
← {"count":0,"nodes":[]}                         # 没有重名，可以建

→ tools/call tak_node_create {"kind":"locator","name":"foot_ctrl"}
← {"status":"dry_run","committed":false,
   "preview":{"operation":"CREATE","name":"foot_ctrl",...},
   "confirm_token":"Xgd..."}                     # 只预览，什么都没建
```

AI 把 preview 原样展示给你，你说「做吧」，它才发第二次：

```text
→ tools/call tak_node_create {
      "kind":"locator","name":"foot_ctrl",
      "confirm":true,"confirm_token":"Xgd..."}
← {"status":"committed","committed":true,
   "result":{"created":"foot_ctrl","type":"transform",...}}
```

模型**不能**自己伪造 `confirm_token`，也不能在第一次调用里传 `confirm=true` 就跳过预览。参数先过白名单（未注册动作、未声明参数、类型不符、越界全部拒绝），模型永远不会输出可执行的 Python / MEL 代码。

## 5. 安全边界

| 边界 | 实现 |
|---|---|
| 网络 | 桥只绑 `127.0.0.1`，所有业务请求要 `X-TAK-Token`（首次启动随机生成） |
| 能力 | 19 个动作的硬白名单；没有 `eval` / `exec` / 任意脚本执行 |
| 参数 | 类型、必填、枚举、范围、长度、未知参数校验；数字不接受 `NaN` |
| 写入 | 默认 `allow_write=false`；即使打开也要 dry-run → token → 人工确认 |
| 主线程 | Maya 侧所有宿主调用经 `maya.utils.executeInMainThreadWithResult` 切回主线程，HTTP 线程不直接碰 `cmds` |
| 撤销 | 每个写操作包在 undo chunk 里，异常按 undo 队列长度差回滚；`tak_undo` 只在「之后没有别的操作」时生效，避免误撤你自己的改动 |
| 审计 | 桥日志 `~/.tak/bridge.log` 记每次调用；注册表内存审计记动作 / 参数 / 确认 / 是否提交 |
| 锁定属性 | 被蒙皮或约束锁定的通道拒绝修改并给出原因，TAK 不擅自解锁 |

## 6. 排错

| 现象 | 原因 / 处理 |
|---|---|
| `MCP initialize timed out for "custom-mcp:tak-scene"` | `command` 被写成了 `maya.exe` 等 DCC 宿主进程：客户端去启动 Maya GUI，stdout 永远没有 JSON-RPC。跑 `python scripts/install_mcp_config.py --check` 看诊断，或 `python validation/mcp_handshake_check.py` 直接判 fatal；修复用 `python scripts/install_mcp_config.py`（或面板「修复 MCP」），然后**重启客户端**。客户端日志里的 `McpAuth stdio-probe-failed` 会列出它实际拉起的 `command` |
| 昨天改对了，重新 Trust / 重新添加后又超时 | 客户端 UI 的表单把含 `maya.exe` 的 v0.7.0 旧片段**写回** `mcp.json`（v0.7.2 实测：`mcp.json` mtime 与 `wb:mcp:remove` → `wb:mcp:connect` 日志对得上）。不要在表单里沿用旧值；重跑一键修复，再重启客户端让它重新读文件 |
| 握手成功，但每个工具都回「桥未启动」 | 客户端把 `env` 当全量替换，进程里没有 `USERPROFILE`，`expanduser("~")` 返回字面 `~`，发现文件找不到。v0.7.2 起 `tak_mcp_entry.py` 会自补，确认 `args` 指向它；若仍是 `-m tak.mcp`，补 `TAK_HOME` 为 `~/.tak` 的绝对路径，并用 `python -m tak.mcp --status` 对比 |
| 自检报 `args 指向的启动脚本不存在` | 仓库被移动或只拷贝了 `tak/` 包。把 `scripts/tak_mcp_entry.py` 一起带上，或让 `config_snippet()` 回落到 `-m tak.mcp` 并补全 `env` |
| 升级后 `tools/list` 工具数没变 / 新动作调不通 | 桥跑在 **DCC 进程里**，DCC 没重启时上报的仍是启动那一刻加载的旧能力表。v0.8.0 起 `tools/list` 会返回 `capabilities_stale=true` 与 `stale_hint`，列出少了哪些动作；**重启 Maya**（或重新加载 TAK）后从 21 变 22 个 |
| 面板报 `AI 端点不可用，已用本地解析：HTTP 422 ... tools[0]: missing field type` | v0.7.x 把 MCP `tools/list` 格式直接发给了 `chat/completions`。v0.8.0 起 `openai_tools()` 转成 `{"type":"function","function":{...,"parameters"}}`，此错误不应再出现 |
| 一句「创建十个随机大小的方块」回「创建需要给出名字」 | v0.7.x 本地解析只认单节点创建。v0.8.0 起识别中文/阿拉伯数量词、范围、单位与父子关键词，映射到 `scene.create_cube_chain`；若 DCC 没重启，面板仍是旧注册表，需重启 Maya |
| `TAK 桥未启动` | 在 Maya 面板点「启动桥接」；`python -m tak.mcp --status` 确认 |
| `401 缺少或错误的 X-TAK-Token` | `~/.tak/config.json` 的 `bridge.token` 与发现文件不一致；停桥再启动一次 |
| `外部写入未启用` | 面板勾选「允许写入」，或 `python -m tak.core.bridge write on` |
| `缺少有效的 confirm_token` | 先不带 `confirm` 调一次拿 token，第二次带同一 token |
| `confirm_token 与本次动作/参数不匹配` | 预览之后参数被改了；重新预览 |
| `宿主主线程 N 秒内没有处理这次请求` | Maya 正卡在模态对话框 / 长计算 / 脚本编辑器同步调用；关掉后再试 |
| `动作 ... 在当前 DCC 没有宿主实现` | 当前只有 Maya 实现了动作；Max / MoBu / UE 的能力表会列出但调用会明确失败 |
| `属性被锁定` | 该通道被蒙皮或约束连接锁定；在 Maya 里解锁后重试或换属性 |

## 7. 不配 AI 端点也能用

面板里的自然语言框有两条解析路径：

- **配了 AI 端点**：OpenAI 兼容 `tools` function calling，任意说法都能理解（12 秒超时，失败自动降级）；
- **没配（默认）**：本地关键词解析，覆盖「场景概况 / 帧范围 / 列节点 / 找节点 / 当前选择 / 骨架层级 / 蒙皮信息 / 节点连接 / 创建 / 改名 / 删除 / 批量创建随机立方体链」这些高频句式。解析不出来会明确提示，不瞎猜，并回退到旧的规则问答。

MCP 侧不依赖 AI 端点：外部 AI 客户端自己就是模型，TAK 只提供工具。

## 8. 验证记录（Maya 2026 · 本机）

`validation/maya_scene_actions_probe.py` 用 `mayapy` + `maya.standalone` 真机跑过完整链路，结果 `ok=true`、63 步无失败、18 个动作全部覆盖：

- 只读：场景摘要、列关节、找节点、节点详情、属性、帧范围、选择集、骨架层级、skinCluster 影响数、反查影响、连接关系；
- 写入：5 种创建、改父级、设属性、重命名、设选择、删除，以及撤销后节点确实恢复；
- 桥 + MCP：`tools/list` 21 个工具、`tools/call` 读到真实 Maya 场景数据、写开关拦截、打开后 MCP **真的创建了节点**。

`mayapy` standalone 没有 Qt 事件循环，`executeInMainThreadWithResult` 的 idle 队列不推进，所以探针里把宿主执行器换成同线程直调；真实 Maya GUI 的线程切换由适配器注册的 `maya.utils` 执行器保证，**这条切换本身不在 standalone 探针覆盖范围内**。HTTP 传输、token 校验、参数白名单与两阶段确认都在真机探针与单元测试中覆盖。

v0.7.1 另外按**客户端实际配置**跑了一次端到端握手（子进程只给 `env` 里的三个键 + `SystemRoot`/`PATH`）：`initialize` 653 ms、`tools/list` 21 个工具且 `bridge_running=True`、`tak_status` 返回真实桥地址、`tak_scene_summary` 从正在运行的 Maya 2026 GUI 读到帧范围 `1–120` 与节点计数。**客户端 UI 里点 Trust 后是否重新加载配置这一步没有代跑**，需要你重启一次客户端确认。

v0.7.2 证明了「重启前那步配置被回写」确实是本次超时的原因，并补了两次端到端验证（同一台机器、Maya 2026 GUI 桥在跑、地址 `http://127.0.0.1:64486`）：

| 场景 | initialize | tools/list | 真实场景读取 |
|---|---|---|---|
| 修复后按 `mcp.json` 正常 env 启动 | 约 700 ms | 21 个工具，`bridge_running=true` | `tak_scene_summary` → `ok=true`，帧范围 `1–120` |
| `--minimal-env`：`env` 整段丢掉，只留 `SystemRoot` / `PATH` | 约 700 ms | 21 个工具，`bridge_running=true` | 同样读到 `1–120` |

第二行是关键：没有 `PYTHONPATH`、没有 `USERPROFILE`、没有 `TAK_HOME`，启动器照样 import 到 tak 并找到桥发现文件。证据文件在 `validation/mcp_handshake_result.json` 与 `validation/mcp_minimal_env_result.json`。**仍然没有代跑的一步**：客户端 UI 里点 Trust / 重启后是否重新加载新配置——这必须由你在客户端里操作一次；操作后如果又超时，先看 `~/.workbuddy/mcp.json` 的 `command`，八成又被表单写回旧值了。

v0.8.0 另外做了三次真实验证：

| 验证 | 结果 |
|---|---|
| 真实 `deepseek-v4-flash` function calling | 修复前 HTTP 422；修复后 1.51 s 返回 `ok=true`、`mode=model`，模型直接选中 `tak_scene_create_cube_chain`，参数 `count=10 / size_by=volume / link=true` |
| `mayapy validation/maya_cube_chain_probe.py` | `ok=true`、26 步无失败。10 个立方体边长 4.596→2.207 cm、体积 97.06→10.75 cm³ 严格降序；父子链正确、自动间距 7.353 cm、一次撤销 10 个节点全消失、重名整体拒绝无半成品 |
| 全量 `pytest` | 1236 个用例通过 |

证据：`validation/maya_cube_chain_probe_result.json`。**注意**：探针是独立 mayapy 进程；你当前开着的 Maya GUI 仍是旧注册表，需要重启 Maya 后桥才会上报第 19 个动作（重启前 `tools/list` 会给 `capabilities_stale=true`）。
