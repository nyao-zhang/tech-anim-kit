# 场景检查（`qa_inspector`）

> 保存、导出、交付之前，一次性跑完所有检查项：命名规范、场景卫生、绑定规范、IK 与重定向、导出前置、导入后。检查项可勾选，勾选结果会记住。

**适用版本** TAK `0.4.0`　·　**DCC** maya / max / mobu / unreal　·　**权限** `scene.read`　·　**UI** `qt`

## 1. 它解决什么问题

**同一件事被拆成了三个入口。** 旧版本在菜单里平铺了「场景体检」「规范校验」「导出校验」，在面板里是三个独立工具。它们背后是同一个规则引擎、同一份快照、同一张结果表 —— 用户点开任何一个看到的都是「体检报告」，但没人说得清这三者到底差在哪。更糟的是每个入口默认只跑自己那一份规则，不点齐三个就等于没检查全。

**检查项是黑盒。** 旧版本的 9 条场景规则依赖 `metadata.health`，而 Maya 适配器当时并没有采集这个字段，于是点「场景体检」得到的是「通过」—— 一条规则都没跑。判断「这个工具到底检查了什么」的唯一办法是读源码。

**现在只有一件事：勾你关心的项，跑，看结果。** 37 个检查项全部来自规则注册表（内置 19 条 + 各插件提供的 18 条），按六个组摊开：

| 组 | 检查什么 | 典型问题 |
|---|---|---|
| 基础规范 | 关节命名、命名空间、镜像标记、大小写冲突 | 右臂关节写成 `Arm_L` |
| 场景卫生 | 孤立节点、未冻结变换、负缩放、重复 shape、权重和、影响骨超限 | 镜像后 `scaleX = -1` 没冻结 |
| 绑定规范 | 控制器命名与颜色、关节层级深度、必需关节、权重上限 | 控制器没上色，动画师分不清左右 |
| IK / 重定向 | 重定向前后骨骼链一致性、IK 手柄与约束残留 | 重定向后脚部抖动 |
| 导出前置 | 引用残留、材质缺失、骨骼数量、帧范围与单位轴一致性 | 导出后引擎里模型变大 100 倍 |
| 导入后校验 | UE 侧资产命名、骨骼层级、材质槽、LOD | 导入 UE 后骨骼层级被打平 |

## 2. 怎么打开

| 入口 | 操作 |
|---|---|
| Maya 菜单 | `TAK / Tech Anim Kit → 打开面板`，左栏「通用」或「绑定」「动画」下选「场景检查」 |
| Python | 见下 |
| Qt 面板 | 选中工具后点「打开工具界面」→ 左侧检查项树、右侧结果表、底部状态条 |

```python
from tak.core import runner

# 跑默认勾选（首次是全开，之后是上次保存的勾选）
result = runner.run_tool("qa_inspector", dcc="maya")
print(result.report.counts())
for finding in result.report.findings:
    print(finding.severity.value, finding.rule_id, finding.node, finding.message)
```

只跑指定检查项：

```python
result = runner.run_tool(
    "qa_inspector", dcc="maya",
    options={"checks": ["rig_health_guard.negative_scale",
                        "rig_health_guard.unfrozen_transform"]},
)
```

列出全部检查项（含分组、级别、默认开关）：

```python
from tak.core import runner
core = runner.load_tool_modules(runner.find_plugin("qa_inspector"))
for group in core.groups(dcc="maya"):
    print(group["name"], len(group["checks"]))
    for item in group["checks"]:
        print("  ", item["id"], item["severity"], item["default_on"])
```

## 3. 输入

| 输入 | 来源 | 说明 |
|---|---|---|
| 场景快照 | 适配器 `get_skeleton_snapshot()` | 至少要有 `joints` / `meshes` / `skin` / `health` 之一 |
| 项目规范 `spec` | `schema/rig_spec.default.json` 或自备 | 决定命名模式、权重上限、必需关节清单 |
| 勾选项 `options["checks"]` | UI 勾选或调用方传入 | 缺省时读 `~/.tak/config.json` 里保存的勾选 |
| DCC 名 `dcc` | 调用方 | 只跑声明支持该 DCC 的规则 |

**快照里没有数据源时会直接返回一条 INFO，不跑任何规则。** 这是刻意的：空场景跑「缺少必需关节 `calf_L`」是纯噪声，会让人以为模型坏了。判据同时排除降级适配器 —— 它返回的骨架长得很像有数据，但 `metadata.degraded` 写着环境不可用。

## 4. 输出

标准 `Report`，`metadata` 里额外带：

| 字段 | 含义 |
|---|---|
| `checks_selected` | 本次实际跑的检查项 id 列表 |
| `checks_available` | 当前 DCC 下可选检查项总数（37） |
| `providers_failed` | 规则提供者加载失败的原因，空字典表示全部就绪 |

勾选状态存在 `~/.tak/config.json` 的 `check_state.qa_inspector`，跨会话保留。

## 5. 校验项清单

**基础规范（内置，19 条）**

| rule_id | 级别 | 说明 |
|---|---|---|
| `naming.joint_pattern` | error | 关节名不符合 spec 里的命名模式 |
| `naming.forbidden_token` | error | 名称含禁用词（如 `null`、`temp`） |
| `naming.namespace` | error | 命名空间不符合约定 |
| `naming.side_tokens` | warning | 左右标记不规范（`_l` / `_L` / `left` 混用） |
| `naming.case_collision` | warning | 仅大小写不同的重名 |
| `naming.control_namespace` | warning | 控制器命名空间与关节不一致 |
| `skeleton.missing_required` | error | 缺少 spec 要求的关节 |
| `skeleton.parent_integrity` | error | 父子关系断裂或成环 |
| `skeleton.joint_orient` | warning | 关节朝向不一致 |
| `skeleton.max_depth` | warning | 层级深度超过 spec 上限 |
| `skeleton.negative_scale` | error | 骨骼带负缩放 |
| `skeleton.unfrozen_transform` | warning | 骨骼变换未冻结 |
| `skin.max_influences` | error | 单顶点影响骨数量超限 |
| `skin.weight_sum` | error | 权重和偏离 1.0 |
| `skin.unknown_deformer` | warning | 蒙皮上挂着未知 deformer |
| `dcc.version_unsupported` | warning | DCC 版本低于声明的最低版本 |
| `export.frame_range_mismatch` | error | 帧范围与 spec 不一致 |
| `export.unit_mismatch` | error | 单位与目标不一致 |
| `export.up_axis_mismatch` | error | 上轴向与目标不一致 |

**场景卫生（`rig_health_guard`，9 条）**

| rule_id | 级别 | 说明 |
|---|---|---|
| `rig_health_guard.orphan_nodes` | warning | 孤立节点（无父子、无连接） |
| `rig_health_guard.negative_scale` | error | 负缩放 |
| `rig_health_guard.unfrozen_transform` | warning | 未冻结变换 |
| `rig_health_guard.namespace_pollution` | warning | 命名空间污染 |
| `rig_health_guard.duplicate_shape` | warning | 重复 shape |
| `rig_health_guard.skin_influence_over_limit` | error | 影响骨超限 |
| `rig_health_guard.skin_weight_sum` | error | 权重和异常 |
| `rig_health_guard.legacy_reference` | warning | 遗留 reference |
| `rig_health_guard.control_uncolored` | info | 控制器未上色 |

**绑定规范（`spec_enforcer`，7 条）**

`spec_enforcer.naming_pattern`、`spec_enforcer.required_joints`、`spec_enforcer.max_depth`、`spec_enforcer.weight_sum`、`spec_enforcer.influence_limit`、`spec_enforcer.control_color`、`spec_enforcer.control_namespace`。

**导出前置（`export_bridge`，2 条）**

`export_bridge.frame_range_consistency`、`export_bridge.unit_axis_consistency`。

**IK / 重定向、导入后校验** 的规则由 `ik_retarget_guard` / `ue_import_guard` 提供，数量随该插件规则集变化，`checks()` 会实时反映，不在本文里写死。

## 6. 典型用法

**场景一：交付前扫一遍**

```python
from tak.core import runner
result = runner.run_tool("qa_inspector", dcc="maya")
print("blocker", result.report.counts()["blocker"], "error", result.report.counts()["error"])
```

空场景（没有 Maya 宿主）时的真实输出：

```text
passed True  findings 1
qa_inspector.datasource.missing info 当前没有采集到场景数据，本次未执行任何检查。
```

注意这里 `passed` 是 `True`：INFO 不阻断。但消息明确写了「未执行任何检查」—— 不会让人误以为检查过了。

**场景二：只关心权重问题**

```python
result = runner.run_tool(
    "qa_inspector", dcc="maya",
    options={"checks": ["skin.weight_sum", "skin.max_influences",
                        "rig_health_guard.skin_weight_sum"]},
)
```

**场景三：一项都不勾**

```python
result = runner.run_tool("qa_inspector", dcc="maya", options={"checks": []})
print([f.rule_id for f in result.report.findings])
# ['qa_inspector.nothing_selected', ...]
```

跑出来是 `qa_inspector.nothing_selected`（INFO），而不是「通过」。**「什么都没检查」和「检查了没问题」必须是两种不同的结果。**

**场景四：保存勾选**

```python
from tak.core import runner
core = runner.load_tool_modules(runner.find_plugin("qa_inspector"))
core.save_selection(["naming.joint_pattern", "skin.weight_sum"])
```

## 7. 边界与限制

- **只读**。不会修改场景，修复建议只写在 `fix_hint` 里。自动修复走「对话控制台」的修复计划流程。
- **依赖快照质量**。Maya 适配器目前不采集 `metadata.health`，所以场景卫生那 9 条在真实 Maya 里会全部跳过 —— 显示为「未执行」而不是「通过」。这是已知缺口，补齐采集后自动生效。
- **规则集取决于进程状态**。`checks_available` 的数量取决于当前进程加载了哪些插件；同一台机器上先加载别的插件再开面板，数字可能不同。
- **不合并重复规则**。`naming.joint_pattern`（内置）和 `spec_enforcer.naming_pattern` 判定相近，两者都开着会报两次。要只留一套就在勾选里关掉其中一组。
- **依赖 `spec`**。没有 `spec` 时用默认规范，命名与层级类检查可能不适合你的项目。

## 8. 与其它模块的关系

- **规则提供者**：`rig_health_guard` / `spec_enforcer` / `ik_retarget_guard` / `export_bridge` / `ue_import_guard` 的 `manifest.json` 都标了 `"internal": true`，不再作为独立工具出现在面板里，只提供规则。
- **导出 / 导入**：`asset_pipeline` 在导出前会复用 `export_bridge` 的规则做预检，与本工具的「导出前置」组是同一套判定。
- **对话控制台**：用户在对话里问「有没有权重问题」，命中意图后调用的就是本工具的规则集。
- **设置面板**：分类、工具归类、隐藏工具在这里改；检查项的勾选在工具自己的界面里改。
