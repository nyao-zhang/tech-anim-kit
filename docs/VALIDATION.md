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

---

## E. 批次 3 新增复验清单（P4 / P5）

批次 3 的代码全部先在离线 mock 与 CI 中回归（见 `docs/BATCH3_REPORT.md`）。下面这些必须依赖真实宿主、GUI 或真实 AI 端点，仍需你逐项复验并回填。

| # | 环境 | 步骤 | 通过标准 |
|---|---|---|---|
| B11 | **3ds Max 2023+**（本机仅 2019/2021） | 在 Max Python 监听器执行：`import sys; sys.path.insert(0, r'<repo>'); from tak.adapters.max import is_available, create_adapter; print(is_available()); a=create_adapter(); print(len(a.list_nodes()))`；再建一段 BoneGeometry 层级 + Skin 修改器，跑 `a.get_skeleton_snapshot()` 与 `a.export_fbx()` | `is_available()=True`；关节数/层级/单位轴向与场景一致；导出产生 `.fbx` 与 `.snapshot.json`；skin 摘要能读到真实权重、影响骨上限与权重和偏差 |
| B12 | **3ds Max 2023+ 菜单与写入** | 执行 `from tak.adapters.max.init_max import initialize; initialize()`；再跑一次 `spec_enforcer` 的四个 DSL 动作（dry-run → 授权 → 确认） | 菜单出现且可点击；四个写方法真实改到节点；`rollback()` 能把状态日志里的旧值逐项恢复，场景无残留 |
| B13 | **MotionBuilder 2023**（GUI） | 打开 MoBu，执行 B11 同结构的导入（换 `tak.adapters.mobu`），跑 `list_nodes` / `get_skeleton_snapshot` / `export_fbx` / `init_mobu.initialize()` | `pyfbsdk` 可用、`FBModelSkeleton` 识别正确；快照与 Maya 端同一骨架 `compare_snapshots` 无差异；FBX + sidecar 生成；菜单可点击 |
| B14 | **MoBu 写入与抽帧** | 跑四个 DSL 动作并检查 `rollback()`；用 `FBApplication` 或 Playblast 导出若干帧 | 写入生效且可回滚；帧列表能作为 `defect_inspector` 的输入（帧号、图片 data URI/URL、骨骼名） |
| B15 | **四端一致性回归（真机）** | 在 Maya / Max / MoBu / UE 各自的内置 Python 里跑 `python validation/four_dcc_regression.py --dccs <本端>`，并与离线 mock 结果对比 | 每端 `ok=true`、漏报/误报 0、契约错误 0；把结果 JSON 回填到仓库 `validation/` |
| B16 | **Agent Console + 真实 provider** | 设 `TAK_AI_OFFLINE=0`、`TAK_AI_API_KEY`、`TAK_AI_BASE_URL`，在 Web 面板发送「检查这个骨架并给出修复计划」；再尝试让模型输出未注册动作或自由脚本 | 候选必须是参数化 JSON 且被白名单校验；未注册动作/自由脚本一律 `rejected`；`chat` 零场景改写；只有 `confirm(confirm=True, dry_run=False, granted=["node.write"])` 才执行，且失败整体回滚 |
| B17 | **Skeleton Mapper 真实资产** | 用真实 UE IK Rig / Retargeter 与 Mixamo/Biped 骨架调 `map_skeleton()`，导出 mapping asset 并在 UE 侧导入 | 必需链 root/pelvis 覆盖；冲突项保留不自动选择；导出的 JSON 能被 `ik_retarget_guard.load_mapping_asset()` 读回；UE 中链映射与肉眼一致 |
| B18 | **VLM Defect Inspector 真实多模态** | 用 B9 的 Playblast 抽帧，配置 `TAK_AI_OFFLINE=0` 且端点支持 `image_url`，跑 `/api/vision` | 返回候选带帧号/类别/骨骼/置信度；帧号越界、类别不在白名单、骨骼不存在都被确定性过滤；离线时明确标注 `deterministic_fallback`，不伪称模型识别 |
| B19 | **绑定知识库 RAG** | 把项目私有规范（命名、权重、导出）以 `.md` 放进 `knowledge/`，重跑 `/api/knowledge` 与 Agent Console 的检索式提问 | 命中预期文档、引用含 doc_id/title/section；相同 query 两次结果一致；离线不联网、不写缓存 |
| B20 | **Web AI 工作台（GUI）** | 在 Chrome 与 UE 内嵌浏览器打开面板，使用对话/知识库/诊断三个入口，切换深浅主题 | 无 CDN/外链、无硬编码颜色；交互不阻塞主线程；错误以 JSON 提示、不暴露 traceback |

回填格式：`B11 ✅ 3dsMax2025 / Python3.11 / 备注：…`。
所有真机结论都以本机实测证据为准；离线 mock 与 CI 的 PASS **不能替代**上述项目。

## F. 批次 4 新增复验清单（P6）

批次 4 的能力全部先在 `tmp_path` / 离线确定性路径上回归（见 `docs/BATCH4_REPORT.md`）。下面这些必须落在真实宿主或真实目录上，仍需逐项复验。

| # | 环境 | 步骤 | 通过标准 |
|---|---|---|---|
| B21 | **Maya 2023+ 安装 + 菜单** | `python -m tak.install --apply --dcc maya`，重启 Maya；再执行 `from tak.adapters.maya.menu import register_menu; print(register_menu())` | 菜单栏出现 `TAK / Tech Anim Kit`；六个菜单项可点；重复执行 `register_menu()` **不产生重复菜单**；`unregister_menu()` 后菜单消失 |
| B22 | **Max / MoBu 安装 + 菜单** | 同上换 `--dcc max` / `--dcc mobu`；Max 看 `scripts/startup`，MoBu 看 `config/PythonStartup` | 菜单出现；Max 的块是 `--` 注释风格且 `python.Execute` 真被执行；MoBu 启动无报错 |
| B23 | **UE 安装** | `python -m tak.install --apply --dcc unreal --root unreal=<项目>/Content/Python`，重启编辑器 | `init_unreal.py` 里出现 TAK 块；UE 输出日志有 `[TAK]` 行；删掉块后重启无残留 |
| B24 | **卸载可逆** | 安装前 `sha256sum` 目标文件，`--apply` 后再 `--uninstall --apply`，再算一次 | 哈希与安装前**完全一致**（含末尾换行）；备份文件 `<file>.tak-backup-1` 存在且内容等于安装后版本 |
| B25 | **动作检索真实资产** | 用真实动作库（FBX + 同名 `.motion.json` sidecar）跑 `MotionLibrary.from_index()` / `scan_directory()`，检索「跑步 循环」「受击 短促」 | 命中符合肉眼判断；`features["estimated"]` 在无 sidecar 时为 1.0；同一 query 两次结果完全一致；`?tab=motion` 深链能直接打开页签 |
| B26 | **数据集工厂真实骨架** | 用**生产骨架**（非 fixture）跑 `build_plan` → `build_manifest` → `validate_manifest`，把 `manifest.json` 交给动画组评审 | 采样覆盖每个形变关节；极值角度不穿模（人工判定）；门槛无 ERROR；同一骨架两次生成 `version` 相同 |

回填格式：`B21 ✅ Maya2023 / Win11 / 备注：…`。
**注意**：B21–B24 会真的写用户目录。执行前先 `--plan` 看一眼，确认 `[create]` / `[append]` 的路径是你预期的那个；安装器只改自己的标记块，但路径选错仍然会写错地方。

### F.1 已在真机实际执行的部分（2026-11-13，Windows 11 / Python 3.13）

下表是**已经跑过并留下输出**的步骤。它覆盖 B21–B24 里「不打开 DCC 也能判定」的那一半，
**不覆盖**「重启 Maya 后菜单栏是否真的出现」这类只有 GUI 能回答的部分——那部分仍待你复验。

| 步骤 | 实际执行 | 结果 |
|---|---|---|
| 探测 | `python -m tak.install --list` | 识别到 **Maya 2026**（`Documents/maya/2026`）与 **MoBu 2023**（`Documents/MB/2023`）；3ds Max 报未发现，因为本机只有 2019/2021，低于 `min_version="2023"`——**这是正确行为，不是缺陷** |
| 首次安装 | `--apply --dcc maya,mobu,unreal --root unreal=<项目>/Content/Python` | `create=3`。三个入口文件此前都不存在，均为新建：`maya/2026/scripts/userSetup.py`、`MB/2023/config/PythonStartup/tak_startup.py`、`面部AR示例/Content/Python/init_unreal.py` |
| 幂等 | 紧接着再跑 `--plan` | 三个目标全部 `[skip]`，`skip=3`，未产生第二个标记块 |
| 卸载可逆 | `--uninstall --apply` 后重新 `--apply` | 块内内容删除后文件回到「只有块」前的状态；重新安装得到同内容（`replace-block` 而非追加） |
| 语法 | 对三份落盘文件跑 `ast.parse(feature_version=(3, 9))` | 全部通过——块在 Maya 2023 的 Python 3.9 里能编译 |
| 执行（无宿主） | 在普通 Python 里 `exec()` 三份启动文件 | 无异常；`tak` 与 `tak.adapters.maya.menu` 均加载成功；Maya 块输出 `[TAK] Maya 菜单未注册：maya.cmds 不可用（无 Maya 宿主）`——**无宿主时这是正确降级** |
| 版本刷新 | 0.3.1 后重新 `--apply` | `replace-block=3` → 再 `--plan` 全部 `skip`；块头变为 `# >>> TAK BEGIN maya v0.3.1` |

**仍需你做的**（GUI 才能回答，agent 无法替代）：

1. **B21 核心项**：启动 Maya 2026，看菜单栏是否出现 `TAK / Tech Anim Kit`；六个菜单项是否可点；
   在脚本编辑器里连跑两次 `from tak.adapters.maya.menu import register_menu; register_menu()`
   —— 应当**只有一份菜单**（`cmds.menu` 本身不查重，代码里是先 `deleteUI` 再建）。
2. **B22**：MoBu 2023 启动是否无报错、菜单是否出现。Max 需要 2023+ 才能测。
3. **B23**：UE 编辑器输出日志里是否有 `[TAK]` 行、`TAK` 菜单是否出现在工具栏。
4. **B24 完整项**：卸载后**哈希**是否与安装前逐字节一致（我这里验的是「块被删除、文件可重建」，
   没做安装前的 `sha256sum` 留底——因为三个文件在安装前都不存在，无法先算哈希）。
5. **B25 / B26**：真实动作库与生产骨架，见上表。
