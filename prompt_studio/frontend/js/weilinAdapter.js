// Thin adapter over the third-party weilin/physton prompt-all-in-one DOM.
//
// Every query that depends on physton-rendered markup (selectors, class names,
// container structure) lives in this module and nowhere else. If the upstream
// plugin changes its DOM, update this file only. Run scripts/check_weilin_dom.cjs
// against a real ComfyUI before upgrading ComfyUI/physton: it asserts the exact
// contract below and prints which selector changed.
//
// This module also owns the Studio Suite ready signal: our own frontend code
// sets body[data-studio-suite-ready="1"] once initialization finished, so tests
// never need to treat third-party DOM as a readiness barrier.
//
// NOTE: CSS rules that style physton markup still live in
// studioSuiteLlmTools.js#installStyle; they are presentation-only and do not
// affect readiness or behavior queries.

export const WEILIN_SELECTORS = Object.freeze({
    promptScope: ".physton-prompt",
    tags: ".prompt-tags",
    promptTags: ".physton-prompt .prompt-tags",
    tagsList: ".prompt-tags-list",
    tagItem: ".prompt-tag",
    tag: ".prompt-tags-list .prompt-tag",
    tagValue: ".prompt-tag-value",
    tagMain: ".prompt-tag-main",
    localLanguageWrap: ".prompt-local-language",
    localLanguage: ".local-language",
    translateButtons: ".physton-prompt .extend-btn-item:has(.icon-svg-translate), .physton-prompt .translate-to-local",
    extendBtnItem: ".extend-btn-item",
    translateIcon: ".icon-svg-translate",
    translateToLocal: ".translate-to-local",
    tagDelete: ".btn-tag-delete",
    // Known trap: these items render only inside the negative editor container,
    // so they stay 0x0 forever on positive-only nodes. Never a readiness barrier.
    groupTagItems: ".group-tabs .tag-item",
    legacyAi: [".icon-svg-chatgpt", ".physton-chatgpt-prompt", ".llm-btn", ".physton-packages-state"],
    legacyAiContainers: ".extend-btn-item, .physton-chatgpt-prompt, .physton-packages-state",
});

export const STUDIO_SUITE_READY_ATTRIBUTE = "data-studio-suite-ready";
export const STUDIO_SUITE_READY_EVENT = "studio-suite-ready";
const READY_PARTS = ["llm-tools", "tag-library"];

// Called by each Studio Suite frontend module once its own init finished.
// The ready attribute/event fires only after every expected part reported.
export function markStudioSuiteReady(part) {
    const body = document.body;
    if (!body) return;
    const parts = (window.__studioSuiteReadyParts ||= new Set());
    if (part) parts.add(part);
    if (body.getAttribute(STUDIO_SUITE_READY_ATTRIBUTE) === "1") return;
    if (!READY_PARTS.every((name) => parts.has(name))) return;
    body.setAttribute(STUDIO_SUITE_READY_ATTRIBUTE, "1");
    window.dispatchEvent(new CustomEvent(STUDIO_SUITE_READY_EVENT));
}

export function weilinAllPromptTagRoots() {
    return [...document.querySelectorAll(WEILIN_SELECTORS.promptTags)];
}

export function weilinPromptTagRoots(kind) {
    const roots = weilinAllPromptTagRoots();
    if (!roots.length) return [];
    if (kind === "negative") {
        return [roots[1] || roots[roots.length - 1]].filter(Boolean);
    }
    return [roots[0]].filter(Boolean);
}

export function weilinReadPromptTagText(tagEl) {
    const value = tagEl?.querySelector(WEILIN_SELECTORS.tagValue);
    if (!value) return "";
    const clone = value.cloneNode(true);
    clone.querySelectorAll(`button, svg, ${WEILIN_SELECTORS.tagDelete}, ${WEILIN_SELECTORS.translateToLocal}`).forEach((node) => node.remove());
    return clone.textContent.replace(/[×✕]$/g, "").trim();
}

// Mount point for our local-language annotation inside a physton tag element.
export function weilinEnsureLocalLanguage(tagEl) {
    let wrap = tagEl.querySelector(WEILIN_SELECTORS.localLanguageWrap);
    if (!wrap) {
        wrap = document.createElement("div");
        wrap.className = "prompt-local-language studio-suite-local-language";
        const main = tagEl.querySelector(WEILIN_SELECTORS.tagMain) || tagEl;
        if (main.parentNode) {
            main.parentNode.insertBefore(wrap, main.nextSibling);
        } else {
            tagEl.appendChild(wrap);
        }
    }
    wrap.classList.add("studio-suite-local-language");
    let local = wrap.querySelector(WEILIN_SELECTORS.localLanguage);
    if (!local) {
        local = document.createElement("span");
        local.className = "local-language";
        wrap.appendChild(local);
    }
    return local;
}

export function weilinTranslateButtons() {
    return document.querySelectorAll(WEILIN_SELECTORS.translateButtons);
}

// Resolve a click event target to the physton translate entry (A/B toolbar
// button or per-tag translate), its owning tag cloud root, and its tag.
// Returns null when the click did not hit a physton translate entry.
export function weilinTranslateContext(target) {
    const element = target instanceof Element ? target : target?.parentElement;
    const button = element?.closest(`${WEILIN_SELECTORS.extendBtnItem}, ${WEILIN_SELECTORS.translateToLocal}`);
    if (!button || (!button.matches(WEILIN_SELECTORS.translateToLocal) && !button.querySelector(WEILIN_SELECTORS.translateIcon))) return null;
    const root = button.closest(WEILIN_SELECTORS.promptScope)?.querySelector(WEILIN_SELECTORS.tags);
    if (!root) return null;
    return { button, root, tag: button.closest(WEILIN_SELECTORS.tagItem) };
}

// MutationObserver predicates for physton tag cloud changes.
export function weilinIsTagListElement(node) {
    return Boolean(node?.closest?.(WEILIN_SELECTORS.tagsList));
}

export function weilinIsTagStructureNode(node) {
    if (node?.nodeType !== 1) return false;
    return node.matches(`${WEILIN_SELECTORS.tags}, ${WEILIN_SELECTORS.tagsList}, ${WEILIN_SELECTORS.tagItem}`)
        || Boolean(node.querySelector(WEILIN_SELECTORS.tagsList));
}

// Hide upstream legacy AI entrypoints we replace with our own toolbar.
// excludeClosest keeps containers inside our own UI untouched.
export function weilinHideLegacyAiEntrypoints(scope, excludeClosest) {
    const root = scope && scope.querySelectorAll ? scope : document;
    for (const selector of WEILIN_SELECTORS.legacyAi) {
        root.querySelectorAll(selector).forEach((node) => {
            const container = node.closest(WEILIN_SELECTORS.legacyAiContainers) || node;
            if (excludeClosest && container.closest && container.closest(excludeClosest)) return;
            container.style.display = "none";
            container.setAttribute("data-studio-suite-hidden-legacy-ai", "1");
        });
    }
}

export function weilinClosestPromptTag(target) {
    return target?.closest?.(WEILIN_SELECTORS.tag) || null;
}
