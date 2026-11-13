# 骨骼语义映射（`skeleton_mapper`）

> 把任意命名体系的骨架按语义对到规范骨架，确定性给候选；一对多、多对一与低置信度项只登记，不替你选。

**适用版本** TAK `0.3.2`　·　**DCC** maya / max / mobu / unreal　·　**权限** `scene.read`　·　**UI** `both`

## 1. 它解决什么问题

Mixamo 导出的骨架叫 `mixamorig:LeftUpLeg`，UE Mannequin 叫 `thigh_l`，3ds Max Biped 叫 `Bip01 L Thigh`。同一套人形骨架在三个 DCC 里能凑出三十多个名字差异，手工对一遍要半天；而对错一个 Hips 指到脚上，整段动画在 UE 里就是反的——这种错误在视口里还未必看得出来，往往到 Sequencer 才发现。

这个模块把活拆成两步。先用内置语义词典加命名归一化，给出确定性的一对一候选，置信度只分三档：原名完全相同 1.0、归一化后相同 0.95、同属一个语义组但规范名不同 0.55。然后把拿不准的东西原样摊开：一对多、多对一、多对多、低置信度、词典压根没覆盖的骨骼，全部进 `conflicts` 与 `unresolved`。

它不改你的场景，也不直接写 IK Retargeter。产出是一份 v1.0 映射资产 JSON（可带 `conflicts` 与未映射清单），你在 DCC 里确认过之后再导入下游。另一种常见用法是手头已有一份对好的映射、只想给新骨架补几条：把旧资产当 `mapping_asset` 传进去，已确认的条目优先保留，词典与 AI 候选只填空位。

## 2. 怎么打开

| 入口 | 操作 |
|---|---|
| Maya 菜单 | 未挂菜单，用 Python 入口。`tak/adapters/maya/menu.py:42` 的 `MENU_ITEMS` 只有「打开面板 / 场景体检 / 规范校验 / 导出校验 / 动作检索 / Web 面板 / 关于 TAK」 |
| Python | 见下 |
| Web 面板 | 页签「骨骼映射」，`panel.html:131` 的 `data-tab="mapping"`；深链 `?tab=mapping`，后端 `POST /api/mapping` |
| Qt 面板 | 无。模块目录只有 `ui_web.py`，没有 `ui_qt.py` |

```python
from tak.core.runner import run_tool

result = run_tool("skeleton_mapper", snapshot, dcc="maya",
                  options={"source_dcc": "maya", "target_dcc": "unreal"})
print(result.passed, len(result.report.findings))
```

```python
from tak.core.mapping import map_skeleton

mapping = map_skeleton(["upperarm_L"], ["LeftUpArm"],
                       options={"source_dcc": "maya", "target_dcc": "unreal"})
print(mapping["entries"])
```

## 3. 输入

数据来源优先级：`ctx.options["source_bones"]` / `["target_bones"]` 直接指定；否则源取 `ctx.snapshot["joints"]` 的键，目标取 `ctx.spec["skeleton"]["required_joints"]`。默认规范里 `required_joints` 是 19 个关节（`root` 到 `foot_R`）。

`map_skeleton(source_bones, target_bones, mapping_asset=None, options=None, provider=None)`：

| 参数 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `source_bones` | `list[str]` / `dict` / `set` | 无（必填） | 源骨骼名；dict 取键。插件层未给时回落 `snapshot["joints"]` |
| `target_bones` | 同上 | 无（必填） | 目标骨骼名；插件层未给时回落 `spec.skeleton.required_joints` |
| `mapping_asset` | `dict` / 文件路径 | `None` | 已确认映射基线，v1.0 资产字典、`suggest_mapping` 结果或 JSON 路径 |
| `options` | `dict` | `None` | 见下表，会原样透传给内部流水线 |
| `provider` | Provider | `None` | 不传则 `get_provider(task="semantic_mapping")`，默认离线 mock |

`options` 里被读取的键：

| 键 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `source_bones` | `list` / `dict` | 未设置 | 覆盖源骨骼，优先于快照 |
| `target_bones` | `list` / `dict` | 未设置 | 覆盖目标骨骼 |
| `required_joints` | `list` | 未设置 | 与 `target_bones` 同义，且优先于它 |
| `mapping_asset` | `dict` / 路径 | `None` | 同上面的同名参数 |
| `use_ai` | `bool` | `False` | 打开 AI 候选；只补词典未覆盖项 |
| `source_dcc` / `target_dcc` | `str` | `""` | 只写进输出与资产 `metadata`，不参与判定 |
| `provider` | Provider | `None` | 同上，覆盖默认 provider |

## 4. 输出

`map_skeleton()` 返回的键：`version`（映射资产版本，恒为 `1.0`，不是插件版本 `0.1.0`）、`source_dcc`、`target_dcc`、`entries`、`conflicts`、`unresolved`、`stats`、`warnings`、`metadata`。

`entries[]` 每条固定八个字段：

| 字段 | 类型 | 说明 |
|---|---|---|
| `source` / `target` | `str` | 源 / 目标骨骼原名，不做改写 |
| `semantic` | `str` | `SEMANTIC_GROUPS` 的语义组名，如 `arm`、`calf` |
| `confidence` | `float` | 保留 4 位；冲突项在 1.0/0.95/0.55 基础上再乘 0.5 |
| `method` | `str` | `exact` / `normalized` / `dictionary` / `ai` / `asset` |
| `origin` | `str` | `dictionary` / `ai` / `asset` |
| `provider` | `str` 或 `null` | 非 AI 来源为 `null` |
| `status` | `str` | `auto`；`confidence < 0.5` 时改为 `needs_review` |

以下为真实输出（Biped 源对 UE 目标，故意留了一个大小写重名的目标骨骼；`entries` 与 `unresolved` 为节选，`stats` 是真实计数）：

```json
{
  "version": "1.0",
  "source_dcc": "max",
  "target_dcc": "unreal",
  "entries": [
    {"source": "Bip01 Neck", "target": "neck_01", "semantic": "neck",
     "confidence": 0.95, "method": "normalized", "origin": "dictionary",
     "provider": null, "status": "auto"}
  ],
  "conflicts": [
    {"type": "duplicate_source", "source": "Bip01 L UpperArm",
     "targets": ["upperarm_L", "upperarm_l"],
     "message": "源骨骼 'Bip01 L UpperArm' 同时命中多个目标骨骼，需人工确认"}
  ],
  "unresolved": [
    {"bone": "Bip01 R UpperArm", "side": "source", "semantic": "arm",
     "reason": "unmapped"},
    {"bone": "upperarm_l", "side": "target", "semantic": "arm",
     "reason": "unmapped"}
  ],
  "stats": {"total": 5, "valid": 3, "errors": 0, "warnings": 1,
            "conflicts": 1, "average_confidence": 0.95,
            "entries": 3, "unresolved": 4,
            "origins": {"dictionary": 3, "ai": 0, "asset": 0},
            "ai_candidates": 0}
}
```

`unresolved[].side` 只有三个取值：`source`（源骨未映射）、`target`（目标骨未映射）、`pair`（成对但置信度低）；`reason` 只有 `unmapped` 与 `low_confidence`。

## 5. 校验项 / 规则清单

本模块 `manifest.json` 里没有 `rules` 字段，`info()["rule_count"]` 是 `0`，**不注册任何全局 `@rule`**，因此不会参与全仓 40 个 golden fixture 的判定。Report 里的 Finding 全部由 `run_report()` 现场构造，只有两个 `rule_id`：

| rule_id | 严重级 | 触发条件 | 建议 |
|---|---|---|---|
| `skeleton_mapper.conflict` | error | `conflicts` 中任意一条（目标重名、源重名、一对多、多对一、多对多、AI 重叠） | 在 DCC 内人工核对骨骼语义后手动确认；fix_hint 明写「绝不自动选择」 |
| `skeleton_mapper.unresolved` | warning | `unresolved` 中任意一条，含 `unmapped` 与 `low_confidence` | 补齐映射或人工确认后再应用到下游 IK Retargeter |

`conflicts[].type` 的取值与来源：

| `type` | 含义 | 产生位置 |
|---|---|---|
| `duplicate_target` | 多条 entry 命中同一目标骨 | `validate_mapping` 交叉校验 |
| `duplicate_source` | 一条源骨命中多个目标骨 | `validate_mapping` 交叉校验 |
| `one_source_many_targets` / `many_sources_one_target` / `many_to_many` | 同一规范名在多对多组合里碰撞 | `deterministic_pairs` |
| `explicit_conflict` | entry 自带 `status="conflict"` 且未被前几类覆盖 | `validate_mapping` |
| `ai_overlaps_dictionary` | AI 候选与词典命中同一源骨 | `merge_candidates`，保留词典项 |
| `ai_target_conflict` | AI 候选的目标骨已被词典占用 | `merge_candidates`，不写入 |

关于低置信度：`confidence < 0.5` 才进 `needs_review` 与 `unresolved`。词典里 0.55 那一档（同语义组、规范名不同）不算低置信度，会留在 `entries` 里但标 `status="auto"`——这是刻意的，它只是「弱同源」，硬拦会把大量正常映射也拖进复核队列。

## 6. 典型用法

最小可跑：只给两串骨骼名，看它能对出多少。

```python
from tak.core.mapping import map_skeleton

m = map_skeleton(
    ["Bip01 Pelvis", "Bip01 Spine", "Bip01 L UpperArm", "Bip01 R UpperArm", "Bip01 Neck"],
    ["pelvis", "spine_01", "upperarm_l", "upperarm_L", "neck_01"],
    options={"source_dcc": "max", "target_dcc": "unreal"},
)
print(m["stats"]["entries"], m["stats"]["conflicts"], m["stats"]["unresolved"])
```

```text
3 1 4
```

第二段：把结果写成 v1.0 资产，再用同一套 reader 读回来（写盘复用 `ik_retarget_guard/mapping.py` 的实现）。

```python
from tak.core.mapping import map_skeleton, write_mapping, load_mapping

m = map_skeleton(["Bip01 Pelvis", "Bip01 L UpperArm"],
                 ["pelvis", "upperarm_l"],
                 options={"source_dcc": "max", "target_dcc": "unreal"})
write_mapping(m, "tak_out/map.json")
asset = load_mapping("tak_out/map.json")
print(asset["version"], asset["generated_by"], len(asset["entries"]), len(asset["conflicts"]))
```

```text
1.0 ik_retarget_guard 2 0
```

第三段：拿旧资产当基线，补新骨架。在基线里出现过的条目 `origin` 是 `asset`，词典候选不会覆盖它；基线没覆盖的骨架仍由词典给（`origin` 为 `dictionary`）。

```python
from tak.core.mapping import map_skeleton

asset = {"version": "1.0", "generated_by": "ik_retarget_guard",
         "entries": [{"source": "Bip01 Spine", "target": "spine_01",
                      "confidence": 0.3, "method": "dictionary"}],
         "conflicts": [], "metadata": {}}
m = map_skeleton(["Bip01 Spine", "Bip01 Pelvis"], ["spine_01", "pelvis"],
                 mapping_asset=asset)
print([(e["source"], e["origin"], e["status"]) for e in m["entries"]])
print([(u["bone"], u["reason"]) for u in m["unresolved"]])
```

```text
[('Bip01 Pelvis', 'dictionary', 'auto'), ('Bip01 Spine', 'asset', 'needs_review')]
[('Bip01 Spine', 'low_confidence')]
```

## 7. 边界与限制

**冲突绝不自动选择，必须人工确认。** 这不是保守，是因为「一对多」在语义上没有唯一解：目标骨架同时存在 `upperarm_L` 与 `upperarm_l` 时，程序无法知道哪个是你要的那个；选错一个，整条手臂的旋转就会挂到别处，而且错得「看起来像在动」，比彻底断链更难查。所以碰撞组合全部保留成 `status="conflict"` 的 entry，且一条都不进 `valid_entries`——干净项照常给，碰撞项只进复核队列。

**AI 只是建议，且只能补空位。** `use_ai=True` 时候选要过三道确定性闸门（源/目标必须真实存在、两端语义都必须已知且一致、置信度必须是 0..1 有限数），与词典重叠的一律不覆盖、只把分歧写进 `conflicts`。离线或 provider 输出非法时整段降级为纯确定性结果并写 `warnings`，不会假装 AI 参与了。

**不写场景、不写 IK Retargeter 资产。** 这个插件只有 `scene.read`。写盘也只写你显式指定的 JSON 路径，`write_mapping` 不会动 UE 资产。

**词典覆盖面有限。** `SEMANTIC_GROUPS` 覆盖 UE Mannequin、Mixamo、Biped 的常见命名与五指链，但自定义命名空间（例如工作室自研的 `CHAR_A_UpperArm_L_Def`）只能靠前后缀剥离规则碰运气，剩下的必须靠基线资产或人工补。

**Web 路径拿不到 `mapping_asset` 与自定义 provider。** `POST /api/mapping` 只传 `source_bones` / `target_bones` / `options` 三个字段给 `map_skeleton`，没有 `mapping_asset` 入参，也不接受 provider 对象；需要基线或指定模型请走 Python。

## 8. 与其它模块的关系

链路是单向的：`ik_retarget_guard` 先产出映射资产，`skeleton_mapper` 消费它。资产契约固定在 `tools/ik_retarget_guard/mapping.py`，版本 `1.0`：

| 顶层字段 | 说明 |
|---|---|
| `version` | 必须形如 `1.x`；`load_mapping_asset` 只接受主版本 1，其它直接 `ValueError` |
| `generated_by` | 写入方，目前恒为 `ik_retarget_guard` |
| `entries` | 每项 `source` / `target` / `confidence` / `method` 四个键，`source` 与 `target` 必须非空字符串，`confidence` 必须是有限数值 |
| `conflicts` | 必须保留的人工复核队列 |
| `metadata` | 含 `unmapped_source` / `unmapped_target` / `consumers`，`skeleton_mapper` 会把 `unresolved` 也塞进来 |

`skeleton_mapper` 读资产时只接受「源与目标都在当前骨架里」的条目，不在骨架里的会写一条 `基线映射 ... 不在当前骨架中，已跳过` 的 warning；`conflicts` 原样并入复核队列。写回时走同一个 `write_mapping_asset`，所以产物能被 `load_mapping_asset` 直接读回，字段与排序规则完全一致。

与另外两个模块的关系：目标骨骼默认取 `spec.skeleton.required_joints`，与 `spec_enforcer` 用的是同一份规范；源骨骼默认取 `snapshot["joints"]`，与 `rig_health_guard` / `export_bridge` 用的是同一份快照契约。
