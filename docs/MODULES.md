# 功能模块总览

这份文档回答三件事：面板里有什么、每件事该用哪个工具、哪些东西**不在**面板里却仍然在工作。

## 0. 面板长什么样

```
┌─────────────┬──────────────────────────────────────────┐
│ 分类         │  场景检查                        [通用][绑定]│
│  全部 · 5    │  一次跑完所有检查项：命名规范、场景卫生…      │
│  通用 · 2    │                                          │
│  绑定 · 3    │  这个工具做什么                           │
│  动画 · 2    │  · 按组列出全部检查项                     │
│  管线 · 1    │  · 每项可单独勾选                         │
│  AI · 3      │                                          │
│              │  怎么用                                  │
│ 工具         │  1. 打开「场景检查」，左侧按组浏览          │
│  场景检查     │  2. 取消不需要的项                        │
│  导出 / 导入  │  3. 点「运行检查」                        │
│  ML Deformer │                                          │
│  VLM 缺陷检查 │  输入 / 输出                              │
│  对话控制台   │  输入  当前 DCC 场景快照、项目规范          │
│              │  输出  检查报告、按组统计的问题计数          │
│  [ 设置 ]     │                                          │
│              │         [快速运行]  [打开工具界面]         │
└─────────────┴──────────────────────────────────────────┘
```

左栏两级：**先分类，再工具**。右侧是选中工具的详情 —— 它解决什么问题、能做哪些事、几步怎么用、吃什么吐什么。底部两个按钮的分工：

- **打开工具界面** —— 打开这个工具自己的窗口（每个工具一个独立 UI，带自己的配置项）；
- **快速运行** —— 用默认参数跑一遍，结果直接进「结果」页。

## 1. 为什么菜单只有三项

`TAK / Tech Anim Kit` 菜单现在只有 **打开面板 / 设置 / 关于 TAK**。

旧版本是六项：面板、场景体检、规范校验、导出校验、动作检索、Web 面板。问题不在数量，在于**前三个是同一件事的三个切片**：同一个规则引擎、同一份快照、同一张结果表，但各自只跑自己那一份规则。点齐三个才算检查完整，而没人知道这一点。

现在检查项全部收进「场景检查」工具，用勾选控制跑哪些 —— 在面板里一次配完，比在菜单里点三次可靠。

## 2. 工具总表

| 分类 | 工具 | 一句话 | 文档 |
|---|---|---|---|
| 通用 / 绑定 / 动画 | 场景检查 | 37 个检查项可勾选，一次跑完 | [qa_inspector](modules/qa_inspector.md) |
| 管线 / 绑定 | 导出 / 导入 | 选内容、选目标、导到文件或缓存位，另一侧导入校验 | [asset_pipeline](modules/asset_pipeline.md) |
| AI / 绑定 | ML Deformer | 采样计划、数据集、五项质量门槛 | [mldeformer_factory](modules/mldeformer_factory.md) |
| AI / 动画 | VLM 缺陷检查 | 抽帧交给视觉模型找变形缺陷，只给候选 | [defect_inspector](modules/defect_inspector.md) |
| AI / 通用 | 对话控制台 | 一句话问场景，得到判定与可审核的修复计划 | [agent_console](modules/agent_console.md) |

（分类列写的是默认归类，在设置面板里可以改。「全部」是视图不是分类。）

## 3. 不在工具列表里的插件

有五个插件仍然存在、仍然在跑，但 `manifest.json` 里标了 `"internal": true`，不再作为独立工具出现在面板里 —— 它们是**规则提供者**：

| 插件 | 现在是什么 | 文档 |
|---|---|---|
| `rig_health_guard` | 「场景卫生」组 9 条规则 | [rig_health_guard](modules/rig_health_guard.md) |
| `spec_enforcer` | 「绑定规范」组 7 条规则 | [spec_enforcer](modules/spec_enforcer.md) |
| `ik_retarget_guard` | 「IK / 重定向」组的规则 | [ik_retarget_guard](modules/ik_retarget_guard.md) |
| `export_bridge` | 「导出前置」组 2 条规则 + 导出预检实现 | [export_bridge](modules/export_bridge.md) |
| `ue_import_guard` | 「导入后校验」组的规则 + 导入一致性实现 | [ue_import_guard](modules/ue_import_guard.md) |

在设置面板里勾「显示内部插件」可以看到它们；日常使用不需要。

## 4. 按任务找工具

| 我想… | 用哪个 | 入口 |
|---|---|---|
| 交付前扫一遍有没有问题 | 场景检查 | 面板 → 通用 → 场景检查 |
| 只看权重问题 | 场景检查 | 打开界面，取消其它组，只留「绑定规范」 |
| 把绑定导到 UE | 导出 / 导入 | 面板 → 管线 → 导出 / 导入 |
| 在两个 DCC 之间传一份骨骼 | 导出 / 导入 | 选「仅骨骼」+ 勾「导出到缓存位」 |
| 判断一份数据够不够训练 ML Deformer | ML Deformer | 面板 → AI → ML Deformer |
| 逐帧找穿模和塌陷 | VLM 缺陷检查 | 面板 → AI → VLM 缺陷检查 |
| 不想点界面，直接问 | 对话控制台 | 面板 → AI → 对话控制台 |
| 改分类、藏工具、导入第三方插件 | 设置 | 菜单 → 设置，或面板左下角「设置」 |
| 换深色 / 浅色 | 设置 | 设置 → 常规 → 主题 |

## 5. 分类可以改

内置五个分类只是初值：`general`（通用）、`rigging`（绑定）、`animation`（动画）、`pipeline`（管线）、`ai`（AI）。

设置面板 → 分类页可以：新增、重命名、删除。删掉一个分类时，原本归在它下面的工具会**回落到「通用」**，不会从列表里消失。

设置面板 → 工具归类页可以改每个工具属于哪些分类，直接填中文名或 id，多个用逗号分隔。

改完存到 `~/.tak/config.json`，插件的 `manifest.json` 不会被改动 —— 升级插件不会覆盖你的偏好。

## 6. 共用概念

| 概念 | 含义 |
|---|---|
| **快照 snapshot** | 从 DCC 取出的纯数据结构（关节、网格、蒙皮、健康数据），所有判定只吃这个 |
| **规则 rule** | 能写成 `if` 的确定性判定。AI 不参与判定，只解释 |
| **报告 Report** | 规则的输出：findings 按 blocker > error > warning > info 排序 |
| **插件 manifest** | `tools/<id>/manifest.json`，声明 id、分类、能力、自描述 |
| **内部插件 internal** | 只提供规则 / 实现，不单独出现在面板里 |
| **profile** | 导出的配置快照，可命名保存，导出端与导入端共用 |

## 7. 当前能力边界

- **场景卫生 9 条在真 Maya 里已经有数据**（v0.5.0 起适配器采集 `metadata.health`，五类）；`unfrozen_transform` 仍由规则从快照推断，适配器刻意不上报该键 —— 两者不能同时用。
- **导入的第三方工具只支持 Python**，且依赖必须能在宿主 Python 里 import；`.mll` / `.pyd` 二进制插件不在范围内。
- **ML Deformer 不做训练**。UE 5.8 的 Python 侧没有暴露 `MLDeformerAsset` / `NeuralMorphModel` 的训练入口，本工具负责采样、建集、验质和数据集导出，训练交给 UE 的 ML Deformer 训练器。
- **VLM 缺陷检查依赖视觉模型**。离线时退化为几何启发式候选，报告里会标明是哪一种。
- **Web 面板是只读报告看板**，见下节。

## 8. Web 面板的定位

Web 面板**不是**第二个工具入口。它的实际场景只有两个：

1. **跨人看报告** —— 在 Maya 里跑完检查，把报告以 HTML 形式发链接给主管或同事，对方不需要装 Maya、不需要许可、不需要同步插件版本；
2. **离线回看** —— 历史报告留在浏览器里，不依赖 DCC 进程。

因此它只保留报告页与只读 API。v0.5.0 把「骨骼映射」「动作检索」两个页签连同后端接口一起删了 —— 它们的核心模块在 0.4.0 就随工具移除，留着页签只会点出一个 503。现存页签是「对话候选 / 知识库 / 抽帧诊断」。日常操作请用 Qt 面板。

## 8.1 外部怎么调 DCC（本地桥）

工具集对外只有一条通道：**DCC 里开一个只监听 `127.0.0.1` 的 HTTP 桥**。

在「对话控制台 → 桥接设置」里启动，或用 `tak.core.bridge.start_bridge()`。启动后写
`~/.tak/bridge.json`（含 url / port / dcc），外部按它找到桥：

| 端点 | 作用 | 是否要 token |
|---|---|---|
| `GET /bridge/health` | 探活，只回版本号，**不带任何场景数据** | 否 |
| `GET /bridge/tools` | 可见工具列表 | 是 |
| `GET /bridge/scene[?full=1]` | 场景摘要（默认只给 40 个关节名，`full=1` 才全量） | 是 |
| `POST /bridge/run` | `{tool_id, options?}` 跑一个工具，回 finding 摘要 | 是 |
| `POST /bridge/chat` | `{message}` 只读对话 | 是 |

```bash
TOKEN=$(python -c "from tak.core import config;print(config.load()['bridge']['token'])")
curl -H "X-TAK-Token: $TOKEN" http://127.0.0.1:<port>/bridge/scene
python -m tak.core.bridge status          # 桥没开时也能看到提示
```

三条边界：

1. **写场景不开放**。`/bridge/chat` 只读，`/bridge/run` 只跑工具插件（插件的
   `run_report` 阶段不写场景）。要改场景，只能在 DCC 面板里走 confirm 流程。
2. **宿主调用回主线程**。HTTP 在自己线程里跑，`cmds` 只能在主线程调，所以桥
   通过适配器注册的 `executeInMainThreadWithResult` 切回去；主线程被占住时
   硬超时 15s 回 503，不会把请求挂死。
3. **mayabatch 里调不通**。batch 没有事件循环，idle 队列不推进，请求必然超时 ——
   这不是 bug。验证桥要用 `maya.exe`。

## 8.2 对话侧连哪个 AI（出站）

入站是"外部调我"，出站是"我去调外部模型"。出站走任何 **OpenAI 兼容端点**：

- 面板：对话控制台 → 「AI 连接」，填 `base_url` / 模型 / 密钥**环境变量名**，点「测试连接」；
- 配置：`~/.tak/config.json` 的 `ai.base_url` / `ai.model` / `ai.api_key_env`；
- 环境变量：`TAK_AI_BASE_URL` / `TAK_AI_API_KEY`（优先级最高，CI 用这个）。

密钥**不写进配置文件**，配置里只存变量名 —— 把 config.json 发给同事不会泄露凭据。
离线默认值跟着钥匙走：没密钥就离线（走确定性 mock），有密钥就真连。

## 9. 文档的维护约定

`docs/modules/<plugin_id>.md` 与 `tools/<plugin_id>/` 一一对应，CI 会检查三件事：

1. 每个插件都有文档，且没有留下已删插件的孤儿文档；
2. 每份文档都有固定的八节（解决什么问题 / 怎么打开 / 输入 / 输出 / 校验项清单 / 典型用法 / 边界与限制 / 与其它模块的关系）；
3. 文档里出现的 `rule_id` 必须在源码里真实存在，文档标注的版本不能高于 `tak/version.py`。

新增插件时先跑 `python tools/new_tool.py <id>`，它会生成 manifest 骨架；补完 `category` / `summary` / `features` / `usage` 后，面板详情区就能直接读到。

第三方脚本不必按这个骨架来：设置面板 →「导入工具」可以直接选 `.py` 装进来，导入器会用 `ast` 只用不跑地读出功能、权限和入口，再生成一份可编辑的 manifest（`tak/core/importer.py`）。
