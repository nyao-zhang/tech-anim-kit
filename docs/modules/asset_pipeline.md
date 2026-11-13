# 导出 / 导入（`asset_pipeline`）

> 选导什么、选导到哪，导到文件或跨 DCC 共享缓存位；在另一个 DCC 里用同一份 profile 打开，做导入校验。

**适用版本** TAK `0.4.0`　·　**DCC** maya / max / mobu / unreal　·　**权限** `scene.read` / `export.fbx` / `import.validate`　·　**UI** `qt`

## 1. 它解决什么问题

**导出和导入是两件事，却被当成两件互不相干的事。** 旧版本里 `export_bridge` 只管「导出前后对不对」，`ue_import_guard` 只管「导入 UE 后对不对」。用户要做一次完整的资产交付，得在两个工具之间手工对齐「上次导的是什么、导到哪、勾了哪些选项」。对不上的时候，问题看起来像导入侧的 bug，实际是导出侧少勾了一项。

**每个 DCC 的导出面板各写一套。** Maya 的 FBX 导出选项和 Max 的不一样，和 MotionBuilder 的又不一样。同一个人在不同 DCC 之间来回导，每次都要重新想「这次该勾哪个」。

**现在是一条对称的管线：**

- 内容用枚举，不用记选项：完整绑定 / 仅骨骼 / 仅蒙皮网格 / 动画缓存 / 仅控制器，也可以逐项自定义；
- 目标决定格式与默认选项：选 Unreal 就只给 FBX / USD，并自动关掉 smoothing groups；
- 配置能存成命名 profile，导出端和导入端读同一份 —— 跨 DCC 交付时不再靠口头同步；
- 缓存位（`TAK_CACHE_DIR`，默认 `~/.tak/cache`）是中转站：在 Maya 导出到缓存位，在 UE 里打开同一工具指向它做导入校验。

## 2. 怎么打开

| 入口 | 操作 |
|---|---|
| Maya 菜单 | `TAK / Tech Anim Kit → 打开面板`，左栏「管线」下选「导出 / 导入」 |
| Python | 见下 |
| Qt 面板 | 「打开工具界面」→ 上半部分是表单（预设 / 目标 / 格式 / 内容 / 路径 / profile），下半部分是计划预览与结果表 |

```python
from tak.core import runner

core = runner.load_tool_modules(runner.find_plugin("asset_pipeline"))

profile = core.default_profile("unreal")
profile["content"] = ["skeleton", "mesh", "skin"]
profile["path"] = r"D:\deliver\hero_rig.fbx"

result = runner.run_tool("asset_pipeline", dcc="maya",
                         options={"profile": profile, "asset_name": "hero_rig"})
print(result.report.metadata["plan_lines"])
```

## 3. 输入

| 输入 | 说明 |
|---|---|
| `profile.content` | 勾选的内容项，见下表 |
| `profile.target` | `unreal` / `maya` / `max` / `mobu` / `generic` |
| `profile.format` | 必须在该目标支持的格式里，否则预检报 `format.unsupported` |
| `profile.path` | 导出文件路径；留空或用 `use_cache_dir` 时落到缓存位 |
| `profile.options` | 目标相关的导出选项，切换目标时自动重置为该目标的默认值 |
| 场景快照 | 用来算内容计数（关节 / 网格 / 蒙皮项）并做前置检查 |

内容项：

| id | 名称 | 说明 |
|---|---|---|
| `skeleton` | 骨骼 | 关节层级、朝向、命名与前缀 |
| `mesh` | 蒙皮网格 | 几何 + UV + 法线 |
| `skin` | 蒙皮权重 | 权重与影响骨，缺了导入后不会变形 |
| `controls` | 控制器形状 | NURBS / 曲线控制器 |
| `animation` | 动画缓存 | 骨骼动画曲线 |
| `materials` | 材质槽 | 材质分配与贴图引用 |

预设：`full_rig`（完整绑定）、`skeleton_only`（仅骨骼）、`mesh_only`（仅蒙皮网格）、`anim_cache`（动画缓存）、`controls_only`（仅控制器）。

目标与可用格式：

| target | 名称 | 格式 | 默认选项 |
|---|---|---|---|
| `unreal` | Unreal Engine | fbx, usd | `apply_unit_scale`、`preserve_bone_naming` |
| `maya` | Maya | fbx, usd, ma | `apply_unit_scale`、`smoothing_groups` |
| `max` | 3ds Max | fbx, usd | `apply_unit_scale`、`smoothing_groups` |
| `mobu` | MotionBuilder | fbx | `apply_unit_scale`、`preserve_bone_naming` |
| `generic` | 通用（其它 DCC） | fbx, usd, abc | `apply_unit_scale`、`smoothing_groups` |

## 4. 输出

| 输出 | 说明 |
|---|---|
| 计划 `metadata.plan` | 完整计划：目标、格式、路径、内容、计数、选项 |
| 计划文本 `metadata.plan_lines` | 给人看的三行摘要，UI 直接显示 |
| 预检 `Report` | 含基础检查 + `export_bridge` 的导出前置规则 |
| 导出结果 | `execute()` 返回 `{"ok", "path", "format", "reason"}` |
| sidecar | 导出时落下的清单，导入侧比对用 |

## 5. 校验项清单

导出/导入本身不注册 `@rule`，但它会产生这些自有的 finding：

| rule_id | 级别 | 触发条件 |
|---|---|---|
| `asset_pipeline.content.empty` | error | 一个内容项都没勾，导出的文件会是空的 |
| `asset_pipeline.target.unknown` | error | 目标 id 不在枚举里 |
| `asset_pipeline.format.unsupported` | error | 格式不在该目标支持的集合里 |
| `asset_pipeline.path.missing_dir` | warning | 导出目录不存在（运行时会尝试创建） |
| `asset_pipeline.scene.no_joints` | warning | 勾了骨骼，但快照里没有关节数据 |
| `asset_pipeline.preflight.unavailable` | info | 导出前置规则未加载，只做了基础检查 |

另外会并入 `export_bridge` 的两条规则（`export_bridge.frame_range_consistency`、`export_bridge.unit_axis_consistency`）—— 一致性判定只有一份实现，不在这里重写。

## 6. 典型用法

**场景一：导一份完整绑定到 UE**

```python
from tak.core import runner
core = runner.load_tool_modules(runner.find_plugin("asset_pipeline"))

profile = core.save_profile("hero_to_ue", {
    "preset": "full_rig",
    "content": ["skeleton", "mesh", "skin", "controls", "materials"],
    "target": "unreal",
    "format": "fbx",
    "path": "",
    "use_cache_dir": True,
})
result = runner.run_tool("asset_pipeline", dcc="maya",
                         options={"profile": core.load_profile("hero_to_ue")})
```

空场景下的真实输出：

```text
passed True  findings 2
asset_pipeline.path.missing_dir warning 导出目录不存在：C:\Users\NYAO\.tak\cache
asset_pipeline.scene.no_joints warning 勾选了骨骼，但当前快照里没有采集到关节数据。
plan_lines:
  目标：Unreal Engine（FBX）
  路径：C:\Users\NYAO\.tak\cache\asset.fbx
  内容：骨骼、蒙皮网格、蒙皮权重、控制器形状、材质槽
  场景：0 关节 / 0 网格 / 3 蒙皮项
```

两条都是 warning：目录不存在会在导出时自动创建，关节为空是离线环境的正常现象。**没有 error 就不会拦导出。**

**场景二：只要骨骼，做一次骨架对齐**

```python
profile = core.default_profile("mobu")
profile["preset"] = "skeleton_only"
profile["content"] = ["skeleton"]
plan = core.build_plan(snapshot, profile, "hero_skel")
print(core.describe_plan(plan)[2])   # 内容：骨骼
```

**场景三：在另一个 DCC 里做导入校验**

```python
# 在 UE 侧执行
core = runner.load_tool_modules(runner.find_plugin("asset_pipeline"))
result = core.import_check(r"D:\cache\hero_rig.fbx", ue_snapshot,
                           core.load_profile("hero_to_ue"))
print(result["ok"], result.get("findings"))
```

**场景四：先预检再导出**

UI 里点「导出」时会先跑一次预检；只要出现 error / blocker 就取消导出，不会写出半成品文件。

## 7. 边界与限制

- **不实现格式本身**。写文件由 DCC 适配器的 `export_fbx` / `export_usd` 完成；适配器没实现 `abc` 时，选 ABC 会在执行阶段返回明确的失败原因，而不是假装成功。
- **`ma` 格式仅 Maya 适配器支持**，在其它 DCC 里选它会被预检拦下。
- **sidecar 结构随插件版本变化**，跨大版本混用导出端与导入端可能字段对不上，建议两端版本一致。
- **缓存位是本地目录**，不是网络共享。多人协作时把 `TAK_CACHE_DIR` 指到同一个网络盘即可，但没有并发写保护。
- **不自动清理旧文件**。同一个路径反复导出会覆盖，不保留历史版本。

## 8. 与其它模块的关系

- **场景检查**：导出前的「导出前置」检查组与本工具的预检是同一套规则。
- **规则提供者**：`export_bridge` 与 `ue_import_guard` 的 `manifest.json` 标了 `"internal": true`，不再作为独立工具出现；`asset_pipeline` 通过 `runner.load_tool_modules` 复用它们的实现。
- **对话控制台**：可以在这里发起「把这套绑定的骨骼导到缓存位」，走的是本工具的计划 + 执行流程。
- **设置面板**：profile 存在 `~/.tak/config.json` 的 `export_profiles`，可用设置面板的「打开配置目录」直接查看。
