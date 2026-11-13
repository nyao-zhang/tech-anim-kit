# tech-anim-kit (TAK)

跨 DCC 技术动画工具集，面向 **Maya 2023+ / 3ds Max 2023+ / MotionBuilder 2023+ / Unreal Engine 5.8–5.9**。

> 当前版本 `0.4.0`（**P7 信息架构重构**）。核心层零 DCC 依赖，四端适配器、Qt 面板与安装器均已落地，Maya 2023/2024/2026 与 UE 5.8.4 真机验证。
>
> v0.4.0 的变化集中在**面板**：工具从 11 个精简为 5 个并按分类组织，每个工具有自己的界面，选中后右侧显示作用 / 功能 / 用法，菜单只留「打开面板 / 设置 / 关于」。详见 [变更日志](CHANGELOG.md) 第 0.4.0 节。

## 设计原则

1. **确定性归规则，AI 只做候选**：判定、阻断、验收一律由规则引擎完成；LLM 只负责解释报错、生成修复候选、语义映射、打标与诊断。
2. **core 不碰 DCC**：`tak/core/` 纯 Python 3.9+，不 `import` 任何 `maya` / `pymxs` / `pyfbsdk` / `unreal`；所有宿主调用经 `adapters/` 注入，因此逻辑可在无 DCC 的机器与 CI 上完整回归。
3. **任何写入都可回滚**：LLM 只输出参数化 JSON，走函数白名单 → dry-run diff 预览 → 权限校验 → 人工确认 → undo chunk → 失败整体回滚。
4. **规范先于工具**：绑定规范是机器可读的 JSON schema，工具产出必须可被验收，而不是"看起来对"。
5. **一套设计 token**：`tak/ui/tokens.json` 同时生成 Qt QSS（Autodesk 三件套）与 Web CSS（UE 侧），含深/浅主题，四个 DCC 视觉一致。
6. **统一运行契约**：UI / 菜单 / commandlet / CI 全部走 `tak.core.runner.run_tool`，同一份快照在四端产出同一份 Report。

## 快速开始

```bash
pip install pytest pytest-cov
python -m pytest -q                                   # 全量用例
python -m pytest --cov=tak.core --cov-fail-under=80 -q  # 核心层硬门槛（当前 90%）
python validation/py_compat_smoke.py                   # 跨版本冒烟：无需 pytest / 无需 DCC
python tak/ui/generate_theme.py --check                # CI：生成物未漂移
python fixtures/generate_fixtures.py --check           # 40 个 golden fixtures 可复现
python validation/four_dcc_regression.py               # 四端一致性回归（maya/max/mobu/unreal）
python tools/new_tool.py --list                        # 列出已发现插件
python tools/new_tool.py my_tool --name "我的工具"       # 一键生成新插件
```

装进 DCC（默认 dry-run，`--apply` 才真写，写前自动备份）：

```bash
python -m tak.install --list                     # 探测四端安装位置
python -m tak.install --plan --dcc maya,max,mobu  # 预览将要做什么
python -m tak.install --apply --dcc maya          # 真写（幂等，可重复执行）
python -m tak.install --uninstall --apply --dcc maya   # 精确移除自己的块
```

`--plan` 里的 `[replace-block]` 表示块内内容与你手上这份不一致（你改过它）。安装器只动
`# >>> TAK BEGIN` 到 `# >>> TAK END` 之间的内容，块外一个字节都不碰。

运行一个工具（无需任何 DCC）：

```python
from tak.core.runner import run_tool, evaluate, list_tools
from tak.adapters.base import NullAdapter

result = run_tool("qa_inspector", snapshot, dcc="maya",
                  adapter=NullAdapter(), options={"checks": ["naming.joint_pattern"]})
print(result.passed, result.render("md"))
```

打开面板：

```bash
python -c "from tak.ui.qt import launch; launch('maya')"   # Maya / Max / MoBu 内
python -c "from tak.ui.qt import open_settings; open_settings('maya')"
python -m tak.ui.web.server --port 8765       # 只读报告看板（跨人分享 / 离线回看）
```

真机验证（需本机安装引擎）：

```bash
"/c/Program Files/Autodesk/Maya2023/bin/mayapy.exe" validation/maya_full_regression.py
python validation/run_probe.py --script ue_adapter_probe.py
```

## 五个工具 + 五个规则提供者

面板里只有五个工具，每个都有自己的独立界面：

| 工具 | 分类 | 作用 | 文档 |
|---|---|---|---|
| 场景检查 `qa_inspector` | 通用 / 绑定 / 动画 | 37 个检查项按六组摊开，勾选后一次跑完；勾选保存在 `~/.tak/config.json` | [qa_inspector](docs/modules/qa_inspector.md) |
| 导出 / 导入 `asset_pipeline` | 管线 / 绑定 | 选内容（完整绑定 / 仅骨骼 / 仅蒙皮 / 动画缓存 / 仅控制器）与目标（Maya / Max / MoBu / UE / 通用），导到文件或跨 DCC 缓存位，另一侧用同一 profile 做导入校验 | [asset_pipeline](docs/modules/asset_pipeline.md) |
| ML Deformer `mldeformer_factory` | AI / 绑定 | 姿态采样计划 + 内容寻址数据集 + 五项质量门槛；训练交给 UE 的 ML Deformer 训练器 | [mldeformer_factory](docs/modules/mldeformer_factory.md) |
| VLM 缺陷检查 `defect_inspector` | AI / 动画 | 抽帧图片序列 → 视觉模型给候选 → 确定性过滤 → 帧号 / 类别 / 骨骼 / 置信度清单 | [defect_inspector](docs/modules/defect_inspector.md) |
| 对话控制台 `agent_console` | AI / 通用 | 一句话问场景，判定走规则引擎；修复计划过白名单 → dry-run → 权限 → 二次确认 → undo | [agent_console](docs/modules/agent_console.md) |

另有两个内置界面：**设置**（分类增删改、工具归类、第三方插件导入、配置目录）与**关于**。两者都在菜单里，也在面板左下角。

还有五个插件在跑但不占工具位 —— 它们的 `manifest.json` 标了 `"internal": true`，是**规则提供者**：

| 插件 | 现在是什么 | 文档 |
|---|---|---|
| `rig_health_guard` | 「场景卫生」组 9 条规则 | [rig_health_guard](docs/modules/rig_health_guard.md) |
| `spec_enforcer` | 「绑定规范」组 7 条规则 | [spec_enforcer](docs/modules/spec_enforcer.md) |
| `ik_retarget_guard` | 「IK / 重定向」组的规则 | [ik_retarget_guard](docs/modules/ik_retarget_guard.md) |
| `export_bridge` | 「导出前置」组 2 条规则 + 导出预检实现 | [export_bridge](docs/modules/export_bridge.md) |
| `ue_import_guard` | 「导入后校验」组的规则 + 导入一致性实现 | [ue_import_guard](docs/modules/ue_import_guard.md) |

每个工具的完整说明——输入参数、输出字段、检查项清单、可直接复制的用法、能做什么与不做什么——见 **[docs/MODULES.md](docs/MODULES.md)**，按模块分文件放在 `docs/modules/`。

Maya 下共 **37 个检查项**（19 条内置规范 + 18 条来自规则提供者）；具体数量随 DCC 与进程内已加载插件变化，`qa_inspector` 的 `checks_available` 会实时反映。判定只读快照与规范，AI 类工具（`agent_console` / `defect_inspector` / `mldeformer_factory`）**不注册任何全局 `@rule`**，只产出候选与解释。

## 目录结构

| 路径 | 作用 |
|---|---|
| `schema/` | `rig_spec.schema.json` 规范定义 + `rig_spec.default.json` 默认人形规范 |
| `tak/core/spec/` | 零第三方依赖的 schema 校验器（DCC 内置 Python 无 `jsonschema` 也能跑） |
| `tak/core/rules/` | 规则基类与 `@rule`；`builtin.py` 19 条内置规则、`engine.py` 执行引擎 |
| `tak/core/runner.py` | **统一运行契约**：`run_tool` / `evaluate` / `list_tools` / `adapter_for` |
| `tak/core/registry.py` | 扫描 `tools/*/manifest.json` 动态发现插件，支持 `TAK_PLUGIN_PATH` 外部目录 |
| `tak/core/config.py` | 用户配置 `~/.tak/config.json`：分类、工具归类、隐藏、检查项勾选、导出 profile |
| `tak/core/dsl/` | 受限动作注册表与安全执行器（参数校验、原子批次、dry-run、回滚） |
| `tak/core/report/` | 统一报告模型，输出 JSON / Markdown / HTML，支持 severity 过滤 |
| `tak/core/diff/` | `SkeletonSnapshot`：导出前后骨架层级、命名、bind pose、单位轴向 diff |
| `tak/core/ai/` | OpenAI 兼容 HTTP、重试与预算统计、确定性离线 mock、三档模型路由 |
| `tak/core/agent/` | 对话式控制台内核：`AgentSession`（chat / propose / confirm / audit）、确定性意图分类 |
| `tak/core/vision/` | 抽帧缺陷诊断内核：帧归一化、候选校验、`inspect_frames`、8 类缺陷分类 |
| `tak/core/knowledge/` | 离线 BM25 检索内核：`KnowledgeBase.search/context_for/citations`、中英混合分词、分块 |
| `tak/core/mldeformer/` | 数据集工厂内核：确定性姿态采样、内容寻址清单、8 条质量门槛 |
| `tak/core/mocks/` | 四套可注入的假 DCC 模块，安装与卸载完全可逆 |
| `tak/adapters/` | `base.py` 抽象接口 + `snapshot_utils.py` / `conformance.py` 共享基线 + 四端适配器（maya / max / mobu / unreal，各含 adapter + menu + init） |
| `tak/install/` | 一条命令安装器：四端探测、标记块幂等写入、备份、精确卸载、CLI |
| `tak/compat/` | PySide2→PySide6 兼容层、UE API 降级探测 |
| `tak/ui/` | 设计 token、`theme.qss`/`theme_light.qss`/`theme.css`、`qt/`（主面板 / 详情面板 / 设置面板）、`web/` 只读报告看板 |
| `tools/` | 10 个插件（5 个工具 + 5 个内部规则提供者）+ `_template/` + `new_tool.py` |
| `knowledge/` | 5 篇绑定知识库种子文档（命名、权重、FBX/UE 流水线、IK 重定向、DCC 差异） |
| `fixtures/` | 20 好 + 20 坏骨架快照，判定器回归基线 |
| `tests/` | 39 个测试模块 / 1214 个用例 |
| `validation/` | UE headless 探测、mayapy 全量回归、跨版本冒烟、**四端一致性回归**与实测证据 JSON |
| `CHANGELOG.md` | 变更日志（含每版的已知限制） |
| `legacy/sources/` | 本机自研代码的只读整理副本（原文件未改动） |
| `docs/` | 版本兼容、验证清单、插件指南、旧代码映射、批次交付报告、发布手册 |
| `docs/modules/` | 十一个功能模块各一份使用文档，总览见 [MODULES.md](docs/MODULES.md) |

## 规范与验收

`schema/rig_spec.default.json` 定义骨骼命名与必需关节、层级深度、joint orient 容差、权重上限（≤4 influences、归一化）、允许的 deformer、FBX/USD 导出参数、UE Skeleton 映射等。所有工具的判定都以这份规范为 ground truth，AI 输出不得直接写入最终结果。

```python
from tak.core.spec import load_spec, validate
from tak.core.rules import evaluate_snapshot, list_rules

print(validate(load_spec()))                  # [] 表示合规
report = evaluate_snapshot(snapshot, dcc="maya")
print(report.passed, report.counts(), report.to_markdown())
```

40 个 golden fixtures 的回归结果：20 个好场景零 ERROR，20 个坏场景全部命中注入的违规项 —— **漏报 0 / 误报 0**（Maya 2023/2024/2026 + 开发机四个解释器重复验证）。

## AI provider

默认离线，无需密钥即可跑通全部测试；启用真实调用时设置：

```bash
TAK_AI_OFFLINE=0
TAK_AI_API_KEY=...
TAK_AI_BASE_URL=https://api.openai.com/v1   # 或任意 OpenAI 兼容端点
```

`provider_for_task("explain" | "complex" | "vision")` 分别返回 DeepSeek-V4.1-Flash / Kimi-K3.1-Pro / MiniMax-M3.1-Flash；缺密钥自动降级为确定性离线实现。**边界**：确定性判定一律走 `tak.core.rules`；AI 只解释、生成候选、打标、诊断，不参与判定。会话级模型切换由使用者在界面完成，本模块不切换当前会话模型。

批次 3 的四个 AI 内核（`agent` / `mapping` / `vision` / `knowledge`）**全部可离线跑**：`MockProvider` / `OfflineProvider` 提供确定性叙事与候选，`rig_kb` 的 BM25 检索本身零模型依赖。因此 CI 不设密钥也能跑满 872 个用例。

## AI 工作台

```python
from tak.core.agent import AgentSession

s = AgentSession(provider=None, adapter=None)      # 全离线
print(s.chat("帮我看下权重超限的骨头"))               # 只读：意图分类 → 规则判定 → AI 解释
plan = s.propose("把受污染骨骼重命名")                 # 只产出参数化 JSON，不执行
print(s.confirm(plan["plan_id"], confirm=False))    # 默认 dry-run
print(s.confirm(plan["plan_id"], confirm=True, granted=["node.write"]))  # 显式授权 + 二次确认才写
```

服务端同样是硬门：`/api/agent` 只在 `confirm is True and dry_run is False and "node.write" in granted` 三项同时成立时才真正执行，否则一律退化为 dry-run。Web 工作台五个页签分别对应 `/api/agent`（chat/plan/confirm）、`/api/knowledge`（GET 状态 / POST 检索）、`/api/vision`（抽帧诊断）、`/api/mapping`（骨骼语义映射）、`/api/motion`（动作语义检索）。

### 动作语义检索（P6.1）

```python
from tak.core.motion import MotionLibrary

lib = MotionLibrary()                                  # 12 个内置示例 clip，全离线
hits = lib.search("走路 循环", top_k=5)                  # 文本分 + 类型加权
hits = lib.search("", options={"filters": {"loopable": True,
                                           "min_duration": 0.8}})   # 空查询 = 列出全部
print(hits[0]["why"])                                  # 中文解释：命中了哪些词、为什么排第一
lib.similar_to("walk_fwd_01", top_k=3)                 # 基于特征向量的相似动作
```

**不解析 FBX 二进制**：特征来自同名 sidecar 或文件名启发式；后者会标 `features["estimated"]=1.0`，
不会假装那是精确值。检索结果完全可复现（score 降序 → clip_id 升序），不写缓存。

### 数据集工厂（P6.2）

```python
from tak.core.mldeformer import build_plan, build_manifest, validate_manifest

plan = build_plan(snapshot)                    # extreme / mid / grid / rest / mirror / random
manifest = build_manifest(plan)                # version 与 pose_digest 都是内容哈希
print(manifest["version"], manifest["pose_count"])
print(validate_manifest(manifest)["issues"])   # 8 条质量门槛，逐条给 code / severity / message
```

同一输入两次运行，manifest **逐字节相同**（含 `version` 与 `pose_digest`）；改一个角度，两个哈希都变。
`mldeformer.train` 能力位恒为 `false` —— 本工具只产出数据集与门槛报告，**不调用任何训练入口**。

## 安装器

```python
from tak.install import plan_install, apply_plan

plan = plan_install(repo_root=".")      # 默认 dry_run=True，只算不写
print(plan.summary())                   # {"create": 2, "append": 1, "skip": 1}
print(apply_plan(plan))                 # dry-run：返回将要做的事，磁盘零改动
```

四条安全约束：默认 dry-run；写前备份 `<file>.tak-backup-<N>`；只改 `# >>> TAK BEGIN` 与
`# >>> TAK END` 之间的内容；卸载精确删块并还原块外内容。连跑两次，第二次全部 `skip`。


## 插件机制

每个工具是一个目录，最小只需 `manifest.json` + `core.py`；`rules.py` / `actions.py` / `ui_qt.py` / `ui_web.py` 按需添加并按文件路径自动加载。外部插件目录用 `TAK_PLUGIN_PATH`（`os.pathsep` 分隔）追加，无需修改框架。详见 [docs/PLUGIN_GUIDE.md](docs/PLUGIN_GUIDE.md)。

## 验证状态

| 环境 | 状态 |
|---|---|
| 本机 Python 3.13 + pytest | ✅ **1214 个测试全绿**；`tak.core` 覆盖率 **88.97%**（CI 硬门槛 80%） |
| 全量覆盖率 | ℹ️ 74%（`tak.adapters` / `tak.ui` 依赖宿主 GUI 与 UE 运行时，无头环境无法真实覆盖；CI 仅作观测，不设门槛） |
| Python 3.9 语法兼容 | ✅ 178 个 `.py` 全量 `ast.parse(feature_version=(3,9))` 通过；core 层无真实 DCC import |
| **四端一致性回归** | ✅ `validation/four_dcc_regression.py`：40 fixtures × maya / max / mobu / unreal **判定漏报 0 / 误报 0 / 契约错误 0 / 基线差异 0 / 跨端差异 0**；UE 适配器 17 项观测性漏报已在 `capability_note` 登记为能力边界（不判失败） |
| Maya **2023 (3.9.7) / 2024 (3.10.8) / 2026 (3.11.9)** | ✅ `mayapy` standalone 实跑：standalone 初始化、4 根关节 + skinCluster（2 影响骨、8/8 顶点、权重和 0 偏差）、undo 回滚、40 fixtures 漏报/误报均 0、9 插件各 40 次、DSL dry-run/权限/回滚全通过 |
| UE **5.8.4** / **5.7.2** | ✅ headless commandlet 实跑，Python 3.11.8；`UnrealAdapter` 引擎内实例化成功，27 项 API 探测两版一致；`IKRigDefinition` / `IKRetargeter` / `ControlRig` 均暴露 |
| `EditorDialogLibrary` | ⚠️ 5.7 / 5.8 均不存在，兼容层已改走 `EditorDialog` |
| `MLDeformerAsset` / `NeuralMorphModel` / `DeformerGraphAsset` | ⚠️ Python 侧未暴露（与版本无关，疑为插件未启用），需在目标项目复验 |
| 安装器 | ✅ `--list` / `--plan` 在真实路径探测通过；写入/备份/卸载只跑 `tmp_path`（不碰真机配置） |
| 动作检索 / 数据集工厂 | ✅ 全离线确定性验证：同输入两次 manifest 逐字节相同、篡改任一姿态后 `version` 与 `pose_digest` 均变 |
| 3ds Max 2023+ / MotionBuilder 2023+ | ⚠️ 本机仅有 Max 2019/2021、MoBu 2023；适配器与菜单脚本已按 mock + AST 校验落地，**真机 GUI 交互待用户在 2023+ 复验** |
| UE 5.9 / 真实资产往返 / ML Deformer 训练 / VLM 真实多模态 / 真实 provider / 安装后菜单 | 📋 待复验，逐项清单见 [docs/VALIDATION.md](docs/VALIDATION.md)（B11–B26）与 [docs/WEB_PANEL.md](docs/WEB_PANEL.md)（W31–W37） |

实测证据：`validation/maya_full_regression_2023|2024|2026.json`、`validation/four_dcc_regression_result.json`、`validation/ue_adapter_probe_result.json`、`validation/ue_probe_result.json`、`validation/ue_probe_5.7.json`。

## 四端一致性契约

`tak/adapters/snapshot_utils.py` 与 `tak/adapters/conformance.py` 把「一份快照结构、四端同一口径」变成可执行断言：

```python
from tak.adapters import validate_conformance, compare_snapshots, capability_matrix

print(validate_conformance(snapshot))          # 结构体检：{ok, errors, warnings, joint_count, dcc}
print(compare_snapshots(before, after))        # 逐字段 diff，忽略 dcc/version/metadata
print(capability_matrix(adapters))             # 四端能力矩阵，缺失能力显式登记而非静默降级
```

- `CONTRACT_VERSION = "1.0"`，`IGNORED_FIELDS = ("dcc", "version", "metadata")`。
- `compare_snapshots` 支持 `ignore` 前缀精确匹配（`joints.<name>` / `joints.<name>.<key>`），用于把 UE 的结构性能力边界（无 bind 变换、无 namespace/depth）写进代码而不是写在注释里。
- 回归用**三层口径**：`judgement_detected`（规则判定基准）、`adapter_detected`（真实适配器读数）、`mock_snapshot`（注入场景），避免「把 UE 的能力边界当成 bug」。


## 路线图

| 阶段 | 目标 |
|---|---|
| **P0** ✅ | 规范 schema、golden fixtures、插件骨架、设计系统、UE 真机基线 |
| **P1** ✅ | 19 条内置规则、四套 DCC mock、真实/离线 AI provider、DSL 强化 |
| **P2** ✅ | Maya 2023+ 适配器、Qt 组件库、三个工具、mayapy 全量回归 |
| **P3** ✅ | UE 5.8 适配器与编辑器入口、导入校验、IK Retargeter 校验、Web 面板 |
| **P4** ✅ | 3ds Max 2023+ / MotionBuilder 2023+ 适配器、Maya 真实写操作、四端一致性契约与回归 |
| **P5** ✅ | Agent Console、Skeleton Mapper、VLM 缺陷诊断、绑定知识库 RAG、Web AI 工作台 |
| **P6** ✅ | 动作语义检索、ML Deformer 数据工厂、一条命令安装器、发布工程化与 `v0.3.0` |
| **P7** | 动作 sidecar 导出器（DCC 侧）、ML Deformer 训练链路验证、插件打包与自动更新 |

## 不包含

- 第三方工具（AdvancedSkeleton、MGTools、StudioLibrary 等）一律不纳入。
- 编译的 `.pyd` 闭源二进制不纳入；`legacy/sources/` 只保留纯 Python 参考副本，重写计划见 [docs/LEGACY_MAPPING.md](docs/LEGACY_MAPPING.md)。
- `legacy/sources/` 是整理后的副本，本机 `Documents` 下的原文件从未被修改。

## License

MIT — 见 [LICENSE](LICENSE)。
