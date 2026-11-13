# 批次 3 交付报告（P4 + P5）

> 对应任务：P4.1–P4.4、P5.1–P5.4 + 集成与文档。分两波执行，波 1 补四端适配器与写操作，波 2 建 AI 内核与插件，最后由主 agent 做四端一致性收敛。
>
> 版本 `0.1.0` → `0.2.0`；测试 **628 → 872**（新增 244）；插件 **5 → 9**。

## 1. 交付概览

| 波次 | 任务 | 模型 | 产出 |
|---|---|---|---|
| 1 | P4.1 3ds Max 适配器 | DeepSeek-V4.1-Flash | `tak/adapters/max/`（adapter / menu / init_max）、`tests/test_max_adapter.py` 18 例 |
| 1 | P4.2 MotionBuilder 适配器 | DeepSeek-V4.1-Flash | `tak/adapters/mobu/`（adapter / menu / init_mobu）、`tests/test_mobu_adapter.py` 18 例 |
| 1 | P4.4 Maya 真实写操作 | DeepSeek-V4.1-Flash | `MayaAdapter` 四个写方法 + `NODE_WRITE`/`WEIGHT_WRITE` 能力、`tests/test_maya_write_ops.py` 13 例 |
| 2 | P5.1 Agent Console | Kimi-K3.1-Pro | `tak/core/agent/`（intents / session）、`tools/agent_console/`、52 例 |
| 2 | P5.2 Skeleton Mapper | DeepSeek-V4.1-Flash | `tak/core/mapping/`、`tools/skeleton_mapper/`、35 例 |
| 2 | P5.3 VLM Defect Inspector | MiniMax-M3.1-Flash | `tak/core/vision/`、`tools/defect_inspector/`、43 例 |
| 2 | P5.4 绑定知识库 RAG | DeepSeek-V4.1-Flash | `tak/core/knowledge/`、`knowledge/*.md` 5 篇、`tools/rig_kb/`、35 例 |
| — | P4.3 四端一致性回归 | 主 agent 执行 | `validation/four_dcc_regression.py`、`tests/test_batch3_conformance.py` 17 例 |
| — | Web AI 工作台接线 | 主 agent 执行 | `server.py` 四端点 + `panel.html`/`app.js`/`app.css`、`tests/test_web_ai_endpoints.py` 13 例 |
| — | 文档与 CI | 主 agent 执行 | `docs/VALIDATION.md`（B11–B20）、`docs/WEB_PANEL.md`（W31–W34）、`docs/PLUGIN_GUIDE.md` 第 10 节、CI 增加四端回归步骤 |

由主 agent 亲自落地的共享基线：`tak/adapters/snapshot_utils.py`（快照工具，纯 Python 3.9）、`tak/adapters/conformance.py`（契约校验 / diff / 能力矩阵）、`tak/adapters/__init__.py` 导出扩容。

代码规模：`tak/` 69 文件 14586 行、`tools/` 44 文件 8082 行、`tests/` 34 文件 10023 行、`validation/` 7 文件 1241 行，共 **155 个 `.py`（不含 legacy）全部通过 `ast.parse(feature_version=(3,9))`**。

## 2. 架构：两条正交的轴

批次 2 立的是「一条运行契约串四端」，批次 3 加的是与之正交的第二条轴 —— **能力轴**，并把 AI 的边界写进代码。

```
        ┌──────────── 运行轴（批次 2）────────────┐
Qt / Web / 菜单 / commandlet / CI
              │
     tak.core.runner.run_tool(...)
              │
   registry.discover ── adapter_for(dcc)
              │                 │
     插件 core.run_report → rules.engine → Report

        ┌──────────── 能力轴（批次 3）────────────┐
   tak/adapters/snapshot_utils.py  ← 一份快照结构
   tak/adapters/conformance.py     ← 校验 / diff / 能力矩阵
              │
   validate_conformance(snapshot)   compare_snapshots(before, after)
   capability_matrix({dcc: adapter})  check_adapter(adapter)

        ┌──────────── AI 轴（批次 3）────────────┐
   tak/core/agent   意图分类（确定性关键词路由）→ 规则判定 → 解释 → 候选
   tak/core/mapping 确定性词典匹配 → 可选的 AI 候选 → 确定性校验
   tak/core/vision  抽帧 → AI 候选 → 确定性过滤（帧号/类别/骨骼白名单）
   tak/core/knowledge  BM25 检索（零模型依赖），给上面三个注入上下文
```

**四条硬规矩**（写进 `docs/PLUGIN_GUIDE.md` 第 10 节，并有回归用例守着）：

1. AI 插件**不注册任何全局 `@rule`**。`agent_console` / `skeleton_mapper` / `defect_inspector` / `rig_kb` 的 `manifest.json` 里没有 `rules` 字段，因此不会污染 40 个 golden fixtures 的判定基线。
2. `chat` 绝不写场景。`AgentSession.chat()` 只返回解释与候选，写入唯一入口是 `confirm(plan_id, confirm=True, dry_run=False, granted=["node.write"])`。
3. 写入只走受限 DSL，且必须过 `ACTION_REGISTRY` 白名单；LLM 的输出是参数化 JSON，**永远不是 DCC 脚本**。
4. 服务端同样硬门。`/api/agent` 只有在 `confirm is True and dry_run is False and "node.write" in granted` 三项同时成立时才真正执行，否则一律退化为 dry-run —— 前端被绕过也写不进场景。

## 3. 四端能力矩阵（`capability_matrix` 实测输出）

| 能力 | Maya 2023+ | 3ds Max 2023+ | MoBu 2023+ | UE 5.8 |
|---|---|---|---|---|
| `scene.query` | ✅ | ✅ | ✅ | ✅ |
| `skin.query` | ✅ | ✅ | ✅ | ✅ |
| `skeleton.snapshot` | ✅ | ✅ | ✅ | ✅ |
| `skin.write` | ✅ | ✅ | ✅ | — |
| `node.write` | ✅ | ✅ | ✅ | — |
| `export.fbx` | ✅ | ✅ | ✅ | — |
| `import.validate` | — | — | — | ✅ |
| `ui.host` | ✅ | ✅ | ✅ | — |
| `undo.scope` | ✅ | ✅ | ✅ | — |
| `export.usd` / `capture.playblast` / `mldeformer.train` | — | — | — | — |

`missing_required: []`、`conformant: true` 四端全绿。**没有一项能力靠「反正也是空实现」蒙过去**：缺失能力显式登记为 `false`，上层工具据此走降级分支而不是静默失败。

几个刻意的取舍：

- **MoBu 不声明 `capture.playblast`**：`pyfbsdk` 没有稳定的 Playblast 接口，硬写一个 `FBApplication` 变通方案会在真机上随机失败，因此 `defect_inspector` 在 MoBu 上明确走「用户自备帧」路径。
- **Max / MoBu 的 undo 走 `undo_strategy="state_log"`**：`pymxs` / `pyfbsdk` 的原生 Python undo 不可靠（跨 `Hold`/`Fetch` 边界会丢状态），所以改用状态日志式回滚 —— 写之前记旧值，`rollback()` 逐项恢复。这一点写在 `describe()["undo_strategy"]` 里，UI 会显式提示。
- **UE 不声明任何写入能力**：批次 1 定下的原则是「UE 侧只读校验、资产改动回 DCC 做」，这条在这次没有松动。

## 4. 四端一致性回归结果

```
python validation/four_dcc_regression.py
[TAK] 四端一致性回归：PASS
[TAK] fixtures=40 dccs=maya,max,mobu,unreal
  maya   判定漏报=0 判定误报=0 适配器漏报=0  契约错误=0 基线差异=0 跨端差异=0 降级=0 OK
  max    判定漏报=0 判定误报=0 适配器漏报=0  契约错误=0 基线差异=0 跨端差异=0 降级=0 OK
  mobu   判定漏报=0 判定误报=0 适配器漏报=0  契约错误=0 基线差异=0 跨端差异=0 降级=0 OK
  unreal 判定漏报=0 判定误报=0 适配器漏报=17 契约错误=0 基线差异=0 跨端差异=0 降级=0 OK
```

结果 JSON（无时间戳，可复现）：`validation/four_dcc_regression_result.json`。CI 每轮跑一次并写到 `validation/four_dcc_ci.json`。

### 为什么 UE 的「适配器漏报 17」不算失败

第一轮跑出来是 FAIL：maya 漏报 13、max 4、mobu 13、unreal 17。这里其实是**两类完全不同的东西被混在一个数字里**：

- **判定漏报**（真的 bug）：规则引擎应该报出来但没报。这个必须为 0。
- **适配器漏报**（能力边界）：真实适配器读不到的字段。UE 不暴露 bind 变换、scale、rotate、namespace、depth —— 它根本没有这些概念。把它算成 bug，等于要求 UE 提供它不存在的 API。

所以回归改成三层口径：

| 口径 | 来源 | 含义 |
|---|---|---|
| `judgement_detected` | `MockAdapter`（注入场景的完整真值） | 判定基准：四端必须完全一致 |
| `adapter_detected` | 真实适配器 `get_skeleton_snapshot()` | 观测值：允许因能力边界少于基准 |
| `mock_snapshot` | `install_mock` 注入的场景 | 用来区分「规则错了」还是「适配器读不到」 |

UE 的 `adapter_false_negatives=17` 用 `capability_note` 显式登记，不判失败；`_comparison_ignore(dcc, snapshot)` 把这个边界写成代码（忽略 `unit` / `up_axis` / `frame_range` / `skin.*` 与每个关节的 `matrix` / `scale` / `rotate` / `namespace` / `depth`），而不是写在注释里靠人记。

## 5. 过程中发现并修掉的真实问题

这一批一共踩到 4 个真正的坑，都是「测试绿了但结果是错的」那一类，值得记下来。

### 5.1 `compare_snapshots` 的 `ignore` 参数完全不生效

`ignore` 只过滤了顶层字段，关节字段仍然逐个比对 —— 于是「忽略 UE 的 `joints.*.matrix`」这条规则静默失效，回归里 UE 的 17 项差异根本没被忽略掉，只是恰好被其他分支掩盖了。

修法：在 `joints.*` 的全量比对分支之前插入前缀精确匹配：

```python
if "joints.%s.%s" % (name, key) in ignored:
    continue
```

同时在 `check_adapter` 里把 `str(getattr(adapter, "dcc"))` 产生 `DCC.MAYA` 的问题改成 `str(getattr(dcc_value, "value", dcc_value)).lower()` —— 枚举的 `str()` 在 3.9/3.11 下行为不同，这种写法在 3.9 上会得到 `"DCC.MAYA"` 而不是 `"maya"`。

### 5.2 `MockAdapter` 把注入的 `depth` 重算回「合法值」

回归要把注入场景当作真值，但 `MockAdapter._records` 用 `depth_map(parents)` 从父子链重新算了一遍 `depth`，覆盖了 `MockScene` 里声明的值。结果：`skeleton.max_depth` 的漏报被「修好」了 —— 因为 mock 自己把不合法的深度算成了合法的。

这是最危险的一类 bug：**测试替身悄悄修复了被测对象的错误**。修法：删掉 `depth_map` 重算，改读场景声明值 `int(joint.depth)`。

清理时又踩了一次：脚本删掉了 `depths = depth_map(parents)` 但 `_read_maya/_read_max/_read_mobu` 还引用 `depths.get(...)`，`NameError` 直接把整个回归打成 `KeyError: 'judgement_detected'`（异常时提前 return，缺键）。用 `"depth": int(scene.joints[name].depth)` 替换修好，`tests/test_dcc_mocks.py` 49 例恢复全绿。

### 5.3 三个 DCC 适配器不读 mock 桥接的元数据

`namespace` / `depth` / `unit` / `up_axis` / `frame_range` 在真实 Maya 里来自 `cmds.namespaceInfo`、`cmds.currentUnit` 等调用，但在 mock 下这些值挂在 `cmds.tak_scene` 桥接对象上。适配器的 `_capture` 没有走桥接，于是 mock 环境下这 5 个字段全丢。

修法：`maya` / `max` / `mobu` 的 `_capture` 优先从 `tak_scene` 桥接补齐（新增辅助 `_bridge_joint(scene, name)`），**真实宿主行为完全不变**（桥接不存在时走原路径）。

### 5.4 Maya 的 DSL 动作此前是 no-op

`spec_enforcer` 的 7 条规则和 4 个 DSL 动作在批次 2 就有了，但 `MayaAdapter` 上并没有对应的写方法 —— `getattr(adapter, "rename_node")` 拿到 `None`，执行器把「动作不存在」当成成功静默跳过。也就是说，**dry-run 预览、权限、二次确认、undo 全都跑通了一遍，什么都没改，报告还说修好了**。

修法：补上四个真实写方法，mock 下写 `tak_scene.metadata["write_log"]` 并标 `mock=True`：

| 方法 | 真实 API | 说明 |
|---|---|---|
| `rename_node(node, name)` | `cmds.rename` | 命名规范修复 |
| `set_control_color(node, color)` | `cmds.setAttr`（overrideEnabled + overrideColor） | control 上色 |
| `normalize_weights(max_influences=None, tolerance=None)` | `cmds.skinPercent -tv` | 权重归一化 + 影响骨上限 |
| `set_namespace(node, namespace)` | `cmds.namespace` | 命名空间迁移 |

统一返回 `_write_result(action, status, **extra)`，`13` 个新用例盯着「真的改了 / 回滚干净了 / 失败时整体回滚」这三件事。

## 6. 已知限制（如实记录）

| 限制 | 说明 |
|---|---|
| **3ds Max 2023+ / MoBu 2023+ 未真机验证** | 本机只有 Max 2019/2021、MoBu 2023（GUI）。Max 适配器按 `pymxs` 文档 + mock 落地，`tests/test_max_adapter.py` 18 例只覆盖 mock 路径。**Max 2023+ 的 API 差异（尤其 `pymxs.runtime` 的属性名）必须真机复验**，见 B11/B12。 |
| MoBu 无 Playblast 能力 | `pyfbsdk` 无稳定接口，`defect_inspector` 在 MoBu 上必须由用户自备帧。 |
| Max / MoBu undo 非原生 | 状态日志式回滚只覆盖本工具自己的写入；用户在中间手动操作会让日志失效（`rollback()` 会报 `stale` 而非静默改错）。 |
| AI 全链路默认离线 | `MockProvider` / `OfflineProvider` 保证 CI 可跑、结果可复现，但**真实 provider 的候选质量、拒答率、幻觉率一律未验证**（B16/B18）。 |
| VLM 只在离线降级路径验证 | `defect_inspector` 的 8 类缺陷分类、帧号/骨骼白名单过滤是确定性的；「模型看得准不准」没有任何实测数据。 |
| RAG 只用 5 篇自带文档 | BM25 参数是按这 5 篇调的，项目私有规范（长文档、表格、代码块）的召回率未知（B19）。 |
| UE 侧无任何写入能力 | 设计如此，但意味着 UE 内的重定向修正仍要回 DCC 做。 |
| `legacy/sources/` 未纳入工具集 | 只读副本，迁移计划见 `docs/LEGACY_MAPPING.md`。 |

## 7. 需要用户复验的部分

代码侧能做的到此为止。下面 14 项必须真机 / GUI / 真实端点，逐项标准已写进文档，回填格式统一为 `B11 ✅ 3dsMax2025 / Python3.11 / 备注：…`：

**`docs/VALIDATION.md` E 节（B11–B20）**

| # | 项目 |
|---|---|
| B11 | 3ds Max 2023+ 适配器：`is_available` / `list_nodes` / `get_skeleton_snapshot` / `export_fbx` |
| B12 | 3ds Max 菜单与四个 DSL 写入 + `rollback()` 无残留 |
| B13 | MoBu 2023 适配器 + `init_mobu.initialize()` + 与 Maya 同骨架 `compare_snapshots` 零差异 |
| B14 | MoBu 四个 DSL 写入 + 抽帧作为 `defect_inspector` 输入 |
| B15 | 四端各自真机跑 `four_dcc_regression.py --dccs <本端>` 并回填 JSON |
| B16 | Agent Console + 真实 provider：未注册动作/自由脚本必须 `rejected` |
| B17 | Skeleton Mapper 真实 UE IK Rig / Retargeter 资产往返 |
| B18 | VLM 真实多模态：越界帧号 / 非法类别 / 不存在骨骼必须被过滤 |
| B19 | RAG 灌入项目私有规范后的召回与引用正确性 |
| B20 | Web AI 工作台在 Chrome 与 UE 内嵌浏览器的真实交互 |

**`docs/WEB_PANEL.md`（W31–W34）**：AI 工作台四个页签的深浅主题、错误提示、会话保持、确认执行按钮的真实交互。headless 回归只覆盖 `handle_api` 的纯函数路径。

## 8. 下一步（P6）

| 任务 | 内容 |
|---|---|
| P6.1 | 动作语义检索：把 `rig_kb` 的 BM25 扩展到动作库（clip / pose）语义检索 |
| P6.2 | ML Deformer 数据工厂：训练数据生成、版本化、质量门槛 |
| P6.3 | 安装器：一条命令把 9 个插件装进四端（含 `userSetup.py` / `startup` / `.ms` 菜单） |
| P6.4 | 全量回归与 Release：四端真机证据回填、`v0.3.0` |

P6 有两条前置：B11–B15 的真机结论回来之后，Max / MoBu 适配器才敢标「已验证」；B16–B19 的真实 provider 数据回来之后，才能决定 AI 插件的默认开关。
