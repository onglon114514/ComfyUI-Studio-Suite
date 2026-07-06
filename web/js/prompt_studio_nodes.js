import { app } from "/scripts/app.js";
import { getLegacyPromptStudioController } from "./prompt_studio/legacy_iframe_controller.js";

const SUPPORTED_NODE_NAMES = new Set([
    "PromptStudioOutput",
    "PromptStudioPositiveOutput",
    "PromptStudioNegativeOutput",
]);

const BUTTON_NAMES = new Set(["Open Prompt Studio", "打开 Prompt Studio"]);
const INSTALL_MARKER = "__studioSuitePromptStudioButtonInstalled";

function getLocaleButtonLabel() {
    const lang = (navigator.language || "en").toLowerCase();
    return lang.startsWith("zh") ? "打开 Prompt Studio" : "Open Prompt Studio";
}

function getNodeClassName(node) {
    return node?.comfyClass || node?.type || node?.constructor?.comfyClass || node?.constructor?.type || "";
}

function isPromptStudioNodeName(name) {
    return SUPPORTED_NODE_NAMES.has(name);
}

function isPromptStudioNode(node) {
    return isPromptStudioNodeName(getNodeClassName(node));
}

function widgetValue(widget) {
    if (!widget) return "";
    if (typeof widget.value === "string") return widget.value;
    const el = widget.inputEl || widget.element;
    if (el && typeof el.value === "string") return el.value;
    return "";
}

function collectPromptWidgets(node) {
    const fields = {};
    for (const widget of node.widgets || []) {
        if (!widget || !widget.name) continue;
        if (widget.name === "positive") {
            fields.positive = { widget, value: widgetValue(widget) };
        } else if (widget.name === "negative") {
            fields.negative = { widget, value: widgetValue(widget) };
        }
    }
    return fields;
}

function hasPromptStudioButton(node) {
    return (node.widgets || []).some((widget) => widget?.type === "button" && BUTTON_NAMES.has(widget.name));
}

function installPromptStudioButton(node) {
    if (!node || !isPromptStudioNode(node) || hasPromptStudioButton(node)) {
        return;
    }

    try {
        node.addWidget("button", getLocaleButtonLabel(), "", () => {
            const controller = getLegacyPromptStudioController();
            controller.open(node, collectPromptWidgets(node));
        });
        node[INSTALL_MARKER] = true;
        node.setSize?.(node.computeSize?.() || node.size);
    } catch (error) {
        console.warn("[ComfyUI-Studio-Suite] Failed to add Prompt Studio button:", error);
    }
}

function refreshExistingPromptStudioNodes() {
    const nodes = app?.graph?._nodes || [];
    for (const node of nodes) {
        installPromptStudioButton(node);
    }
    app?.graph?.setDirtyCanvas?.(true, true);
}

app.registerExtension({
    name: "comfyui_studio_suite.prompt_studio_nodes",
    async beforeRegisterNodeDef(nodeType, nodeData) {
        if (!isPromptStudioNodeName(nodeData.name)) {
            return;
        }

        const originalOnNodeCreated = nodeType.prototype.onNodeCreated;
        nodeType.prototype.onNodeCreated = async function () {
            const result = originalOnNodeCreated ? originalOnNodeCreated.apply(this, arguments) : undefined;
            installPromptStudioButton(this);
            return result;
        };
    },
    setup() {
        setTimeout(refreshExistingPromptStudioNodes, 0);
        setTimeout(refreshExistingPromptStudioNodes, 500);
        setTimeout(refreshExistingPromptStudioNodes, 1500);
    },
});
