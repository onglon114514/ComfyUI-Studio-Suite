const WINDOW_KEYS = {
    randomId: "weilin_prompt_randomid",
    theme: "weilin_prompt_theme",
    closeRight: "weilin_prompt_ui_change_close",
    windowMode: "weilin_prompt_ui_is_window",
    boxStatus: "weilin_prompt_box_status",
    firstOpen: "weilin_prompt_ui_onfirst",
    globalPast: "weilin_prompt_global_past_setting",
};
const PROMPT_STUDIO_UI_URL = "/studio-suite/prompt-studio/ui/index.html";

function ensureLocalStorageDefaults() {
    const defaults = {
        [WINDOW_KEYS.theme]: "dark",
        [WINDOW_KEYS.closeRight]: "true",
        [WINDOW_KEYS.windowMode]: "no",
        [WINDOW_KEYS.boxStatus]: "nom",
        [WINDOW_KEYS.firstOpen]: "1",
        [WINDOW_KEYS.globalPast]: "0",
    };
    for (const [key, value] of Object.entries(defaults)) {
        if (localStorage.getItem(key) == null || localStorage.getItem(key) === "") {
            localStorage.setItem(key, value);
        }
    }
}

function widgetValue(widget) {
    if (!widget) return "";
    if (typeof widget.value === "string") return widget.value;
    const el = widget.inputEl || widget.element;
    if (el && typeof el.value === "string") return el.value;
    return "";
}

function syncWidgetValue(node, widget, value) {
    if (!widget) return;
    widget.value = value;
    const widgetIndex = Array.isArray(node.widgets) ? node.widgets.indexOf(widget) : -1;
    if (widgetIndex >= 0) {
        if (!Array.isArray(node.widgets_values)) node.widgets_values = [];
        node.widgets_values[widgetIndex] = value;
    }
    const inputEl = widget.inputEl || widget.element;
    if (inputEl) {
        if (inputEl.value !== value) inputEl.value = value;
        inputEl.dispatchEvent(new Event("input", { bubbles: true }));
        inputEl.dispatchEvent(new Event("change", { bubbles: true }));
    }
    if (typeof widget.callback === "function") {
        widget.callback(value, null, node, null, null);
    }
    node.setDirtyCanvas?.(true, true);
    node.graph?.setDirtyCanvas?.(true, true);
}

function legacyPromptNodeName(node) {
    const name = node?.comfyClass || node?.type || "PromptStudioOutput";
    if (name === "PromptStudioPositiveOutput") return "WeiLinComfyUIPromptAllInOneGreat";
    if (name === "PromptStudioNegativeOutput") return "WeiLinComfyUIPromptAllInOneNeg";
    if (name === "PromptStudioOutput") return "WeiLinPromptToString";
    return name;
}

class LegacyPromptStudioController {
    constructor() {
        ensureLocalStorageDefaults();
        this.node = null;
        this.fields = null;
        this.randomId = null;
        this.iframeReady = false;
        this.loadedTheme = null;
        this.overlay = this.#createOverlay();
        document.body.appendChild(this.overlay);
        window.addEventListener("message", (event) => this.#handleMessage(event));
        window.addEventListener("keydown", (event) => {
            if (!this.isOpen()) return;
            if (event.key === "Escape") {
                event.preventDefault();
                this.close();
            }
        });
    }

    isOpen() {
        return this.overlay.style.display === "flex";
    }

    open(node, fields) {
        ensureLocalStorageDefaults();
        this.node = node;
        this.fields = fields || {};
        this.randomId = (Math.random() + Date.now()).toString(32).slice(0, 10);
        localStorage.setItem(WINDOW_KEYS.randomId, this.randomId);
        const theme = localStorage.getItem(WINDOW_KEYS.theme) || "dark";
        const src = `${PROMPT_STUDIO_UI_URL}?type=prompt&refid=${encodeURIComponent(this.randomId)}&__theme=${encodeURIComponent(theme)}`;
        const shouldReload = !this.iframeReady || !this.iframe.src || this.loadedTheme !== theme;
        if (shouldReload) {
            this.iframeReady = false;
            this.loadedTheme = theme;
            this.iframe.src = src;
        } else {
            this.#sendCurrentPromptSnapshot("openWeiLinPrompt");
        }
        this.overlay.hidden = false;
        this.overlay.style.display = "flex";
        this.overlay.style.pointerEvents = "auto";
        document.body.style.overflow = "hidden";
    }

    close() {
        this.overlay.hidden = true;
        this.overlay.style.display = "none";
        this.overlay.style.pointerEvents = "none";
        document.body.style.overflow = "";
        this.node = null;
        this.fields = null;
        this.randomId = null;
    }

    #sendCurrentPromptSnapshot(handel = "responeseWeiLinPrompt") {
        if (!this.iframe?.contentWindow || !this.randomId) return;
        this.iframe.contentWindow.postMessage({
            handel,
            g_value: this.fields?.positive ? widgetValue(this.fields.positive.widget) : "",
            n_value: this.fields?.negative ? widgetValue(this.fields.negative.widget) : "",
            randomid: this.randomId,
            type: "prompt",
            nodeName: legacyPromptNodeName(this.node),
        }, "*");
    }

    #handleMessage(event) {
        const data = event?.data || {};
        if (!data || !data.handel) return;
        if (this.randomId && data.randomid && data.randomid !== this.randomId) return;

        switch (data.handel) {
            case "getWeilinPromptBox":
            case "openWeiLinPrompt":
                this.#sendCurrentPromptSnapshot();
                break;
            case "changeWeiLinPrompt":
                if (this.node && this.fields) {
                    if (this.fields.positive) syncWidgetValue(this.node, this.fields.positive.widget, data.g_value || "");
                    if (this.fields.negative) syncWidgetValue(this.node, this.fields.negative.widget, data.n_value || "");
                }
                break;
            case "closeWeilinPromptBox":
                this.close();
                break;
            case "refreshWeilinPromptBox":
                if (this.iframe) {
                    this.iframe.src = this.iframe.src;
                }
                break;
            case "fullBoxWeilinPromptBox":
                this.modal.style.width = "100vw";
                this.modal.style.height = "100vh";
                this.modal.style.maxWidth = "100vw";
                this.modal.style.maxHeight = "100vh";
                localStorage.setItem(WINDOW_KEYS.boxStatus, "full");
                event.source?.postMessage({ handel: "fullBoxWeilinPromptBoxResponse", randomid: this.randomId }, "*");
                break;
            case "nomBoxWeilinPromptBox":
                this.modal.style.width = "92vw";
                this.modal.style.height = "92vh";
                this.modal.style.maxWidth = "1600px";
                this.modal.style.maxHeight = "960px";
                localStorage.setItem(WINDOW_KEYS.boxStatus, "nom");
                event.source?.postMessage({ handel: "nomBoxWeilinPromptBoxResponse", randomid: this.randomId }, "*");
                break;
            case "changeWeilinPromptWindowMode":
                break;
            default:
                break;
        }
    }

    #createOverlay() {
        const style = document.createElement("style");
        style.textContent = `
            .studio-suite-prompt-overlay { position: fixed; inset: 0; z-index: 99999; background: rgba(0,0,0,0.62); display: none; align-items: center; justify-content: center; }
            .studio-suite-prompt-modal { width: 92vw; height: 92vh; max-width: 1600px; max-height: 960px; min-width: 980px; min-height: 680px; border-radius: 12px; overflow: hidden; box-shadow: 0 24px 70px rgba(0,0,0,0.45); background: #111; }
            .studio-suite-prompt-iframe { width: 100%; height: 100%; border: 0; display: block; background: #111; }
        `;
        document.head.appendChild(style);

        this.iframe = document.createElement("iframe");
        this.iframe.className = "studio-suite-prompt-iframe";
        this.iframe.setAttribute("allow", "clipboard-read; clipboard-write");
        this.iframe.addEventListener("load", () => {
            this.iframeReady = true;
            this.#sendCurrentPromptSnapshot();
        });

        this.modal = document.createElement("div");
        this.modal.className = "studio-suite-prompt-modal";
        this.modal.addEventListener("click", (event) => event.stopPropagation());
        this.modal.appendChild(this.iframe);

        const overlay = document.createElement("div");
        overlay.className = "studio-suite-prompt-overlay";
        overlay.hidden = true;
        overlay.addEventListener("click", (event) => {
            if (event.target === overlay) this.close();
        });
        overlay.appendChild(this.modal);
        return overlay;
    }
}

let controller = null;
export function getLegacyPromptStudioController() {
    if (!controller) controller = new LegacyPromptStudioController();
    return controller;
}
