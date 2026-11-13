# Web 面板与目标机验证清单（批次 2 · P3.4）

> 本文件是 [docs/VALIDATION.md](VALIDATION.md) 的**补充清单**，只覆盖 Web 面板（`tak/ui/web/`）与 UE 目标机交互部分。
> `VALIDATION.md` 不修改；两处编号独立（本文用 `W#`，原文用 `B#`）。
> 分工原则不变：**能在 headless / 纯 Python 里客观测到的已实测并标注；需要 GUI、真实资产或未安装版本的，列出逐项清单由你验证。**

---

## 1. Web 面板是什么

`tak/ui/web/` 提供一个**纯标准库**的单页工具面板：

| 文件 | 作用 |
|---|---|
| `tak/ui/web/panel.html` | 单页 UI：左侧工具列表、右侧主区、底部状态条、粘贴快照区 |
| `tak/ui/web/app.css` | 面板组件样式（只使用 `var(--tak-*)`，不出现任何字面颜色值） |
| `tak/ui/web/app.js` | 交互逻辑（原生 `fetch` + DOM API，无任何框架） |
| `tak/ui/web/server.py` | `http.server.ThreadingHTTPServer` 服务与 JSON API |
| `tak/ui/web/__init__.py` | 导出 `create_server` / `serve` / `main` 与面板路径常量 |

设计约束（已在代码与单测中固化）：

- **完全离线**：无 CDN、无外部字体、无外部图片、无第三方 JS 框架；`href` / `src` 只允许同源相对路径。
- **视觉同源**：颜色全部取自 `tak/ui/theme.css` 的 `var(--tak-*)`，类名沿用 `tak-panel` / `tak-header` / `tak-meta` / `tak-table` / `tak-row` / `tak-badge` / `tak-sev-*` / `tak-state` / `tak-hint` / `tak-state-pass` / `tak-state-fail`，与 Qt 端、HTML 报告同一套 token。
- **判定同源**：所有结论都走 `tak.core.runner`（`run_tool` / `evaluate`），Web 端不自己下判断。
- **不外泄异常**：服务端异常统一返回 `{"ok": false, "error": "..."}` + HTTP 400/404/500，traceback 只写服务端日志。

---

## 2. 三种启动方式

### 2.1 本地浏览器（开发机）

```bash
cd <仓库根>
python -m tak.ui.web.server --port 8765
```

启动后控制台打印：

```
[TAK][web] 面板已启动: http://127.0.0.1:8765/
[TAK][web] 版本 0.3.1 · DCC none
```

默认会自动打开系统浏览器；`--no-browser` 可关闭。参数：

| 参数 | 默认 | 说明 |
|---|---|---|
| `--host` | `127.0.0.1` | 监听地址 |
| `--port` | `8765` | 监听端口，`0` 表示随机 |
| `--ue` | 关 | UE 模式：强制绑定 `127.0.0.1`、关闭目录浏览、打印内嵌浏览器地址 |
| `--no-browser` | 关 | 启动后不自动打开本地浏览器 |
| `--repo-root` | 自动 | 仓库根目录，用于额外发现 `<repo_root>/tools` 插件 |

### 2.2 UE 内嵌浏览器（UE 5.8 / 5.9 编辑器内）

```bash
python -m tak.ui.web.server --ue --port 8765
```

`--ue` 模式会打印可直接粘贴的地址：

```
[TAK][web] 面板已启动: http://127.0.0.1:8765/
[TAK][web] UE 内嵌浏览器地址: http://127.0.0.1:8765/
[TAK][web] 在 UE 里用 WebBrowserWidget 打开上面的地址即可。
```

在 UE 侧打开面板的三条路径（按推荐度排序）：

1. **UE 编辑器内嵌 WebBrowserWidget**（推荐）：在 Editor Utility Widget / Slate 里放一个 `WebBrowser` 控件，`LoadURL("http://127.0.0.1:8765/")`。UE 的 WebBrowser 走 CEF，支持 `fetch`、CSS 变量与 `localStorage`。
2. **`TAK_WEB_PANEL_URL` 环境变量 + 菜单项**：设好 `TAK_WEB_PANEL_URL=http://127.0.0.1:8765/` 后，`tak.adapters.unreal.menu` 的 `open_web_panel` 会优先打开该地址；未设置时退化为打开落盘的 HTML 报告（当前实现）。
3. **UE 外部浏览器**：`SystemLibrary.launch_url(url)` 或系统浏览器打开同一地址（服务只监听本机）。

> **注意**：服务只绑定 `127.0.0.1`，UE 内嵌浏览器与编辑器同机同进程族，可直连；若要从另一台机器访问，需显式 `--host 0.0.0.0` 并自行承担网络暴露风险（本项目默认不做）。

### 2.3 UE commandlet（无头批处理）

commandlet 路径**不经过 HTTP 服务**，直接调 `tak.core.runner`，用于 CI 与批量体检：

```bash
# 离线：直接读 sidecar 快照，不需要引擎
python -m tak.adapters.unreal.commandlet --snapshot fixtures/bad/bad_001.json --out r.json

# UE 无头：从当前场景采集快照
UnrealEditor-Cmd.exe TakProbe.uproject -nullrhi -unattended \
  -ExecutePythonScript="from tak.adapters.unreal.commandlet import run_headless; run_headless()"
```

| 入口 | 用途 | 是否需要 GUI |
|---|---|---|
| `python -m tak.ui.web.server` | 本地浏览器交互式体检 | 否 |
| `python -m tak.ui.web.server --ue` | UE 内嵌浏览器 | 是（UE 侧 UI） |
| `python -m tak.adapters.unreal.commandlet` | CI / 批量无头体检 | 否 |

---

## 3. API 路由表

所有 JSON 响应使用 `ensure_ascii=False`（中文不转义）。失败一律 `{"ok": false, "error": "..."}`。

| 方法 | 路径 | 请求体 / 查询参数 | 成功响应 | 说明 |
|---|---|---|---|---|
| GET | `/` | — | HTML | 面板单页（`panel.html`） |
| GET | `/theme.css` | — | `text/css` | 生成的 `tak/ui/theme.css`（唯一颜色来源） |
| GET | `/app.js` | — | `application/javascript` | 面板交互脚本 |
| GET | `/app.css` | — | `text/css` | 面板组件样式 |
| GET | `/api/health` | — | `{"ok": true, "version": "0.1.0", "dcc": "...", "dccs": [...], "service": "tak-web-panel"}` | 存活探测；`dcc` 为当前进程所在 DCC（纯 Python 下为 `none`） |
| GET | `/api/tools` | `?dcc=maya` | `{"ok": true, "dcc": "maya", "tools": [...], "count": N}` | 工具清单，来自 `runner.list_tools(dcc)`；每项含 `id` / `name` / `dcc` / `min_version` / `ui` / `permissions` / `description` |
| POST | `/api/run` | `{"tool_id": "...", "dcc": "maya", "snapshot": {...}}` | `runner.run_tool(...).to_dict()` + `"ok": true` | 运行单个工具插件；`snapshot` 可省略（由适配器取当前场景） |
| POST | `/api/evaluate` | `{"snapshot": {...}, "dcc": "maya"}` | `runner.evaluate(...).to_dict()` + `"ok": true` | 不经插件，直接跑全部内置规则 |
| GET | `/api/report` | `?tool_id=...&dcc=...&severity=...` | 报告 dict + `html` + `markdown` | 附带渲染结果的完整报告；`tool_id` 为空时等价于 `evaluate` |

错误码：

| 状态码 | 触发条件 |
|---|---|
| 400 | 请求体不是合法 JSON / 不是 JSON 对象；`snapshot` 不是对象；缺少 `tool_id`；工具不支持该 DCC |
| 404 | 未知接口、未知路由、工具 id 不存在 |
| 405 | 方法不匹配（如对 `/api/health` 发 POST） |
| 500 | 服务端未预期异常（详情只写日志，返回体不含 traceback） |

**Python 侧纯函数入口**（不占端口，便于单测与脚本复用）：

```python
from tak.ui.web.server import handle_api

status, payload = handle_api("GET", "/api/tools?dcc=maya")
status, payload = handle_api("POST", "/api/evaluate", body={"snapshot": {...}})
```

---

## 4. UE 5.8 / 5.9 目标机逐项验证清单

> 「当前状态」列取值：
> - **已由 headless 实测** —— 已在无 GUI 环境客观验证并通过；
> - **未验证** —— 需要 GUI / 真实资产 / 未安装版本，必须由你实机确认后回填。
>
> 回填建议格式：`W3 ✅ UE 5.8.4 / 内嵌浏览器正常 / 备注：…`

### 4.1 UE 引擎与插件环境

| # | 验证动作 | 预期 | 由谁验证 | 当前状态 |
|---|---|---|---|---|
| W1 | 在 UE 5.8/5.9 编辑器中 **编辑 → 编辑器偏好 → 插件** 搜索并启用 `PythonScriptPlugin` | 插件勾选并重启后生效；`unreal` 模块可 import | 用户 | 未验证（headless 已在探测工程启用，结论见 `VALIDATION.md` A4） |
| W2 | 同上，确认 `EditorScriptingUtilities` 已启用 | `EditorUtilityLibrary` / `EditorAssetLibrary` 可用（`VALIDATION.md` A5 已确认存在） | 用户 | 未验证 |
| W3 | 在**真实项目**（非探测工程）的 `.uproject` 中启用 `MLDeformerFramework`、`NeuralMorphModel`、`DeformerGraph` 插件后，重跑 `validation/run_probe.py` | `MLDeformerAsset` / `NeuralMorphModel` / `VertexDeltaModel` / `MLDeformerComponent` / `DeformerGraphAsset` 由 `false` 变 `true`；若仍为 `false`，说明这些类型不以该 Python 名暴露，P6.2 需改走 C++ / 蓝图 / Deformer Graph 路径 | 用户 | 未验证（对应 `VALIDATION.md` B2；探测工程未启用这些插件，5.7/5.8 差异为空） |
| W4 | UE **5.9** 本机未安装：安装后跑 `python validation/run_probe.py --engine "<UE_5.9>\Engine\Binaries\Win64\UnrealEditor-Cmd.exe" --out validation/ue_probe_5.9.json` | `ok=true`；27 项 API 探测相对 5.8 **无新增缺失**；`EditorDialogLibrary` 依旧不存在 | 用户 | 未验证（本机无 5.9，全部 5.9 结论均为推断） |
| W5 | UE 内置 Python 版本确认：`<Engine>\Binaries\ThirdParty\Python3\Win64\python.exe -V` | 3.11.x；`tak` 可 import、`load_spec()['version'] == "1.0.0"` | 用户 | 已由 headless 实测（5.7 / 5.8 均为 3.11.8，见 `VALIDATION.md` A2 / A4） |

### 4.2 菜单注册与 GUI 点验

| # | 验证动作 | 预期 | 由谁验证 | 当前状态 |
|---|---|---|---|---|
| W6 | 在 UE 编辑器 **Output Log** 确认 `init_unreal.main()` 执行后打印 `[TAK] 初始化完成：engine=… menu=True` | `menu=True`；无 ImportError | 用户 | 未验证（headless commandlet 无 GUI，菜单注册路径未覆盖） |
| W7 | 在 UE **主菜单 Help → TAK** 下逐项点验三个菜单项：`Run Health Guard`、`Open Web Panel`、`Validate Import` | 菜单可见、可点击、无崩溃；`Run Health Guard` 弹出体检摘要并把 HTML 报告写到 `Saved/TAK/health_guard.html` | 用户 | 未验证（对应 `VALIDATION.md` B7） |
| W8 | 在**内容浏览器右键**已导入资产 → `TAK: Validate Import` | 弹出「校验通过 / 校验未通过」对话框；sidecar 与 UE Skeleton 的 diff 列出骨骼数 / 命名 / bind 矩阵 / 单位轴向差异 | 用户 | 未验证 |
| W9 | 重复加载编辑器 / 热重载 `init_unreal` 后再看菜单 | 菜单**不堆叠**（`unregister_owner_by_name` 先清理再挂），仍只有一组 TAK 项 | 用户 | 未验证 |
| W10 | 菜单项动作里故意触发异常（如指向不存在的 sidecar 路径） | 编辑器**不崩溃**，只在 Output Log 打 `[TAK][error] 菜单动作 … 失败` | 用户 | 未验证 |

### 4.3 UE 内嵌浏览器打开 Web 面板

| # | 验证动作 | 预期 | 由谁验证 | 当前状态 |
|---|---|---|---|---|
| W11 | 启动 `python -m tak.ui.web.server --ue --port 8765`，在 UE 内用 `WebBrowser` 控件 `LoadURL("http://127.0.0.1:8765/")` | 面板正常渲染：顶栏 + 左侧工具列表 + 右侧主区 + 底部状态条；CEF 控制台无 CSP / 混合内容报错 | 用户 | 未验证（浏览器端已在 Chrome 实测渲染、工具选择、快照体检、深浅主题切换与错误提示） |
| W12 | 在内嵌浏览器里切换目标 DCC 为 `Unreal Engine`，观察工具列表 | `/api/tools?dcc=unreal` 返回的插件按 unreal 过滤；空列表时显示「暂无可用工具」空态而非报错 | 用户 | 未验证 |
| W13 | 点击「运行工具」，观察右侧结果区 | 状态徽章变为「通过 / 不通过」，统计摘要出现各 severity 计数，结果表格按 `级别 / 规则 / 节点 / 说明` 渲染，空态在无结果时显示 | 用户 | 未验证（浏览器端已实测通过与不通过两种渲染） |
| W14 | 粘贴非法 JSON 到「快照体检」区并点「用快照体检」 | 页面**内联**提示 `tak-state-fail` 错误框，**不弹 alert**，编辑器不崩 | 用户 | 未验证（浏览器端已实测） |
| W15 | 粘贴 Export Bridge 的 sidecar 快照后点「用快照体检」 | 走 `POST /api/evaluate`，返回与 commandlet 完全一致的判定结论（同快照 → 同结果） | 用户 | 未验证 |
| W16 | 点击右上角「浅色 / 深色」按钮 | 根节点 `data-theme` 在 `dark` / `light` 间切换，配色随 `theme.css` 变量整体切换；刷新后仍保持（`localStorage`） | 用户 | 未验证（浏览器端已实测：深浅两套主题 + 刷新后记忆） |
| W17 | 关闭 UE 编辑器 | 无残留进程占用 8765 端口；面板进程需手动 Ctrl+C 结束 | 用户 | 未验证 |
| W18 | 在 UE 内嵌浏览器里打开面板的同时，另一个面板进程用 `--port 0` 启动 | 两个实例互不冲突（随机端口），互不串数据 | 用户 | 未验证（`create_server(port=0)` 已在单测中实测可分配随机端口） |

### 4.4 真实项目资产导入校验

| # | 验证动作 | 预期 | 由谁验证 | 当前状态 |
|---|---|---|---|---|
| W19 | 从 Maya 2023/2024 导出 FBX/USD → UE 导入同一角色资产 | 骨骼数、命名、bind 矩阵、单位、轴向与源端一致；UE 侧轴向（Z-up）被正确转换到 TAK 规范空间（Y-up / cm） | 用户 | 未验证（对应 `VALIDATION.md` B8） |
| W20 | 在面板「快照体检」区粘贴 W19 导出的 sidecar 快照并执行规范化验 | 判定为通过；如源端确有负缩放 / 命名违规，则精确列出对应规则与节点，且与 `python -m tak.adapters.unreal.commandlet --snapshot …` 的结论**逐条一致** | 用户 | 未验证 |
| W21 | 在真实项目中跑 `TAK Validate Import` 菜单项（sidecar vs UE Skeleton diff） | diff 只列出预期差异；骨骼缺失 / 新增 / 重命名被点名 | 用户 | 未验证 |
| W22 | 含中文路径与 UNC 网络盘路径的工程里跑一次完整导出 + 面板体检 | 无编码错误、无路径截断；JSON 响应中的中文正常显示（`ensure_ascii=False`） | 用户 | 未验证（对应 `VALIDATION.md` B10） |

### 4.5 IK Rig / Retargeter 资产

| # | 验证动作 | 预期 | 由谁验证 | 当前状态 |
|---|---|---|---|---|
| W23 | 在 UE 5.8/5.9 中确认 `IKRigDefinition` / `IKRetargeter` 类型存在 | `hasattr(unreal, "IKRigDefinition") == True`、`hasattr(unreal, "IKRetargeter") == True` | 用户 | 已由 headless 实测（5.8.4 与 5.7.2 均存在，见 `VALIDATION.md` A5 / `COMPATIBILITY.md` §3） |
| W24 | 导入含 IK Rig 的 FBX，检查面板工具列表与详情区徽章 | 工具的 `min_version.unreal` 与 `dcc` 徽章正确显示为 `unreal≥5.8`、`Unreal Engine`；版本不足的工具不出现 | 用户 | 未验证 |
| W25 | 在 UE 里创建 IK Retargeter 链（源 IK Rig → 目标 IK Rig）并跑体检 | 链路的骨骼命名、层级与命名空间规则按既有规则集判定，不产生误报 | 用户 | 未验证 |

### 4.6 其余 DCC 与工具

| # | 验证动作 | 预期 | 由谁验证 | 当前状态 |
|---|---|---|---|---|
| W26 | **3ds Max 2023+**（本机仅 2019/2021，未安装）：在 Max 的 Python 监听器执行 `import tak; from tak.core.spec import load_spec; print(load_spec()['version'])` | 输出 `1.0.0`，无 ImportError；`pymxs` 的 Skin 修改器 API 差异需记录 | 用户 | 未验证（本机无 2023+，实现只能 mock，对应 `VALIDATION.md` B4） |
| W27 | **MotionBuilder 2023**（GUI）：打开 MoBu 后运行 `import tak; from tak.compat.qt import QT_BINDING; print(QT_BINDING)` | 输出 `PySide2`，且无 Qt 崩溃 | 用户 | 未验证（对应 `VALIDATION.md` B5） |
| W28 | 在 Max / MoBu 里同样启动 Web 面板（`python -m tak.ui.web.server --port <空闲端口>`）并在系统浏览器打开 | 面板可用；DCC 选择切到 `3ds Max` / `MotionBuilder` 时工具列表按对应 manifest 过滤 | 用户 | 未验证 |
| W29 | **GUI 交互类操作**：在面板上执行写操作类工具（若插件声明 `permissions` 含写权限） | 写操作走 `tak.core.dsl` 的权限校验 → dry-run diff → 人工确认 → undo chunk → 失败整体回滚；面板不提供绕过确认的开关 | 用户 | 未验证（当前仓库 `tools/` 下无写操作插件，Web 面板只暴露已注册插件） |
| W30 | **ML Deformer 训练**（P6.2）：在真实项目启用 ML Deformer 相关插件后，验证 `MLDeformerAsset` 的训练入口与数据流 | `unreal` 侧暴露训练 API，训练产物能被 TAK 的快照/diff 链路读取 | 用户 | 未验证（能力清单含 `mldeformer.train`，但引擎侧未暴露类型，见 W3） |

---

## 5. 复现命令

```bash
# 纯 Python 全量回归（无需任何 DCC）
python -m pytest -q
python -m pytest tests/test_web_panel.py -q

# 面板启动（本地浏览器）
python -m tak.ui.web.server --port 8765

# 面板启动（UE 内嵌浏览器）
python -m tak.ui.web.server --ue --port 8765

# 服务可创建性自检
python -c "from tak.ui.web import create_server; s=create_server(port=0); print(s.server_address[1]); s.server_close()"

# UE 无头体检（不需要 GUI）
python -m tak.adapters.unreal.commandlet --snapshot fixtures/good/good_001.json --out r.json
python -m tak.adapters.unreal.commandlet --snapshot fixtures/bad/bad_001.json  --out r.json
```

---

## 6. 已知限制

1. **无鉴权**：面板只绑定 `127.0.0.1`，任何能访问该端口的进程都能调 API 并运行工具。仅适用于本机开发/编辑场景。
2. **工具清单依赖 `tools/`**：批次 2 起已有 5 个插件，批次 3 再增加 4 个（`agent_console` / `skeleton_mapper` / `defect_inspector` / `rig_kb`），`_template` 以 `_` 开头会被跳过；新增插件只要含合法 `manifest.json` 就会自动出现在页面，无需改框架。
3. **快照只读**：面板的「快照体检」只做判定，绝不写回场景；写操作必须走 `tak.core.dsl` 的确认流程。
4. **`/api/run` 无快照时**由适配器取当前场景；纯 Python 环境下降级为 `NullAdapter`，返回空快照结果。
5. **headless 无法验证任何 GUI 交互**：菜单点击、内嵌浏览器渲染、主题切换、真实资产往返、ML Deformer 训练全部归入 W6–W30，必须由你实机验证。
6. **UE 5.9 未安装**：所有 5.9 结论均为推断，必须经 W4 复验。

---

## 7. 批次 3：AI 工作台 API

页面新增「AI 工作台」区块（四个页签），后端新增四个端点，全部只依赖 `tak.core` 的纯 Python 模块：

| 端点 | 方法 | 请求 | 返回 | 安全边界 |
|---|---|---|---|---|
| `/api/agent` | POST | `{"action":"chat"\|"confirm"\|"history"\|"describe"\|"reset", "message":..., "session_id":"web", "snapshot":{...}, "plan_id":..., "confirm":bool, "dry_run":bool, "granted":[...]}` | `{"ok","action","session_id","result"}` | `chat` 绝不写场景；`confirm` 服务端强制二次校验：仅当 `confirm=true` + `dry_run=false` + `granted` 含白名单 `node.write` 时才可能执行，否则一律退化为 dry-run 预览。会话状态在进程内按 `session_id` 保存 |
| `/api/knowledge` | GET / POST | `?q=权重归一化&top_k=3` 或 `{"query":...,"top_k":3}` | `{"ok","query","top_k","hits","count"}`，hits 含 doc_id/title/section/excerpt/citation | 本地离线倒排索引，不联网、不写缓存、结果可复现 |
| `/api/vision` | POST | `{"frames":[{"frame":1,"image":"data:image/png;base64,..."}], "snapshot":{...}, "options":{}}` | `{"ok","result"}`，result 含 candidates / rejected / warnings / source | 帧号、类别、置信度、骨骼名由确定性校验过滤；离线标注 `deterministic_fallback`，不伪称模型识别 |
| `/api/mapping` | POST | `{"source_bones":[...],"target_bones":[...],"options":{}}` | `{"ok","result"}`，result 含 entries / conflicts / unresolved / stats | 冲突保留供人工确认，不自动裁决；AI 候选只补词典未覆盖项 |
| `/api/motion` | POST | `{"query":"走路 循环","top_k":8,"options":{"motion_type":"walk","filters":{"min_duration":0.8,"loopable":true}}}` | `{"ok","count","hits","stats","warning"}`，hit 含 clip_id / name / score / text_score / type_score / motion_type / duration / loopable / bones / rank / matched_terms / why | `query` 为空表示列出全部；`filters` 是**硬过滤**（不满足直接排除，不是降权）；`min_duration`/`max_duration`/`min_fps` 强转 float，非法值丢弃而非当成 0 |

`GET /api/health` 新增 `ai` 字段，逐项报告 `agent` / `knowledge` / `vision` / `mapping` 四个模块是否就绪；模块缺失时对应端点返回 **503** 与 JSON 错误，不会把 traceback 发给浏览器。

复现（纯 Python，无需任何 DCC）：

```bash
python -m tak.ui.web.server --port 8765 --no-browser
curl -s http://127.0.0.1:8765/api/health
curl -s -X POST http://127.0.0.1:8765/api/agent -H "Content-Type: application/json" -d "{\"action\":\"chat\",\"message\":\"检查这个骨架\"}"
curl -s "http://127.0.0.1:8765/api/knowledge?q=%E6%9D%83%E9%87%8D%E5%BD%92%E4%B8%80%E5%8C%96"
```

> W31–W34：AI 工作台前四个页签在 Chrome / UE 内嵌浏览器中的真实交互（含深浅主题、错误提示、会话保持、确认执行按钮）需要用户复验，headless 回归只覆盖 `handle_api` 的纯函数路径。
>
> W35–W37（批次 4）：**动作检索页签**的真实交互与深链 `?tab=motion`、**进程内面板**（DCC 菜单点「Web 面板」不阻塞宿主、重复点击复用同一实例、退出时能停）、**安装后**从 DCC 菜单进入面板的完整链路。headless 只覆盖 `serve_in_thread` 的单例与地址拼接。
