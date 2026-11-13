# tech-anim-kit (TAK)

跨 DCC 技术动画工具集，面向 **Maya 2023+ / 3ds Max 2023+ / MotionBuilder 2023+ / Unreal Engine 5.8–5.9**。

> 当前版本 `0.1.0`（**P0 / P1 / P2 / P3 已完成**，即批次 1 + 批次 2）。核心层零 DCC 依赖，**628 个测试全绿、`tak.core` 覆盖率 90%**；五个工具插件、Maya/UE 适配器、Qt 与 Web 面板均已落地，并在 Maya 2023/2024/2026 与 UE 5.8.4 真机验证。详见 [批次 2 交付报告](docs/BATCH2_REPORT.md)。

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
python -m pytest -q                                   # 628 tests
python -m pytest --cov=tak.core --cov-fail-under=80 -q  # 核心层硬门槛（当前 90%）
python validation/py_compat_smoke.py                   # 跨版本冒烟：无需 pytest / 无需 DCC
python tak/ui/generate_theme.py --check                # CI：生成物未漂移
python fixtures/generate_fixtures.py --check           # 40 个 golden fixtures 可复现
python tools/new_tool.py --list                        # 列出 5 个已发现插件
python tools/new_tool.py my_tool --name "我的工具"       # 一键生成新插件
```

运行一个工具（无需任何 DCC）：

```python
from tak.core.runner import run_tool, evaluate, list_tools
from tak.adapters.base import NullAdapter

result = run_tool("rig_health_guard", snapshot, dcc="maya",
                  adapter=NullAdapter(), options={"include_builtin": True})
print(result.passed, result.render("md"))
```

打开面板：

```bash
python -m tak.ui.web.server --port 8765       # 浏览器 / UE 内嵌浏览器
python -c "from tak.ui.qt import launch; launch('maya')"   # Maya / Max / MoBu 内
```

真机验证（需本机安装引擎）：

```bash
"/c/Program Files/Autodesk/Maya2023/bin/mayapy.exe" validation/maya_full_regression.py
python validation/run_probe.py --script ue_adapter_probe.py
```

## 五个工具插件

| 插件 | DCC | 作用 |
|---|---|---|
| `rig_health_guard` | 四端 | 场景体检：9 条 Maya 专项规则 + 保存/导出前钩子 + 只读 AI 解释层；`include_builtin=True` 时连同 19 条内置规范规则一起跑 |
| `spec_enforcer` | Maya/Max/MoBu | 7 条规范规则；4 个受限 DSL 动作（重命名、上色、权重归一化、命名空间），全部走 dry-run → 确认 → undo |
| `export_bridge` | 四端 | 导出前后骨架快照 diff，9 类偏差按阈值阻断；写 sidecar JSON 供 UE 侧校验 |
| `ue_import_guard` | UE | 9 条 UE 资产规则（Skeleton 兼容、根运动、压缩、曲线、材质槽、LOD、sidecar 不匹配）+ 导入校验 |
| `ik_retarget_guard` | UE | 6 条 IK Retargeter 链规则；离线骨骼语义映射词典与 mapping asset（供 P5.2 消费） |

全部插件共 **35 条确定性规则**，判定只读快照与规范，不直接改场景。

## 目录结构

| 路径 | 作用 |
|---|---|
| `schema/` | `rig_spec.schema.json` 规范定义 + `rig_spec.default.json` 默认人形规范 |
| `tak/core/spec/` | 零第三方依赖的 schema 校验器（DCC 内置 Python 无 `jsonschema` 也能跑） |
| `tak/core/rules/` | 规则基类与 `@rule`；`builtin.py` 19 条内置规则、`engine.py` 执行引擎 |
| `tak/core/runner.py` | **统一运行契约**：`run_tool` / `evaluate` / `list_tools` / `adapter_for` |
| `tak/core/registry.py` | 扫描 `tools/*/manifest.json` 动态发现插件，支持 `TAK_PLUGIN_PATH` 外部目录 |
| `tak/core/dsl/` | 受限动作注册表与安全执行器（参数校验、原子批次、dry-run、回滚） |
| `tak/core/report/` | 统一报告模型，输出 JSON / Markdown / HTML，支持 severity 过滤 |
| `tak/core/diff/` | `SkeletonSnapshot`：导出前后骨架层级、命名、bind pose、单位轴向 diff |
| `tak/core/ai/` | OpenAI 兼容 HTTP、重试与预算统计、确定性离线 mock、三档模型路由 |
| `tak/core/mocks/` | 四套可注入的假 DCC 模块，安装与卸载完全可逆 |
| `tak/adapters/` | `base.py` 抽象接口 + `maya/`（cmds/OpenMaya）+ `unreal/`（适配器、菜单、init、commandlet） |
| `tak/compat/` | PySide2→PySide6 兼容层、UE API 降级探测 |
| `tak/ui/` | 设计 token、`theme.qss`/`theme_light.qss`/`theme.css`、`qt/` 组件库、`web/` 面板与 HTTP 服务 |
| `tools/` | 5 个工具插件 + `_template/` 模板 + `new_tool.py` 脚手架 |
| `fixtures/` | 20 好 + 20 坏骨架快照，判定器回归基线 |
| `tests/` | 20 个测试模块 / 628 个用例 |
| `validation/` | UE headless 探测、mayapy 全量回归、跨版本冒烟脚本与实测证据 JSON |
| `legacy/sources/` | 本机自研代码的只读整理副本（原文件未改动） |
| `docs/` | 版本兼容、验证清单、插件指南、旧代码映射、批次 2 交付报告 |

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

## 插件机制

每个工具是一个目录，最小只需 `manifest.json` + `core.py`；`rules.py` / `actions.py` / `ui_qt.py` / `ui_web.py` 按需添加并按文件路径自动加载。外部插件目录用 `TAK_PLUGIN_PATH`（`os.pathsep` 分隔）追加，无需修改框架。详见 [docs/PLUGIN_GUIDE.md](docs/PLUGIN_GUIDE.md)。

## 验证状态

| 环境 | 状态 |
|---|---|
| 本机 Python 3.13 + pytest | ✅ **628 个测试全绿**；`tak.core` 覆盖率 **90%**（CI 硬门槛 80%） |
| 全量覆盖率 | ℹ️ 74%（`tak.adapters` / `tak.ui` 依赖宿主 GUI 与 UE 运行时，无头环境无法真实覆盖；CI 仅作观测，不设门槛） |
| Python 3.9 语法兼容 | ✅ 117 个 `.py` 全量 `ast.parse(feature_version=(3,9))` 通过；core 层无真实 DCC import |
| Maya **2023 (3.9.7) / 2024 (3.10.8) / 2026 (3.11.9)** | ✅ `mayapy` standalone 实跑：standalone 初始化、4 根关节 + skinCluster（2 影响骨、8/8 顶点、权重和 0 偏差）、undo 回滚、40 fixtures 漏报/误报均 0、5 插件各 40 次、DSL dry-run/权限/回滚全通过 |
| UE **5.8.4** / **5.7.2** | ✅ headless commandlet 实跑，Python 3.11.8；`UnrealAdapter` 引擎内实例化成功，27 项 API 探测两版一致；`IKRigDefinition` / `IKRetargeter` / `ControlRig` 均暴露 |
| `EditorDialogLibrary` | ⚠️ 5.7 / 5.8 均不存在，兼容层已改走 `EditorDialog` |
| `MLDeformerAsset` / `NeuralMorphModel` / `DeformerGraphAsset` | ⚠️ Python 侧未暴露（与版本无关，疑为插件未启用），需在目标项目复验 |
| UE 5.9 / 3ds Max 2023+ / GUI 交互 / 真实资产往返 / ML Deformer 训练 | 📋 待复验，逐项清单见 [docs/VALIDATION.md](docs/VALIDATION.md) 与 [docs/WEB_PANEL.md](docs/WEB_PANEL.md)（30 项） |

实测证据：`validation/maya_full_regression_2023|2024|2026.json`、`validation/ue_adapter_probe_result.json`、`validation/ue_probe_result.json`、`validation/ue_probe_5.7.json`。

## 路线图

| 阶段 | 目标 |
|---|---|
| **P0** ✅ | 规范 schema、golden fixtures、插件骨架、设计系统、UE 真机基线 |
| **P1** ✅ | 19 条内置规则、四套 DCC mock、真实/离线 AI provider、DSL 强化 |
| **P2** ✅ | Maya 2023+ 适配器、Qt 组件库、三个工具、mayapy 全量回归 |
| **P3** ✅ | UE 5.8 适配器与编辑器入口、导入校验、IK Retargeter 校验、Web 面板 |
| **P4** | 3ds Max 2023+ / MotionBuilder 2023+ 适配器与四端一致性回归 |
| **P5** | Agent Console、Skeleton Mapper、VLM 缺陷诊断、绑定知识库 RAG |
| **P6** | 动作语义检索、ML Deformer 数据工厂、安装器、全量回归与 Release |

## 不包含

- 第三方工具（AdvancedSkeleton、MGTools、StudioLibrary 等）一律不纳入。
- 编译的 `.pyd` 闭源二进制不纳入；`legacy/sources/` 只保留纯 Python 参考副本，重写计划见 [docs/LEGACY_MAPPING.md](docs/LEGACY_MAPPING.md)。
- `legacy/sources/` 是整理后的副本，本机 `Documents` 下的原文件从未被修改。

## License

MIT — 见 [LICENSE](LICENSE)。
