import { api } from "/scripts/api.js";
import { $el, ComfyDialog } from "/scripts/ui.js";
import { addStylesheet } from "./utils.js";


addStylesheet(import.meta.url);


export class PromptStudioModelInfoDialog extends ComfyDialog {
    constructor(localize) {
        super();
        this.localize = localize;
        this.element.classList.add("prompt-studio-model-dialog");
    }

    async show(modelType, modelName) {
        this.modelType = modelType;
        this.modelName = modelName;
        this.infoBody = $el("section");
        this.sideBody = $el("section");
        this.notesEditor = $el("textarea", { placeholder: this.localize("Notes") });
        const body = $el("div", [
            $el("h2", { textContent: `${modelName}` }),
            $el("main", [this.infoBody, this.sideBody]),
        ]);

        this.infoBody.append($el("p", { textContent: this.localize("Loading model info...") }));
        super.show(body);

        const response = await api.fetchApi(`/studio-suite/prompt-studio/model-info?type=${encodeURIComponent(modelType)}&name=${encodeURIComponent(modelName)}`);
        const payload = await response.json();
        if (payload.status !== "ok") {
            this.infoBody.replaceChildren($el("p", { textContent: this.localize("Failed to load model info.") }));
            return;
        }

        this.data = payload.data;
        this.notesEditor.value = this.data.notes || "";
        this.render();
    }

    render() {
        const trainedWords = Array.isArray(this.data.trained_words) ? this.data.trained_words : [];
        const metadataJson = JSON.stringify(this.data.metadata || {}, null, 2);

        this.infoBody.replaceChildren(
            this.#entry(this.localize("File"), this.data.file),
            this.#entry(this.localize("Base model"), this.data.base_model || "-"),
            this.#entry(this.localize("Resolution"), this.data.resolution || "-"),
            this.#entry(this.localize("SHA256"), this.data.sha256 || "-"),
            this.#entry(this.localize("Usage hint"), this.data.usage_hint || "-"),
            $el("div", [
                $el("label", { textContent: this.localize("Notes") }),
                this.notesEditor,
                $el("button", {
                    textContent: this.localize("Save notes"),
                    onclick: () => this.saveNotes(),
                }),
            ]),
        );

        this.sideBody.replaceChildren(
            $el("div", [
                $el("label", { textContent: `${this.localize("Trained words")} (${trainedWords.length})` }),
                $el("ol.prompt-studio-trained-word-list", trainedWords.slice(0, 300).map((item) =>
                    $el("li", { textContent: `${item.word}${item.count ? ` (${item.count})` : ""}` }),
                )),
                $el("button", {
                    textContent: this.localize("Copy trained words"),
                    onclick: async () => {
                        const content = trainedWords.map((item) => item.word).join(", ");
                        await navigator.clipboard.writeText(content);
                    },
                }),
            ]),
            $el("div", [
                $el("label", { textContent: this.localize("Raw metadata") }),
                $el("textarea", {
                    readOnly: true,
                    value: metadataJson,
                }),
            ]),
        );
    }

    async saveNotes() {
        await api.fetchApi(
            `/studio-suite/prompt-studio/model-info/notes?type=${encodeURIComponent(this.modelType)}&name=${encodeURIComponent(this.modelName)}`,
            {
                method: "POST",
                body: this.notesEditor.value,
            },
        );
    }

    #entry(label, value) {
        return $el("p", [
            $el("strong", { textContent: `${label}: ` }),
            $el("span", { textContent: value }),
        ]);
    }
}
