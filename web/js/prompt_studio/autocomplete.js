import { app } from "/scripts/app.js";
import { $el } from "/scripts/ui.js";
import { addStylesheet } from "./utils.js";


addStylesheet(import.meta.url);

const CHAR_CODE_ZERO = "0".charCodeAt(0);
const CHAR_CODE_NINE = "9".charCodeAt(0);

const getCaretCoordinates = (function () {
    const properties = [
        "direction",
        "boxSizing",
        "width",
        "height",
        "overflowX",
        "overflowY",
        "borderTopWidth",
        "borderRightWidth",
        "borderBottomWidth",
        "borderLeftWidth",
        "borderStyle",
        "paddingTop",
        "paddingRight",
        "paddingBottom",
        "paddingLeft",
        "fontStyle",
        "fontVariant",
        "fontWeight",
        "fontStretch",
        "fontSize",
        "fontSizeAdjust",
        "lineHeight",
        "fontFamily",
        "textAlign",
        "textTransform",
        "textIndent",
        "textDecoration",
        "letterSpacing",
        "wordSpacing",
        "tabSize",
        "MozTabSize",
    ];

    return function getCoordinates(element, position) {
        const div = document.createElement("div");
        document.body.appendChild(div);
        const style = div.style;
        const computed = window.getComputedStyle(element);
        const isInput = element.nodeName === "INPUT";

        style.whiteSpace = "pre-wrap";
        if (!isInput) {
            style.wordWrap = "break-word";
        }
        style.position = "absolute";
        style.visibility = "hidden";

        properties.forEach((prop) => {
            style[prop] = computed[prop];
        });

        style.overflow = "hidden";
        div.textContent = element.value.substring(0, position);
        if (isInput) {
            div.textContent = div.textContent.replace(/\s/g, "\u00a0");
        }

        const span = document.createElement("span");
        span.textContent = element.value.substring(position) || ".";
        div.appendChild(span);

        const result = {
            top: span.offsetTop + parseInt(computed.borderTopWidth, 10),
            left: span.offsetLeft + parseInt(computed.borderLeftWidth, 10),
            height: parseInt(computed.lineHeight, 10),
        };

        document.body.removeChild(div);
        return result;
    };
})();


class TextAreaCaretHelper {
    constructor(el) {
        this.el = el;
    }

    #isDigit(charCode) {
        return CHAR_CODE_ZERO <= charCode && charCode <= CHAR_CODE_NINE;
    }

    #getLineHeightPx() {
        const computed = getComputedStyle(this.el);
        const lineHeight = computed.lineHeight;
        if (this.#isDigit(lineHeight.charCodeAt(0))) {
            const value = parseFloat(lineHeight);
            return this.#isDigit(lineHeight.charCodeAt(lineHeight.length - 1))
                ? value * parseFloat(computed.fontSize)
                : value;
        }
        return parseFloat(computed.fontSize) * 1.2;
    }

    getCursorOffset() {
        const rect = this.el.getBoundingClientRect();
        const cursor = getCaretCoordinates(this.el, this.el.selectionEnd);
        return {
            top: rect.top + window.scrollY + cursor.top + this.#getLineHeightPx(),
            left: rect.left + window.scrollX + cursor.left,
        };
    }

    getBeforeCursor() {
        if (this.el.selectionStart !== this.el.selectionEnd) {
            return null;
        }
        return this.el.value.substring(0, this.el.selectionEnd);
    }

    insertAtCursor(value, offset, finalOffset) {
        const startPos = this.el.selectionStart;
        this.el.selectionStart = this.el.selectionStart + offset;
        let pasted = true;
        try {
            pasted = document.execCommand("insertText", false, value);
        } catch (error) {
            pasted = false;
        }
        if (!pasted) {
            this.el.setRangeText(value, this.el.selectionStart, this.el.selectionEnd, "end");
        }
        this.el.selectionEnd = this.el.selectionStart = startPos + value.length + offset + (finalOffset ?? 0);
    }
}


export class TextAreaAutoComplete {
    static globalSeparator = ", ";
    static enabled = true;
    static insertOnTab = true;
    static insertOnEnter = true;
    static replacer = undefined;
    static suggestionCount = 20;
    static groups = {};
    static globalGroups = new Set();
    static globalWords = {};

    static updateWords(id, words, addGlobal = true) {
        const isUpdate = id in TextAreaAutoComplete.groups;
        TextAreaAutoComplete.groups[id] = words;
        if (addGlobal) {
            TextAreaAutoComplete.globalGroups.add(id);
        }

        if (isUpdate) {
            TextAreaAutoComplete.globalWords = Object.assign(
                {},
                ...Object.keys(TextAreaAutoComplete.groups)
                    .filter((key) => TextAreaAutoComplete.globalGroups.has(key))
                    .map((key) => TextAreaAutoComplete.groups[key]),
            );
        } else if (addGlobal) {
            Object.assign(TextAreaAutoComplete.globalWords, words);
        }
    }

    constructor(el, mountEl, words = null, separator = null) {
        this.el = el;
        this.mountEl = mountEl;
        this.helper = new TextAreaCaretHelper(el);
        this.dropdown = $el("div.prompt-studio-autocomplete");
        this.overrideWords = words;
        this.overrideSeparator = separator;
        this.#setup();
    }

    get words() {
        return this.overrideWords ?? TextAreaAutoComplete.globalWords;
    }

    get separator() {
        return this.overrideSeparator ?? TextAreaAutoComplete.globalSeparator;
    }

    #setup() {
        this.el.addEventListener("keydown", this.#keyDown.bind(this));
        this.el.addEventListener("keypress", this.#keyPress.bind(this));
        this.el.addEventListener("keyup", this.#keyUp.bind(this));
        this.el.addEventListener("click", this.#hide.bind(this));
        this.el.addEventListener("blur", () => setTimeout(() => this.#hide(), 120));
    }

    #keyDown(event) {
        if (!TextAreaAutoComplete.enabled || !this.dropdown.parentElement) {
            return;
        }

        if (event.key === "ArrowUp") {
            event.preventDefault();
            if (this.selected.index) {
                this.#setSelected(this.currentWords[this.selected.index - 1].wordInfo);
            } else {
                this.#setSelected(this.currentWords[this.currentWords.length - 1].wordInfo);
            }
        } else if (event.key === "ArrowDown") {
            event.preventDefault();
            if (this.selected.index === this.currentWords.length - 1) {
                this.#setSelected(this.currentWords[0].wordInfo);
            } else {
                this.#setSelected(this.currentWords[this.selected.index + 1].wordInfo);
            }
        } else if (event.key === "Tab" && TextAreaAutoComplete.insertOnTab) {
            this.#insertItem();
            event.preventDefault();
        }
    }

    #keyPress(event) {
        if (!TextAreaAutoComplete.enabled) {
            return;
        }

        if (this.dropdown.parentElement && event.key === "Enter" && !event.ctrlKey && TextAreaAutoComplete.insertOnEnter) {
            this.#insertItem();
            event.preventDefault();
        }

        if (!event.defaultPrevented) {
            this.#update();
        }
    }

    #keyUp(event) {
        if (!TextAreaAutoComplete.enabled) {
            return;
        }

        if (this.dropdown.parentElement && event.key === "Escape") {
            event.preventDefault();
            this.#hide();
            return;
        }

        if (!this.dropdown.parentElement && event.key.length > 1 && event.key !== "Delete" && event.key !== "Backspace") {
            return;
        }

        if (!event.defaultPrevented) {
            this.#update();
        }
    }

    #setSelected(item) {
        if (this.selected?.el) {
            this.selected.el.classList.remove("prompt-studio-autocomplete-item--selected");
        }
        this.selected = item;
        this.selected.el.classList.add("prompt-studio-autocomplete-item--selected");
    }

    #insertItem() {
        if (this.selected?.el) {
            this.selected.el.click();
        }
    }

    #getFilteredWords(term) {
        const loweredTerm = term.toLowerCase();
        const priorityMatches = [];
        const prefixMatches = [];
        const includesMatches = [];

        for (const word of Object.keys(this.words)) {
            const loweredWord = word.toLowerCase();
            if (loweredWord === loweredTerm) {
                continue;
            }

            const pos = loweredWord.indexOf(loweredTerm);
            if (pos === -1) {
                continue;
            }

            const wordInfo = this.words[word];
            if (wordInfo.priority) {
                priorityMatches.push({ pos, wordInfo });
            } else if (pos === 0) {
                prefixMatches.push({ pos, wordInfo });
            } else {
                includesMatches.push({ pos, wordInfo });
            }
        }

        priorityMatches.sort(
            (a, b) =>
                b.wordInfo.priority - a.wordInfo.priority ||
                a.wordInfo.text.length - b.wordInfo.text.length ||
                a.wordInfo.text.localeCompare(b.wordInfo.text),
        );

        return priorityMatches.concat(prefixMatches, includesMatches).slice(0, TextAreaAutoComplete.suggestionCount);
    }

    #update() {
        let before = this.helper.getBeforeCursor();
        if (before?.length) {
            const match = before.match(/([^\s,;"]+)$/);
            before = match ? match[0] : null;
        }

        if (!before) {
            this.#hide();
            return;
        }

        this.currentWords = this.#getFilteredWords(before);
        if (!this.currentWords.length) {
            this.#hide();
            return;
        }

        let hasSelected = false;
        const items = this.currentWords.map(({ wordInfo, pos }, index) => {
            const parts = [
                $el("span", { textContent: wordInfo.text.substring(0, pos) }),
                $el("span.prompt-studio-autocomplete-highlight", { textContent: wordInfo.text.substring(pos, pos + before.length) }),
                $el("span", { textContent: wordInfo.text.substring(pos + before.length) }),
            ];

            if (wordInfo.hint) {
                parts.push($el("span.prompt-studio-autocomplete-pill", { textContent: wordInfo.hint }));
            }

            if (wordInfo.value && wordInfo.value !== wordInfo.text) {
                parts.push($el("span.prompt-studio-autocomplete-pill", { textContent: wordInfo.value }));
            }

            if (wordInfo.info) {
                parts.push(
                    $el("a.prompt-studio-autocomplete-item-info", {
                        textContent: "ℹ",
                        href: "#",
                        onclick: (event) => {
                            event.preventDefault();
                            event.stopPropagation();
                            wordInfo.info();
                        },
                    }),
                );
            }

            const item = $el("div.prompt-studio-autocomplete-item", {
                onclick: () => {
                    this.el.focus();
                    let value = wordInfo.value ?? wordInfo.text;
                    if (TextAreaAutoComplete.replacer && wordInfo.use_replacer !== false) {
                        value = TextAreaAutoComplete.replacer(value);
                    }
                    this.helper.insertAtCursor(value + this.separator, -before.length, wordInfo.caretOffset);
                    this.el.dispatchEvent(new Event("input", { bubbles: true }));
                    this.el.dispatchEvent(new Event("change", { bubbles: true }));
                    setTimeout(() => this.#update(), 80);
                },
                onmousemove: () => this.#setSelected(wordInfo),
            }, parts);

            wordInfo.index = index;
            wordInfo.el = item;
            if (wordInfo === this.selected) {
                hasSelected = true;
            }
            return item;
        });

        this.#setSelected(hasSelected ? this.selected : this.currentWords[0].wordInfo);
        this.dropdown.replaceChildren(...items);
        if (!this.dropdown.parentElement) {
            this.mountEl.append(this.dropdown);
        }

        const position = this.helper.getCursorOffset();
        this.dropdown.style.maxHeight = Math.max(140, window.innerHeight - position.top - 40) + "px";
    }

    #hide() {
        this.selected = null;
        this.dropdown.remove();
    }
}


TextAreaAutoComplete.globalSeparator = localStorage.getItem("prompt_studio.autocomplete.separator") ?? ", ";
TextAreaAutoComplete.insertOnTab = localStorage.getItem("prompt_studio.autocomplete.insertOnTab") !== "false";
TextAreaAutoComplete.insertOnEnter = localStorage.getItem("prompt_studio.autocomplete.insertOnEnter") !== "false";
TextAreaAutoComplete.replacer = localStorage.getItem("prompt_studio.autocomplete.replaceUnderscore") === "true"
    ? (value) => value.replaceAll("_", " ")
    : undefined;

const localLang = navigator.language?.toLowerCase?.() || "en";
app.ui.settings.addSetting({
    id: localLang.startsWith("zh") ? "prompt_studio.自动补全.启用" : "prompt_studio.autocomplete.enabled",
    name: localLang.startsWith("zh") ? "Prompt Studio 自动补全启用" : "Prompt Studio autocomplete enabled",
    type: "boolean",
    defaultValue: true,
    onChange: (value) => {
        TextAreaAutoComplete.enabled = !!value;
    },
});
TextAreaAutoComplete.enabled = true;
