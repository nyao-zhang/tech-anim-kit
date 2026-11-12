# 兼容矩阵与工程约束

> 本文件是 P0.1 的产出，也是所有阶段改代码前必须先读的契约。
> 标注「实测」的数据来自本机真机运行，其余为版本基线推断，需按 VALIDATION.md 复验。

## 1. 版本矩阵

| DCC | 目标最低版本 | 本机已装 | Python | Qt 绑定 | 本机能否真机验证 |
|---|---|---|---|---|---|
| Maya | 2023 | 2018 / 2022 / **2023 / 2024** / 2026 | 2023=3.9、2024=3.10（由 `MH_SSDR_Tool/__pycache__/*.cpython-310.pyc` 佐证）、2026=3.11+ | PySide2（2025+ 为 PySide6） | ✅ 2023 / 2024 可用 mayapy |
| 3ds Max | 2023 | **2019 / 2021**（无 2023+） | 2023=3.9 | PySide2 | ❌ 只能 mock |
| MotionBuilder | 2023 | **2023** | 3.9 | PySide2 | ⚠️ 需 GUI，headless 受限 |
| Unreal Engine | **5.8 ~ 5.9** | **5.8.4**（H:\UE_5.8）/ **5.7.2**（H:\UE_5.7）/ 5.6.1 / 4.27 | **3.11.8（5.7 / 5.8 均实测）** | 引擎内无 PySide，用 UMG / Web 面板 | ✅ 5.7 与 5.8 均已 headless 实测；5.9 未安装 |
| 开发/CI | — | WorkBuddy Python 3.13.14 | 3.9 / 3.10 / 3.11 / 3.13 矩阵 | — | ✅ 全部单测在此跑 |

**关键结论：`tak/core` 必须兼容 Python 3.9。** 禁止 `match` 语句、禁止在运行时使用 `X | Y` 联合类型（可用 `from __future__ import annotations` + 字符串注解）、禁止依赖 3.10+ 标准库新增 API。`dataclasses`、`enum`、`typing`、`importlib.util` 均可用。

## 2. 架构边界

```
tak/core/       纯 Python 3.9+，零 DCC 依赖，禁止 import maya / pymxs / pyfbsdk / unreal
tak/adapters/   唯一允许接触宿主 API 的层，实现 DCCAdapter 抽象接口
tak/compat/     Qt 与 Unreal 的兼容垫片（从用户既有工具提炼）
tak/ui/         tokens.json 单一事实源 → 生成 QSS + CSS
tools/<id>/     插件，含 manifest.json，由 tak.core.registry 动态发现
```

`DCCAdapter` 抽象接口（`tak/adapters/base.py`）：`supports` / `list_nodes` / `get_node_attrs` / `get_skin_summary` / `get_skeleton_snapshot` / `export_fbx` / `export_usd` / `import_validate` / `begin_undo` / `commit_undo` / `rollback` / `ui_parent` / `log`。能力用 `Capability` 枚举声明，工具据此自动隐藏不可用功能。

所有骨架快照统一为 `SkeletonSnapshot` 的纯 JSON 结构，四个 DCC 输出同构，因此 golden fixtures 与回归测试完全不需要启动任何 DCC。

## 3. UE Python API 实测（5.8.4，2026-11-12）

证据：`validation/ue_probe_result.json`（5.8.4）与 `validation/ue_probe_5.7.json`（5.7.2），均为 headless commandlet 实跑。**两个版本的 27 项 API 探测结果完全一致（差异为空）**，因此下表可视为 5.7/5.8 的共同基线；5.9 仍需复验。

| API | 5.8.4 | 处理 |
|---|---|---|
| `EditorDialog` | ✅ | `tak.compat.unreal.dialog` 首选 |
| `EditorDialogLibrary` | ❌ **不存在** | 旧 `acc_unreal_api.try_editor_dialog_library_save_dialog` 在 5.8 会失败，必须走降级 |
| `EditorActorSubsystem` | ✅ | 推荐路径 |
| `EditorLevelLibrary` | ✅ 仍存在 | 旧路径，逐步迁移 |
| `IKRigDefinition` / `IKRetargeter` | ✅ | P3.3 可直接实现 |
| `MeshDeformerCollection` / `SkeletalMesh` | ✅ | — |
| `MLDeformerAsset` / `NeuralMorphModel` / `VertexDeltaModel` / `MLDeformerComponent` / `DeformerGraphAsset` | ❌ **Python 侧未暴露（5.7 与 5.8 相同）** | 引擎插件目录虽存在，但探测项目未启用这些插件；差异为空说明与引擎小版本无关，须在目标项目启用后复验，见 VALIDATION.md |
| `ToolMenus` / `ToolMenuEntryScript` / `EditorUtilityWidget` / `PythonScriptLibrary` | ✅ | 沿用 `acc_menu.py` 的菜单写法可行 |

`tak` 包已在 UE 5.8.4 的 Python 3.11.8 环境中成功 import，规范 `load_spec()` 校验通过 —— 证明核心层确实零 DCC 依赖、跨版本可移植。

用户既有 5 个 `acc_*.py` 在 Python 3.11 下 `compile()` 语法检查全部通过（只编译不执行，避免依赖编译 pyd）。

## 4. Qt 兼容

`MH_SSDR_Tool/tool_ui.py` 与 `vot_ui.py` 已采用 `try PySide2 / except PySide6` 双兼容，本仓库把它收敛为 `tak/compat/qt.py` 单一入口，插件不得再各写一份版本判断。在无 Qt 的 headless 环境中该模块把绑定置为 `None` 而不抛异常，因此 `mayapy` 与 `UnrealEditor-Cmd` 都能安全导入 `tak`。

## 5. 不变量

1. 确定性判定只走 `tak.core.rules`；AI 只做解释、候选、打标、诊断。
2. LLM 只输出参数化 JSON，经 `ACTION_REGISTRY` 白名单 → dry-run diff → 人工确认 → undo chunk → 失败整体回滚。
3. 主题文件由 `tokens.json` 生成，禁止手改 `theme.qss` / `theme.css`。
4. 新增工具不改框架：`python tools/new_tool.py <id>` 即可。
