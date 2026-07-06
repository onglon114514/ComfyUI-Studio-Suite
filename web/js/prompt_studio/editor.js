import { api } from "/scripts/api.js";
import { $el } from "/scripts/ui.js";
import { TextAreaAutoComplete } from "./autocomplete.js";
import { PromptStudioModelInfoDialog } from "./modelInfoDialog.js";

const LOCALIZED_TEXT = {
    zh: {
        title: "Prompt Studio 编辑器",
        save: "写回节点",
        cancel: "关闭",
        positive: "正向提示词",
        negative: "反向提示词",
        helper: "LoRA 快捷助手",
        search: "搜索 LoRA",
        insert: "插入",
        info: "信息",
    },
    en: {
        title: "Prompt Studio Editor",
        save: "Apply to node",
        cancel: "Close",
        positive: "Positive prompt",
        negative: "Negative prompt",
        helper: "LoRA quick helper",
        search: "Search LoRAs",
        insert: "Insert",
        info: "Info",
    },
};

function getLocalePack() {
    const key = (navigator.language || "en").toLowerCase().startsWith("zh") ? "zh" : "en";
    const pack = LOCALIZED_TEXT[key];
    return (token) => pack[token] || token;
}

function buildAutocompleteWordsFromCsv(text) {
    const words = {};
    for (const rawLine of String(text || "").split(/\r?\n/)) {
        const line = rawLine.trim();
        if (!line) continue;
        const parts = line.split(",");
        const head = (parts[0] || "").trim();
        if (!head) continue;
        const alias = (parts[1] || "").trim();
        const priorityValue = Number((parts[2] || "").trim());
        const entry = { text: alias || head, value: head };
        if (!Number.isNaN(priorityValue) && priorityValue > 0) {
            entry.priority = priorityValue;
        }
        words[entry.text] = entry;
    }
    return words;
}

function syncWidgetValue(node, widget, value) {
    widget.value = value;
    const widgetIndex = Array.isArray(node.widgets) ? node.widgets.indexOf(widget) : -1;
    if (widgetIndex >= 0) {
        if (!Array.isArray(node.widgets_values)) {
            node.widgets_values = [];
        }
        node.widgets_values[widgetIndex] = value;
    }
    const inputEl = widget.inputEl || widget.element;
    if (inputEl && inputEl.value !== value) {
        inputEl.value = value;
        inputEl.dispatchEvent(new Event("input", { bubbles: true }));
        inputEl.dispatchEvent(new Event("change", { bubbles: true }));
    }
    if (typeof widget.callback === "function") {
        widget.callback(value, null, node, null, null);
    }
    node.setDirtyCanvas?.(true, true);
    node.graph?.setDirtyCanvas?.(true, true);
}

class PromptStudioEditorController {
    constructor() {
        this.t = getLocalePack();
        this.modelInfoDialog = new PromptStudioModelInfoDialog(this.t);
        this.customWordListLoaded = false;
        this.lorasLoaded = false;
        this.loraNames = [];
        this.overlay = this.#buildOverlay();
        document.body.appendChild(this.overlay);
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

    async ensureWordSources() {
        if (!this.customWordListLoaded) {
            try {
                const response = await api.fetchApi("/studio-suite/prompt-studio/autocomplete/custom", { cache: "no-store" });
                if (response.status === 200) {
                    TextAreaAutoComplete.updateWords("prompt_studio.custom_words", buildAutocompleteWordsFromCsv(await response.text()));
                }
            } catch {}
            this.customWordListLoaded = true;
        }
        if (!this.lorasLoaded) {
            try {
                const response = await api.fetchApi("/studio-suite/prompt-studio/autocomplete/loras", { cache: "no-store" });
                this.loraNames = await response.json();
                const entries = {};
                for (const name of this.loraNames) {
                    const value = `<lora:${name}:1.0>`;
                    entries[value] = {
                        text: value,
                        value,
                        hint: "LoRA",
                        use_replacer: false,
                        info: () => this.modelInfoDialog.show("loras", name),
                    };
                }
                TextAreaAutoComplete.updateWords("prompt_studio.loras", entries);
                this.renderLoraList("");
            } catch {
                this.loraNames = [];
                this.renderLoraList("");
            }
            this.lorasLoaded = true;
        }
    }

    async open(node, fields) {
        this.node = node;
        this.fields = fields || {};
        const hasPositive = !!this.fields.positive;
        const hasNegative = !!this.fields.negative;
        this.positiveField.style.display = hasPositive ? "flex" : "none";
        this.negativeField.style.display = hasNegative ? "flex" : "none";
        this.positiveInput.value = this.fields.positive?.value ?? this.fields.positive?.widget?.value ?? "";
        this.negativeInput.value = this.fields.negative?.value ?? this.fields.negative?.widget?.value ?? "";
        this.activeTextarea = hasPositive ? this.positiveInput : this.negativeInput;
        this.searchInput.value = "";
        this.overlay.hidden = false;
        this.overlay.style.display = "flex";
        this.overlay.style.pointerEvents = "auto";
        document.body.style.overflow = "hidden";
        await this.ensureWordSources();
        if (!this.positiveAutocomplete && hasPositive) {
            this.positiveAutocomplete = new TextAreaAutoComplete(this.positiveInput, this.positiveAutoMount);
        }
        if (!this.negativeAutocomplete && hasNegative) {
            this.negativeAutocomplete = new TextAreaAutoComplete(this.negativeInput, this.negativeAutoMount);
        }
        this.renderLoraList("");
        (hasPositive ? this.positiveInput : this.negativeInput).focus();
    }

    close() {
        this.overlay.hidden = true;
        this.overlay.style.display = "none";
        this.overlay.style.pointerEvents = "none";
        document.body.style.overflow = "";
        this.node = null;
        this.fields = null;
    }

    apply() {
        if (!this.node || !this.fields) return;
        if (this.fields.positive) syncWidgetValue(this.node, this.fields.positive.widget, this.positiveInput.value);
        if (this.fields.negative) syncWidgetValue(this.node, this.fields.negative.widget, this.negativeInput.value);
        this.close();
    }

    renderLoraList(filterText) {
        const lowered = String(filterText || "").trim().toLowerCase();
        const list = this.loraNames
            .filter((name) => !lowered || name.toLowerCase().includes(lowered))
            .slice(0, 120)
            .map((name) =>
                $el("div", { className: "prompt-studio-lora-row" }, [
                    $el("span", { textContent: name }),
                    $el("div", { className: "prompt-studio-lora-actions" }, [
                        $el("button", { textContent: this.t("insert"), onclick: () => this.insertLora(name) }),
                        $el("button", { textContent: this.t("info"), onclick: () => this.modelInfoDialog.show("loras", name) }),
                    ]),
                ]),
            );
        this.loraList.replaceChildren(...list);
    }

    insertLora(name) {
        const target = this.activeTextarea || this.positiveInput || this.negativeInput;
        const snippet = `<lora:${name}:1.0>`;
        const start = target.selectionStart ?? target.value.length;
        const end = target.selectionEnd ?? target.value.length;
        const before = target.value.slice(0, start);
        const after = target.value.slice(end);
        const needsComma = before.trim() && !before.trimEnd().endsWith(",");
        const insertText = `${needsComma ? ", " : ""}${snippet}, `;
        target.value = `${before}${insertText}${after}`;
        const caret = before.length + insertText.length;
        target.selectionStart = target.selectionEnd = caret;
        target.dispatchEvent(new Event("input", { bubbles: true }));
        target.focus();
    }

    #buildOverlay() {
        const style = document.createElement("style");
        style.textContent = `
            .prompt-studio-overlay { position: fixed; inset: 0; z-index: 99999; background: rgba(0, 0, 0, 0.58); display: none; align-items: center; justify-content: center; }
            .prompt-studio-modal { width: min(1320px, 94vw); height: min(860px, 92vh); background: var(--comfy-menu-bg, #1f1f24); color: var(--input-text, #f2f2f2); border: 1px solid var(--border-color, #484848); border-radius: 16px; box-shadow: 0 28px 80px rgba(0, 0, 0, 0.45); display: grid; grid-template-columns: minmax(0, 1fr) 340px; overflow: hidden; }
            .prompt-studio-main { display: flex; flex-direction: column; min-width: 0; min-height: 0; padding: 18px; gap: 14px; }
            .prompt-studio-header { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
            .prompt-studio-fields { display: grid; grid-template-rows: 1fr 1fr; gap: 14px; min-height: 0; flex: 1; }
            .prompt-studio-field { display: flex; flex-direction: column; gap: 8px; min-height: 0; }
            .prompt-studio-field textarea { width: 100%; min-height: 0; flex: 1; resize: none; border-radius: 12px; padding: 12px; background: rgba(255, 255, 255, 0.04); color: inherit; border: 1px solid rgba(255, 255, 255, 0.12); }
            .prompt-studio-sidebar { display: flex; flex-direction: column; min-height: 0; border-left: 1px solid var(--border-color, #484848); background: rgba(255, 255, 255, 0.03); padding: 18px; gap: 12px; }
            .prompt-studio-sidebar input { width: 100%; }
            .prompt-studio-lora-list { min-height: 0; flex: 1; overflow: auto; display: flex; flex-direction: column; gap: 8px; }
            .prompt-studio-lora-row { display: flex; align-items: center; justify-content: space-between; gap: 8px; padding: 8px 10px; border-radius: 10px; background: rgba(255, 255, 255, 0.05); }
            .prompt-studio-lora-actions { display: flex; gap: 6px; }
            .prompt-studio-actions { display: flex; gap: 8px; }
        `;
        document.head.appendChild(style);

        this.positiveInput = $el("textarea", { onfocus: () => { this.activeTextarea = this.positiveInput; } });
        this.negativeInput = $el("textarea", { onfocus: () => { this.activeTextarea = this.negativeInput; } });
        this.positiveAutoMount = $el("div");
        this.negativeAutoMount = $el("div");
        this.positiveField = $el("div.prompt-studio-field", [ $el("label", { textContent: this.t("positive") }), this.positiveInput, this.positiveAutoMount ]);
        this.negativeField = $el("div.prompt-studio-field", [ $el("label", { textContent: this.t("negative") }), this.negativeInput, this.negativeAutoMount ]);
        this.searchInput = $el("input", { placeholder: this.t("search"), oninput: () => this.renderLoraList(this.searchInput.value) });
        this.loraList = $el("div.prompt-studio-lora-list");

        return $el("div.prompt-studio-overlay", {
            hidden: true,
            onclick: (event) => { if (event.target === event.currentTarget) this.close(); },
        }, [
            $el("div.prompt-studio-modal", {
                onclick: (event) => event.stopPropagation(),
            }, [
                $el("div.prompt-studio-main", [
                    $el("div.prompt-studio-header", [
                        $el("h2", { textContent: this.t("title") }),
                        $el("div.prompt-studio-actions", [
                            $el("button", { textContent: this.t("cancel"), onclick: () => this.close() }),
                            $el("button", { textContent: this.t("save"), onclick: () => this.apply() }),
                        ]),
                    ]),
                    $el("div.prompt-studio-fields", [ this.positiveField, this.negativeField ]),
                ]),
                $el("aside.prompt-studio-sidebar", [ $el("h3", { textContent: this.t("helper") }), this.searchInput, this.loraList ]),
            ]),
        ]);
    }
}

let sharedController = null;
export function getPromptStudioEditorController() {
    if (!sharedController) sharedController = new PromptStudioEditorController();
    return sharedController;
}
