# 变更日志

本文件记录所有值得使用者注意的变更。格式参考 [Keep a Changelog](https://keepachangelog.com/)，
版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

三条约定：

1. **`Fixed` 要写「原来错在哪」**，不是「修了 bug」。
2. **`Known limitations` 必须如实写**，没验证就写没验证。
3. 每条尽量带文件路径，方便回溯。

## [Unreleased]

（无）

## [0.3.1] - 2026-11-13

补丁版：修掉 0.3.0 安装器的一处静默失败，并为此补上回归护栏。无接口变更，
无行为变更——只让失败能被看见。测试 **1117 → 1121**（+4）。

### Fixed

- **「装了、导入成功了、菜单却没出现」时安装块一声不吭**（`tak/install/targets.py`）。
  `tak/adapters/maya/menu.py` 与 `mobu/menu.py` 按约定**不抛异常**、只返回
  `{"ok": False, "reason": "..."}`；而安装器生成的启动块是 `_tak_register_menu()`
  裸调用、把返回值丢掉，外层的 `except` 也只在真抛异常时才打印。两者叠加的结果是
  绝大多数失败路径都走不到任何输出。
  这不是推测：真机安装（Maya 2026 / MoBu 2023 / UE 5.8）后执行 `userSetup.py`，
  控制台**一行都没有**。现在块内检查 `.get("ok")` 并打印 `reason`，Maya / MoBu 两处同改。
  3ds Max 与 UE 本来就没这个问题：`init_max.initialize()` 会打 `menu.ok=%s` 与降级原因，
  `init_unreal.main()` 把每条降级都 `print` 出来并把结果放进返回字典。
- **`tak/install/writer.py` docstring 里的标记块示例写死 `v0.3.0`**：版本一动它就变成过期文档。
  改成 `v<版本>` 占位。`tests/test_release_meta.py` 只拦**带引号**的版本字面量，
  这种写在 docstring 里没引号的示例它扫不到，所以手工修——这也说明那道护栏有已知盲区。

### Added

- `tests/test_installer.py` 新增 4 个用例：Maya / MoBu 块必须检查 `register_menu()` 的 `ok`
  并打印失败原因；Maya / MoBu / UE 三个块的模板必须能过 `ast.parse(feature_version=(3, 9))`
  （块是直接写进宿主启动文件的，语法错等于宿主启动错）；Max 块必须保留 `initialize()` 调用。

### Known limitations

- **备份会一直累积**：每次 `replace-block` 都在入口文件旁留一份 `<file>.tak-backup-<N>`，
  `N` 只增不覆盖；`--uninstall` 也会再留一份（这是刻意的——`backup-1` 往往就是装上 TAK 之前
  的原始文件，删掉就没法回到原点）。代价是频繁重装会在宿主启动目录堆出若干 1 KB 小文件，
  目前**没有清理入口**，需要手工删。真机现状：`Documents/maya/2026/scripts/` 下 2 份，
  `MB/2023/config/PythonStartup/` 下 2 份，UE 项目 `Content/Python/` 下 1 份。

## [0.3.0] - 2026-11-13

对应批次 4（P6）：动作语义检索、ML Deformer 数据工厂、一条命令安装器、发布工程化。
测试 **872 → 1117**（+245），插件 **9 → 11**。

### Added

- **Motion Library 动作语义检索**（P6.1）
  - `tak/core/motion/`：`MotionClip` 模型、`extract_features()` 数值特征提取（根速度 / 位移 / 循环误差 / 对称性 / 姿态方差，公式写在 docstring）、`MotionLibrary.search()`（BM25 文本分 + 类型加权 + 硬过滤）、`similar_to()`、`context_for()`、`to_index()/from_index()`。
  - `tools/motion_library/`：纯只读插件，12 个内置示例 clip 覆盖 idle/walk/run/jump/attack/hit/turn/crouch/climb/dance/death。
  - `scan_directory()` 支持 `.fbx` + 同名 sidecar；**不解析 FBX 二进制**，无 sidecar 时走文件名启发式并标 `estimated`。
  - Web 面板第 5 个页签「动作检索」+ `POST /api/motion`；支持 `?tab=` 深链。
- **ML Deformer 数据工厂**（P6.2）
  - `tak/core/mldeformer/`：`build_plan()` 确定性姿态采样（extreme/mid/grid/rest/mirror/random 六类）、`build_manifest()` 内容寻址清单（`version` / `pose_digest` 均为 sha256 前 16 位）、`validate_manifest()` 8 条质量门槛。
  - `tools/mldeformer_factory/`：`plan` / `manifest` / `validate` / `markdown_summary`，Report 按 issue 逐条出 Finding。
- **一条命令安装器**（P6.3）
  - `tak/install/`：四端探测（`TAK_MAYA_ROOT` 等环境变量优先）、标记块幂等写入、备份、精确卸载、CLI（`--list` / `--plan` / `--apply` / `--uninstall` / `--json`）。
  - 默认 dry-run；`--apply` 才写盘；块外内容逐字节不动。
- **Maya 菜单入口**（补 P2 的漏项）
  - `tak/adapters/maya/menu.py`：`register_menu` / `unregister_menu` / `dispatch` / `menu_script`。此前 Max / MoBu / UE 都有 `menu.py`，唯独 Maya 没有，`tak.install` 的 Maya 启动片段导入它必然失败。
- **进程内 Web 面板** `server.serve_in_thread()` / `stop_panel()`：DCC 菜单项可在不阻塞宿主的前提下拉起面板，重复调用复用同一实例（不再撞端口、不泄漏线程）。
- `docs/RELEASE.md`：七步发布流程（版本号 / 变更日志 / 证据回填 / 全量回归 / 打包 / tag 推送 / 发布后），含推送失败分类与三种回滚路径。
- `tests/test_release_meta.py`：版本、插件 manifest、文档链接的一致性护栏。

### Changed

- `tak/version.py` 升到 `0.3.0`，并明确为**全仓库唯一版本出口**。
- `tak/ui/web/app.css` 新增 `.tak-field-inline`（复选框行内布局）。
- CI 增加安装器 dry-run 探测与 P6 模块冒烟步骤。

### Fixed

- **`tak/version.py` 与 README 版本号不一致**：批次 3 只把 README 改到 `0.2.0`，代码里仍停在 `0.1.0`，`/api/health` 也一直在报旧版本。现在版本只在 `tak/version.py` 定义，`tests/test_release_meta.py` 负责拦。
- **Maya 安装后没有菜单且无任何提示**：安装块的 `except Exception:` 是静默的，用户只能看到「什么都没发生」。除了补 `menu.py`，还把该 `except` 改为打印具体原因（Maya / MoBu 两处）。
- **`build_plan()` 传错层级时静默返回 0 个 pose**：仓库里所有 fixture 都是 `{"id":…, "snapshot": {…}}` 的外层包装，直接把它传给 `build_plan` 会得到空计划。现在会返回带 `warnings` 的空计划而不是让人误以为「骨架没问题」——但**仍不自动解包**，因为 `ToolContext.snapshot` 本身就是内层快照，自动解包会让两条路径行为不一致。
- **安装器在干净机器上永远探测不到 Maya**：原实现只检查 `<Documents>/maya/<version>/scripts` 是否存在，而该目录是 Maya 首次需要时才创建的 —— 本机装了 2023/2024/2026，三个版本目录都在，`scripts` 一个都没有，于是 `--list` 报「未检测到 Maya」。现在探测证据用版本目录本身（`probe` 字段），写入根仍指向 `scripts`，由 writer 在写入前 `makedirs`。附 5 个回归用例（含「版本目录存在但 scripts 不存在时 apply 必须建目录」）。

### Known limitations

- **3ds Max 2023+ / MotionBuilder 2023+ 仍未真机验证**（本机只有 Max 2019/2021、MoBu 2023 GUI）。适配器与菜单只跑过 mock + AST 校验，见 `docs/VALIDATION.md` B11–B14。
- **ML Deformer 训练入口不存在**：UE 5.7/5.8 的 Python 侧均未暴露 `MLDeformerAsset` 等类型（B2 / W3 / W30）。`tak/core/mldeformer` 只产出数据集与门槛报告，能力位 `mldeformer.train` 恒为 `false`，**不得**据此宣称支持训练。
- **动作检索不解析 FBX 二进制**：特征依赖 sidecar 或文件名启发式，`features["estimated"]=1.0` 会标记估算值。真实 clip 的数值特征需要 DCC 侧导出 sidecar。
- **镜像姿态在对称骨架下会被去重**：左右限值相同时，镜像角度与对侧极值重合，按 `(joint, axis, angle)` 去重后 `mirror` 计数为 0（信息保留在 `tags` 里）。非对称 limits 才会产出真实镜像行。
- **安装器只写文件，不启动宿主**：装完需要用户重启 DCC；`--apply` 的实际效果（菜单是否出现）必须人工确认（W35–W37）。
- **真实 AI provider 仍未验证**：B16–B19 全部待复验。

## [0.2.0] - 2026-11-13

对应批次 3（P4 + P5）：四端适配器与能力契约、AI 工作台。测试 **628 → 872**（+244），插件 **5 → 9**。

### Added

- 3ds Max 适配器（`pymxs`，`undo_strategy="state_log"`）、MotionBuilder 适配器（`pyfbsdk`，不声明 `capture.playblast`）。
- `tak/adapters/snapshot_utils.py`（一份快照结构）、`tak/adapters/conformance.py`（`validate_snapshot` / `compare_snapshots` / `capability_matrix` / `check_adapter`，`CONTRACT_VERSION="1.0"`）。
- `validation/four_dcc_regression.py`：40 fixtures × 4 端，三层口径（判定基准 / 适配器观测 / mock 场景）。
- `tak/core/agent`、`tak/core/mapping`、`tak/core/vision`、`tak/core/knowledge` 四个 AI 内核；`agent_console` / `skeleton_mapper` / `defect_inspector` / `rig_kb` 四个插件。
- Web AI 工作台四页签与 `/api/agent` `/api/knowledge` `/api/vision` `/api/mapping`。
- Maya 适配器四个真实写方法：`rename_node` / `set_control_color` / `normalize_weights` / `set_namespace`。

### Fixed

- `compare_snapshots(ignore=...)` 只过滤顶层字段，关节字段仍参与比对，导致 UE 的能力边界被误判为差异。
- `MockAdapter` 用父子链重算 `depth`，覆盖了注入场景的声明值 —— 测试替身悄悄修好了被测对象的错误，`skeleton.max_depth` 漏报被抹平。
- Maya / Max / MoBu 的 `_capture` 不读 mock 桥接，`namespace` / `depth` / `unit` / `up_axis` / `frame_range` 在 mock 下全丢。
- **Maya 的 DSL 动作此前是 no-op**：`getattr(adapter, "rename_node")` 返回 `None`，执行器把「动作不存在」当成功静默跳过，dry-run / 权限 / undo 全跑通但场景一个字节没改，报告还说修好了。

## [0.1.0] - 2026-11-12

对应批次 1 + 批次 2（P0–P3）：规范 schema、规则引擎、受限 DSL、Maya/UE 适配器、5 个插件、Qt 与 Web 面板。测试 **249 → 628**。

### Added

- `schema/rig_spec.schema.json` 规范定义 + 40 个 golden fixtures（20 好 / 20 坏）。
- 19 条内置规则 + `@rule` 规则引擎 + 统一运行契约 `tak.core.runner.run_tool`。
- 受限 DSL：函数白名单 → dry-run diff → 权限 → 人工确认 → undo chunk → 失败整体回滚。
- 四套可注入可卸载的 DCC mock（maya / pymxs / pyfbsdk / unreal）。
- OpenAI 兼容 AI provider + 指数退避重试 + 预算统计 + 确定性离线 mock。
- Maya 2023+ 适配器、UE 5.8 适配器与编辑器入口、Qt 组件库、Web 面板。
- `rig_health_guard` / `spec_enforcer` / `export_bridge` / `ue_import_guard` / `ik_retarget_guard` 五个插件，共 33 条规则。

### Fixed

- `ToolResult.to_dict()` 覆盖而非合并 `report.metadata`，插件的 AI 解释与映射摘要在 Web API 上丢失。
- CI 覆盖率门槛范围错误（整体覆盖率被当成核心层门槛）。
- 回归脚本中 `rollback()` 先于蒙皮采样执行，把刚建的 skinCluster 撤销了。
- `runner.clear_cache()` 被测试 fixture 调用后 `rules.py` 二次 exec，规则重复注册污染 19 个测试。

---

[Unreleased]: #unreleased
[0.3.1]: #031---2026-11-13
[0.3.0]: #030---2026-11-13
[0.2.0]: #020---2026-11-13
[0.1.0]: #010---2026-11-12
