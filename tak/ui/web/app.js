/* TAK Web 面板交互脚本。
 *
 * 约束：只用原生 fetch 与 DOM API，无任何框架、无外部依赖、完全离线。
 * 错误一律在页面内以 tak-state-fail 呈现，绝不使用 alert / confirm。
 */
(function () {
    "use strict";

    var DCC_LABEL = { maya: "Maya", max: "3ds Max", mobu: "MotionBuilder", unreal: "Unreal Engine" };
    var SEVERITY_ORDER = ["blocker", "error", "warning", "info"];
    var SEVERITY_LABEL = { blocker: "阻断", error: "错误", warning: "警告", info: "提示" };
    var THEME_KEY = "tak.web.theme";

    var state = {
        tools: [],
        selectedId: "",
        filter: "",
        lastResult: null,
        lastDuration: 0
    };

    function $(id) { return document.getElementById(id); }

    function el(tag, className, text) {
        var node = document.createElement(tag);
        if (className) { node.className = className; }
        if (text !== undefined && text !== null) { node.textContent = String(text); }
        return node;
    }

    function clear(node) {
        while (node && node.firstChild) { node.removeChild(node.firstChild); }
    }

    function fmtDuration(ms) {
        if (typeof ms !== "number" || !isFinite(ms) || ms < 0) { return "—"; }
        if (ms < 1000) { return Math.round(ms) + " ms"; }
        return (ms / 1000).toFixed(2) + " s";
    }

    // --------------------------------------------------------------- 网络
    function request(path, options) {
        var opts = options || {};
        var init = { method: opts.method || "GET", headers: { "Accept": "application/json" } };
        if (opts.body !== undefined) {
            init.headers["Content-Type"] = "application/json";
            init.body = JSON.stringify(opts.body);
        }
        return fetch(path, init).then(function (res) {
            return res.text().then(function (text) {
                var data = null;
                try { data = text ? JSON.parse(text) : {}; } catch (err) { data = null; }
                if (!res.ok || !data || data.ok === false) {
                    var message = (data && data.error) ? data.error : ("HTTP " + res.status);
                    throw new Error(message);
                }
                return data;
            });
        });
    }

    function setService(online, text) {
        var badge = $("serviceBadge");
        var status = $("statusService");
        badge.className = "tak-state " + (online ? "tak-state-pass" : "tak-state-fail");
        badge.textContent = text;
        status.textContent = text;
    }

    function showError(message) {
        var box = $("errorBox");
        $("errorText").textContent = String(message || "未知错误");
        box.hidden = false;
        $("resultState").className = "tak-state tak-state-fail";
        $("resultState").textContent = "失败";
    }

    function clearError() {
        $("errorBox").hidden = true;
        $("errorText").textContent = "";
    }

    // --------------------------------------------------------------- 主题
    function applyTheme(theme) {
        var root = document.documentElement;
        root.setAttribute("data-theme", theme === "light" ? "light" : "dark");
        var button = $("themeToggle");
        button.textContent = theme === "light" ? "深色" : "浅色";
        button.setAttribute("aria-pressed", theme === "light" ? "true" : "false");
        try { window.localStorage.setItem(THEME_KEY, theme); } catch (err) { /* 隐私模式忽略 */ }
    }

    function currentTheme() {
        try {
            var saved = window.localStorage.getItem(THEME_KEY);
            if (saved === "light" || saved === "dark") { return saved; }
        } catch (err) { /* 忽略 */ }
        return "dark";
    }

    // ------------------------------------------------------------- 工具列表
    function loadTools() {
        var dcc = $("dccSelect").value;
        var url = "/api/tools" + (dcc ? "?dcc=" + encodeURIComponent(dcc) : "");
        $("toolCount").textContent = "加载中…";
        return request(url).then(function (data) {
            state.tools = data.tools || [];
            $("toolCount").textContent = state.tools.length + " 个工具 · " + (data.dcc || "全部");
            renderTools();
            return state.tools;
        }).catch(function (err) {
            state.tools = [];
            $("toolCount").textContent = "加载失败";
            renderTools();
            setService(false, "服务不可用");
            showError("读取工具列表失败：" + err.message);
        });
    }

    function renderTools() {
        var list = $("toolList");
        var empty = $("toolEmpty");
        clear(list);
        var keyword = state.filter.toLowerCase();
        var visible = state.tools.filter(function (tool) {
            if (!keyword) { return true; }
            var hay = (tool.name || "") + " " + (tool.id || "") + " " + (tool.description || "");
            return hay.toLowerCase().indexOf(keyword) >= 0;
        });
        visible.forEach(function (tool) {
            var card = el("button", "tak-tool-card");
            card.type = "button";
            card.setAttribute("role", "option");
            card.setAttribute("aria-selected", tool.id === state.selectedId ? "true" : "false");
            if (tool.id === state.selectedId) { card.className += " is-active"; }

            var top = el("div", "tak-tool-card-top");
            top.appendChild(el("span", "tak-tool-name", tool.name || tool.id));
            top.appendChild(el("span", "tak-tool-id", tool.id));
            card.appendChild(top);

            if (tool.description) { card.appendChild(el("p", "tak-tool-desc", tool.description)); }

            var dccRow = el("div", "tak-tool-dcc");
            (tool.dcc || []).forEach(function (name) {
                dccRow.appendChild(el("span", "tak-dcc-tag", name));
            });
            if ((tool.dcc || []).length) { card.appendChild(dccRow); }

            card.addEventListener("click", function () { selectTool(tool.id); });
            list.appendChild(card);
        });

        if (!visible.length) {
            empty.hidden = false;
            empty.querySelector(".tak-empty-title").textContent = state.tools.length
                ? "没有匹配的工具" : "暂无可用工具";
            empty.querySelector(".tak-hint").textContent = state.tools.length
                ? "换一个关键词试试。" : "在 tools/ 下放置带 manifest.json 的插件，刷新后即可出现。";
        } else {
            empty.hidden = true;
        }
    }

    function findTool(id) {
        for (var i = 0; i < state.tools.length; i += 1) {
            if (state.tools[i].id === id) { return state.tools[i]; }
        }
        return null;
    }

    function selectTool(id) {
        var tool = findTool(id);
        if (!tool) { return; }
        state.selectedId = id;
        $("detailTitle").textContent = tool.name || tool.id;
        $("detailId").textContent = tool.id;
        $("detailDesc").textContent = tool.description || "（该工具未提供描述）";
        $("detailState").className = "tak-state";
        $("detailState").textContent = "就绪";

        var badges = $("detailDcc");
        clear(badges);
        (tool.dcc || []).forEach(function (name) {
            var tag = el("span", "tak-badge", DCC_LABEL[name] || name);
            badges.appendChild(tag);
        });
        if (tool.ui) { badges.appendChild(el("span", "tak-badge", "UI: " + tool.ui)); }
        if ((tool.permissions || []).length) {
            badges.appendChild(el("span", "tak-badge", "权限: " + tool.permissions.join(", ")));
        }
        var minVersion = tool.min_version || {};
        var versionText = Object.keys(minVersion).map(function (key) {
            return key + "≥" + minVersion[key];
        }).join(" · ");
        if (versionText) { badges.appendChild(el("span", "tak-badge", versionText)); }

        var runBtn = $("runBtn");
        runBtn.disabled = false;
        renderTools();
    }

    // --------------------------------------------------------------- 结果
    function renderResult(result, durationMs) {
        state.lastResult = result;
        state.lastDuration = durationMs;
        var passed = result.passed !== false;
        var counts = result.counts || {};
        var findings = result.findings || [];
        var total = 0;
        SEVERITY_ORDER.forEach(function (name) { total += Number(counts[name] || 0); });

        var stateBox = $("resultState");
        stateBox.className = "tak-state " + (passed ? "tak-state-pass" : "tak-state-fail");
        stateBox.textContent = passed ? "通过" : "不通过";

        $("resultMeta").textContent = (result.title || "体检结果")
            + " · " + (result.dcc || "—")
            + " · 规范 " + (result.spec_version || "—")
            + " · " + total + " 条";

        var summary = $("resultSummary");
        clear(summary);
        SEVERITY_ORDER.forEach(function (name) {
            var count = Number(counts[name] || 0);
            if (!count) { return; }
            var chip = el("span", "tak-chip tak-chip-" + name);
            chip.appendChild(el("span", null, SEVERITY_LABEL[name] + " "));
            chip.appendChild(el("b", null, count));
            summary.appendChild(chip);
        });
        if (!total) {
            var ok = el("span", "tak-chip tak-chip-success");
            ok.appendChild(el("span", null, "无异常"));
            summary.appendChild(ok);
        }

        var body = $("resultBody");
        clear(body);
        findings.forEach(function (item) {
            var severity = String(item.severity || "info").toLowerCase();
            var row = el("tr", "tak-row tak-sev-" + severity);
            var cellBadge = el("td");
            cellBadge.appendChild(el("span", "tak-badge", severity.toUpperCase()));
            row.appendChild(cellBadge);
            var cellRule = el("td");
            cellRule.appendChild(el("code", null, item.rule_id || "—"));
            row.appendChild(cellRule);
            row.appendChild(el("td", "tak-node", item.node || "—"));
            var cellMsg = el("td", "tak-msg");
            cellMsg.appendChild(document.createTextNode(item.message || ""));
            if (item.fix_hint) {
                cellMsg.appendChild(el("span", "tak-hint tak-fix", item.fix_hint));
            }
            row.appendChild(cellMsg);
            body.appendChild(row);
        });

        $("resultEmpty").hidden = findings.length > 0;
        $("resultTable").hidden = findings.length === 0;
        $("statusCount").textContent = total;
        $("statusDuration").textContent = fmtDuration(durationMs);
    }

    function resetResults() {
        state.lastResult = null;
        state.lastDuration = 0;
        clear($("resultSummary"));
        clear($("resultBody"));
        $("resultTable").hidden = true;
        $("resultEmpty").hidden = false;
        $("resultState").className = "tak-state";
        $("resultState").textContent = "待机";
        $("resultMeta").textContent = "尚未运行任何检查";
        $("statusCount").textContent = "0";
        $("statusDuration").textContent = "—";
    }

    // --------------------------------------------------------------- 运行
    function snapshotPayload() {
        var raw = $("snapshotInput").value.trim();
        if (!raw) { return null; }
        return JSON.parse(raw);
    }

    function markSnapshotState(text, ok) {
        var badge = $("snapshotState");
        badge.className = "tak-state " + (ok ? "tak-state-pass" : "tak-state-fail");
        badge.textContent = text;
    }

    function runTool() {
        if (!state.selectedId) { return; }
        clearError();
        var runBtn = $("runBtn");
        runBtn.disabled = true;
        $("detailState").className = "tak-state";
        $("detailState").textContent = "运行中";
        var started = Date.now();
        var payload = { tool_id: state.selectedId, dcc: $("dccSelect").value };
        try {
            var snap = snapshotPayload();
            if (snap) { payload.snapshot = snap; }
        } catch (err) {
            runBtn.disabled = false;
            $("detailState").textContent = "待机";
            showError("快照 JSON 解析失败：" + err.message);
            return;
        }
        request("/api/run", { method: "POST", body: payload }).then(function (data) {
            runBtn.disabled = false;
            $("detailState").className = "tak-state tak-state-pass";
            $("detailState").textContent = "已完成";
            renderResult(data, Date.now() - started);
        }).catch(function (err) {
            runBtn.disabled = false;
            $("detailState").className = "tak-state tak-state-fail";
            $("detailState").textContent = "失败";
            showError("运行失败：" + err.message);
        });
    }

    function evaluateSnapshot(snapshot) {
        clearError();
        var started = Date.now();
        var payload = { dcc: $("dccSelect").value };
        if (snapshot) { payload.snapshot = snapshot; }
        return request("/api/evaluate", { method: "POST", body: payload })
            .then(function (data) {
                renderResult(data, Date.now() - started);
                return data;
            }).catch(function (err) {
                showError("规范化验失败：" + err.message);
                throw err;
            });
    }

    // ------------------------------------------------- 批次 3：AI 工作台
    function aiState(text, ok) {
        var badge = $("aiState");
        var cls = "tak-state";
        if (ok === true) { cls += " tak-state-pass"; }
        if (ok === false) { cls += " tak-state-fail"; }
        badge.className = cls;
        badge.textContent = text;
    }

    function aiPrint(id, value) {
        $(id).textContent = (typeof value === "string")
            ? value : JSON.stringify(value, null, 2);
    }

    function aiGet(path, outputId, label) {
        aiState(label + " 运行中", null);
        return request(path).then(function (data) {
            aiPrint(outputId, data);
            aiState(label + " 完成", true);
            return data;
        }).catch(function (err) {
            aiPrint(outputId, { ok: false, error: err.message });
            aiState(label + " 失败", false);
        });
    }

    function aiPost(path, payload, outputId, label) {
        aiState(label + " 运行中", null);
        return request(path, { method: "POST", body: payload }).then(function (data) {
            aiPrint(outputId, data);
            aiState(label + " 完成", true);
            return data;
        }).catch(function (err) {
            aiPrint(outputId, { ok: false, error: err.message });
            aiState(label + " 失败", false);
        });
    }

    function agentSnapshot() {
        try { return snapshotPayload(); } catch (err) { return null; }
    }

    function agentCall(action, extra, label) {
        var payload = {
            action: action,
            session_id: "web",
            message: $("agentInput").value.trim()
        };
        var snap = agentSnapshot();
        if (snap) { payload.snapshot = snap; }
        if (extra) {
            Object.keys(extra).forEach(function (key) { payload[key] = extra[key]; });
        }
        return aiPost("/api/agent", payload, "agentOutput", label || "对话");
    }

    function parseJsonInput(id, label, outputId) {
        var raw = $(id).value.trim();
        if (!raw) { return null; }
        try { return JSON.parse(raw); } catch (err) {
            aiPrint(outputId, { ok: false, error: label + " JSON 解析失败：" + err.message });
            aiState(label + " 输入无效", false);
            return undefined;
        }
    }

    function bindAI() {
        var tabs = document.querySelectorAll(".tak-ai-tab");
        var panes = document.querySelectorAll(".tak-ai-pane");
        Array.prototype.forEach.call(tabs, function (tab) {
            tab.addEventListener("click", function () {
                Array.prototype.forEach.call(tabs, function (other) {
                    other.className = "tak-btn tak-ai-tab";
                    other.setAttribute("aria-selected", "false");
                });
                tab.className = "tak-btn tak-ai-tab is-active";
                tab.setAttribute("aria-selected", "true");
                var active = tab.getAttribute("data-tab");
                Array.prototype.forEach.call(panes, function (pane) {
                    pane.hidden = pane.getAttribute("data-pane") !== active;
                });
            });
        });

        $("agentSendBtn").addEventListener("click", function () {
            agentCall("chat", null, "对话");
        });
        $("agentPlanBtn").addEventListener("click", function () {
            var text = $("agentInput").value.trim();
            $("agentInput").value = (text ? text + " " : "") + "plan 生成修复计划";
            agentCall("chat", null, "修复计划");
        });
        $("agentDryRunBtn").addEventListener("click", function () {
            agentCall("confirm", {
                plan_id: $("agentPlanId").value.trim(),
                confirm: false, dry_run: true, granted: []
            }, "dry-run 预览");
        });
        $("agentConfirmBtn").addEventListener("click", function () {
            agentCall("confirm", {
                plan_id: $("agentPlanId").value.trim(),
                confirm: true, dry_run: false, granted: ["node.write"]
            }, "确认执行");
        });

        $("kbSearchBtn").addEventListener("click", function () {
            var query = $("kbInput").value.trim();
            if (!query) {
                aiPrint("kbOutput", { ok: false, error: "请输入检索词" });
                aiState("知识库检索待输入", false);
                return;
            }
            aiGet("/api/knowledge?q=" + encodeURIComponent(query) + "&top_k=3",
                  "kbOutput", "知识库检索");
        });

        $("visionRunBtn").addEventListener("click", function () {
            var frames = parseJsonInput("visionInput", "帧列表", "visionOutput");
            if (frames === undefined) { return; }
            var payload = { frames: frames || [] };
            var snap = agentSnapshot();
            if (snap) { payload.snapshot = snap; }
            aiPost("/api/vision", payload, "visionOutput", "抽帧诊断");
        });

        $("mappingRunBtn").addEventListener("click", function () {
            var data = parseJsonInput("mappingInput", "骨骼列表", "mappingOutput");
            if (data === undefined) { return; }
            data = data || {};
            aiPost("/api/mapping", {
                source_bones: data.source_bones || [],
                target_bones: data.target_bones || [],
                options: data.options || {}
            }, "mappingOutput", "骨骼映射");
        });
    }

    // --------------------------------------------------------------- 启动
    function bind() {
        $("themeToggle").addEventListener("click", function () {
            applyTheme(currentTheme() === "light" ? "dark" : "light");
        });

        $("dccSelect").addEventListener("change", function () {
            resetResults();
            loadTools();
        });

        $("toolFilter").addEventListener("input", function (event) {
            state.filter = event.target.value || "";
            renderTools();
        });

        $("runBtn").addEventListener("click", runTool);
        $("evaluateBtn").addEventListener("click", function () { evaluateSnapshot(null); });

        $("snapshotEvalBtn").addEventListener("click", function () {
            var snap;
            try {
                snap = snapshotPayload();
            } catch (err) {
                markSnapshotState("JSON 无效", false);
                showError("快照 JSON 解析失败：" + err.message);
                return;
            }
            if (!snap) {
                markSnapshotState("未载入快照", false);
                showError("请先粘贴一份 JSON 快照再体检。");
                return;
            }
            markSnapshotState("快照已载入", true);
            evaluateSnapshot(snap);
        });

        $("snapshotClearBtn").addEventListener("click", function () {
            $("snapshotInput").value = "";
            markSnapshotState("未载入快照", true);
        });
    }

    function boot() {
        applyTheme(currentTheme());
        bind();
        bindAI();
        request("/api/health").then(function (data) {
            setService(true, "在线 · " + (data.dcc || "离线"));
            $("statusVersion").textContent = "TAK " + (data.version || "—");
            return loadTools();
        }).catch(function (err) {
            setService(false, "服务不可用");
            showError("无法连接面板服务：" + err.message);
            renderTools();
        });
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", boot);
    } else {
        boot();
    }
}());
