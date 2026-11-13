# 批次 4 交付报告（P6）

> 对应任务：P6.1–P6.4。版本 `0.2.0` → **`0.3.0`**；测试 **872 → 1117**（新增 245）；插件 **9 → 11**。
>
> 一句话概括这批做了什么：把「工具集」补成「能装、能查、能出数据集、能发版」的东西。
> 前三个批次解决「检查得准不准」，这批解决「装得进去、找得到、数据出得来」。

## 1. 交付概览

| 任务 | 模型 | 产出 | 用例 |
|---|---|---|---|
| P6.1 Motion Library 动作语义检索 | DeepSeek-V4.1-Flash | `tak/core/motion/`（5 文件）、`tools/motion_library/`、Web 第 5 页签 + `/api/motion` | 78 |
| P6.2 ML Deformer 数据工厂 | DeepSeek-V4.1-Flash | `tak/core/mldeformer/`（3 文件）、`tools/mldeformer_factory/` | 63 |
| P6.3 一条命令安装器 | DeepSeek-V4.1-Flash | `tak/install/`（5 文件）、CLI、`docs/RELEASE.md` | 64 |
| P6.4 发布工程化（主 agent） | — | Maya 菜单补齐、`serve_in_thread`、`/api/motion` 接线、CHANGELOG、README/docs/CI、发布元数据护栏、Maya 探测修复 | 8 + 15 + 17 |

代码规模：`tak/` 83 文件 19198 行、`tools/` 49 文件 9303 行、`tests/` 38 文件 12677 行、`validation/` 7 文件 1241 行。
**178 个 `.py` 全部通过 `ast.parse(feature_version=(3,9))`**，`tak/core/` 仍然零 DCC import。

## 2. 三个新内核：都是「产出候选」，都不碰场景

```
tak/core/motion/          tak/core/mldeformer/          tak/install/
├── model.py  裁剪后的 clip    ├── sampling.py 姿态采样   ├── targets.py 四端定义
├── features.py 数值特征       ├── dataset.py  清单+门槛   ├── writer.py  标记块写入
├── library.py BM25+加权检索   └── __init__.py            ├── cli.py     命令行
└── loader.py  目录扫描/索引                              └── __main__.py
```

三者的共同点：**纯 Python 3.9、零第三方、零网络、确定性**。CI 不需要密钥、不需要 DCC 就能跑满。

### 2.1 动作检索（P6.1）

输入是「clip 元数据 + 可选帧采样」，输出是带中文解释的排序结果：

```python
lib = MotionLibrary()                 # 12 个内置示例 clip
lib.search("走路 循环", top_k=5)
# → walk_fwd_01  13.96  "命中关键词：走路、循环；类型查询命中：行走"
```

三层打分，顺序固定：

1. **文本分**：name / tags / bones 的 BM25（中文单字 + bigram，英文小写词元）。
2. **类型分**：query 命中 `MOTION_TYPES` 的中英文关键词时给对应类型加权。
3. **过滤**：`filters` 是**硬排除**，不是降权。`min_duration` / `max_duration` / `loopable` / `root_motion` / `has_bone` / `min_fps`。

一个刻意的限制：**不解析 FBX 二进制**。纯 Python 解析 FBX 是几百行的无底洞，而且一定会对某个导出器版本出错。取而代之：

- 优先读同名 `.motion.json` sidecar；
- 没有 sidecar 时用文件名启发式（`Walk_Fwd_01.fbx` → walk + forward + 循环候选）；
- 走启发式的 clip 一定标 `features["estimated"]=1.0`。

**标记估算值比假装精确重要**。下游如果看到 `estimated` 还拿它当基线，那是下游的问题，不是数据在骗人。

### 2.2 数据集工厂（P6.2）

```python
plan = build_plan(snapshot)                    # 19 关节 → 240 个姿态
manifest = build_manifest(plan)
# version=8542257e7b188b05  pose_digest=f565ee0d97213a72  pose_count=240
```

六类采样：`extreme`（每关节每轴极值）/ `mid` / `grid` / `rest` / `mirror` / `random`（种子固定）。

**内容寻址**：`version` 与 `pose_digest` 都是规范化 JSON 的 `sha256` 前 16 位。同一骨架两次生成逐字节相同；改任意一个角度，两个哈希都变。这不是「为了方便缓存」，是为了让「动画组评审的那份数据」和「三个月后重新生成的数据」能对上是同一份。

8 条质量门槛，每条一个稳定 `code`：

| code | 级别 | 含义 |
|---|---|---|
| `mld.contract.mismatch` | ERROR | 契约版本不匹配 |
| `mld.pose_count.too_low` | ERROR | 少于 24 个姿态，训不出东西 |
| `mld.pose_count.over_budget` | ERROR | 超过 2000，超出预算 |
| `mld.coverage.missing_joint` | ERROR | 清单声明的关节没有覆盖记录 |
| `mld.coverage.low` | WARN | 单关节少于 4 条 |
| `mld.duplicate.too_high` | ERROR | 重复率 > 10% |
| `mld.category.missing` | WARN | 缺 extreme / mid |
| `mld.extreme.missing` | WARN | 没有极值姿态（ML Deformer 的关键） |

**这个工具不训练**。UE 5.7/5.8 的 Python 侧都没有暴露 `MLDeformerAsset`（`docs/VALIDATION.md` B2，`docs/WEB_PANEL.md` W3/W30），能力位 `mldeformer.train` 恒为 `false`。代码里没有 `train` 调用，`info()["training_api"]` 是 `False`，`metadata["safety"]` 里逐字段写死。测试会反向扫描源码，确认没人偷偷加回去。

### 2.3 安装器（P6.3）

```bash
python -m tak.install --list                      # 探测（本机实测：MoBu 2023 找到了）
python -m tak.install --plan --dcc maya,max       # 预览
python -m tak.install --apply --dcc maya          # 真写
python -m tak.install --uninstall --apply --dcc maya
```

四种动作：`create` / `append` / `replace-block` / `skip`。判定逻辑是**内容比对**，不是版本号比对 —— 块内容一样就 `skip`，不一样就 `replace-block`（哪怕版本号相同）。

四条安全约束（都有测试盯着）：

1. 默认 `dry_run=True`。`tests/test_installer.py` 里有一条用 `monkeypatch` 替换 `builtins.open`，断言 dry-run 路径上**零次写模式调用**。
2. 写前备份 `<file>.tak-backup-<N>`，N 递增不覆盖旧备份。
3. 只改 `# >>> TAK BEGIN` 到 `# >>> TAK END` 之间，块外逐字节不动（含末尾换行与 CRLF）。
4. 卸载精确删块；删完只剩空白的文件才删除，否则保留并 warn。

`--plan` 输出里的 `[replace-block]` 是个信号：说明块内内容和你手上这份不一致，可能是你手改过，也可能是包升级了。两种都得看一眼再 `--apply`。

## 3. P6.4 补的两个真实缺口

### 3.1 Maya 一直没有菜单模块

安装器子任务在实现 Maya 启动块时发现：`from tak.adapters.maya.menu import register_menu` 必然失败 —— 因为**这个文件根本不存在**。Max / MoBu / UE 从批次 2/3 起都有 `menu.py`，唯独 Maya 没有。P2 的交付清单写的是「Maya adapter + Qt 组件库」，菜单入口从来就没做。

更糟的是安装块里写的是：

```python
try:
    from tak.adapters.maya.menu import register_menu
except Exception:
    _tak_register_menu = None          # 静默
```

于是现象是：安装成功，Maya 里没菜单，控制台没有任何提示。用户只能得出「这工具坏了」的结论。

补法：
- 新增 `tak/adapters/maya/menu.py`：`register_menu` / `unregister_menu` / `dispatch` / `menu_script` / `menu_items`，六个菜单项（打开面板 / 场景体检 / 规范校验 / 导出校验 / 动作检索 / Web 面板）+ 一个分隔符 + 关于。
- **幂等**：`cmds.menu` 不查重，反复执行 `userSetup.py` 会堆出多个同名菜单。实现是「先 `menu -exists` 判断，再 `deleteUI`，然后重建」。
- **不抛异常**：这些函数在 Maya 启动时被调用，抛出去会让用户静默失去后续所有启动项。无宿主时返回 `{"ok": False, "reason": "maya.cmds 不可用（无 Maya 宿主）"}`。
- 把两处静默 `except` 改成打印具体原因（Maya / MoBu）。
- `tests/test_maya_menu.py` 15 个用例，含一个假 `maya.cmds` 替身验证菜单真的建出来、且重复注册不翻倍。

### 3.2 菜单里点「Web 面板」会阻塞宿主

原来只有 `serve()`，它是阻塞的（`serve_forever`）。在 Maya 菜单里调它，等于把 Maya 主线程卡死到用户 Ctrl+C。

补 `serve_in_thread()` / `stop_panel()`：

- 后台线程跑 `serve_forever`，立即返回地址；
- **进程内单例**：点第二次复用同一实例，不再起第二个服务器抢端口、泄漏线程；
- `port=0` 由系统分配空闲端口，最不容易冲突；
- 支持 `tab=` 参数生成 `url?tab=motion`，前端 `boot()` 读 `?tab=` 自动切页签。

## 4. 发布工程化

### 4.1 `docs/RELEASE.md`：七步，缺一步就不算发布

版本号 → 变更日志 → **证据回填** → 全量回归 → 打包 → tag 推送 → 发布后。

写得最细的是两处容易出事的地方：

- **推送失败要分清两类**：`Empty reply from server` / `443 超时` 是网络问题，重试即可；`rejected (fetch first)` 是远端有新提交，要 `pull --rebase`，**不要 `--force`**。批次 2 和批次 3 都各遇到过一次网络抖动，第二次才推上去 —— 这类事不该每次靠临场判断。
- **回滚不要 `git reset --hard`**：按影响面分三种（代码 bug 打补丁版 / 装坏了就 `--uninstall` 并恢复备份 / 契约不兼容就升 major 并写迁移）。

### 4.2 `tests/test_release_meta.py`：一条护栏，四个坑

批次 3 结束时 README 写着 `0.2.0`，`tak/version.py` 还是 `0.1.0`，`/api/health` 也一直在报旧版本。**没有任何东西拦得住它**，所以它就这么错了整整一个批次。

现在拦四件事：

1. `tak/version.py` 是唯一版本出口 —— 除它之外 `tak/` 下不得出现版本号字面量（这条当场抓到了安装器的 `MARKER_VERSION = "0.3.0"`，改成 `= __version__`）。
2. README 首段版本号、`/api/health` 返回值都必须等于 `__version__`。
3. 插件目录名 == `manifest.json` 的 `id` == `core.py` 的 `TOOL_ID`；`min_version` 的键必须是 `dcc` 的子集（拼错一个键，版本门槛就永远不生效）。
4. README 里指向 `docs/*.md` 的链接必须存在。

### 4.3 CI

新增两步：安装器 `--list` / `--plan`（断言 dry-run 不写盘，否则 CI 会在 runner 的用户目录里写启动脚本）；动作检索与数据集工厂的 DCC-free 冒烟（含「两次 manifest 哈希必须相同」）。

## 5. 过程中发现并修掉的问题

| # | 问题 | 性质 |
|---|---|---|
| 1 | `tak/adapters/maya/menu.py` 不存在，安装块导入必然失败且静默 | 功能缺口 + 静默失败 |
| 2 | 菜单里调 `serve()` 会阻塞 DCC 主线程 | 设计缺陷 |
| 3 | `tak/version.py` 停在 `0.1.0`，README 已写 `0.2.0` | 元数据漂移（错了整整一个批次没人发现） |
| 4 | 安装器 `MARKER_VERSION` 又写了一份版本字面量 | 同一个坑的第二次踩 |
| 5 | `build_plan()` 传错层级（整个 fixture 包装）静默返回 0 个 pose | 易用性陷阱（已加明确 warning，但保留了「不自动解包」的取舍） |
| 6 | `grid` 采样用 25%/75% 分位，对称区间下恰好等于半极值、被去重吃掉导致整类消失 | 子任务实现阶段的真实 bug（自查发现并修正为 1/6、5/6） |
| 7 | **安装器在干净机器上永远探测不到 Maya** | 功能性 bug（会让 B21 在所有机器上直接失败） |

第 7 条是本批次最值得记的一个。安装器只检查 `<Documents>/maya/<version>/scripts` 是否存在，而该目录是 **Maya 首次需要时才创建** 的。本机装了 2023 / 2024 / 2026 三个版本，版本目录全在，`scripts` 一个都没有 —— 于是 `--list` 理直气壮地报「未检测到 Autodesk Maya 安装目录（可用 --root maya=PATH 指定）」。

也就是说：**这个安装器在每一台干净机器上都装不了 Maya**，而它自己的 59 个测试全绿 —— 因为测试都用 `--root` 显式指定了路径，绕过了探测逻辑。

修法是给路径模板加一个 `probe` 字段：探测证据用版本目录本身，写入根仍指向 `scripts`，由 writer 在写入前 `makedirs`（它本来就有这行）。补了 5 个用例，其中一条专门断言「版本目录存在但 scripts 不存在时，`apply` 必须把目录建出来」。

这类 bug 的教训不是「多加测试」，而是：**用真实机器跑一遍默认路径**。测试里的 `tmp_path` 永远比现实整洁。

第 6 条也值得单独说：`mirror` 在对称骨架下计数必然是 0 —— 镜像角度与对侧极值完全重合，按 `(joint, axis, angle)` 去重后必然合并。这不是 bug，是几何事实。处理方式是**把 mirror 标记并入存活行的 `tags`**（信息不丢、行不重复），并在 docstring 与测试里写清「对称骨架断言 extreme/mid/grid/rest 必备，mirror/random 可产」。测试如果不写清这条，下一个人会以为去重坏了。

## 6. 已知限制（如实记录）

| 限制 | 说明 |
|---|---|
| **3ds Max 2023+ / MoBu 2023+ 仍未真机验证** | 本机只有 Max 2019/2021、MoBu 2023 GUI。安装器能探测到真实 MoBu 路径，但**没在真机上 `--apply` 过**（B21–B24）。 |
| 安装器未在任何真机上执行过 `--apply` | 全部写入测试跑在 `tmp_path`。这是刻意的（不碰用户配置），但意味着「装完菜单真的会出现」完全未验证。探测本身已在本机实测（Maya 2026 / MoBu 2023 均正确识别）。 |
| 动作检索不解析 FBX | 数值特征依赖 sidecar 或文件名启发式，估算值有 `estimated` 标记。真实动作库的检索质量未验证（B25）。 |
| 数据集工厂只在 fixture 上跑过 | 240 个姿态是否穿模、极值角度是否合理，需要动画组在生产骨架上评审（B26）。 |
| ML Deformer 训练链路不存在 | UE 侧类型未暴露，本工具只到「数据集 + 门槛」。训练入口一旦可用，需要重新设计数据格式对接。 |
| 镜像姿态在对称骨架下为 0 | 几何必然，已并入 `tags`；非对称 limits 才会产出真实镜像行。 |
| 真实 AI provider 仍未验证 | B16–B19 全部待复验，与批次 3 相同。 |

## 7. 需要复验的部分

`docs/VALIDATION.md` F 节 B21–B26，`docs/WEB_PANEL.md` W35–W37，共 9 项。

最该先做的是 **B21 + B24**：安装 → 看菜单是否出现 → 卸载 → 比对文件哈希。这两项如果不过，整个安装器就是没用的，后面的 B22/B23 也不用试了。

## 8. 下一步（P7）

| 任务 | 内容 | 前置 |
|---|---|---|
| P7.1 动作 sidecar 导出器 | DCC 侧把 clip 的数值特征导出成 `.motion.json`，取代文件名启发式 | 无（可直接做） |
| P7.2 ML Deformer 训练链路 | UE 侧 `MLDeformerAsset` 可用后对接数据集格式；否则走 Deformer Graph / C++ 路径 | B2 / W3 先验证插件是否只是没启用 |
| P7.3 插件打包与自动更新 | wheel / 单文件 zip + 版本检查；安装器支持从包安装而不只是从仓库路径 | B21–B24 结论回来 |
| P7.4 全量真机回归 | 四端各跑一遍四端一致性回归 + 安装后菜单冒烟，回填全部 B 项 | P7.1–P7.3 |

P7 的排序原则：**先把「不确定性最大、影响面最广」的验证做掉**（真机安装），再做增量功能。如果 B21 挂了，P7.3 的打包方案要重做。
