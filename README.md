# tech-anim-kit (TAK)

跨 DCC 技术动画工具集，面向 **Maya 2023+ / 3ds Max 2023+ / MotionBuilder 2023+ / Unreal Engine 5.8–5.9**。

> 当前版本 `0.1.0`（P0 基线 + P1 核心层完成）。核心层零 DCC 依赖，**249 个测试全绿、覆盖率 86%**，已在 Python 3.9 / 3.10 / 3.11 / 3.13 真机解释器验证；DCC 内的工具窗口将在 P2 / P3 落地。

## 设计原则

1. **确定性归规则，AI 只做候选**：判定、阻断、验收一律由规则引擎完成；LLM 只负责解释报错、生成修复候选、语义映射、打标与诊断。
2. **core 不碰 DCC**：`tak/core/` 纯 Python 3.9+，不 `import` 任何 `maya` / `pymxs` / `pyfbsdk` / `unreal`；所有宿主调用经 `adapters/` 注入，因此逻辑可在无 DCC 的机器与 CI 上完整回归。
3. **任何写入都可回滚**：LLM 只输出参数化 JSON，走函数白名单 → dry-run diff 预览 → 权限校验 → 人工确认 → undo chunk → 失败整体回滚。
4. **规范先于工具**：绑定规范是机器可读的 JSON schema，工具产出必须可被验收，而不是"看起来对"。
5. **一套设计 token**：`tak/ui/tokens.json` 同时生成 Qt QSS（Autodesk 三件套）与 Web CSS（UE 侧），含深/浅主题。

## 快速开始

```bash
pip install pytest pytest-cov
python -m pytest -q                                 # 249 tests
python -m pytest --cov=tak --cov-fail-under=80 -q   # 覆盖率门槛（当前 86%）
python validation/py_compat_smoke.py                 # 跨版本冒烟：无需 pytest / 无需 DCC
python tak/ui/generate_theme.py                      # 由 tokens.json 生成 QSS / CSS
python tak/ui/generate_theme.py --check              # CI：确认生成物未漂移
python fixtures/generate_fixtures.py --check         # 40 个 golden fixtures 可复现
python tools/new_tool.py --list                      # 列出已发现的插件
python tools/new_tool.py rig_health_guard --name "场景体检"
```

UE 真机探测（需本机安装引擎，headless commandlet，约 1 分钟）：

```bash
python validation/run_probe.py --engine "H:\UE_5.8\Engine\Binaries\Win64\UnrealEditor-Cmd.exe"
```

## 目录结构

| 路径 | 作用 |
|---|---|
| `schema/` | `rig_spec.schema.json` 规范定义 + `rig_spec.default.json` 默认人形规范 |
| `tak/core/spec/` | 零第三方依赖的 schema 校验器（DCC 内置 Python 无 `jsonschema` 也能跑） |
| `tak/core/rules/` | 规则基类与 `@rule`；`builtin.py` 19 条内置规则、`engine.py` 规则执行引擎 |
| `tak/core/registry.py` | 扫描 `tools/*/manifest.json` 动态发现插件，支持 `TAK_PLUGIN_PATH` 外部目录 |
| `tak/core/dsl/` | 受限动作注册表与安全执行器（参数校验、原子批次、dry-run、回滚） |
| `tak/core/report/` | 统一报告模型，输出 JSON / Markdown / HTML，支持 severity 过滤 |
| `tak/core/diff/` | `SkeletonSnapshot`：导出前后骨架层级、命名、bind pose、单位轴向 diff |
| `tak/core/ai/` | OpenAI 兼容 HTTP、重试与预算统计、确定性离线 mock、三档模型路由 |
| `tak/core/mocks/` | 四套可注入的假 DCC 模块（maya / pymxs / pyfbsdk / unreal），安装与卸载完全可逆 |
| `tak/adapters/base.py` | `DCCAdapter` 抽象接口：`scene_query` / `node_query` / `skin_query` / `export` / `ui_host` / `undo_scope` |
| `tak/compat/` | PySide2→PySide6 兼容层、UE API 降级探测 |
| `tak/ui/` | 设计 token 与生成物 `theme.qss` / `theme_light.qss` / `theme.css` |
| `tools/_template/` | 标准插件模板；`tools/new_tool.py` 一键生成 |
| `fixtures/` | 20 好 + 20 坏骨架快照，判定器回归基线 |
| `tests/` | 13 个测试模块 / 249 个用例，含 golden fixtures 漏报/误报、DCC mock、AI provider、DSL 边界回归 |
| `validation/` | UE headless 探测、跨 Python 版本冒烟脚本与实测结果 JSON |
| `legacy/sources/` | 本机自研代码的只读整理副本（原文件未改动，见 LEGACY_MAPPING.md） |
| `docs/` | 版本兼容、验证清单、插件开发、旧代码映射 |

## 规范与验收

`schema/rig_spec.default.json` 定义骨骼命名与必需关节、层级深度、joint orient 容差、权重上限（≤4 influences、归一化）、允许的 deformer、FBX/USD 导出参数、UE Skeleton 映射等。

```python
from tak.core.spec import load_spec, validate

spec = load_spec()                 # 默认规范
print(validate(spec))              # [] 表示合规
```

所有工具的判定都以这份规范为 ground truth，AI 输出不得直接写入最终结果。

## 内置规则集

`tak/core/rules/builtin.py` 提供 **19 条确定性规则**（ERROR 14 / WARNING 5），覆盖命名与命名空间、必需关节、层级深度、负缩放、joint orient、权重和归一化、影响骨上限、deformer 白名单、导出轴与单位等。11 条带结构化 fix hint，但**只给建议，绝不直接改场景**。

```python
from tak.core.rules import evaluate_snapshot, list_rules

report = evaluate_snapshot(snapshot, dcc="maya")
print(report.passed, report.counts())
print(report.to_markdown())
```

40 个 golden fixtures 的回归结果：20 个好场景零 ERROR，20 个坏场景全部命中注入的违规项 —— **漏报 0 / 误报 0**。

## AI provider

默认离线，无需密钥即可跑通全部测试；启用真实调用时设置：

```bash
TAK_AI_OFFLINE=0
TAK_AI_API_KEY=...
TAK_AI_BASE_URL=https://api.openai.com/v1   # 或任意 OpenAI 兼容端点
```

`provider_for_task("explain" | "complex" | "vision")` 分别返回 DeepSeek-V4.1-Flash / Kimi-K3.1-Pro / MiniMax-M3.1-Flash；`get_provider(task=..., offline=True)` 返回确定性离线实现。缺密钥或离线开关打开时自动降级，不抛异常。带超时、指数退避重试（仅网络 / 超时 / 5xx）、调用与 token 预算统计。

**边界**：确定性判定一律走 `tak.core.rules`；AI 只用于解释报错、生成修复候选、语义映射、打标与诊断，输出不得直接写入最终判定。会话级模型切换由使用者在界面完成，本模块不切换当前会话模型。

## 插件机制

每个工具是一个目录，最小只需 `manifest.json`：

```json
{
  "id": "my_tool",
  "name": "我的工具",
  "dcc": ["maya", "max", "mobu", "unreal"],
  "min_version": { "maya": "2023", "unreal": "5.8" },
  "entry": "my_tool",
  "ui": "qt",
  "permissions": ["scene.read"]
}
```

加上 `core.py` / `rules.py` / `ui_qt.py` / `test_core.py` 即构成完整插件。规则用装饰器注册：

```python
from tak.core.rules import Finding, Severity, rule

@rule("my_tool.no_negative_scale", severity=Severity.ERROR,
      description="骨骼不允许负缩放")
def no_negative_scale(ctx):
    for name, data in (ctx.snapshot or {}).get("joints", {}).items():
        if any(v < 0 for v in data.get("scale", [])):
            yield Finding(node=name, message="存在负缩放")
```

外部插件目录用 `TAK_PLUGIN_PATH`（`os.pathsep` 分隔）追加，无需修改框架。详见 [docs/PLUGIN_GUIDE.md](docs/PLUGIN_GUIDE.md)。

## 验证状态

| 环境 | 状态 |
|---|---|
| 本机 Python 3.13 + pytest | ✅ **249 个测试全绿**，覆盖率 **86%**（CI 设 80% 门槛） |
| 真机解释器冒烟 | ✅ `validation/py_compat_smoke.py` 在 **Maya2023 (3.9.7) / Maya2024 (3.10.8) / Maya2026 (3.11.9) / UE5.8 (3.11.8) / 开发机 (3.13.14)** 五个解释器全部 PASS，各 8 项检查 |
| Python 3.9 语法兼容 | ✅ 59 个 `.py` 文件全量 `ast.parse(feature_version=(3,9))` 通过；core 层无真实 DCC import |
| UE **5.8.4** 与 **5.7.2** | ✅ headless commandlet 实跑，均为 Python 3.11.8；`tak` 可在引擎内 import；27 项 API 探测两版本完全一致 |
| `EditorDialogLibrary` | ⚠️ 5.7 / 5.8 均不存在，兼容层已改走 `EditorDialog` |
| `MLDeformerAsset` / `NeuralMorphModel` / `DeformerGraphAsset` | ⚠️ Python 侧未暴露（与版本无关，疑为插件未启用），需在目标项目复验 |
| UE 5.9 / 3ds Max 2023+ / GUI 交互 / 真实资产往返 | 📋 待复验，逐项清单见 [docs/VALIDATION.md](docs/VALIDATION.md) |

实测证据：`validation/ue_probe_result.json`、`validation/ue_probe_5.7.json`；版本差异见 [docs/COMPATIBILITY.md](docs/COMPATIBILITY.md)。

## 路线图

| 阶段 | 目标 |
|---|---|
| **P0** ✅ | 规范 schema、golden fixtures、插件骨架、设计系统、UE 真机基线 |
| **P1** ✅ | 19 条内置规则、四套 DCC mock、真实/离线 AI provider、DSL 强化、覆盖率 86% |
| **P2** | Maya 2023+：Rig Health Guard / Spec Enforcer / Export Bridge + mayapy 回归 |
| **P3** | UE 5.8/5.9：导入校验、IK Retargeter 链检查、Web 面板 |
| **P4** | 3ds Max 2023+ / MotionBuilder 2023+ 适配器与一致性回归 |
| **P5** | Agent Console、Skeleton Mapper、VLM 缺陷诊断、绑定知识库 RAG |
| **P6** | 动作语义检索、ML Deformer 数据工厂、CI / 安装器 / 文档、Release |

## 不包含

- 第三方工具（AdvancedSkeleton、MGTools、StudioLibrary 等）一律不纳入。
- 编译的 `.pyd` 闭源二进制不纳入；`legacy/sources/` 只保留纯 Python 参考副本，重写计划见 [docs/LEGACY_MAPPING.md](docs/LEGACY_MAPPING.md)。
- `legacy/sources/` 是整理后的副本，本机 `Documents` 下的原文件从未被修改。

## License

MIT — 见 [LICENSE](LICENSE)。
