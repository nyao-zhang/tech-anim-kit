# 抽帧缺陷诊断（`defect_inspector`）

> 对 Playblast / UE 录屏抽帧做缺陷候选诊断：模型只出候选，帧号、类别、骨骼、置信度全部经确定性过滤，结论由人下。

**适用版本** TAK `0.3.2`　·　**DCC** maya / max / mobu / unreal　·　**权限** `scene.read`　·　**UI** `web`

## 1. 它解决什么问题

一段两秒的循环走路 60 帧，脚滑往往只出现在其中三四帧，而这三四帧恰好是审核时最容易滑过去的。人工逐帧看一段 600 帧的镜头，第二遍注意力就散了；就算抓到，描述给动画师时也容易变成「第 300 帧左右脚有点飘」，对方还得自己找。

这个模块把抽帧列表丢给多模态模型，让它按白名单字段报候选：帧号、缺陷类别、骨骼、置信度、一句可见现象。返回结果先过确定性过滤——帧号必须在输入帧里、类别必须在八类白名单里、置信度必须是 0..1、骨骼必须在快照 `joints` 里——不合格的直接进 `rejected` 并写原因。

没配模型或模型输出不合法时，它不会假装看到了东西：切到确定性降级路径，只根据帧数量、帧号间隔、有没有骨骼定位这些可判定信号给低置信度候选，`category` 一律 `unknown`，`source` 标 `deterministic_fallback`。它的输出是「待复核清单」，不是「缺陷判定结果」。

## 2. 怎么打开

| 入口 | 操作 |
|---|---|
| Maya 菜单 | 未挂菜单，用 Python 入口。`tak/adapters/maya/menu.py:42` 的 `MENU_ITEMS` 里没有本项 |
| Python | 见下；`core.inspect(frames, snapshot=None, options=None, prompt=None, provider=None, **kwargs)` 是 Web 与脚本共用的入口；要走统一 Report 用 `run_tool("defect_inspector", snapshot, dcc="maya", options={"frames": frames})` |
| Web 面板 | 页签「抽帧诊断」，`panel.html:130` 的 `data-tab="vision"`；深链 `?tab=vision`，后端 `POST /api/vision` |
| Qt 面板 | 无。模块目录只有 `ui_web.py`，没有 `ui_qt.py` |

```python
from tools.defect_inspector import core

result = core.inspect(frames, snapshot={"joints": {"hand_L": {}}},
                      options={"focus": "foot contact slip"})
print(result["source"], len(result["candidates"]))
```

## 3. 输入

抽帧由调用方提供，本模块不解码视频、不读本地图片、不调外部进程。`frames` 每项：

| 字段 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `frame` / `frame_number` | `int`（可取纯数字字符串） | 必填 | 帧号；两者都认，`frame` 优先 |
| `image` | `str` | 必填 | 只允许 `http://` / `https://` / `data:` 开头；**不接受本地路径** |
| `bones` | `list[str]` / `str` | `[]` | 该帧涉及的骨骼名，用于提示与白名单校验 |
| `note` | `str` | `""` | 截断到 500 字符；为空且给了 `timestamp` 时自动写 `timestamp=...` |

上限与选项：

| 参数 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `frames` | `list[dict]` | `[]` | 超过 `24` 条（`MAX_FRAMES`）截断并标 `truncated`；重复帧号保留首次出现 |
| 单张 `image` 长度 | — | `2_000_000` 字符 | 超过（`MAX_IMAGE_CHARS`）该项进 `errors`，不抛异常 |
| `snapshot.joints` | `dict` / `list` / `set` | 无 | 骨骼白名单；不给就跳过骨骼校验（`bone_known` 恒 `true`） |
| `options.focus` | `str` | 未设置 | 截断到 300 字符后拼进提示词的「补充关注点」 |
| `prompt` | `str` | `None` | 覆盖内置结构化提示词；内置版会写入帧号、类别、骨骼三份白名单 |
| `provider` | Provider | `None` | 不传则 `get_provider(task="vision")`；默认离线（`TAK_AI_OFFLINE` 未置 `0`）时走 mock |

## 4. 输出

`inspect_frames()` / `inspect()` 返回：

| 字段 | 类型 | 说明 |
|---|---|---|
| `ok` | `bool` | 有至少一条有效帧即为真 |
| `candidates` | `list[dict]` | 过完确定性过滤的候选，按 `(frame, category, bone)` 稳定排序 |
| `rejected` | `list[dict]` | `{"index", "candidate", "reason"}`；`candidate` 只保留安全预览字段 |
| `warnings` | `list[dict]` | `{"type", "message"}`，见下 |
| `provider` / `model` | `str` | provider 类名与 `model` 属性；无 provider 时是 `none` / `""` |
| `source` | `str` | `vision_model` 或 `deterministic_fallback` |
| `frames_checked` | `int` | 通过规整的帧数 |
| `stats` | `dict` | `input`（候选条数，不是帧数）、`valid`、`rejected`、`duplicates`、`frames_checked`、`joints_checked`、`truncated`、`invalid_inputs`、`parse_error`、`source` |
| `deterministic` | `bool` | 是否走了降级路径 |

`candidates[]` 固定七个字段：`frame`（`int`）、`category`（白名单内）、`bone`（可能为空串）、`confidence`（4 位小数）、`evidence`（截断 1000 字符）、`source`、`bone_known`（骨骼不在白名单时降为 `false`）。

真实输出（离线降级，三帧、其中一帧没给骨骼）：

```json
{
  "ok": true,
  "source": "deterministic_fallback",
  "provider": "MockProvider",
  "model": "mock",
  "deterministic": true,
  "frames_checked": 3,
  "candidates": [
    {"frame": 1002, "category": "unknown", "bone": "", "confidence": 0.15,
     "evidence": "该帧未提供骨骼定位信息，候选无法定位到具体关节；离线确定性降级，需人工复核",
     "source": "deterministic_fallback", "bone_known": true},
    {"frame": 1005, "category": "unknown", "bone": "", "confidence": 0.2,
     "evidence": "帧号间隔不均匀（首段 1，第 3 段 3），连续性缺陷判读可能失真；离线确定性降级，需人工复核",
     "source": "deterministic_fallback", "bone_known": true}
  ],
  "stats": {"input": 2, "valid": 2, "rejected": 0, "duplicates": 0,
            "warnings": 0, "frames_checked": 3, "joints_checked": 2,
            "truncated": false, "invalid_inputs": 0,
            "parse_error": "json_parse_failed", "source": "deterministic_fallback"}
}
```

`stats["input"]` 是**候选**条数，帧数看 `frames_checked`：上面的例子里 3 帧进来、2 条候选留下，所以 `input` 是 2。`warnings[].type` 的取值：`frame_rejected`、`truncated`、`provider_unavailable`、`parse_error`、`provider_error`、`deterministic_fallback`、`unknown_bone`、`no_valid_frames`、`internal_error`。

## 5. 校验项 / 规则清单

本模块不注册 `@rule`，`info()` 里也没有 `rule_count`。Report 中的 Finding 由 `run_report()` 从候选现场构造，`rule_id` 由 `"%s.candidate.%s" % (TOOL_ID, item.get("category"))` 拼出，形如 `defect_inspector.candidate.foot_slide`；严重级不是模型给的，由确定性规则算。

八个缺陷类别与严重级映射（`_HIGH_CONFIDENCE = 0.75`）：

| rule_id 或 code | 严重级 | 触发条件 | 建议 |
|---|---|---|---|
| `defect_inspector.candidate.foot_slide` | warning / info | 候选类别 `foot_slide` 且 `confidence >= 0.75` → warning，否则 info | 人工核对落地脚接触点，检查 IK 是否锁脚 |
| `defect_inspector.candidate.popping` | warning / info | 同上规则 | 核对相邻帧旋转曲线是否跳变 |
| `defect_inspector.candidate.jitter` | warning / info | 同上规则 | 检查采样率与约束抖动 |
| `defect_inspector.candidate.penetration` | warning / info | 同上规则 | 检查碰撞体与裙摆 / 道具穿插 |
| `defect_inspector.candidate.weight_artifact` | warning / info | 同上规则 | 检查权重过渡与形变器 |
| `defect_inspector.candidate.twist` | warning / info | 同上规则 | 检查扭转分配与关节朝向 |
| `defect_inspector.candidate.ik_snap` | warning / info | 同上规则 | 检查 IK 切换点与极向量 |
| `defect_inspector.candidate.unknown` | info | 类别 `unknown`，或置信度不足 0.75 | 只能人看；降级路径产出的全是这一类 |

降级与拒绝的 code（写进 `rejected[].reason` / `errors[].reason`，不是 Finding）：

| code | 出现位置 | 含义 |
|---|---|---|
| `frame_out_of_range` | `rejected` | 候选帧号不在输入帧里 |
| `unknown_category` | `rejected` | 类别不在八类白名单 |
| `invalid_confidence` | `rejected` | 置信度不是 0..1 有限数 |
| `duplicate` | `rejected` | `(frame, category, bone)` 重复 |
| `not_object` | `rejected` / `errors` | 项不是对象 |
| `invalid_frame` / `invalid_image` / `image_too_long` | `errors` | 帧号非整数 / image 不是字符串或方案非法 / 超过字符上限 |
| `duplicate_frame` | `errors` | 帧号重复，保留首次出现 |
| `frames_not_list` | `errors` | `frames` 不是列表 |

## 6. 典型用法

第一段：最小帧列表跑离线降级，确认它在没有模型时不编造结果。

```python
from tools.defect_inspector import core

frames = [
    {"frame": 1001, "image": "data:image/png;base64,AAAA", "bones": ["hand_L"]},
    {"frame": 1002, "image": "data:image/png;base64,BBBB", "bones": []},
    {"frame": 1005, "image": "data:image/png;base64,CCCC", "bones": ["hand_L"]},
]
r = core.inspect(frames, snapshot={"joints": {"hand_L": {}, "pelvis": {}}})
print(r["source"], r["deterministic"], r["frames_checked"])
print([(c["frame"], c["category"], c["confidence"]) for c in r["candidates"]])
```

```text
deterministic_fallback True 3
[(1002, 'unknown', 0.15), (1005, 'unknown', 0.2)]
```

第二段：接一个返回固定 JSON 的 provider，验证确定性过滤真的在拦。

```python
import json
from tools.defect_inspector import core

class Stub:
    model = "demo-vision"
    def vision(self, images, prompt):
        return json.dumps({"candidates": [
            {"frame": 1001, "category": "foot_slide", "bone": "hand_L",
             "confidence": 0.82, "evidence": "contact point slides"},
            {"frame": 1002, "category": "popping", "bone": "tail_01",
             "confidence": 0.9, "evidence": "jump at wrist"},
            {"frame": 9999, "category": "jitter", "bone": "", "confidence": 0.5,
             "evidence": "frame not in whitelist"},
            {"frame": 1005, "category": "explosion", "bone": "", "confidence": 0.6,
             "evidence": "category not in whitelist"},
        ]})

r = core.inspect(frames, snapshot={"joints": {"hand_L": {}, "pelvis": {}}},
                 provider=Stub())
print(r["source"], [(c["frame"], c["category"], c["confidence"], c["bone_known"])
                    for c in r["candidates"]])
print([x["reason"] for x in r["rejected"]])
```

```text
vision_model [(1001, 'foot_slide', 0.82, True), (1002, 'popping', 0.3, False)]
['frame_out_of_range', 'unknown_category']
```

`tail_01` 不在快照 `joints` 里：候选保留但置信度从 0.9 压到 0.3、`bone_known` 置 `false`，同时写一条 `unknown_bone` 警告——它仍然是你人工复核的线索，不该被静默丢掉。

第三段：Web 路径，`POST /api/vision`。

```json
POST /api/vision
{"frames": [{"frame": 1001, "image": "data:image/png;base64,AAAA", "bones": ["hand_L"]}],
 "snapshot": {"joints": {"hand_L": {}}}}
```

```text
200 {"ok": true, "result": {"ok": true, "source": "deterministic_fallback",
     "frames_checked": 1, "deterministic": true,
     "candidates": [{"frame": 1001, "category": "unknown", "bone": "",
                     "confidence": 0.2, "source": "deterministic_fallback"}]}}
```

## 7. 边界与限制

**AI 只产候选，结论要人复核。** 严重级由 `confidence` 与类别算，不由模型说；即使到了 `warning`，`fix_hint` 写的也是「需人工复核（AI 候选，非确定性判定）」。`metadata["safety"]` 里逐字段写死 `ai_role: "candidates_only"`、`needs_human_review: true`。原因是多模态模型对「脚滑」与「脚在动」的区分并不可靠，把它当判定就会把误报变成返工。

**不写场景。** 只有 `scene.read`，`writes_scene: false`；它连「跳到第 N 帧」都不会做，帧号只是文本。

**离线降级不伪称识别。** `source` 变成 `deterministic_fallback`、`category` 一律 `unknown`、`evidence` 明写「未做视觉缺陷识别」。降级候选只来自四个信号：帧数少于 3（0.2）、帧号间隔不均匀（0.2）、该帧没给骨骼（0.15）、以及什么都没命中时的兜底一条（0.1）。

**不读本地文件，也不提供抽帧。** `image` 必须是 `http(s)` URL 或 `data:` URI，本地路径会被判为 `invalid_image`，这样既避免意外上传，也保证「模型看到的就是你给的」。四个适配器都没有声明 `capture.playblast` 能力（`tak/adapters/maya/adapter.py:38`、`max`、`mobu`、`unreal` 的能力集合里都没有），所以帧列表必须由你或你的脚本先导出。

## 8. 与其它模块的关系

**与 Playblast 导出是「自备输入」的关系。** 本模块只接受帧列表，不做渲染；MoBu 侧尤其明确——`pyfbsdk` 没有稳定的视口截图接口，`tak/adapters/mobu/adapter.py:150` 直接写明 `CAPTURE.PLAYBLAST 未声明`，因此 MoBu 上必须用 `FBApplication` 或外部录屏自取帧。

**与 `export_bridge` 没有调用关系。** `export_bridge` 做的是导出前 preflight 与导出前后 sidecar 快照 diff（能力 `export.fbx` / `diff.verify`），不产帧、不读图；两者只在工作流上相邻：导出前后各抽几帧丢进来，可以给「导出把蒙皮搞坏了」这类问题留一份旁证。

**坐标定位靠快照契约。** `snapshot["joints"]` 与体检 / 规范模块共用同一份 `SkeletonSnapshot.to_dict()` 结构，所以同一份快照既能给 `rig_health_guard` 体检，也能给这里当骨骼白名单——不给白名单时骨骼校验整段跳过，`bone_known` 恒 `true`，这一点在报告里要留意。

**离线路径与真实模型路径的验证程度不同。** 八类分类、帧号 / 骨骼白名单过滤是确定性的，已被测试覆盖；「模型看得准不准」没有任何实测数据（`docs/BATCH3_REPORT.md` 记录：VLM 只在离线降级路径验证过，真实多模态见 `docs/VALIDATION.md` B18）。
