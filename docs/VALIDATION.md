# 验证文档

分工原则：**能在 headless / 纯 Python 里客观测到的，我已实测；需要 GUI、真实资产或未安装版本的，列出逐项清单由你验证。** 每项都有复现命令与通过标准，验证完请把结果回填到本文件。

---

## A. 我已实测（2026-11-12，本机）

| # | 项目 | 方法 | 实测结果 | 证据 |
|---|---|---|---|---|
| A1 | UE5.8 安装位置 | `LauncherInstalled.dat` + `Build.version` | `H:\UE_5.8`，**5.8.4-58902052+++UE5+Release-5.8** | 本机文件 |
| A2 | UE 内置 Python | `Engine/Binaries/ThirdParty/Python3/Win64/python.exe -V` | **3.11.8** 64bit | — |
| A3 | headless commandlet 可执行 | `UnrealEditor-Cmd.exe TakProbe.uproject -nullrhi -unattended -ExecutePythonScript=...` | **exit=0**，耗时约 68 秒 | `validation/ue_probe.log` |
| A4 | `tak` 包在 UE Python 3.11 内可导入 | 探测脚本 `import tak` + `load_spec()` | **成功**，tak 0.1.0 / spec 1.0.0 | `validation/ue_probe_result.json` |
| A5 | 27 个 `unreal` API 存在性 | `tak.compat.unreal.api_probe` | 见下表 | 同上 |
| A6 | 既有 `acc_*.py` 语法兼容 | Python 3.11 `compile()`（不执行，避开 pyd 依赖） | 5/5 全部通过 | 同上 |
| A7 | 核心层单测 | `python -m pytest -q` | **76 passed** | 本地 |
| A8 | 主题与 fixtures 可复现 | `generate_theme.py --check` / `generate_fixtures.py --check` | 均一致；40 个 fixtures（20 好 / 20 坏），判定器**漏报 0 / 误报 0** | 本地 |
| A9 | 插件脚手架 | `test_scaffold.py` | 生成后立即可被发现、加载、跑自带单测 | 本地 |
| A10 | UE 5.7 跨版本对比 | 同一探测脚本换 `--engine` 到 `H:\UE_5.7` | **5.7.2 / Python 3.11.8，27 项 API 与 5.8.4 差异为空**；`tak` 导入成功；`acc_*.py` 语法 5/5 通过 | `validation/ue_probe_5.7.json` |

**A5 关键 API 结果（5.8.4）**

- ✅ 可用：`EditorDialog`、`EditorActorSubsystem`、`EditorLevelLibrary`、`AssetRegistryHelpers`、`EditorAssetLibrary`、`EditorUtilityLibrary`、`ToolMenus`、`ToolMenuEntryScript`、`EditorUtilityWidget`、`PythonScriptLibrary`、`IKRigDefinition`、`IKRetargeter`、`MeshDeformerCollection`、`ControlRig`、`AnimBlueprint`、`Skeleton`、`SkeletalMesh`、`AnimSequence`
- ❌ 不存在：`EditorDialogLibrary` —— 旧 `acc_unreal_api.py` 的 `try_editor_dialog_library_save_dialog` 在 5.8 会失效；`tak/compat/unreal.py` 已改为优先 `EditorDialog`。
- ❌ 未暴露：`MLDeformerAsset`、`NeuralMorphModel`、`VertexDeltaModel`、`MLDeformerComponent`、`DeformerGraphAsset` —— 引擎插件目录存在，但探测项目未启用这些插件，Python 侧因此无类型。**这不等于功能不存在**，见 B2。

---

## B. 需要你验证（逐项勾选后回填）

| # | 环境 | 步骤 | 通过标准 |
|---|---|---|---|
| B1 | **UE 5.9**（本机未安装） | `python validation/run_probe.py --engine "H:\UE_5.9\Engine\Binaries\Win64\UnrealEditor-Cmd.exe" --out validation/ue_probe_5.9.json` | `ok=true`，且 A5 的 API 差异列表无新增缺失 |
| B2 | UE 5.8/5.9 真实项目 | 在 `.uproject` 启用 `MLDeformerFramework` / `NeuralMorphModel` / `DeformerGraph` 插件后重跑探测 | `MLDeformerAsset` 等变为 `true`；5.7 与 5.8 结果一致说明这不是版本差异，**更可能是插件未启用或这些类型不以该 Python 名字暴露**；若仍为 false，P6.2 需改走 C++ / 蓝图 / Deformer Graph 路径 |
| B3 | Maya 2023 / 2024 | `mayapy -c "import sys; sys.path.insert(0, r'<repo>'); import tak; from tak.core.spec import load_spec; print(load_spec()['version'])"` | 两版本均输出 `1.0.0`，无 ImportError |
| B4 | **3ds Max 2023+**（本机仅 2019/2021） | 在 Max 的 Python 监听器中执行 B3 的导入语句 | 输出 `1.0.0`；并记录 `pymxs` 的 Skin 修改器 API 版本差异 |
| B5 | MotionBuilder 2023（GUI） | 打开 MoBu 后运行 `import tak; from tak.compat.qt import QT_BINDING; print(QT_BINDING)` | `PySide2`，且无 Qt 崩溃 |
| B6 | Maya / Max / MoBu 窗口 | P2 交付后打开任一工具窗口，检查 dock、深浅主题、日志台、diff 预览 | 主题与 `tak/ui/theme.qss` 一致，窗口可停靠、可关闭、不阻塞主线程 |
| B7 | UE 编辑器（GUI） | 检查 ToolMenus 菜单注册、Editor Utility Widget / Web 面板打开 | 菜单可见、点击执行、面板渲染与 Qt 端视觉一致 |
| B8 | 真实角色资产 | 从 Maya 导出 FBX/USD → UE 导入，跑 Export Bridge 前后快照 diff | diff 为空（或仅列出预期差异），骨骼数/命名/bind 矩阵/单位/轴向一致 |
| B9 | Playblast / UE 录屏 | 跑 P5.3 Defect Inspector 抽帧诊断 | 输出的帧号与骨骼定位与肉眼判断一致 |
| B10 | 中文路径 / 网络盘 | 在含中文与 UNC 路径的工程里跑一次完整导出 | 无编码错误、无路径截断 |

回填格式建议：`B3 ✅ Maya2024 / Python3.10 / 备注：…`。

---

## C. 复现命令

```bash
# 纯 Python 全量回归（无需任何 DCC）
python -m pytest -q
python fixtures/generate_fixtures.py --check
python tak/ui/generate_theme.py --check

# UE 真机探测（可换引擎版本 / 输出路径）
python validation/run_probe.py
python validation/run_probe.py --engine "H:\UE_5.9\Engine\Binaries\Win64\UnrealEditor-Cmd.exe" --out validation/ue_probe_5.9.json

# 新增工具插件
python tools/new_tool.py my_tool --name "我的工具" --dcc maya,unreal
python tools/new_tool.py --list
```

探测项目 `validation/TakProbe.uproject` 只启用 `PythonScriptPlugin` 与 `EditorScriptingUtilities`，不含任何游戏内容；B2 需在**真实项目**中启用 ML Deformer 相关插件。

---

## D. 已知风险与边界

1. **UE 5.9 未安装**，所有 5.9 结论均为推断，必须经 B1 复验。
2. **3ds Max 2023+ 未安装**，P4.1 的 `pymxs` 相关实现只能 mock，需 B4 复验。
3. 本机既有 `acc_*.cp37/cp39/cp311.pyd` 为编译二进制，**未嵌入仓库**（见 LEGACY_MAPPING.md），新代码保持纯 Python。
4. headless commandlet 无法验证任何 GUI 交互（Qt dock、菜单点击、Widget 渲染），全部归入 B6/B7。
5. 探测脚本只做 `compile()` 语法检查，不代表 `acc_*.py` 在运行时能脱离 pyd 正常工作；功能级迁移由 P0.5 / P3.1 负责。
