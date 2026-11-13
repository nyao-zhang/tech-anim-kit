# 功能模块总览

> 十一个模块，每个一份详细文档，都在 [`docs/modules/`](modules/)。本页告诉你**哪个模块解决哪类问题**、**从哪进**、**它们共用什么概念**。

## 0. 先回答一个问题：为什么菜单里只有六项

`TAK / Tech Anim Kit` 菜单放的是**高频入口**：打开面板、场景体检、规范校验、导出校验、动作检索、Web 面板。十一个模块里其余几个不是不存在，而是入口不同：

- 点 **打开面板** → Qt 主面板里列出当前 DCC 支持的**全部**工具（Maya 下 9 个、Unreal 下 10 个），逐个运行；
- 点 **Web 面板** → 浏览器里五个页签：对话候选、知识库、抽帧诊断、骨骼映射、动作检索；
- 任何模块都能用 Python 直接调（每份文档第 2 节都有可复制的代码）。

差异来自 `manifest.json` 的 `dcc` 字段：`ik_retarget_guard` 与 `ue_import_guard` 只支持 Unreal，`spec_enforcer` 只支持 Maya / Max / MoBu，所以在 Maya 的工具列表里看不到它们——这是正确行为，不是缺失。

## 1. 入口总表

| 模块 | 中文名 | DCC | Maya 菜单 | Web 页签 | Qt 面板 | 读取场景 |
|---|---|---|---|---|---|---|
| [`rig_health_guard`](modules/rig_health_guard.md) | 场景体检 | 四端 | 场景体检 | — | 有 | 只读 |
| [`spec_enforcer`](modules/spec_enforcer.md) | 规范校验与修复 | Maya/Max/MoBu | 规范校验 | — | 有 | 只读 + 计划写 |
| [`export_bridge`](modules/export_bridge.md) | 导出前后校验 | 四端 | 导出校验 | — | 有 | 只读 |
| [`ue_import_guard`](modules/ue_import_guard.md) | UE 导入校验 | Unreal | — | — | 有 | 只读 |
| [`skeleton_mapper`](modules/skeleton_mapper.md) | 骨骼语义映射 | 四端 | — | `?tab=mapping` | 有 | 只读 |
| [`ik_retarget_guard`](modules/ik_retarget_guard.md) | 重定向映射校验 | Unreal | — | — | 有 | 只读 |
| [`motion_library`](modules/motion_library.md) | 动作库语义检索 | 四端 | 动作检索 | `?tab=motion` | — | 不读 |
| [`rig_kb`](modules/rig_kb.md) | 绑定知识库 RAG | 四端 | — | `?tab=kb` | — | 不读 |
| [`mldeformer_factory`](modules/mldeformer_factory.md) | ML Deformer 数据工厂 | 四端 | — | — | 有 | 只读 |
| [`defect_inspector`](modules/defect_inspector.md) | 抽帧缺陷诊断 | 四端 | — | `?tab=vision` | — | 不读 |
| [`agent_console`](modules/agent_console.md) | 对话式控制台 | 四端 | — | `?tab=chat` | 有 | 只读 + 确认后写 |

「只读 + 计划写」的意思是：它只产出修复计划，写场景必须经过 dry-run 预览、权限授予、人工二次确认三步，且执行器开 undo、失败整体回滚。

## 2. 按任务找工具

| 你要做的事 | 按顺序用 |
|---|---|
| 接手一个别人做的绑定文件，先摸清有没有坑 | `rig_health_guard` → `spec_enforcer` |
| 按项目规范查命名、层级、权重，并生成修复计划 | `spec_enforcer` |
| 把 Maya 的骨架导成 FBX 前确认没变形 | `export_bridge`（导出前）→ `ue_import_guard`（导入后） |
| 把 Mixamo / Biped 的动画套到 UE Mannequin 上 | `ik_retarget_guard`（查映射）→ `skeleton_mapper`（解冲突） |
| 找一段合适的跑步循环，或找和某段相似的动作 | `motion_library` |
| 想知道「权重超 4 个影响骨」到底怎么改 | `rig_kb` |
| 要训 ML Deformer，但不知道怎么摆姿势采样 | `mldeformer_factory` |
| Playblast 里角色手臂抖动，但不知道第几帧 | `defect_inspector` |
| 不确定该用哪个工具，或想用一句话描述问题 | `agent_console` |

## 3. 共用概念

读懂任何一份模块文档，只需要先认识这六个东西。

**快照（snapshot）** — 场景的纯数据投影，一个 JSON。核心键：`dcc`、`version`、`unit`、`up_axis`、`frame_range`、`joints`（每根骨的名字、父级、深度、变换、命名空间）、`skin`（影响骨上限、权重和偏差、deformer 列表）、`metadata`。所有检查都读快照，不读场景本身——这样才能在无宿主环境里跑、才能单测。

**规范（spec）** — `schema/rig_spec.default.json` 那套约定：命名正则、必需关节、层级上限、权重容差、控制器命名空间与颜色。`spec_enforcer` 拿它判合规，其它模块拿它当参考。

**结论（Finding）** — 一条检查结果，六个字段：`rule_id`（稳定标识，形如 `rig_health_guard.orphan_nodes`）、`severity`、`message`、`node`、`fix_hint`、`data`（含可选的结构化修复指令 `fix`）。`rule_id` 是稳定的，可以拿去建工单、做统计、写豁免规则。

**严重级（Severity）** — `info` / `warning` / `error` / `blocker`。只有 `error` 与 `blocker` 会让报告判为不通过。所以「有结论」和「不通过」是两件事。

**报告（Report）** — 一次运行的完整结果：`passed`、按严重级的计数、结论列表、`metadata`（各模块自己往里放解释、计划、溯源信息）。

**权限（permissions）** — 写在 `manifest.json` 里，运行时校验：`scene.read`（读快照）、`node.write`（改场景）、`export.fbx`（触发导出）、`import.validate`（校验导入）。缺权限的写操作会抛 `DSLError`，不会静默降级成只读。

还有一条贯穿全部模块的约定：**判定由确定性代码给，AI 只写解释和候选**。同样的输入，两次运行结果逐字节相同（`mldeformer_factory` 的 `version` 与 `pose_digest` 就是这么算的）。这条不是洁癖——它让"上次也是这个问题"这句话有意义。

## 4. 当前能力边界

文档写的是**代码实现的边界**，以下是几条需要你知道的现状，避免误判：

- **场景体检**：九条规则已实现，但它们读的是快照里的 `metadata.health`。Maya 适配器目前还不采集这一段，所以菜单点开会看到一条「未提供 metadata.health，本次未执行体检规则」的提示——**这是如实报告，不是通过**。真正采集是下一步工作。
- **规范校验**：菜单入口会自动开启作用域，能跑满七条规则；从脚本调用必须显式传 `options={"spec_enforcer": True}`，否则会得到一条「未开启作用域」的提示。
- **UE 导入校验**：Unreal 适配器只产常规骨架字段，`ue_assets`（资产级信息）需要调用方注入。
- **ML Deformer**：只产数据集与门槛结论，**不调用训练入口**——UE 5.7/5.8 的 Python 侧没有暴露 `MLDeformerAsset` / `NeuralMorphModel` / `DeformerGraphAsset`，能力位 `mldeformer.train` 恒为 `false`。
- **AI 相关**：默认离线。没有配 `TAK_AI_API_KEY` 时，解释层与候选层降级为确定性结果，链路不中断。

## 5. 建议阅读顺序

1. 先读 [`agent_console`](modules/agent_console.md) 的前两节，理解「对话 → 确定性判定 → 候选动作 → 确认」这条主线；
2. 再读 [`rig_health_guard`](modules/rig_health_guard.md) 与 [`spec_enforcer`](modules/spec_enforcer.md)，它们代表最典型的两类工具（只读检查 / 检查加计划）；
3. 有导入导出需求时读 [`export_bridge`](modules/export_bridge.md) 与 [`ue_import_guard`](modules/ue_import_guard.md)；
4. 做重定向时按 [`ik_retarget_guard`](modules/ik_retarget_guard.md) → [`skeleton_mapper`](modules/skeleton_mapper.md) 的顺序读，后者消费前者的映射资产；
5. 其余按需查。

## 6. 文档的维护约定

每份模块文档头部的版本号是**它对照的代码版本**。模块行为变化时改对应文档；只改版本号不改内容说明这次发版没碰这个模块。

`tests/test_docs_modules.py` 会检查：每个插件都有文档、文档是八节结构、文档里出现的 `rule_id` 必须在源码里真实存在、本页覆盖全部插件。所以漏写文档或编造 `rule_id` 会让 CI 失败。
