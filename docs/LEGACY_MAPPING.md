# 既有自研代码整理与嵌入映射

> 原则：**只复制，绝不改动** `C:\Users\NYAO\Documents` 下的原文件；仓库内 `legacy/sources/` 是整理起点，不是运行时代码。

## 1. 纳入范围（28 个纯 Python 文件，约 509 KB）

| 来源 | 文件 | 价值 | 目标模块 / 阶段 |
|---|---|---|---|
| `Documents/UnrealEngine/Python/` | `acc_unreal_api.py` | UE 引擎信息、对话框、资产注册表、依赖查询；`hasattr` 降级写法 | → `tak/compat/unreal.py`（**P0 已提炼**）、`adapters/unreal/`（P3.1） |
| 同上 | `acc_menu.py` | `unreal.uclass` + `ToolMenuEntryScript` 菜单注册、语言状态、剪贴板 JSON | → `adapters/unreal/menu.py`（P3.1） |
| 同上 | `acc_unreal_def.py` | 常量/定义 | → `adapters/unreal/defs.py`（P3.1） |
| 同上 | `acc_perf_audit_ue.py` | 性能审计启停与报告 | → 可选插件 `tools/perf_audit/`（P6 之后） |
| 同上 | `init_unreal.py` | UE Python 启动引导 + 热重载 | → `adapters/unreal/bootstrap.py`（P3.1） |
| `maya/2024/.../scripts/` | `vot_core.py` | 顶点序传递核心算法（62 KB） | → `tools/vertex_order_transfer/`（P2 之后，纯算法可抽 `core/`） |
| 同上 | `vot_ui.py` | 边循环选择交互 UI | → 同插件 Qt 层；PySide 兼容已由 `tak/compat/qt.py` 统一 |
| 同上 | `pose_delta.py` | 姿态增量 / corrective 相关（30 KB） | → P6.2 ML Deformer 姿态采样的数据基础 |
| 同上 | `maya_weight_joints_tool.py` | 权重工具 | → `tools/weight_tools/`（P2 之后） |
| 同上 | `userSetup.py` | Maya 启动引导 | → `adapters/maya/bootstrap.py`（P2.1） |
| `MH_SSDR_Tool/` | `mh_ssdr/ssdr_core.py` | SSDR 求解核心 | → `tools/mh_ssdr/core/`（保留算法，去 Maya 依赖） |
| 同上 | `mh_ssdr/maya_bridge.py` | Maya 场景桥接 | → `tools/mh_ssdr/bridge/`（P0.1 adapter 接口对齐） |
| 同上 | `mh_ssdr/mirror.py` | 左右镜像 | → 同插件 |
| 同上 | `mh_ssdr/tool_ui.py` | PySide2/6 双兼容界面 | → 同插件 Qt 层；兼容层已统一到 `tak/compat/qt.py` |
| 同上 | `mh_ssdr/selftest.py`、`tests/test_*.py`（11 个） | 现成回归测试资产 | → 各插件 `tests/`，并纳入 CI |
| 同上 | `install.py`、`mh_ssdr_run.py`、`__init__.py` | 安装与入口 | → 一键安装器（P6.3） |

## 2. 明确排除

**第三方 / 授权不明，一律不纳入仓库**：AdvancedSkeleton / AdvancedSkeleton5、MGTools、SHAPES、StudioLibrary（`studiolibrary-main`）、ThRigTools_v2022(Free)、bb_Tools、`vaccine.py`、`PY_RIGASSIT` 等。这些只作为「本机已装、不上传」记录在案。

**编译二进制不纳入**：`Documents/UnrealEngine/Python/acc_*.cp37-win_amd64.pyd` / `.cp39-…` / `.cp311-…` 及无后缀 `.pyd`（`acc_communication`、`acc_menu_export`、`acc_perf_audit_core`、`acc_perf_audit_scan`、`acc_unreal_action`、`acc_unreal_tools`）。原因：闭源、不可审计、绑定 Python 小版本、跨平台不可用。

处理方式：保留为**可选外部依赖**，并在 P3.1 列出需要纯 Python 重写的接口清单：

| pyd 模块 | 暴露的功能（由调用点推断） | 重写优先级 |
|---|---|---|
| `acc_unreal_tools` | 资产导入/导出、引用检查、UE 路径 ↔ 磁盘路径 | 高（Export Bridge 依赖） |
| `acc_menu_export` | 导出对话框与资产选择 | 中 |
| `acc_perf_audit_scan` / `_core` | 性能扫描与 JSON 报告 | 低（可选插件） |
| `acc_communication` / `acc_unreal_action` | 与外部程序通信、启动引导 | 低 |

## 3. P0 已完成的整理优化

1. `tak/compat/qt.py`：把散落在 `MH_SSDR_Tool/tool_ui.py` 与 `vot_ui.py` 的 PySide2→PySide6 双兼容收敛为单一入口，并增加 headless 安全降级、`host_main_window`、`apply_theme`。
2. `tak/compat/unreal.py`：把 `acc_unreal_api.py` 的 `hasattr` 降级模式收敛为 `engine_version` / `asset_registry` / `editor_actor_subsystem` / `dialog` / `api_probe`；UE5.8 实测证明 `EditorDialogLibrary` 已不存在，降级路径是必需的。
3. `docs/COMPATIBILITY.md`：把版本矩阵、Python 3.9 约束、adapter 接口、API 弃用实测结果写成契约。
4. `validation/ue_probe.py`：在 UE 5.8.4 真机验证了 5 个 `acc_*.py` 在 Python 3.11 下语法兼容，且 `tak` 包可导入。

## 4. 后续重构安排（不阻塞 P0）

- P2.1：`userSetup.py` → Maya bootstrap；`vot_*` 抽成第一个示范插件，验证「旧代码 → 新插件」的迁移路径。
- P3.1：`acc_*` → Unreal adapter；先纯 Python 重写 `acc_unreal_tools` 的路径/引用功能，摆脱 pyd。
- P4：`mh_ssdr` 的 core/bridge/ui/tests 分层作为 Max/MoBu 插件的参照实现。
- P6.2：`pose_delta.py` 作为 corrective 姿态采样与 ML Deformer 训练数据生成的起点。

每次重构都必须先在 `legacy/sources/` 保留原文，并补一条回归测试，避免「优化」把已验证的行为改坏。
