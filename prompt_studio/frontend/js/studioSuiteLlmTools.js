(function () {
    "use strict";

    const API_URL = "/studio-suite/prompt-studio/llm_translate";
    const STATUS_URL = "/studio-suite/prompt-studio/llm_status";
    const PRELOAD_URL = "/studio-suite/prompt-studio/llm_preload";
    let queueBusy = false;
    let translationBusy = false;
    let preloadBusy = false;
    let workerWarm = false;
    let queueMonitorStarted = false;
    let preloadTimer = null;
    let statusPollTimer = null;
    let dictionaryState = { ready: false, mode: "builtin_only", manual_aliases: 0, indexed_aliases: 0 };
    const DIRECTION_KEY = "studio_suite_translate_direction";
    const CONTENT_TYPE_KEY = "studio_suite_translate_content_type";

    function byId(id) {
        return document.getElementById(id);
    }

    function toast(message, type) {
        if (window.toastr && typeof window.toastr[type || "info"] === "function") {
            window.toastr[type || "info"](message);
            return;
        }
        console.log("[Prompt Studio]", message);
    }

    function currentTextArea(kind) {
        return kind === "negative" ? byId("weilin_prompt_text_neg_input") : byId("weilin_prompt_text_input");
    }

    function setProgress(active, startedAt, label) {
        const wrap = byId("studio-suite-llm-progress");
        const text = byId("studio-suite-llm-progress-text");
        if (!wrap || !text) return;
        if (!active) {
            wrap.classList.remove("is-active");
            text.textContent = "空闲";
            return;
        }
        wrap.classList.add("is-active");
        const elapsed = Math.max(0, Math.floor((Date.now() - startedAt) / 1000));
        text.textContent = `${label || "LLM 推理中"} ${elapsed}s`;
    }

    function setStatusChip(name, label, state) {
        const chip = document.querySelector(`[data-studio-suite-status="${name}"]`);
        if (!chip) return;
        chip.textContent = label;
        chip.dataset.state = state || "idle";
    }

    function updateRuntimeStatus() {
        const dictionaryReady = Boolean(dictionaryState && dictionaryState.ready);
        const aliasCount = dictionaryReady
            ? Number(dictionaryState.indexed_aliases || 0).toLocaleString()
            : Number(dictionaryState.manual_aliases || 0).toLocaleString();
        setStatusChip(
            "dictionary",
            dictionaryReady ? `词典 ${aliasCount}` : `基础词典 ${aliasCount}`,
            dictionaryReady ? "ready" : "limited"
        );
        setStatusChip(
            "model",
            preloadBusy ? "模型加载中" : workerWarm ? "模型已预热" : "模型未加载",
            preloadBusy ? "busy" : workerWarm ? "ready" : "idle"
        );
        setStatusChip(
            "queue",
            queueBusy ? "队列占用" : "队列空闲",
            queueBusy ? "blocked" : "ready"
        );
    }

    function updateLlmControls() {
        const disabled = queueBusy || translationBusy || preloadBusy;
        document.querySelectorAll("[data-studio-suite-llm-translate]").forEach((button) => {
            button.disabled = disabled;
            button.classList.toggle("is-queue-blocked", queueBusy);
            button.title = queueBusy ? "ComfyUI 有运行中或等待中的任务，翻译已暂停" : "";
        });
        const directionSelect = byId("studio-suite-llm-direction");
        const contentTypeSelect = byId("studio-suite-llm-content-type");
        if (directionSelect) directionSelect.disabled = translationBusy || preloadBusy;
        if (contentTypeSelect) contentTypeSelect.disabled = translationBusy || preloadBusy;
        if (queueBusy) {
            const wrap = byId("studio-suite-llm-progress");
            const text = byId("studio-suite-llm-progress-text");
            if (wrap && text) {
                wrap.classList.add("is-active", "is-queue-blocked");
                text.textContent = "跑图队列占用，翻译暂停";
            }
        } else {
            const wrap = byId("studio-suite-llm-progress");
            if (wrap) wrap.classList.remove("is-queue-blocked");
            if (!translationBusy && !preloadBusy) setProgress(false, Date.now());
        }
        updateRuntimeStatus();
    }

    async function preloadLlm() {
        if (queueBusy || translationBusy || preloadBusy || workerWarm) return;
        preloadBusy = true;
        updateLlmControls();
        const startedAt = Date.now();
        setProgress(true, startedAt, "正在预热翻译模型");
        const timer = window.setInterval(() => setProgress(true, startedAt, "正在预热翻译模型"), 500);
        try {
            const response = await fetch(PRELOAD_URL, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    context_size: 512,
                    keep_warm_seconds: 600,
                    n_gpu_layers: 999,
                    max_safe_gpu_layers: 8,
                    n_batch: 128,
                }),
            });
            const payload = await response.json().catch(() => ({}));
            if (response.status === 409 || payload.error === "comfy_queue_busy") {
                queueBusy = true;
                return;
            }
            if (!response.ok || !payload.success) throw new Error(payload.error || `HTTP ${response.status}`);
            workerWarm = true;
        } catch (error) {
            if (!queueBusy) console.warn("[Prompt Studio] LLM preload failed:", error);
        } finally {
            window.clearInterval(timer);
            preloadBusy = false;
            updateLlmControls();
        }
    }

    function schedulePreload(delay) {
        if (preloadTimer) window.clearTimeout(preloadTimer);
        preloadTimer = window.setTimeout(() => {
            preloadTimer = null;
            preloadLlm();
        }, Math.max(0, delay || 0));
    }

    function startQueueMonitor() {
        if (queueMonitorStarted) return;
        queueMonitorStarted = true;
        const poll = async () => {
            try {
                const response = await fetch(STATUS_URL, { cache: "no-store" });
                if (!response.ok) throw new Error(`HTTP ${response.status}`);
                const payload = await response.json();
                const wasBusy = queueBusy;
                queueBusy = Boolean(payload.queue && payload.queue.busy);
                workerWarm = Boolean(payload.worker && payload.worker.alive);
                dictionaryState = payload.dictionary || dictionaryState;
                if (queueBusy) {
                    workerWarm = false;
                    if (!wasBusy) {
                        fetch("/studio-suite/prompt-studio/llm_unload", {
                            method: "POST",
                            headers: { "Content-Type": "application/json" },
                            body: "{}",
                        }).catch(() => {});
                    }
                } else if (wasBusy) {
                    schedulePreload(1800);
                }
                updateLlmControls();
            } catch (error) {
                console.debug("[Prompt Studio] queue status unavailable:", error);
            } finally {
                const nextDelay = queueBusy || translationBusy || preloadBusy ? 900 : 2500;
                statusPollTimer = window.setTimeout(poll, nextDelay);
            }
        };
        poll();
        schedulePreload(1200);
    }

    function dispatchTextChange(textarea) {
        if (!textarea) return;
        textarea.dispatchEvent(new Event("input", { bubbles: true }));
        textarea.dispatchEvent(new Event("change", { bubbles: true }));
        if (typeof window.syncPromptToHost === "function") {
            window.syncPromptToHost(false);
        }
    }

    function currentDirection() {
        const direction = byId("studio-suite-llm-direction");
        const contentType = byId("studio-suite-llm-content-type");
        const directionValue = direction && direction.value ? direction.value : "zh_to_en";
        const typeValue = contentType && contentType.value ? contentType.value : "text";
        return `${directionValue}_${typeValue}`;
    }

    function splitPromptParts(text) {
        const source = String(text || "").trim();
        if (!source) return [];
        const parts = [];
        let current = "";
        let depth = 0;
        let escaped = false;
        for (const char of source) {
            if (escaped) {
                current += char;
                escaped = false;
                continue;
            }
            if (char === "\\") {
                current += char;
                escaped = true;
                continue;
            }
            if (char === "(" || char === "[" || char === "{") depth += 1;
            if ((char === ")" || char === "]" || char === "}") && depth > 0) depth -= 1;
            if ((char === "," || char === "\n") && depth === 0) {
                const item = current.trim();
                if (item) parts.push(item);
                current = "";
                continue;
            }
            current += char;
        }
        const tail = current.trim();
        if (tail) parts.push(tail);
        return parts.length ? parts : [source];
    }

    function normalizePromptPart(text) {
        return String(text || "")
            .replace(/\s+/g, " ")
            .replace(/\s*,\s*/g, ", ")
            .trim()
            .toLowerCase();
    }

    function buildPairMap(sourceText, translatedText, pairs) {
        const map = new Map();
        const safePairs = Array.isArray(pairs) ? pairs : [];
        for (const pair of safePairs) {
            if (!pair || typeof pair !== "object") continue;
            const source = String(pair.source || "").trim();
            const translated = String(pair.translated || "").trim();
            if (!source || !translated) continue;
            map.set(normalizePromptPart(translated), source);
        }

        const sourceParts = splitPromptParts(sourceText);
        const translatedParts = splitPromptParts(translatedText);
        const count = Math.min(sourceParts.length, translatedParts.length);
        for (let i = 0; i < count; i += 1) {
            const source = sourceParts[i];
            const translated = translatedParts[i];
            if (source && translated && !map.has(normalizePromptPart(translated))) {
                map.set(normalizePromptPart(translated), source);
            }
        }
        return map;
    }

    function promptTagRoots(kind) {
        const roots = Array.from(document.querySelectorAll(".physton-prompt .prompt-tags"));
        if (!roots.length) return [];
        if (kind === "negative") {
            return [roots[1] || roots[roots.length - 1]].filter(Boolean);
        }
        return [roots[0]].filter(Boolean);
    }

    function readPromptTagText(tagEl) {
        const value = tagEl.querySelector(".prompt-tag-value");
        if (!value) return "";
        const clone = value.cloneNode(true);
        clone.querySelectorAll("button, svg, .btn-tag-delete, .translate-to-local").forEach((node) => node.remove());
        return clone.textContent.replace(/[×✕]$/g, "").trim();
    }

    function ensureLocalLanguage(tagEl) {
        let wrap = tagEl.querySelector(".prompt-local-language");
        if (!wrap) {
            wrap = document.createElement("div");
            wrap.className = "prompt-local-language studio-suite-local-language";
            const main = tagEl.querySelector(".prompt-tag-main") || tagEl;
            if (main.parentNode) {
                main.parentNode.insertBefore(wrap, main.nextSibling);
            } else {
                tagEl.appendChild(wrap);
            }
        }
        let local = wrap.querySelector(".local-language");
        if (!local) {
            local = document.createElement("span");
            local.className = "local-language";
            wrap.appendChild(local);
        }
        return local;
    }

    function applyLocalLanguageToPromptTags(kind, sourceText, translatedText, pairs) {
        const map = buildPairMap(sourceText, translatedText, pairs);
        const translatedParts = splitPromptParts(translatedText);
        const sourceParts = splitPromptParts(sourceText);
        const roots = promptTagRoots(kind);
        for (const root of roots) {
            const tags = Array.from(root.querySelectorAll(".prompt-tags-list .prompt-tag"));
            tags.forEach((tagEl, index) => {
                const tagText = readPromptTagText(tagEl);
                if (!tagText) return;
                const localText =
                    map.get(normalizePromptPart(tagText)) ||
                    (normalizePromptPart(tagText) === normalizePromptPart(translatedParts[index]) ? sourceParts[index] : "") ||
                    "";
                if (!localText || normalizePromptPart(localText) === normalizePromptPart(tagText)) return;
                const local = ensureLocalLanguage(tagEl);
                local.textContent = localText;
                tagEl.setAttribute("data-studio-suite-local-source", localText);
            });
        }
    }

    function scheduleLocalLanguageSync(kind, sourceText, translatedText, pairs) {
        [80, 250, 700].forEach((delay) => {
            window.setTimeout(() => applyLocalLanguageToPromptTags(kind, sourceText, translatedText, pairs), delay);
        });
    }

    async function translateText(kind) {
        if (queueBusy) {
            toast("跑图队列占用中，翻译已暂停", "warning");
            return;
        }
        const textarea = currentTextArea(kind);
        const text = (textarea && textarea.value ? textarea.value : "").trim();
        if (!textarea || !text) {
            toast("没有可翻译的文本", "warning");
            return;
        }

        const button = document.querySelector(`[data-studio-suite-llm-translate="${kind}"]`);
        const oldText = button ? button.textContent : "";
        const startedAt = Date.now();
        let timer = null;
        translationBusy = true;
        if (button) button.textContent = "翻译中...";
        updateLlmControls();
        setProgress(true, startedAt);
        timer = window.setInterval(() => setProgress(true, startedAt), 500);

        try {
            const response = await fetch(API_URL, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    text,
                    direction: currentDirection(),
                    translation_mode: currentDirection().endsWith("_text") ? "sentence" : "tags",
                    target_profile: "prompt_studio_bilingual",
                    dynamic_runtime: true,
                    keep_warm: true,
                    keep_warm_seconds: 600,
                    temperature: currentDirection().endsWith("_text") ? 0.22 : 0.18,
                    n_gpu_layers: 999,
                    max_safe_gpu_layers: 8,
                    n_batch: 256,
                    unload_after_run: false,
                }),
            });
            const payload = await response.json().catch(() => ({}));
            if (!response.ok || !payload.success) {
                if (response.status === 409 || payload.error === "comfy_queue_busy") {
                    queueBusy = true;
                    throw new Error("跑图队列已启动，翻译模型已释放");
                }
                throw new Error(payload.error || `HTTP ${response.status}`);
            }
            workerWarm = true;
            const translatedText = payload.translated_text || "";
            textarea.value = translatedText;
            dispatchTextChange(textarea);
            scheduleLocalLanguageSync(
                kind,
                payload.source_text || text,
                translatedText,
                payload.translation_pairs || (payload.json_result && payload.json_result.translation_pairs) || []
            );
            const dictionaryCount = Array.isArray(payload.dictionary_matches) ? payload.dictionary_matches.length : 0;
            toast(dictionaryCount ? `翻译完成，词典保护 ${dictionaryCount} 项` : "翻译完成", "success");
        } catch (error) {
            toast(`LLM 翻译失败：${error.message || error}`, "error");
        } finally {
            if (timer) window.clearInterval(timer);
            translationBusy = false;
            if (button) {
                button.textContent = oldText;
            }
            updateLlmControls();
        }
    }

    async function unloadLlm() {
        const button = document.querySelector("[data-studio-suite-llm-unload]");
        const oldText = button ? button.textContent : "";
        if (button) {
            button.disabled = true;
            button.textContent = "释放中...";
        }
        try {
            const response = await fetch("/studio-suite/prompt-studio/llm_unload", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({}),
            });
            const payload = await response.json().catch(() => ({}));
            if (!response.ok || !payload.success) {
                throw new Error(payload.error || `HTTP ${response.status}`);
            }
            toast("Prompt Studio LLM 已释放", "success");
        } catch (error) {
            toast(`释放失败：${error.message || error}`, "error");
        } finally {
            if (button) {
                button.disabled = false;
                button.textContent = oldText;
            }
        }
    }

    function makeButton(kind, label) {
        const button = document.createElement("button");
        button.type = "button";
        button.className = "studio-suite-llm-btn";
        button.dataset.studioSuiteLlmTranslate = kind;
        button.textContent = label;
        button.addEventListener("click", () => translateText(kind));
        return button;
    }

    function installToolbar() {
        if (document.getElementById("studio-suite-llm-tools")) return;

        const toolbar = document.createElement("div");
        toolbar.id = "studio-suite-llm-tools";
        toolbar.className = "studio-suite-llm-tools";
        const modeGroup = document.createElement("div");
        modeGroup.className = "studio-suite-translate-mode";
        const directionSelect = document.createElement("select");
        directionSelect.id = "studio-suite-llm-direction";
        directionSelect.className = "studio-suite-llm-select";
        directionSelect.innerHTML = [
            '<option value="zh_to_en">中文 → 英文</option>',
            '<option value="en_to_zh">英文 → 中文</option>',
        ].join("");
        const contentTypeSelect = document.createElement("select");
        contentTypeSelect.id = "studio-suite-llm-content-type";
        contentTypeSelect.className = "studio-suite-llm-select";
        contentTypeSelect.innerHTML = [
            '<option value="text">自然语言整句</option>',
            '<option value="tags">Danbooru Tag / 角色名</option>',
        ].join("");
        const savedDirection = localStorage.getItem(DIRECTION_KEY);
        const savedContentType = localStorage.getItem(CONTENT_TYPE_KEY);
        directionSelect.value = ["zh_to_en", "en_to_zh"].includes(savedDirection) ? savedDirection : "zh_to_en";
        contentTypeSelect.value = ["text", "tags"].includes(savedContentType) ? savedContentType : "text";
        directionSelect.addEventListener("change", () => localStorage.setItem(DIRECTION_KEY, directionSelect.value));
        contentTypeSelect.addEventListener("change", () => localStorage.setItem(CONTENT_TYPE_KEY, contentTypeSelect.value));
        modeGroup.appendChild(directionSelect);
        modeGroup.appendChild(contentTypeSelect);
        toolbar.appendChild(modeGroup);
        toolbar.appendChild(makeButton("positive", "翻译正向"));
        toolbar.appendChild(makeButton("negative", "翻译反向"));
        const unloadButton = document.createElement("button");
        unloadButton.type = "button";
        unloadButton.className = "studio-suite-llm-btn studio-suite-llm-btn-muted";
        unloadButton.dataset.studioSuiteLlmUnload = "1";
        unloadButton.textContent = "释放LLM";
        unloadButton.addEventListener("click", unloadLlm);
        toolbar.appendChild(unloadButton);
        const status = document.createElement("div");
        status.className = "studio-suite-runtime-status";
        status.innerHTML = [
            '<span class="studio-suite-status-chip" data-studio-suite-status="dictionary">基础词典</span>',
            '<span class="studio-suite-status-chip" data-studio-suite-status="model">模型未加载</span>',
            '<span class="studio-suite-status-chip" data-studio-suite-status="queue">队列空闲</span>',
        ].join("");
        toolbar.appendChild(status);
        const progress = document.createElement("div");
        progress.id = "studio-suite-llm-progress";
        progress.className = "studio-suite-llm-progress";
        progress.innerHTML = '<div class="studio-suite-llm-progress-bar"></div><span id="studio-suite-llm-progress-text">空闲</span>';
        toolbar.appendChild(progress);

        const target =
            document.querySelector(".topBar .topBox") ||
            document.querySelector(".topBar") ||
            document.body;
        target.appendChild(toolbar);
    }

    function hideLegacyAiEntrypoints(root) {
        const scope = root && root.querySelectorAll ? root : document;
        const selectors = [
            ".icon-svg-chatgpt",
            ".physton-chatgpt-prompt",
            ".llm-btn",
            ".physton-packages-state",
        ];
        for (const selector of selectors) {
            scope.querySelectorAll(selector).forEach((node) => {
                const container = node.closest(".extend-btn-item, .physton-chatgpt-prompt, .physton-packages-state") || node;
                if (container.closest && container.closest("#studio-suite-llm-tools")) return;
                container.style.display = "none";
                container.setAttribute("data-studio-suite-hidden-legacy-ai", "1");
            });
        }
    }

    function installLegacyUiObserver() {
        hideLegacyAiEntrypoints(document);
        if (window.__studioSuiteLegacyUiObserver) return;
        window.__studioSuiteLegacyUiObserver = new MutationObserver((mutations) => {
            for (const mutation of mutations) {
                mutation.addedNodes.forEach((node) => {
                    if (node.nodeType === 1) hideLegacyAiEntrypoints(node);
                });
            }
        });
        window.__studioSuiteLegacyUiObserver.observe(document.body, {
            childList: true,
            subtree: true,
        });
    }

    function installStyle() {
        if (document.getElementById("studio-suite-llm-tools-style")) return;
        const style = document.createElement("style");
        style.id = "studio-suite-llm-tools-style";
        style.textContent = `
            .studio-suite-llm-tools {
                display: flex;
                align-items: center;
                gap: 8px;
                flex-wrap: wrap;
            }
            .studio-suite-translate-mode {
                display: inline-flex;
                align-items: center;
                gap: 6px;
                padding: 3px;
                border: 1px solid #44444d;
                border-radius: 18px;
                background: #202027;
            }
            .studio-suite-llm-btn {
                background: #2f3a32;
                color: #a7f3c3;
                border: 1px solid #4f7b5a;
                border-radius: 18px;
                padding: 7px 12px;
                font-size: 12px;
                font-weight: 600;
                cursor: pointer;
            }
            .studio-suite-llm-btn:hover {
                background: #3c4b40;
                border-color: #72a77d;
            }
            .studio-suite-llm-btn:disabled {
                opacity: 0.55;
                cursor: wait;
            }
            .studio-suite-llm-btn.is-queue-blocked:disabled {
                color: #8b8b94;
                background: #303036;
                border-color: #494950;
                opacity: 0.78;
                cursor: not-allowed;
                filter: grayscale(1);
            }
            .studio-suite-llm-progress.is-queue-blocked {
                color: #b7b7bf;
            }
            .studio-suite-llm-select {
                height: 30px;
                max-width: 180px;
                border-radius: 16px;
                border: 0;
                background: #2b2b33;
                color: #e8e8ef;
                padding: 0 10px;
                font-size: 12px;
                outline: none;
            }
            .studio-suite-llm-select:focus {
                border-color: #e8a630;
                box-shadow: 0 0 0 2px rgba(232, 166, 48, 0.16);
            }
            .studio-suite-llm-btn-muted {
                background: #34343a;
                color: #d4d4dd;
                border-color: #555561;
            }
            .studio-suite-llm-btn-muted:hover {
                background: #404049;
                border-color: #777785;
            }
            .studio-suite-runtime-status {
                display: inline-flex;
                align-items: center;
                gap: 5px;
                padding-left: 2px;
            }
            .studio-suite-status-chip {
                display: inline-flex;
                align-items: center;
                min-height: 22px;
                padding: 0 8px;
                border: 1px solid #4b4b54;
                border-radius: 999px;
                background: #292930;
                color: #bdbdc8;
                font-size: 10px;
                white-space: nowrap;
            }
            .studio-suite-status-chip[data-state="ready"] {
                color: #9ce7b7;
                border-color: #436b50;
                background: #26362b;
            }
            .studio-suite-status-chip[data-state="limited"] {
                color: #e6bd72;
                border-color: #735d37;
                background: #393225;
            }
            .studio-suite-status-chip[data-state="busy"] {
                color: #8fd7ee;
                border-color: #3c6572;
                background: #24343a;
            }
            .studio-suite-status-chip[data-state="blocked"] {
                color: #c0c0c8;
                border-color: #56565e;
                background: #303036;
            }
            .studio-suite-llm-progress {
                display: none;
                align-items: center;
                gap: 8px;
                min-width: 170px;
                color: #d6eadc;
                font-size: 12px;
            }
            .studio-suite-llm-progress.is-active {
                display: flex;
            }
            .studio-suite-llm-progress-bar {
                position: relative;
                width: 72px;
                height: 6px;
                overflow: hidden;
                border-radius: 999px;
                background: rgba(167, 243, 195, 0.16);
                border: 1px solid rgba(167, 243, 195, 0.28);
            }
            .studio-suite-llm-progress-bar::after {
                content: "";
                position: absolute;
                top: 0;
                bottom: 0;
                width: 38%;
                border-radius: inherit;
                background: linear-gradient(90deg, transparent, #a7f3c3, transparent);
                animation: studio-suite-llm-progress-sweep 1.05s infinite ease-in-out;
            }
            @keyframes studio-suite-llm-progress-sweep {
                0% { left: -45%; }
                100% { left: 110%; }
            }
            .physton-prompt .prompt-tags .prompt-tags-list .prompt-tag {
                align-items: flex-start !important;
            }
            .physton-prompt .prompt-tags .prompt-tags-list .prompt-tag .prompt-tag-main {
                width: 100% !important;
                min-width: 0 !important;
            }
            .physton-prompt .prompt-tags .prompt-tags-list .prompt-tag .prompt-tag-main .prompt-tag-edit {
                min-height: 28px !important;
            }
            .physton-prompt .prompt-tags .prompt-tags-list .prompt-tag .prompt-tag-main .prompt-tag-edit .prompt-tag-value {
                box-sizing: border-box !important;
                min-height: 28px !important;
                line-height: 1.25 !important;
                align-items: center !important;
                white-space: normal !important;
                word-break: break-word !important;
                overflow-wrap: anywhere !important;
            }
            .physton-prompt .prompt-tags .prompt-tags-list .prompt-tag .prompt-tag-main .prompt-tag-edit .prompt-tag-value .character {
                white-space: normal !important;
                word-break: break-word !important;
                overflow-wrap: anywhere !important;
            }
            .physton-prompt .prompt-tags .prompt-tags-list .prompt-tag .prompt-local-language {
                box-sizing: border-box !important;
                width: calc(100% - 16px) !important;
                margin: 2px 0 0 0 !important;
                padding: 0 4px !important;
                justify-content: flex-start !important;
                align-items: center !important;
                min-height: 16px !important;
                line-height: 1.2 !important;
            }
            .physton-prompt .prompt-tags .prompt-tags-list .prompt-tag .prompt-local-language .local-language {
                display: block !important;
                max-width: 100% !important;
                white-space: nowrap !important;
                overflow: hidden !important;
                text-overflow: ellipsis !important;
                font-size: 11px !important;
                opacity: 0.78 !important;
            }
            .physton-prompt .prompt-tags .prompt-tags-list .prompt-tag .prompt-local-language.studio-suite-local-language {
                display: flex !important;
            }
            .physton-prompt .group-tabs .group-body .group-main .sub-group-main .group-tags .tag-item {
                display: inline-flex !important;
                flex-direction: column !important;
                vertical-align: top !important;
                min-width: 86px !important;
                max-width: 240px !important;
            }
            .physton-prompt .group-tabs .group-body .group-main .sub-group-main .group-tags .tag-item .tag-local,
            .physton-prompt .group-tabs .group-body .group-main .sub-group-main .group-tags .tag-item .tag-en {
                box-sizing: border-box !important;
                width: 100% !important;
                min-height: 24px !important;
                display: flex !important;
                align-items: center !important;
                justify-content: center !important;
                line-height: 1.2 !important;
                white-space: normal !important;
                word-break: break-word !important;
                overflow-wrap: anywhere !important;
            }
            .physton-prompt .group-tabs .group-body .group-main .sub-group-main .group-tags .tag-item .tag-en {
                min-height: 20px !important;
                font-size: 11px !important;
                opacity: 0.82 !important;
            }
        `;
        document.head.appendChild(style);
    }

    function boot() {
        installStyle();
        installToolbar();
        installLegacyUiObserver();
        startQueueMonitor();
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", boot);
    } else {
        boot();
    }
    window.addEventListener("load", boot);
})();
