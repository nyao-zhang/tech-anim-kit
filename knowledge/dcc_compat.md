# DCC 兼容矩阵与工程约束

Tags: compat, python39, pyside, unreal, probe

## Python 3.9 基线
docs/COMPATIBILITY.md 的关键结论：`tak/core` 必须兼容 Python 3.9。
禁止 match 语句、禁止运行时使用 `X | Y` 联合类型（可用 `from __future__ import annotations` 加字符串注解）、禁止依赖 3.10 及以上标准库新增 API。
dataclasses、enum、typing、importlib.util 均可直接使用；Deterministic 判定只走 tak.core.rules，AI 只做解释、候选、打标与诊断。
版本矩阵：Maya 目标最低 2023（Python 3.9）、3ds Max 目标最低 2023、MotionBuilder 目标最低 2023、Unreal 目标最低 5.8（实测内置 Python 3.11.8）。
开发 / CI 用 WorkBuddy Python 3.13.14 覆盖 3.9 / 3.10 / 3.11 / 3.13 矩阵跑单测。

## Qt 绑定
Maya 2023 / 3ds Max / MotionBuilder 使用 PySide2，Maya 2025+ 为 PySide6，UE 引擎内无 PySide，改用 UMG / Web 面板。
仓库把 PySide2 / PySide6 的双兼容收敛到 `tak/compat/qt.py` 单一入口，插件不得再各写一份版本判断。
无 Qt 的 headless 环境中该模块把绑定置为 None 而不抛异常，因此 mayapy 与 UnrealEditor-Cmd 都能安全导入 tak。
Web 端使用 tak/ui/theme.css，颜色、圆角、间距只改 tak/ui/tokens.json 后重新生成，禁止手改生成的 theme.qss / theme.css。

## UE 5.7 / 5.8 API 探测结论
证据为 validation/ue_probe_result.json（5.8.4）与 validation/ue_probe_5.7.json（5.7.2），均为 headless commandlet 实跑，两个版本 27 项 API 差异为空。
可用 API 包括 EditorDialog、EditorActorSubsystem、EditorLevelLibrary、AssetRegistryHelpers、EditorAssetLibrary、ToolMenus、ToolMenuEntryScript、EditorUtilityWidget、PythonScriptLibrary、IKRigDefinition、IKRetargeter、ControlRig、MeshDeformerCollection、SkeletalMesh、AnimSequence。
`EditorDialogLibrary` **不存在**：旧 acc_unreal_api.py 的 try_editor_dialog_library_save_dialog 在 5.8 会失败，tak/compat/unreal.py 已改为优先 EditorDialog。

## ML Deformer 未暴露
`MLDeformerAsset`、`NeuralMorphModel`、`VertexDeltaModel`、`MLDeformerComponent`、`DeformerGraphAsset` 在 5.7 与 5.8 的 Python 侧均未暴露（差异为空）。
这不等于功能不存在：探测项目只启用了 PythonScriptPlugin 与 EditorScriptingUtilities，需在真实项目启用相关插件后按 VALIDATION.md 的 B2 复验；若仍为 false，ML Deformer 路径需改走 C++ / 蓝图 / Deformer Graph。
UE 5.9 未安装，所有 5.9 结论均为推断，必须经 B1 复验；3ds Max 2023+ 也未安装，pymxs 相关实现只能 mock。
tak 包已在 UE 5.8.4 的 Python 3.11.8 环境成功 import 且 load_spec 校验通过，证明核心层确实零 DCC 依赖、跨版本可移植。
