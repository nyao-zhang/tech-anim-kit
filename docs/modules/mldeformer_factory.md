# ML Deformer 数据工厂（`mldeformer_factory`）

> 把骨架快照变成确定性的姿态采样计划与内容寻址的数据清单，并按质量门槛出结论；只产数据，不调训练。

**适用版本** TAK `0.3.2`　·　**DCC** maya / max / mobu / unreal　·　**权限** `scene.read`　·　**UI** `both`

## 1. 它解决什么问题

ML Deformer 的效果上限几乎完全由数据决定：极值不够，学不到可动范围；某个关节只有单向数据，变形器在另一侧就乱猜；一批 pose 里混进大量角度重复，训练集看着很大其实没用。这些都不是训练算法能补的，必须在摆姿势之前就把清单定下来。

这个模块把「拍什么姿势」写成确定性计划：按关节、轴、角度生成六类候选（极值、半极值、网格、零位、镜像、随机），用局部 `Random(seed)` 保证同种子同序列，按 `(joint, axis, angle)` 去重，再按预算轮转裁剪。产出的每条 pose 带 `pose_id` 与中文 `label`，可以直接当拍摄单发给美术。

第二步是把计划固化成清单：`dataset_id` / `version` / `pose_digest` 都是规范化 JSON 的 sha256 前 16 位，同一输入两次构建逐字节相同，改一个角度就换 id。清单再过六项质量门槛，把「pose 太少 / 某关节没数据 / 重复率过高 / 缺极值类别 / 契约不符」逐条列出来。

第三步，没有第三步——训练入口不存在，下面第 7 节会讲清为什么。

## 2. 怎么打开

| 入口 | 操作 |
|---|---|
| Maya 菜单 | 未挂菜单，用 Python 入口。`tak/adapters/maya/menu.py:42` 的 `MENU_ITEMS` 里没有本项 |
| Python | 见下；`plan` / `manifest` / `validate` / `markdown_summary` 四个纯函数，另有 `run_tool` 走统一 Report |
| Web 面板 | 无专属页签，`panel.html` 的五个 `data-tab`（`chat` / `kb` / `vision` / `mapping` / `motion`）里没有它；用 `POST /api/run` 带 `tool_id` |
| Qt 面板 | 有，`tools/mldeformer_factory/ui_qt.py` 的 `show(dcc="maya", omui=None)`；窗口里一个「生成数据集清单」按钮 |

```python
from tools.mldeformer_factory import core

result = core.manifest(snapshot)          # SkeletonSnapshot.to_dict() or a joints dict
print(result["ok"], result["manifest"]["pose_count"])
```

```python
from tools.mldeformer_factory.ui_qt import show

window = show(dcc="maya")                 # returns None without Qt / QApplication
```

## 3. 输入

`core.plan(snapshot=None, options=None)` 与 `core.manifest(snapshot=None, options=None)` 的采样选项，缺省值与 `tak/core/mldeformer/sampling.py` 一致：

| 参数 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `max_poses` | `int` | `240` | pose 预算；硬上限 `2000`，超出夹取并写 warning，非正数回落 240 |
| `seed` | `int` | `20240101` | 只影响 `random` 类别；用局部 `random.Random(seed)`，不碰全局随机 |
| `categories` | `list[str]` | 全部六类 | 只采指定类别，按固定序 `extreme` / `mid` / `grid` / `rest` / `mirror` / `random` 生效 |
| `limits` | `dict` | `None` | `{关节: {"x": [lo, hi]}}` / `{关节: [lo, hi]}` / `{关节: 单值}`；命中即硬边界，采样绝不越界 |
| `include_rest` | `bool` | `True` | 关掉即剔除 `rest` 类别 |

不传 `limits` 时按关节名启发式取安全角度：`lowerarm` / `forearm` / `elbow` 150°，`lowerleg` / `calf` / `knee` 150°，`upperarm` / `shoulder` / `clavicle` 180°，`spine` / `chest` / `torso` / `waist` 45°，`pelvis` / `hip` 45°，`neck` 60°，`head` / `jaw` / `eye` 60°，`wrist` / `hand` / 五指 90°，`ankle` / `foot` / `toe` / `ball` 90°，`thigh` 120°，其余 90°。

质量门槛（`gates`）可以放在 `options` 顶层，也可以放 `options["gates"]`，六个键：`min_total_poses`（默认 `24`）、`max_total_poses`（`2000`）、`min_poses_per_joint`（`4`）、`max_duplicate_ratio`（`0.1`）、`require_categories`（`["extreme", "mid"]`）、`require_contract`（`"1.0"`）。非法覆盖值静默回落缺省。

`snapshot` 只用 `joints`：可以是 `SkeletonSnapshot`、它的 `to_dict()`，或直接是关节列表 / 字典。字段缺失或畸形一律降级为空计划加 `warnings`，不抛异常。

## 4. 输出

`manifest()` 返回 `{"ok", "tool_id", "manifest", "validation", "summary", "warnings", "safety"}`。`manifest` 的字段：

| 字段 | 类型 | 说明 |
|---|---|---|
| `contract` | `str` | 清单契约，`1.0` |
| `dataset_id` / `version` | `str` | 同一个值，内容寻址；16 位十六进制 |
| `pose_count` / `joint_count` | `int` | pose 总数与关节数（取覆盖数与声明数的较大者） |
| `categories` | `dict` | 各类别存活条数，按固定序 |
| `coverage` | `dict` | 逐关节条数，键已排序 |
| `joints` | `list[str]` | 有采样数据的关节名 |
| `pose_digest` | `str` | 只对 pose 列表求值的内容摘要 |
| `seed` / `duplicates` / `duplicate_ratio` | `int` / `int` / `float` | 种子、重复条数、重复率（保留 4 位） |
| `gates` | `dict` | 本次生效的六项门槛 |
| `generated_by` | `str` | `tak.core.mldeformer` |
| `notes` | `list[str]` | 口径说明，含「内容寻址」「不含训练任务」两条固定文案 |

注意清单**不含逐条 pose 列表**——pose 明细在 `plan()` 的 `plan.poses` 里，清单只留摘要与统计。

真实值（`fixtures/good/good_001.json` 的 `snapshot`，19 关节，默认选项）：

```json
{
  "contract": "1.0",
  "dataset_id": "8542257e7b188b05",
  "version": "8542257e7b188b05",
  "pose_count": 240,
  "joint_count": 19,
  "pose_digest": "f565ee0d97213a72",
  "seed": 20240101,
  "duplicates": 0,
  "duplicate_ratio": 0.0,
  "generated_by": "tak.core.mldeformer",
  "categories": {"extreme": 57, "mid": 57, "grid": 57, "rest": 19, "random": 50}
}
```

`version` 与 `pose_digest` 怎么算出来的：先按 `json.dumps(..., ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)` 得到规范化文本（无空格、键序固定、无时间戳、无绝对路径），再取 `sha256` 十六进制前 16 位。`dataset_id` 与 `version` 的输入是 `{contract, seed, joint_count, pose_count, categories, coverage, joints, poses}` 这个 payload；`pose_digest` 的输入只有规范化后的 pose 列表。可以自己复算：

```python
import hashlib, json
from tak.core.mldeformer.dataset import _normalize_pose

poses = [_normalize_pose(p) for p in plan.poses]     # plan = build_plan(snapshot)
text = json.dumps(poses, ensure_ascii=False, sort_keys=True,
                  separators=(",", ":"), allow_nan=False)
print(hashlib.sha256(text.encode("utf-8")).hexdigest()[:16])
```

```text
f565ee0d97213a72
```

## 5. 校验项 / 规则清单

本模块不注册 `@rule`，`manifest.json` 里没有 `rules` 字段；门槛 issue 的 `code` 由 `gate_codes()` 给出，当前 9 个（8 条内容门禁 + 1 条输入畸形兜底）。`run_report` 把它们逐条翻成 Finding，`rule_id` 直接用 code，另加一条 `mldeformer_factory.summary`（`info`，写数据集摘要与边界提示）。

| rule_id 或 code | 严重级 | 触发条件 | 建议 |
|---|---|---|---|
| `mld.contract.mismatch` | error | `contract` 不等于 `require_contract` | 升级消费方到同一契约版本后重跑，禁止混用清单 |
| `mld.pose_count.too_low` | error | `pose_count < min_total_poses`（默认 24） | 放宽 `max_poses` 或补采更多关节 / 类别 |
| `mld.pose_count.over_budget` | error | `pose_count > max_total_poses`（默认 2000） | 收紧 `max_poses` 或分批出清单 |
| `mld.coverage.missing_joint` | error | 声明的关节在 `coverage` 里完全没有；或 `joint_count` 大于实际覆盖数 | 确认快照里该关节存在并参与采样 |
| `mld.coverage.low` | warning | 某关节条数 `< min_poses_per_joint`（默认 4） | 对该关节补采极值与半极值，至少 4 条 |
| `mld.duplicate.too_high` | error | `duplicate_ratio > max_duplicate_ratio`（默认 0.1） | 检查 `limits` 是否过窄导致角度退化重复 |
| `mld.category.missing` | warning | `require_categories` 中某类计数为 0 | 把缺失类别加入 `categories` 重新采样 |
| `mld.extreme.missing` | warning | `extreme` 计数为 0 | 必须包含 `extreme` 类别，否则学不到可动范围 |
| `mld.manifest.invalid` | error | 传入的清单不是对象 | 传入 `build_manifest` 产出的清单对象 |

`ok` 只看 error：warning 不阻断。门槛 issue 只有 `error` / `warning` 两档，不会出现 `blocker`；Report 里额外那条 `mldeformer_factory.summary` 是 `info`。

## 6. 典型用法

第一段：`fixtures/good/good_001.json` 的 `snapshot` 跑默认采样，复现已知结果 240 poses。

```python
import json
from tools.mldeformer_factory import core

with open("fixtures/good/good_001.json", encoding="utf-8") as fh:
    snapshot = json.load(fh)["snapshot"]
r = core.manifest(snapshot)
m = r["manifest"]
print(r["ok"], m["pose_count"], m["version"], m["pose_digest"])
print(m["categories"])
print(r["warnings"])
```

```text
True 240 8542257e7b188b05 f565ee0d97213a72
{'extreme': 57, 'mid': 57, 'grid': 57, 'rest': 19, 'random': 50}
['候选 418 条超出 max_poses=240，已按类别/关节轮转裁剪 178 条']
```

第二段：只采极值与半极值、预算 20，看裁剪如何保住类别。

```python
p = core.plan(snapshot, {"categories": ["extreme", "mid"], "max_poses": 20})
print(p["summary"]["pose_count"], p["summary"]["categories"])
print(p["plan"]["warnings"])
```

```text
20 {'extreme': 19, 'mid': 1}
['候选 228 条超出 max_poses=20，已按类别/关节轮转裁剪 208 条']
```

裁剪不是简单切前 N 条，而是按「类别 × 关节」桶轮转，先把每个桶的第一条铺满再加厚——所以 20 条预算下 19 个关节仍有 `extreme` 覆盖，代价是 `mid` 只剩 1 条（这正是门槛要警告的情形）。

第三段：拿一份被改坏的清单跑门禁。

```python
m = dict(core.manifest(snapshot)["manifest"])
m["contract"] = "0.9"
m["coverage"] = {"pelvis": 2}
m["categories"] = {"random": 240}
m["duplicate_ratio"] = 0.42
v = core.validate(m)
print(v["ok"], v["counts"])
print([i["code"] for i in v["issues"]][:3])
```

```text
False {'error': 20, 'warning': 4, 'info': 0}
['mld.contract.mismatch', 'mld.coverage.missing_joint', 'mld.coverage.missing_joint']
```

## 7. 边界与限制

**不调用任何训练入口，这是硬边界不是待办。** UE 侧 `MLDeformerAsset` / `NeuralMorphModel` / `DeformerGraphAsset` 在 5.7 与 5.8 的 Python 侧都没有暴露（`docs/VALIDATION.md` B2、`docs/WEB_PANEL.md` W3 / W30），所以仓库把能力位 `mldeformer.train`（`tak/adapters/base.py`）恒定为 `false`，四个适配器没有一个声明它。`info()["training_api"]` 是 `False`，`safety()` 里 `training_api` 也写死 `False`，`metadata["safety"]` 逐字段带出去。

**所以它交付的是「数据 + 结论」，不是「训练好的变形器」。** 清单里刻意没有、也不允许出现训练任务 ID 与引擎资产路径；`notes` 里固定写了一条「本清单只描述采样数据与质量门槛」。看到这份清单能说的只有：这批 pose 够不够、偏不偏、重复多不多。

**不写场景、不联网。** `scene_write: false`、`network: false`；`limits` 与 `categories` 只影响计算，不会去改你的骨骼限值。

**采样计划不是物理正确的姿势。** 角度取的是启发式安全上限或你给的 `limits`，没有解 IK、没有检查自穿插、没有考虑道具与地面。它是拍摄单，不是最终动画。

**`mirror` 计数为 0 是正常的。** 左右限值对称时，镜像角度必然与对侧自身的极值 / 半极值重合，按 `(joint, axis, angle)` 去重后只留一条，`tags` 里保留 `mirror` 与 `mirror_of:` 标记——plan 的 `notes` 会写「同 (joint, axis, angle) 的 N 条候选已按类别优先级去重合并」。

## 8. 与其它模块的关系

**输入是骨架快照，和体检 / 规范 / 导出模块共用同一份契约。** `tak/core/diff/snapshot.py` 的 `SkeletonSnapshot.to_dict()` 固定八个键：`dcc` / `version` / `unit` / `up_axis` / `joints` / `frame_range` / `skin` / `metadata`。本模块只读 `joints`（键名或 `{"name": ...}` 都认），其余字段忽略；所以同一份快照既能让 `rig_health_guard` 查体检、`spec_enforcer` 对规范，也能直接喂给数据工厂出清单，不需要各模块自定义容器。

**`pose_digest` 让清单可追溯。** 采样类别、种子、角度与标签任一变化都会换 `dataset_id`，因此「这次训练的到底是哪批数据」可以用一个 16 位字符串回答。清单与 `plan()` 输出可以一起存档：清单给门槛报告，plan 给拍摄单。

**上游没有依赖 AI。** 这个模块在 README 里被归入六个 AI 类插件，但它实际是纯确定性的：不注册 `@rule`、不联网、不 import DCC，连 provider 都不取。放在这一组是因为它服务的是同一类「数据与诊断」工作，不是因为用了模型。
