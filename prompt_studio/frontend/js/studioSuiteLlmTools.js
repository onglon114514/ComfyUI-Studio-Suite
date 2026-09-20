import { annotationKey, sanitizeBilingualState, BILINGUAL_LIMIT } from "./bilingualState.js";
import {
    markStudioSuiteReady,
    weilinAllPromptTagRoots,
    weilinPromptTagRoots,
    weilinReadPromptTagText,
    weilinEnsureLocalLanguage,
    weilinTranslateButtons,
    weilinTranslateContext,
    weilinIsTagListElement,
    weilinIsTagStructureNode,
    weilinHideLegacyAiEntrypoints,
} from "./weilinAdapter.js";

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
    let hyMtState = { available: false, alive: false, reason: "not_checked" };
    const DIRECTION_KEY = "studio_suite_translate_direction";
    const CONTENT_TYPE_KEY = "studio_suite_translate_content_type";
    const ENGINE_KEY = "studio_suite_translate_engine";
    const REFERENCE_LANGUAGE_KEY = "studio_suite_reference_language";
    let annotations = { positive: new Map(), negative: new Map() };
    let hostSession = null;
    let annotationFrame = null;
    let comparisonController = null;
    let annotationPublishScheduled = false;

    function publishAnnotations() {
        if (annotationPublishScheduled || !hostSession) return;
        annotationPublishScheduled = true;
        queueMicrotask(() => {
            annotationPublishScheduled = false;
            const state = sanitizeBilingualState({
                positive: [...annotations.positive.values()], negative: [...annotations.negative.values()],
            });
            window.parent.postMessage({ handel: "studioSuiteBilingualUpdate", randomid: hostSession, state }, window.location.origin);
        });
    }

    function rememberAnnotation(kind, text, local, lang) {
        const key = annotationKey(text);
        if (!key || !local || key === annotationKey(local)) return;
        const map = annotations[kind];
        map.delete(key);
        map.set(key, { text, local, lang });
        while (map.size > BILINGUAL_LIMIT) map.delete(map.keys().next().value);
    }

    window.addEventListener("message", (event) => {
        if (event.source !== window.parent || event.origin !== window.location.origin) return;
        const data = event.data;
        if (!["openWeiLinPrompt", "responeseWeiLinPrompt"].includes(data?.handel) || !data.randomid) return;
        const state = sanitizeBilingualState(data.studio_suite_bilingual);
        hostSession = data.randomid;
        annotations = {
            positive: new Map(state.positive.map(item => [annotationKey(item.text), item])),
            negative: new Map(state.negative.map(item => [annotationKey(item.text), item])),
        };
        scheduleAnnotationRefresh();
    });

    function byId(id) {
        return document.getElementById(id);
    }

    function setAttributeIfChanged(element, name, value) {
        if (element.getAttribute(name) !== value) element.setAttribute(name, value);
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
            if (wrap.classList.contains("is-active")) wrap.classList.remove("is-active");
            if (text.textContent !== "空闲") text.textContent = "空闲";
            return;
        }
        if (!wrap.classList.contains("is-active")) wrap.classList.add("is-active");
        const elapsed = Math.max(0, Math.floor((Date.now() - startedAt) / 1000));
        const nextText = `${label || "LLM 推理中"} ${elapsed}s`;
        if (text.textContent !== nextText) text.textContent = nextText;
    }

    function setStatusChip(name, label, state) {
        const chip = document.querySelector(`[data-studio-suite-status="${name}"]`);
        if (!chip) return;
        if (chip.textContent !== label) chip.textContent = label;
        const nextState = state || "idle";
        if (chip.dataset.state !== nextState) chip.dataset.state = nextState;
    }

    function updateRuntimeStatus() {
        const engineSelect = byId("studio-suite-llm-engine");
        const hyMtOption = engineSelect && engineSelect.querySelector('option[value="hy_mt"]');
        if (hyMtOption && hyMtState.reason !== "not_checked") {
            const disabled = !hyMtState.available;
            const label = hyMtState.available
                ? "HY-MT CPU（实验）"
                : "HY-MT CPU（未安装）";
            if (hyMtOption.disabled !== disabled) hyMtOption.disabled = disabled;
            if (hyMtOption.textContent !== label) hyMtOption.textContent = label;
            if (!hyMtState.available && engineSelect.value === "hy_mt") {
                engineSelect.value = "auto";
                localStorage.setItem(ENGINE_KEY, "auto");
            }
        }
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
            preloadBusy
                ? "模型加载中"
                : hyMtState.alive
                    ? "HY-MT 已预热"
                    : workerWarm
                        ? "模型已预热"
                        : hyMtState.available
                            ? "HY-MT 可用"
                            : "通用 LLM",
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
        weilinTranslateButtons().forEach(button => {
            button.classList.toggle("studio-suite-reference-blocked", disabled);
            setAttributeIfChanged(button, "aria-disabled", String(disabled));
            const title = disabled ? "队列或翻译正在使用资源，请稍后" : "生成下方对照，不改上方提示词（词典优先，Hy-MT 补缺）";
            if (button.title !== title) button.title = title;
        });
        document.querySelectorAll("[data-studio-suite-llm-translate], [data-studio-suite-reference]").forEach((button) => {
            if (button.disabled !== disabled) button.disabled = disabled;
            button.classList.toggle("is-queue-blocked", queueBusy);
            const title = queueBusy ? "ComfyUI 有运行中或等待中的任务，翻译已暂停" : "";
            if (button.title !== title) button.title = title;
        });
        const directionSelect = byId("studio-suite-llm-direction");
        const contentTypeSelect = byId("studio-suite-llm-content-type");
        const engineSelect = byId("studio-suite-llm-engine");
        const unloadButton = document.querySelector("[data-studio-suite-llm-unload]");
        const controlDisabled = translationBusy || preloadBusy;
        if (directionSelect && directionSelect.disabled !== controlDisabled) directionSelect.disabled = controlDisabled;
        if (contentTypeSelect && contentTypeSelect.disabled !== controlDisabled) contentTypeSelect.disabled = controlDisabled;
        if (engineSelect && engineSelect.disabled !== controlDisabled) engineSelect.disabled = controlDisabled;
        const referenceLanguage = byId("studio-suite-reference-language");
        if (referenceLanguage && referenceLanguage.disabled !== controlDisabled) referenceLanguage.disabled = controlDisabled;
        const cancelButton = byId("studio-suite-reference-cancel");
        if (cancelButton && cancelButton.hidden !== !comparisonController) cancelButton.hidden = !comparisonController;
        if (unloadButton && unloadButton.disabled !== controlDisabled) unloadButton.disabled = controlDisabled;
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
                    engine: currentEngine(),
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
                hyMtState = payload.hy_mt || hyMtState;
                workerWarm = Boolean(
                    (payload.worker && payload.worker.alive) ||
                    (payload.hy_mt && payload.hy_mt.alive)
                );
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

    function currentEngine() {
        const engine = byId("studio-suite-llm-engine");
        return engine && engine.value ? engine.value : "auto";
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
        return annotationKey(text);
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
        const count = sourceParts.length === translatedParts.length ? sourceParts.length : 0;
        for (let i = 0; i < count; i += 1) {
            const source = sourceParts[i];
            const translated = translatedParts[i];
            if (source && translated && !map.has(normalizePromptPart(translated))) {
                map.set(normalizePromptPart(translated), source);
            }
        }
        return map;
    }

    // Third-party (weilin/physton) DOM queries live in weilinAdapter.js.

    function scheduleLocalLanguageSync(kind, sourceText, translatedText, pairs) {
        const map = buildPairMap(sourceText, translatedText, pairs);
        map.set(annotationKey(translatedText), sourceText);
        for (const [text, local] of map) {
            rememberAnnotation(kind, text, local, /[\u3400-\u9fff]/.test(local) ? "zh" : "en");
        }
        publishAnnotations();
        scheduleAnnotationRefresh();
    }

    function scheduleAnnotationRefresh() {
        if (annotationFrame != null) return;
        annotationFrame = requestAnimationFrame(() => {
            annotationFrame = null;
            for (const kind of ["positive", "negative"]) {
                for (const root of weilinPromptTagRoots(kind)) {
                    for (const tag of root.querySelectorAll(".prompt-tags-list .prompt-tag")) {
                        const entry = annotations[kind].get(annotationKey(weilinReadPromptTagText(tag)));
                        if (!entry) continue;
                        const local = weilinEnsureLocalLanguage(tag);
                        if (local.textContent !== entry.local) local.textContent = entry.local;
                        if (local.title !== entry.local) local.title = entry.local;
                    }
                }
            }
        });
    }

    async function compareText(kind, singleTag) {
        if (queueBusy || translationBusy || preloadBusy) return;
        const language = byId("studio-suite-reference-language")?.value || "zh";
        const textarea = currentTextArea(kind);
        const snapshot = textarea?.value || "";
        const snapshotKey = JSON.stringify(splitPromptParts(snapshot).map(annotationKey));
        // The legacy tag view is debounced; the textarea is the current source of truth.
        const values = singleTag ? [weilinReadPromptTagText(singleTag)] : splitPromptParts(snapshot);
        const terms = [...new Set(values.filter(text => text && !/^\s*<(lora|lyco|embedding):/i.test(text)))];
        if (!terms.length) { toast("没有可生成对照的关键词", "info"); return; }
        if (terms.length > BILINGUAL_LIMIT) { toast(`请分段处理，每次最多 ${BILINGUAL_LIMIT} 个关键词`, "warning"); return; }
        const missing = terms.filter(text => {
            const cached = annotations[kind].get(annotationKey(text));
            return (!cached || cached.lang !== language) && (language === "zh" ? /[A-Za-z]/.test(text) : /[\u3400-\u9fff]/.test(text));
        });
        if (!missing.length) { scheduleAnnotationRefresh(); toast("已保留现有对照，无需重复翻译", "success"); return; }
        const controller = new AbortController();
        comparisonController = controller;
        translationBusy = true;
        updateLlmControls();
        const started = Date.now();
        let completed = 0;
        let ambiguousCount = 0;
        let failure = null;
        const checkCurrent = () => {
            if (controller.signal.aborted) throw new DOMException("Cancelled", "AbortError");
            if (JSON.stringify(splitPromptParts(textarea.value).map(annotationKey)) !== snapshotKey) throw new Error("提示词已改变，对照已停止；不会覆盖你的编辑");
        };
        const save = (text, local) => {
            checkCurrent();
            rememberAnnotation(kind, text, local, language);
            completed++;
            publishAnnotations();
            scheduleAnnotationRefresh();
            setProgress(true, started, `生成对照 ${completed}/${missing.length}`);
        };
        try {
            setProgress(true, started, "查询本地译名");
            let matches = [];
            let ambiguous = new Set();
            try {
                const response = await fetch("/studio-suite/prompt-studio/glossary_lookup", {
                    method: "POST", headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ terms: missing, language }), signal: controller.signal,
                });
                if (response.ok) {
                    const data = await response.json();
                    if (Array.isArray(data.matches)) matches = data.matches;
                    if (Array.isArray(data.ambiguous)) ambiguous = new Set(data.ambiguous.map(annotationKey));
                }
            } catch (error) { if (controller.signal.aborted) throw error; }
            const known = new Map(matches.filter(item => item && typeof item.text === "string" && typeof item.local === "string" && item.local.trim()).map(item => [annotationKey(item.text), item.local]));
            for (const text of missing) {
                checkCurrent();
                if (ambiguous.has(annotationKey(text))) { ambiguousCount++; continue; }
                const local = known.get(annotationKey(text));
                if (local) { save(text, local); continue; }
                const direction = language === "zh" ? "en_to_zh_text" : "zh_to_en_text";
                const response = await fetch(API_URL, {
                    method: "POST", headers: { "Content-Type": "application/json" }, signal: controller.signal,
                    body: JSON.stringify({ text, direction, engine: currentEngine(), translation_mode: "sentence", target_profile: "prompt_studio_bilingual", dynamic_runtime: true, keep_warm: true, keep_warm_seconds: 600 }),
                });
                const payload = await response.json();
                if (response.status === 409 || payload.error === "comfy_queue_busy") {
                    queueBusy = true;
                    throw new Error("跑图队列已启动，对照已暂停");
                }
                if (!response.ok || !payload.success || !payload.translated_text) throw new Error(payload.error || `HTTP ${response.status}`);
                save(text, payload.translated_text.trim());
            }
        } catch (error) { failure = error; }
        finally {
            comparisonController = null;
            translationBusy = false;
            updateLlmControls();
        }
        if (failure) toast(failure.name === "AbortError" ? "已取消；已完成的对照已保留" : `对照未全部完成：${failure.message}`, "warning");
        else if (ambiguousCount) toast(`已完成 ${completed} 项；${ambiguousCount} 个多义名称请在词典库确认，未自动猜测`, "warning");
        else toast(`对照完成 ${completed} 项，上方提示词未改动`, "success");
    }

    // Capture the retained A/B controls before their obsolete translator runs.
    document.addEventListener("click", (event) => {
        const context = weilinTranslateContext(event.target);
        if (!context) return;
        event.preventDefault();
        event.stopImmediatePropagation();
        const kind = weilinAllPromptTagRoots().indexOf(context.root) === 1 ? "negative" : "positive";
        compareText(kind, context.tag);
    }, true);

    async function translateText(kind) {
        if (translationBusy || preloadBusy) return;
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
                    engine: currentEngine(),
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
            const runtime = payload.runtime || {};
            workerWarm = Boolean(
                payload.engine === "general_llm" ||
                (runtime.hy_mt && runtime.hy_mt.alive)
            );
            const translatedText = payload.translated_text || "";
            if (!translatedText || textarea.value.trim() !== text) {
                toast("提示词已改变或翻译结果为空，未覆盖原文", "warning");
                return;
            }
            textarea.value = translatedText;
            dispatchTextChange(textarea);
            scheduleLocalLanguageSync(
                kind,
                payload.source_text || text,
                translatedText,
                payload.translation_pairs || (payload.json_result && payload.json_result.translation_pairs) || []
            );
            const dictionaryCount = Array.isArray(payload.dictionary_matches) ? payload.dictionary_matches.length : 0;
            const skipped = Boolean(
                payload.skipped_already_target_language ||
                runtime.skipped_already_target_language
            );
            const cacheHit = Boolean(payload.cache_hit || runtime.cache_hit);
            if (skipped) {
                toast("内容已是目标语言，未重复翻译", "success");
            } else if (cacheHit) {
                toast("已使用翻译缓存", "success");
            } else {
                toast(dictionaryCount ? "翻译完成，词典保护 " + dictionaryCount + " 项" : "翻译完成", "success");
            }
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
        const engineSelect = document.createElement("select");
        engineSelect.id = "studio-suite-llm-engine";
        engineSelect.className = "studio-suite-llm-select";
        engineSelect.innerHTML = [
            '<option value="auto">自动（优先 HY-MT）</option>',
            '<option value="hy_mt">HY-MT CPU（实验）</option>',
            '<option value="general_llm">通用 LLM</option>',
        ].join("");
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
        const savedEngine = localStorage.getItem(ENGINE_KEY);
        engineSelect.value = ["auto", "hy_mt", "general_llm"].includes(savedEngine) ? savedEngine : "auto";
        directionSelect.value = ["zh_to_en", "en_to_zh"].includes(savedDirection) ? savedDirection : "zh_to_en";
        contentTypeSelect.value = ["text", "tags"].includes(savedContentType) ? savedContentType : "text";
        engineSelect.addEventListener("change", async () => {
            localStorage.setItem(ENGINE_KEY, engineSelect.value);
            workerWarm = false;
            try {
                await fetch("/studio-suite/prompt-studio/llm_unload", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: "{}",
                });
            } finally {
                schedulePreload(50);
            }
        });
        directionSelect.addEventListener("change", () => localStorage.setItem(DIRECTION_KEY, directionSelect.value));
        contentTypeSelect.addEventListener("change", () => localStorage.setItem(CONTENT_TYPE_KEY, contentTypeSelect.value));
        modeGroup.appendChild(engineSelect);
        modeGroup.appendChild(directionSelect);
        modeGroup.appendChild(contentTypeSelect);
        toolbar.appendChild(modeGroup);
        toolbar.appendChild(makeButton("positive", "翻译正向"));
        toolbar.appendChild(makeButton("negative", "翻译反向"));
        const referenceGroup = document.createElement("div");
        referenceGroup.className = "studio-suite-reference-tools";
        referenceGroup.title = "查看下方对照，不改上方提示词；本地词典优先，缺词使用当前翻译引擎";
        const referenceLanguage = document.createElement("select");
        referenceLanguage.id = "studio-suite-reference-language";
        referenceLanguage.className = "studio-suite-llm-select";
        referenceLanguage.setAttribute("aria-label", "对照语言");
        referenceLanguage.innerHTML = '<option value="zh">中文对照</option><option value="en">English 对照</option>';
        referenceLanguage.value = localStorage.getItem(REFERENCE_LANGUAGE_KEY) === "en" ? "en" : "zh";
        referenceLanguage.addEventListener("change", () => localStorage.setItem(REFERENCE_LANGUAGE_KEY, referenceLanguage.value));
        referenceGroup.appendChild(referenceLanguage);
        for (const [kind, label] of [["positive", "正向对照"], ["negative", "反向对照"]]) {
            const button = document.createElement("button");
            button.type = "button";
            button.className = "studio-suite-llm-btn studio-suite-llm-btn-muted";
            button.dataset.studioSuiteReference = kind;
            button.textContent = label;
            button.addEventListener("click", () => compareText(kind));
            referenceGroup.appendChild(button);
        }
        const cancel = document.createElement("button");
        cancel.id = "studio-suite-reference-cancel";
        cancel.type = "button";
        cancel.hidden = true;
        cancel.className = "studio-suite-llm-btn studio-suite-llm-btn-muted";
        cancel.textContent = "取消对照";
        cancel.addEventListener("click", () => comparisonController?.abort());
        referenceGroup.appendChild(cancel);
        toolbar.appendChild(referenceGroup);
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

    function installLegacyUiObserver() {
        weilinHideLegacyAiEntrypoints(document, "#studio-suite-llm-tools");
        if (window.__studioSuiteLegacyUiObserver) return;
        window.__studioSuiteLegacyUiObserver = new MutationObserver((mutations) => {
            let tagsChanged = false;
            for (const mutation of mutations) {
                const target = mutation.target.nodeType === 1 ? mutation.target : mutation.target.parentElement;
                if (target?.closest(".studio-suite-local-language")) continue;
                if (weilinIsTagListElement(target)) tagsChanged = true;
                mutation.addedNodes.forEach((node) => {
                    if (node.nodeType !== 1) return;
                    weilinHideLegacyAiEntrypoints(node, "#studio-suite-llm-tools");
                    if (weilinIsTagStructureNode(node)) tagsChanged = true;
                });
            }
            if (tagsChanged) scheduleAnnotationRefresh();
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
            html { scroll-behavior: auto !important; }
            body, body * { scroll-behavior: auto !important; }
            .physton-prompt *, .common-popup-main *, .topBar *, #newbie-xml-wizard * {
                animation: none !important;
                transition: none !important;
            }
            body *, body *::before, body *::after { animation: none !important; transition: none !important; }
            .studio-suite-reference-blocked { opacity: 0.45 !important; cursor: not-allowed !important; }
            .physton-prompt .hover-scale-120:hover, .physton-prompt .hover-scale-140:hover,
            .topBar button:hover { transform: none !important; }
            .studio-suite-reference-tools { display: inline-flex; align-items: center; gap: 5px; flex-wrap: wrap; border-left: 1px solid #555; padding-left: 8px; }
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
                border-radius: inherit;
                left: 0;
                width: 100%;
                background: #a7f3c3;
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
                width: auto !important;
                max-width: 32em !important;
            }
            .physton-prompt .prompt-tags .prompt-tags-list .prompt-tag .studio-suite-local-language .local-language {
                white-space: normal !important;
                overflow-wrap: anywhere !important;
                text-overflow: clip !important;
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
        scheduleAnnotationRefresh();
        startQueueMonitor();
        markStudioSuiteReady("llm-tools");
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", boot);
    } else {
        boot();
    }
    window.addEventListener("load", boot);
    window.addEventListener("pagehide", () => {
        comparisonController?.abort();
        clearTimeout(preloadTimer);
        clearTimeout(statusPollTimer);
        if (annotationFrame != null) cancelAnimationFrame(annotationFrame);
        window.__studioSuiteLegacyUiObserver?.disconnect();
    });
})();
