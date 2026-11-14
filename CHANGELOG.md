# 变更日志

本文件记录所有值得使用者注意的变更。格式参考 [Keep a Changelog](https://keepachangelog.com/)，
版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

三条约定：

1. **`Fixed` 要写「原来错在哪」**，不是「修了 bug」。
2. **`Known limitations` 必须如实写**，没验证就写没验证。
3. 每条尽量带文件路径，方便回溯。

## [Unreleased]

（无）

## [0.6.0] - 2026-11-14

一句话：**让两条通道真的能用**。0.5.0 把桥和 AI 端点做出来了，但用起来都要先
读一段说明、进一个对话框、再配一次系统环境变量 —— 于是实际状态是「有功能，
没人用」。这一版只改可达性。

### Added

- **`tak/core/ai/credential.py` 本机凭据文件**（`~/.tak/credential.json`）：
  密钥单独存、权限收到当前用户（POSIX `chmod 600`，Windows `icacls /inheritance:r`），
  面板里可一键清除。这样不必为了用 AI 去「系统属性 → 环境变量」建条目并重启 DCC。
  读取永不抛异常（坏 JSON 当作没有凭据），写入原子替换。
- **供应商预设 `connection.PRESETS`**：DeepSeek / Kimi / 百炼 / 智谱 / 硅基流动 /
  OpenAI / 本地 Ollama / 自定义。选中即填好 `base_url`、模型、密钥变量名与申请地址。
  DeepSeek 预设用 `deepseek-v4-flash`（`deepseek-chat` / `deepseek-reasoner`
  已于 2026-07-24 弃用，照旧文档填会 404）。
- **`connection.list_models()`**：问端点 `GET /models` 拿真实可用的模型名。
  `probe()` 在模型为空时先问端点再请求；404 时把该端点真实可用的名字列进提示，
  而不是只丢一句「模型名不对」。
- **`probe(overrides=...)`**：面板里改了地址/密钥，**不必先保存**就能测试。
- **`connection.apply_preset()` / `save_endpoint()`**：只写地址类配置，**不写密钥**。
- **桥接一行开关**：对话控制台底部直接显示「桥接：未启动（外部调不进来）　
  [启动桥接]　[详情]　[AI 连接]」，点一下起、再点停，地址实时反映在标签上。
  对话框降级为「详情」，只放 token、发现文件路径和 curl 示例。
- **AI 连接三步向导**：「服务商 → 粘密钥 → 测试」。附「拉取模型列表」「清除已存密钥」
  「去申请密钥」（直接打开对应平台）。

### Changed

- **`get_provider()` 的密钥取值与 `probe()` 同源**：都走 `resolve_settings()`。
  以前 `probe` 能过而真实对话回落到 mock，就是因为两处各自拼了一遍取值逻辑。
- **`DEFAULT_BASE_URL` 从 `https://api.openai.com/v1` 改为 `https://api.deepseek.com`**：
  国内直连 OpenAI 通常不通，默认值指向一个连不上的地址只会制造「配了也不通」。
- **`describe()` 不再返回密钥字段**：它的结果会被写进日志和 JSON。
- **README 的 AI 章节重写**：从「设四个环境变量」改成「面板里点三下」，并列出三种
  设置方式的优先级。顺带删掉引用已删除模块 `tak.core.motion` 的示例代码，以及
  「Web 工作台五个页签」这类 0.4.0 之后就不再成立的说法。

### Fixed

- **点「启动桥接」没反应**（真机探针抓到）：槽函数里用 `status` 承接
  `bridge_status()` 的结果，遮蔽了外层状态栏对象，于是 `status.set_status(...)`
  打在 dict 上抛 `AttributeError` —— 而 Qt 会把槽函数里的异常吞掉，界面表现就是
  「按钮点了没用」，桥其实已经起来了。现在变量改名，并给整个槽加兜底：任何异常
  都写进日志台和 `state["bridge_error"]`，不再交给 Qt 静默吞掉。
- **向导永远显示「就绪：否」**：`probe()` 的返回里没有 `ready` 字段，界面读不到就
  当作未就绪。现在回落看 `ok`（一次成功的最小请求就等于能用）。
- **`DEEPSEEK_API_KEY` 等常见变量名不被识别**：以前只查配置里指定的那一个名字。
  现在按 `KNOWN_KEY_ENVS` 兜底扫描，并把真实命中的变量名报出来，避免「我明明配了
  它却说没有密钥」。

### Known limitations

- **预设里的模型名是初值不是真理**：各家改名很快，只能靠 `/models` 兜底。
  端点若不实现 `GET /models`（部分自建网关），仍会得到 404，此时只能手填模型名。
- **Windows 上的权限收紧依赖 `icacls`**：受管机器若禁止改 ACL，保存仍会成功但权限
  未收，`describe()["permissions"]` 会如实报 `windows-acl` 而不是假装已收紧。
- **桥仍然默认不自启**：`bridge.autostart` 默认 `false`。一个监听端口不该自己起来，
  要开就点一下。
- **向导不做密钥有效性预校验**：粘贴什么就存什么，是否有效由「测试连接」回答。
- **真机探针在这台机器上退出不干净**：`cmds.quit(force=True)` 会卡在退出流程里不返回，
  换 Python 线程做看门狗也不行（主线程卡在 C 扩展时不释放 GIL，看门狗拿不到执行权），
  最后改成写盘后直接 `os._exit(0)`；即便如此，装了大量第三方插件的 Maya 仍会留下
  单线程残留进程（CPU 不增长、`taskkill` 报「没有此任务的实例在运行」，只能重启回收）。
  证据文件是完整的，所以不影响验证结论；清理残留进程需要手工或重启。

### 验证

| 项 | 结果 |
|---|---|
| `python -m pytest -q` | exit=0 全绿（新增 `tests/test_ai_credential.py` 24 条） |
| `validation/py_compat_smoke.py` | PASS（Python 3.9 语法/API 检查） |
| `generate_theme --check` / `generate_fixtures --check` | 一致 / 40 个一致 |
| Maya 2026 GUI 面板探针 | `bridge_toggle.ok=true`（起点→启动→停止，标签与按钮文字同步）；`ai_wizard.ok=true`（8 个预设、选 DeepSeek 自动填 `api.deepseek.com` + `deepseek-v4-flash`、测试通过、保存后密钥进凭据文件且 `config.json` 不含密钥、清除干净） |

---

## [0.5.0] - 2026-11-15

把「工具集」接上外部世界。0.4.0 解决了「工具怎么组织」，这一版解决
「**别人怎么用我的工具，我怎么用别人的工具**」—— 也就是你点名的那三件事：
对话控制台应该能连 WorkBuddy、设置里应该能导入第三方插件、场景体检不该
永远是空的。

### Added

- **`tak/core/bridge.py` 本地桥**：DCC 侧监听 `127.0.0.1`，把场景查询、工具运行、
  对话暴露成 HTTP，外部 AI 与脚本用 `X-TAK-Token` 调进来。五个端点：
  `/bridge/health`、`/bridge/tools`、`/bridge/scene`、`/bridge/run`、`/bridge/chat`。
  发现文件 `~/.tak/bridge.json`，审计日志 `~/.tak/bridge.log`（512 KB 滚动）。
  `python -m tak.core.bridge status|scene|tools|run|chat` 可直接排查。
- **`tak/core/ai/connection.py` AI 端点解析**：`base_url` / `model` / 密钥**环境
  变量名**进配置（密钥本身不落盘），`probe()` 发一次 8 token 的请求，把
  401 / 404 / 429 / 超时翻译成「下一步该改什么」。
- **`tak/core/importer.py` 第三方工具导入器**：`analyze()` 只做 `ast` 解析不执行
  脚本，`draft_manifest()` 生成可直接用的 manifest，`install()` 复制到
  `~/.tak/plugins/<id>/` 并写两个薄壳。设置面板新增「导入工具」页：
  选脚本 → 分析 → 改说明 → 安装，可勾选「用 AI 补全说明与用法」。
- **`tak/core/importer_runtime.py` 导入插件运行时**：有 `run_report`/`run` 的直接
  转调；只有 `show()` 的去开它自己的窗口；两者都没有的按原样执行并如实回报。
- **Maya 适配器采集 `metadata.health`**（`_collect_health`）：孤立节点、负缩放、
  重复 shape、可疑 reference、控制器上色五类，每类都是最窄判据。
  场景卫生 9 条规则第一次在真 Maya 里有数据。
- `tak.core.registry.user_plugin_dir()`：`~/.tak/plugins` 默认进搜索路径，
  导入完不用再设环境变量。

### Changed

- `get_provider()` 的离线默认值改为「**跟着钥匙走**」：有密钥就可连，没密钥
  才离线。以前默认一律离线，用户配好地址和密钥仍然连不上，还得再找一个
  界面上没写的 `TAK_AI_OFFLINE=0`。
- 模型选择：显式 `model` > 配置 `ai.model` > 任务路由。配置里没写模型时
  不再用默认值顶掉 vision → MiniMax 这类路由。
- Web 面板收缩为**只读报告看板**：`kb` / `mapping` / `motion` 三个页签里，
  后两个后端模块已随工具删除，接口与前端代码一并移除（`/api/motion`、
  `/api/mapping` 现在返回 404，`/api/health` 的能力表也不再谎报）。

### Fixed

- **空场景跑出 19 条「缺少必需关节」**（0.4.0 遗留的真问题）：降级适配器返回
  `{"joints": {}, "metadata": {"degraded": ...}}`，长得像有数据。现在判据是
  「键存在**且** `degraded` 为空」。
- **场景卫生 9 条规则永远是空的**：适配器从来不采集 `metadata.health`，规则读不到
  就整组静默退出，面板显示「通过」。现在适配器采了 5 类，另 2 类显式声明
  「不采」并给理由（`unresolved_transforms` 会屏蔽规则自身的快照推断，
  `max_influences` 属于规范而不是场景）。
- **`qa_inspector` 的「假通过」**：四类只依赖 `metadata.health` 的检查在快照
  缺字段时会安静返回 0 条结论。现在补一条 `qa_inspector.health.partial`，
  点名哪几项没查、为什么。
- **导入的工具「跑完就是通过」**：没有 TAK 契约的脚本执行完毕曾经 `passed=True`，
  结论里却写着「未判定」。真机探针抓到后改成 ERROR 级，`passed` 与文案对齐。
- **桥在宿主主线程被占住时无限挂起**：`executeInMainThreadWithResult` 靠 idle
  队列执行，主线程卡住就永不返回。现在 `_host_call` 硬超时（默认 15s）并回 503，
  附带"主线程可能被对话框占住"的提示。

### Removed

- `tak/ui/web/app.js` 里 `/api/motion`、`/api/mapping` 的调用与两个页签。
- `tak/core/registry.py` 不再把「用户导入的插件」混进内置插件发现（`include_user=False`）。

### Known limitations

- **绑定规范里 `unfrozen_transform` 仍靠规则从快照推断**：适配器刻意不上报该键，
  否则规则会跳过 joints 推断。两者不能同时用。
- **导入器只支持 Python 脚本**，且假设依赖在宿主 Python 里可 import（`.mll` /
  `.pyd` 二进制插件不在范围内）。
- **AI 补全依赖端点可用**：没配密钥时导入照常完成，只是说明字段是确定性的
  草稿（`ai_fields` 为空，UI 会说明）。
- **桥的写场景能力故意不开放**：`/bridge/chat` 只读，confirm 流程只能在 DCC
  面板里人工点。
- **mayabatch 下调用桥会超时**（主线程不泵 idle 队列）：这不是 bug，是 batch
  没有事件循环。真机验证必须用 `maya.exe`。

### 验证

| 项 | 结果 |
|---|---|
| `python -m pytest -q` | exit=0，全绿 |
| `validation/py_compat_smoke.py` | PASS（Python 3.9 语法/API 检查） |
| Maya 2026 GUI 桥探针 `validation/bridge_probe_result.json` | `ok=true`：`thread_safe=true`、无 token 401、场景 2 关节、health 5 类、`qa_inspector` 抓到孤立节点与未上色控制器、chat 走通 |
| Maya 2026 GUI 面板探针 `validation/qt_panel_probe_result.json` | `ok=true`：设置 4 页签（含「导入工具」）、导入一个真脚本后出现在工具列表、运行后 `imported.no_contract` 且 `passed=false`、卸载干净 |
| Maya 2026 batch `validation/maya_health_probe_result.json` | `ok=true`：孤立节点只抓垃圾节点、镜像组只报负缩放不重复、missing reference 被识别、healthy reference 不误报 |

---

## [0.4.0] - 2026-11-14

一次结构性重构，起因是一条很直接的反馈：**「所有工具运行都是各种体检」**。
查下来不是 bug 而是设计问题 —— 面板把所有工具的执行结果都渲染成同一张
Finding 表，各工具自带的 `ui_qt.show()` 从来没有被调用过；`manifest.json`
里也没有分类字段，左侧就是一个平铺列表。加上菜单里平铺了三个「体检」入口，
「这个工具到底做什么」变成了只能读源码才能回答的问题。

### Changed

- **工具从 11 个精简为 5 个**，每个都有独立界面：
  - `qa_inspector` 场景检查（合并 `rig_health_guard` + `spec_enforcer` +
    `ik_retarget_guard` + `export_bridge` 校验 + 内置规范规则，37 个检查项可勾选）
  - `asset_pipeline` 导出 / 导入（合并 `export_bridge` + `ue_import_guard`，
    内容与目标枚举、统一 profile、跨 DCC 缓存位）
  - `mldeformer_factory` ML Deformer（对齐 Epic 工作流，补齐定位与文档）
  - `defect_inspector` VLM 缺陷检查（把「输入什么 / 输出什么」写进 manifest）
  - `agent_console` 对话控制台
- **`tak/ui/qt/components.py` 主面板重写**：左栏改为「分类 → 工具」两级导航，
  右栏从「一张卡片 + 一个运行按钮」改为详情面板（作用 / 功能 / 怎么用 /
  输入输出 / 权限徽章）。「打开工具界面」调插件自己的 `ui_qt.show()`，
  「快速运行」走通用契约 —— 二者不再是同一条路径。
- **`tak/adapters/maya/menu.py` 菜单精简为三项**：打开面板 / 设置 / 关于。
  场景体检、规范校验、导出校验不再是独立菜单项，它们是「场景检查」里可勾选的项。
- **`tak/core/registry.py` 扩展 `Plugin`**：新增 `category` / `summary` /
  `features` / `usage` / `inputs` / `outputs` / `tags` / `icon` / `internal`。
  旧 manifest 不含这些字段时全部留空，仍能被发现和运行。

### Added

- **`tak/core/config.py`（新）用户配置层**：`~/.tak/config.json` 管理分类列表、
  工具归类覆盖、隐藏工具、检查项勾选、导出 profile、主题、桥接端口。读取永不
  抛异常（坏 JSON / 类型不对一律回退默认并附 `_warnings`），写入用
  `tmp` + `os.replace` 原子替换。支持 `TAK_HOME` 覆盖路径。
- **`tak/ui/qt/detail.py`（新）`ToolDetailPanel`**：从 manifest 自描述字段渲染
  结构化详情，空态、加载失败、状态提示各自有明确文案。
- **`tak/ui/qt/settings.py`（新）`SettingsDialog`**：分类增删改、工具归类
  （中文名或 id 都认）、隐藏工具、主题、配置目录、恢复默认。保存后回调主面板刷新。
- **`tak/ui/qt/launcher.py` 新增 `open_tool` / `open_settings`**：`open_tool`
  按签名探测调 `ui_qt.show()`，缺失或失败时回退通用运行，两条路径都打印原因。
- **`runner.list_categories`**：左栏分类列表带每个分类下的可见工具数。
- **`runner.list_tools` 新增 `category` / `include_internal` 参数**。
- **`tools/qa_inspector/`（新）**：`checks()` 从规则注册表实时聚合，
  `groups()` 按六组聚合，`resolve_selection()` 处理「显式传入 > 用户保存 > 默认全开」，
  `save_selection()` 落配置。
- **`tools/asset_pipeline/`（新）**：`CONTENT_ITEMS` / `PRESETS` / `TARGETS`
  三张枚举表，`build_plan` / `describe_plan` / `preflight` / `execute` /
  `import_check` 五个环节，profile 存 `~/.tak/config.json`。
- **`docs/modules/qa_inspector.md`、`docs/modules/asset_pipeline.md`**：
  八节结构，含空场景下的真实输出。
- 主题 token 升到 1.1.0：新增 `size_display`，QSS 补 `QListWidget::item`、
  `takBadge` / `takSection` / `takDisplay` / `takGhost`、`QScrollArea`、
  `QSplitter`、`QToolTip` 等选择器（`tak/ui/generate_theme.py`）。

### Removed

- **`tools/motion_library/`、`tools/rig_kb/`、`tools/skeleton_mapper/`** 及
  对应测试、文档、Web 页签。理由：动作库与知识库已有成熟替代品，骨骼映射
  在 `ik_retarget_guard` 的 mapping 词典里已覆盖，三者都是「重复造轮子」。
- **`tak/core/motion/`、`tak/core/mapping/`** 两个内核包。
- 菜单项 `tak_health_guard` / `tak_spec_enforcer` / `tak_export_bridge` /
  `tak_motion_library` / `tak_web_panel`。

### Fixed

- **「所有工具都是体检」**：`ToolMainWindow.run_tool` 是唯一执行路径，任何工具
  的结果都被塞进 `ResultTable`。现在「打开工具界面」优先走插件自己的
  `ui_qt.show()`，只有不存在或失败时才回退。
- **空场景误报 19 条「缺少必需关节」**：`qa_inspector` 现在先判数据源。判据是
  「键存在」而不是「值非空」（`{"health": {}}` 是合法的空结果），并且排除
  降级适配器 —— 它返回的骨架长得很像有数据，但 `metadata.degraded` 写着环境
  不可用。没有数据源时只报一条 `qa_inspector.datasource.missing`（INFO）。
- **`ensure_providers` 每次报「未发现插件 builtin」**：`builtin` 不是插件，
  它的规则在 `tak/core/rules/engine.py` 导入时已注册，不该走 `find_plugin`。

### Known limitations

- **Maya 适配器仍不采集 `metadata.health`**，所以「场景卫生」9 条在真实 Maya
  里会跳过，显示为「未执行」而不是「通过」。补齐采集需要真机配合。
- **`defect_inspector` 只有 `ui_web.py`，没有 `ui_qt.py`**。面板里点「打开工具
  界面」会回退到通用运行 + 报告窗口。独立 Qt 界面待补。
- **ML Deformer 不做训练**：UE 5.8 Python 侧未暴露训练入口，本工具负责采样、
  建集、验质与数据集导出。
- **第三方插件导入与「AI 读插件自动生成描述」尚未实现**，设置面板预留了入口
  文案但按钮未上线。
- **Web 面板未完成收缩**：`panel.html` 仍保留 `kb` / `mapping` / `motion` 三个
  页签，对应模块已删，点击会失败。定位改为只读报告看板的改造待下一批。
- **备份无限累积**（`--clean-backups` 未做）。

### 验证

- 全量测试通过；`tools/qa_inspector`、`tools/asset_pipeline` 的发现、运行、
  边界（空场景 / 空勾选 / 无宿主）均有用例覆盖。
- `docs/MODULES.md` 与 `docs/modules/*.md` 通过八节结构、`rule_id` 真实性、
  版本不超前三条护栏。
- Qt 面板**尚未在真 Maya 里验证**（本机 pytest 环境无 PySide）。需要真机确认
  分类切换、详情渲染、独立界面打开三条路径。

## [0.3.3] - 2026-11-14

写模块文档时把「菜单点开到底是什么效果」逐条跑了一遍，于是发现两个工具在
菜单入口是空转的：显示「通过」，实际一条规则都没跑。这一版补文档、也补掉
这两处静默。测试 **1130 → 1214**（+84，其中 81 条是文档护栏）。

### Added

- **`docs/modules/` 十一个功能模块文档**，每份八节：解决什么问题、怎么打开、
  输入参数、输出字段、校验项清单、典型用法（附真实运行输出）、边界与限制、
  与其它模块的关系。所有 `rule_id`、字段名、默认值都对着代码核过，示例都实跑过。
- **`docs/MODULES.md` 总览**：回答了「菜单为什么只有六项」（其余在 Qt 面板的
  工具列表与 Web 五页签里）、入口总表、按任务找工具、共用概念（快照 / 规范 /
  Finding / 严重级 / 报告 / 权限）、当前能力边界。
- **`tests/test_docs_modules.py`（81 个用例）**：每个插件必须有文档、文档八节
  结构、代码块闭合、无 emoji、标注版本不得比当前版本新；正向查「文档提到的
  标识源码里真有」，反向查「注册了规则文档里必须写到」。`rule_id` 多为运行时
  拼接（`TOOL_ID + ".orphan_nodes"`、`"%s.candidate.%s"`），所以判定按「插件
  前缀 + 源码中的拼接形式」而不是简单 grep。
- CI 新增独立步骤 `Module docs match code`，文档与代码漂移时失败信息直接指向
  `docs/modules/`。
- `rig_health_guard` 新增 `rig_health_guard.datasource.missing`（INFO），
  `spec_enforcer` 新增 `spec_enforcer.scope.disabled`（INFO）：数据源缺失或
  作用域未开启时，报告里明确写「本次未执行」，不再让空结论看起来像通过。

### Fixed

- **菜单「场景体检」是空转的**。`tak/adapters/maya/menu.py` 的 `_run_tool` 调用
  `run_tool(tool_id, snapshot, dcc="maya", adapter=adapter)` 时**不传 `options`**，
  于是 `include_builtin` 为 `False`，只跑插件专项规则；而 9 条专项规则全部依赖
  `metadata["health"]`，Maya 适配器又不采集该字段，整组跳过。实测结果是
  `passed=True, findings=0` —— 用户点开看到「通过」，其实什么都没检查。
  现在 `_MENU_OPTIONS` 给体检传 `{"include_builtin": True}`，与 Qt 面板一致。
- **菜单「规范校验」同样是空转的**。7 条规则要求显式开启作用域，菜单不传
  `options` 时 `_scope_enabled` 为 `False`，同样是 `passed=True, findings=0`。
  现在菜单传 `{"spec_enforcer": True}`。
- **「没检查」被显示成「没问题」**。两处静默的共同点：规则整组跳过、报告却是
  通过，没有任何一处告诉使用者「这次没查」。现在各留一条 INFO 说明，
  不影响 `passed`（仍然零阻断），但结论列表里看得见。
- `README.md` 工具数「35 条确定性规则」与实际不符：插件规则是 **33 条**
  （体检 9 + UE 导入 9 + 规范 7 + 重定向 6 + 导出 2），加 19 条内置共 52 条。

### Changed

- `rig_health_guard` 与 `spec_enforcer` 两份文档头部标注 `0.3.3`（它们描述的
  行为在本版变了）；其余九份保持 `0.3.2`，表示对照的是那一版代码。

### Known limitations

- **Maya 适配器仍不采集 `metadata.health`**。所以「场景体检」在真机上现在是
  *如实报告未检查*，而不是*真的检查了*。要让它干活需要一个采集环节把孤立节点、
  未冻结变换、负缩放、重复 shape、遗留 reference、控制器颜色、影响骨上限塞进
  快照。这是下一步工作，本版没有动——采集逻辑要读真实场景，需要你在 Maya 里
  配合验证，我不想在没验证的情况下让它看起来能用。
- `run_rules(plugin_id=None)` 会跑进程内**已加载**的全部插件规则，
  `metadata.rules_count` 因此不是固定值：只装本插件时体检是 28，同进程里还
  加载过 `spec_enforcer` / `export_bridge` 时会变成 37。判定不受影响（多跑的
  规则是确定性的），但别把这个数字当常量。已写进 `rig_health_guard` 文档。

## [0.3.2] - 2026-11-14

补丁版：修掉 0.3.1 在**中文界面 Maya** 上「装了等于没装」的问题，以及两处
「假成功」。测试 **1121 → 1130**（+9）。

### Fixed

- **安装器写进了 Maya 根本不扫描的目录**（0.3.1 上线 1 小时内由真机暴露）。
  中文界面的 Maya 2026 把用户脚本目录放在
  ``Documents/maya/2026/zh_CN/scripts/``，实测 ``internalVar(userScriptDir=True)``
  正是这个路径，而 ``Documents/maya/2026/scripts`` **完全不在** ``sys.path`` 里；
  0.3.1 按后者写入，于是用户打开 Maya 后既没有菜单，手动
  ``from tak.adapters.maya.menu import register_menu`` 还报
  ``ModuleNotFoundError: No module named 'tak'`` —— 因为那个文件从未被执行。
  现在探测顺序是 ``<版本>/<语言>/scripts`` → ``<版本>/scripts`` →
  版本无关的 ``Documents/maya/scripts``；语言目录按存在性判定，多个并存时取
  ``prefs`` 最近修改的那个，可用 ``TAK_MAYA_LANG`` / ``MAYA_UI_LANGUAGE`` 强制指定。
- **``register_menu()`` 会假成功**：Maya 在没有主窗口时（batch / 无界面启动）
  ``cmds.menu`` **不抛异常**，而是直接返回 ``False``；老实现只判 ``menu is None``，
  于是返回 ``{"ok": True, "items": 7}`` 却一个菜单都没建（用
  ``mayabatch -script`` 实测复现）。现在创建后必须
  ``cmds.menu(..., q=True, exists=True)`` 查得回来才算成功，否则返回
  ``ok=False`` 并说明「找不到主窗口」。
- **``cmds.about(mayaMainWindow=True)`` 在 Maya 2026 是非法标志**：
  ``TypeError: 标志"mayaMainWindow"无效``。异常被 ``call()`` 吞掉后永远落回
  写死的 ``"MayaWindow"``，也就是说「主窗口探测」从来没真正探测过。改为按候选名
  ``window -q -exists`` 验证，再用 ``lsUI(window=True)`` 兜底。
- ``tests/test_installer.py`` 里 ``_fake_home`` 被定义了两次，后一个静默覆盖前一个。

### Added

- 9 个用例：语言子目录优先 / 英文界面回落 / 环境变量覆盖 / 多语言取活跃 prefs /
  install 端到端必须写进语言目录；无主窗口必须 ``ok=False`` 且不留注册痕迹；
  宿主「返回名字却没真建」时必须报失败；``_main_window`` 不得再调 ``about``。

### Known limitations

- **语言目录是启发式判定**：靠 ``<版本>/`` 下 ``xx_YY`` 形态的目录名 + ``prefs``
  修改时间。若用户手工建了多个语言目录且时间接近，可能选错；此时用
  ``TAK_MAYA_LANG`` 显式指定。真正的权威值是 Maya 进程里的
  ``internalVar(userScriptDir=True)``，安装器不启动 Maya 就拿不到。
- **0.3.1 装过的机器会留下死文件**：``<版本>/scripts/userSetup.py``（Maya 不扫描）。
  升级到 0.3.2 后 install 不会自动删它（卸载只处理当前探测到的路径），需要手工
  删除。本机已清。
- **GUI 菜单是否真的出现仍需人工确认**：batch 模式没有主窗口，能验证到
  「块被执行、``tak`` 可导入、``dispatch`` 可运行、失败路径如实报错」，但
  「菜单栏里那一行」只有开 GUI 能看见。

## [0.3.1] - 2026-11-13

补丁版：修掉 0.3.0 安装器的一处静默失败，并为此补上回归护栏。无接口变更，
无行为变更——只让失败能被看见。测试 **1117 → 1121**（+4）。

### Fixed

- **「装了、导入成功了、菜单却没出现」时安装块一声不吭**（`tak/install/targets.py`）。
  `tak/adapters/maya/menu.py` 与 `mobu/menu.py` 按约定**不抛异常**、只返回
  `{"ok": False, "reason": "..."}`；而安装器生成的启动块是 `_tak_register_menu()`
  裸调用、把返回值丢掉，外层的 `except` 也只在真抛异常时才打印。两者叠加的结果是
  绝大多数失败路径都走不到任何输出。
  这不是推测：真机安装（Maya 2026 / MoBu 2023 / UE 5.8）后执行 `userSetup.py`，
  控制台**一行都没有**。现在块内检查 `.get("ok")` 并打印 `reason`，Maya / MoBu 两处同改。
  3ds Max 与 UE 本来就没这个问题：`init_max.initialize()` 会打 `menu.ok=%s` 与降级原因，
  `init_unreal.main()` 把每条降级都 `print` 出来并把结果放进返回字典。
- **`tak/install/writer.py` docstring 里的标记块示例写死 `v0.3.0`**：版本一动它就变成过期文档。
  改成 `v<版本>` 占位。`tests/test_release_meta.py` 只拦**带引号**的版本字面量，
  这种写在 docstring 里没引号的示例它扫不到，所以手工修——这也说明那道护栏有已知盲区。

### Added

- `tests/test_installer.py` 新增 4 个用例：Maya / MoBu 块必须检查 `register_menu()` 的 `ok`
  并打印失败原因；Maya / MoBu / UE 三个块的模板必须能过 `ast.parse(feature_version=(3, 9))`
  （块是直接写进宿主启动文件的，语法错等于宿主启动错）；Max 块必须保留 `initialize()` 调用。

### Known limitations

- **备份会一直累积**：每次 `replace-block` 都在入口文件旁留一份 `<file>.tak-backup-<N>`，
  `N` 只增不覆盖；`--uninstall` 也会再留一份（这是刻意的——`backup-1` 往往就是装上 TAK 之前
  的原始文件，删掉就没法回到原点）。代价是频繁重装会在宿主启动目录堆出若干 1 KB 小文件，
  目前**没有清理入口**，需要手工删。真机现状：`Documents/maya/2026/scripts/` 下 2 份，
  `MB/2023/config/PythonStartup/` 下 2 份，UE 项目 `Content/Python/` 下 1 份。

## [0.3.0] - 2026-11-13

对应批次 4（P6）：动作语义检索、ML Deformer 数据工厂、一条命令安装器、发布工程化。
测试 **872 → 1117**（+245），插件 **9 → 11**。

### Added

- **Motion Library 动作语义检索**（P6.1）
  - `tak/core/motion/`：`MotionClip` 模型、`extract_features()` 数值特征提取（根速度 / 位移 / 循环误差 / 对称性 / 姿态方差，公式写在 docstring）、`MotionLibrary.search()`（BM25 文本分 + 类型加权 + 硬过滤）、`similar_to()`、`context_for()`、`to_index()/from_index()`。
  - `tools/motion_library/`：纯只读插件，12 个内置示例 clip 覆盖 idle/walk/run/jump/attack/hit/turn/crouch/climb/dance/death。
  - `scan_directory()` 支持 `.fbx` + 同名 sidecar；**不解析 FBX 二进制**，无 sidecar 时走文件名启发式并标 `estimated`。
  - Web 面板第 5 个页签「动作检索」+ `POST /api/motion`；支持 `?tab=` 深链。
- **ML Deformer 数据工厂**（P6.2）
  - `tak/core/mldeformer/`：`build_plan()` 确定性姿态采样（extreme/mid/grid/rest/mirror/random 六类）、`build_manifest()` 内容寻址清单（`version` / `pose_digest` 均为 sha256 前 16 位）、`validate_manifest()` 8 条质量门槛。
  - `tools/mldeformer_factory/`：`plan` / `manifest` / `validate` / `markdown_summary`，Report 按 issue 逐条出 Finding。
- **一条命令安装器**（P6.3）
  - `tak/install/`：四端探测（`TAK_MAYA_ROOT` 等环境变量优先）、标记块幂等写入、备份、精确卸载、CLI（`--list` / `--plan` / `--apply` / `--uninstall` / `--json`）。
  - 默认 dry-run；`--apply` 才写盘；块外内容逐字节不动。
- **Maya 菜单入口**（补 P2 的漏项）
  - `tak/adapters/maya/menu.py`：`register_menu` / `unregister_menu` / `dispatch` / `menu_script`。此前 Max / MoBu / UE 都有 `menu.py`，唯独 Maya 没有，`tak.install` 的 Maya 启动片段导入它必然失败。
- **进程内 Web 面板** `server.serve_in_thread()` / `stop_panel()`：DCC 菜单项可在不阻塞宿主的前提下拉起面板，重复调用复用同一实例（不再撞端口、不泄漏线程）。
- `docs/RELEASE.md`：七步发布流程（版本号 / 变更日志 / 证据回填 / 全量回归 / 打包 / tag 推送 / 发布后），含推送失败分类与三种回滚路径。
- `tests/test_release_meta.py`：版本、插件 manifest、文档链接的一致性护栏。

### Changed

- `tak/version.py` 升到 `0.3.0`，并明确为**全仓库唯一版本出口**。
- `tak/ui/web/app.css` 新增 `.tak-field-inline`（复选框行内布局）。
- CI 增加安装器 dry-run 探测与 P6 模块冒烟步骤。

### Fixed

- **`tak/version.py` 与 README 版本号不一致**：批次 3 只把 README 改到 `0.2.0`，代码里仍停在 `0.1.0`，`/api/health` 也一直在报旧版本。现在版本只在 `tak/version.py` 定义，`tests/test_release_meta.py` 负责拦。
- **Maya 安装后没有菜单且无任何提示**：安装块的 `except Exception:` 是静默的，用户只能看到「什么都没发生」。除了补 `menu.py`，还把该 `except` 改为打印具体原因（Maya / MoBu 两处）。
- **`build_plan()` 传错层级时静默返回 0 个 pose**：仓库里所有 fixture 都是 `{"id":…, "snapshot": {…}}` 的外层包装，直接把它传给 `build_plan` 会得到空计划。现在会返回带 `warnings` 的空计划而不是让人误以为「骨架没问题」——但**仍不自动解包**，因为 `ToolContext.snapshot` 本身就是内层快照，自动解包会让两条路径行为不一致。
- **安装器在干净机器上永远探测不到 Maya**：原实现只检查 `<Documents>/maya/<version>/scripts` 是否存在，而该目录是 Maya 首次需要时才创建的 —— 本机装了 2023/2024/2026，三个版本目录都在，`scripts` 一个都没有，于是 `--list` 报「未检测到 Maya」。现在探测证据用版本目录本身（`probe` 字段），写入根仍指向 `scripts`，由 writer 在写入前 `makedirs`。附 5 个回归用例（含「版本目录存在但 scripts 不存在时 apply 必须建目录」）。

### Known limitations

- **3ds Max 2023+ / MotionBuilder 2023+ 仍未真机验证**（本机只有 Max 2019/2021、MoBu 2023 GUI）。适配器与菜单只跑过 mock + AST 校验，见 `docs/VALIDATION.md` B11–B14。
- **ML Deformer 训练入口不存在**：UE 5.7/5.8 的 Python 侧均未暴露 `MLDeformerAsset` 等类型（B2 / W3 / W30）。`tak/core/mldeformer` 只产出数据集与门槛报告，能力位 `mldeformer.train` 恒为 `false`，**不得**据此宣称支持训练。
- **动作检索不解析 FBX 二进制**：特征依赖 sidecar 或文件名启发式，`features["estimated"]=1.0` 会标记估算值。真实 clip 的数值特征需要 DCC 侧导出 sidecar。
- **镜像姿态在对称骨架下会被去重**：左右限值相同时，镜像角度与对侧极值重合，按 `(joint, axis, angle)` 去重后 `mirror` 计数为 0（信息保留在 `tags` 里）。非对称 limits 才会产出真实镜像行。
- **安装器只写文件，不启动宿主**：装完需要用户重启 DCC；`--apply` 的实际效果（菜单是否出现）必须人工确认（W35–W37）。
- **真实 AI provider 仍未验证**：B16–B19 全部待复验。

## [0.2.0] - 2026-11-13

对应批次 3（P4 + P5）：四端适配器与能力契约、AI 工作台。测试 **628 → 872**（+244），插件 **5 → 9**。

### Added

- 3ds Max 适配器（`pymxs`，`undo_strategy="state_log"`）、MotionBuilder 适配器（`pyfbsdk`，不声明 `capture.playblast`）。
- `tak/adapters/snapshot_utils.py`（一份快照结构）、`tak/adapters/conformance.py`（`validate_snapshot` / `compare_snapshots` / `capability_matrix` / `check_adapter`，`CONTRACT_VERSION="1.0"`）。
- `validation/four_dcc_regression.py`：40 fixtures × 4 端，三层口径（判定基准 / 适配器观测 / mock 场景）。
- `tak/core/agent`、`tak/core/mapping`、`tak/core/vision`、`tak/core/knowledge` 四个 AI 内核；`agent_console` / `skeleton_mapper` / `defect_inspector` / `rig_kb` 四个插件。
- Web AI 工作台四页签与 `/api/agent` `/api/knowledge` `/api/vision` `/api/mapping`。
- Maya 适配器四个真实写方法：`rename_node` / `set_control_color` / `normalize_weights` / `set_namespace`。

### Fixed

- `compare_snapshots(ignore=...)` 只过滤顶层字段，关节字段仍参与比对，导致 UE 的能力边界被误判为差异。
- `MockAdapter` 用父子链重算 `depth`，覆盖了注入场景的声明值 —— 测试替身悄悄修好了被测对象的错误，`skeleton.max_depth` 漏报被抹平。
- Maya / Max / MoBu 的 `_capture` 不读 mock 桥接，`namespace` / `depth` / `unit` / `up_axis` / `frame_range` 在 mock 下全丢。
- **Maya 的 DSL 动作此前是 no-op**：`getattr(adapter, "rename_node")` 返回 `None`，执行器把「动作不存在」当成功静默跳过，dry-run / 权限 / undo 全跑通但场景一个字节没改，报告还说修好了。

## [0.1.0] - 2026-11-12

对应批次 1 + 批次 2（P0–P3）：规范 schema、规则引擎、受限 DSL、Maya/UE 适配器、5 个插件、Qt 与 Web 面板。测试 **249 → 628**。

### Added

- `schema/rig_spec.schema.json` 规范定义 + 40 个 golden fixtures（20 好 / 20 坏）。
- 19 条内置规则 + `@rule` 规则引擎 + 统一运行契约 `tak.core.runner.run_tool`。
- 受限 DSL：函数白名单 → dry-run diff → 权限 → 人工确认 → undo chunk → 失败整体回滚。
- 四套可注入可卸载的 DCC mock（maya / pymxs / pyfbsdk / unreal）。
- OpenAI 兼容 AI provider + 指数退避重试 + 预算统计 + 确定性离线 mock。
- Maya 2023+ 适配器、UE 5.8 适配器与编辑器入口、Qt 组件库、Web 面板。
- `rig_health_guard` / `spec_enforcer` / `export_bridge` / `ue_import_guard` / `ik_retarget_guard` 五个插件，共 33 条规则。

### Fixed

- `ToolResult.to_dict()` 覆盖而非合并 `report.metadata`，插件的 AI 解释与映射摘要在 Web API 上丢失。
- CI 覆盖率门槛范围错误（整体覆盖率被当成核心层门槛）。
- 回归脚本中 `rollback()` 先于蒙皮采样执行，把刚建的 skinCluster 撤销了。
- `runner.clear_cache()` 被测试 fixture 调用后 `rules.py` 二次 exec，规则重复注册污染 19 个测试。

---

[Unreleased]: #unreleased
[0.6.0]: #060---2026-11-14
[0.5.0]: #050---2026-11-15
[0.3.3]: #033---2026-11-14
[0.3.2]: #032---2026-11-14
[0.3.1]: #031---2026-11-13
[0.3.0]: #030---2026-11-13
[0.2.0]: #020---2026-11-13
[0.1.0]: #010---2026-11-12
