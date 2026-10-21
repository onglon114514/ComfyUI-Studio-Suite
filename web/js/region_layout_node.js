import { app } from "/scripts/app.js";

const NODE_NAME = "StudioSuiteRegionLayout";
const SCHEMA = "prompt-studio/region-v1";
const CANVAS_SIZE = 360;
const CANVAS_MARGIN = 4;
const MIN_BOX_PX = 8;
const EDITOR_HEIGHT = 548;
const MIN_NODE_WIDTH = 420;
const MIN_NODE_HEIGHT = 800;

function clampNumber(value, lo, hi) {
    return Math.min(hi, Math.max(lo, value));
}

function clamp01(value) {
    return clampNumber(value, 0, 1);
}

function normalizeBbox(raw) {
    if (!Array.isArray(raw) || raw.length !== 4) return null;
    const values = raw.map((value) => Number(value));
    if (values.some((value) => !Number.isFinite(value))) return null;
    return [
        clamp01(Math.min(values[0], values[2])),
        clamp01(Math.min(values[1], values[3])),
        clamp01(Math.max(values[0], values[2])),
        clamp01(Math.max(values[1], values[3])),
    ];
}

function regionTagValue(tag) {
    return typeof tag === "string" ? tag : (tag && tag.tag) || "";
}

function splitTags(text) {
    return String(text || "")
        .split(/[,，]/)
        .map((tag) => tag.trim())
        .filter(Boolean);
}

function blankDoc() {
    return {
        schema: SCHEMA,
        canvas: { w: 1024, h: 1024 },
        global: { caption_zh: "", caption_en: "" },
        regions: [],
    };
}

function sanitizeRegion(raw, index) {
    if (!raw || typeof raw !== "object") return null;
    const bbox = normalizeBbox(raw.bbox);
    if (!bbox) return null;
    const nl = raw.nl && typeof raw.nl === "object" ? raw.nl : {};
    const tags = Array.isArray(raw.tags)
        ? raw.tags.map(regionTagValue).map((tag) => String(tag).trim()).filter(Boolean)
        : [];
    return {
        id: typeof raw.id === "string" && raw.id ? raw.id : "r" + (index + 1),
        bbox,
        role: raw.role === "text" ? "text" : "obj",
        literalText: typeof raw.literalText === "string" ? raw.literalText : "",
        nl: { zh: String(nl.zh || ""), en: String(nl.en || "") },
        tags,
        muted: !!raw.muted,
        linkedTo: raw.linkedTo ? String(raw.linkedTo) : null,
        colors: Array.isArray(raw.colors) ? raw.colors.slice() : [],
    };
}

function sanitizeDoc(raw, width, height) {
    const doc = blankDoc();
    doc.canvas = { w: width, h: height };
    if (!raw || typeof raw !== "object") return doc;
    const global = raw.global && typeof raw.global === "object" ? raw.global : {};
    doc.global = {
        caption_zh: String(global.caption_zh || ""),
        caption_en: String(global.caption_en || ""),
    };
    const regions = Array.isArray(raw.regions) ? raw.regions : [];
    doc.regions = regions.map(sanitizeRegion).filter(Boolean);
    return doc;
}

function installRegionEditor(node) {
    if (node.__studioSuiteRegionEditor) return;
    node.__studioSuiteRegionEditor = true;

    const container = document.createElement("div");
    container.style.cssText =
        "display:flex;flex-direction:column;gap:6px;padding:6px;box-sizing:border-box;" +
        "width:100%;font:12px sans-serif;color:#ddd;overflow:auto;";
    container.tabIndex = 0;

    const toolbar = document.createElement("div");
    toolbar.style.cssText = "display:flex;align-items:center;gap:6px;min-height:24px;";

    const loadButton = document.createElement("button");
    loadButton.type = "button";
    loadButton.textContent = "载入参考图";
    loadButton.style.cssText =
        "height:24px;padding:0 8px;border:1px solid #555;border-radius:3px;" +
        "background:#2a2a2a;color:#ddd;font:12px sans-serif;cursor:pointer;";

    const imageName = document.createElement("span");
    imageName.textContent = "未载入参考图";
    imageName.style.cssText =
        "flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;opacity:.65;";

    const fileInput = document.createElement("input");
    fileInput.type = "file";
    fileInput.accept = "image/*";
    fileInput.style.display = "none";

    toolbar.appendChild(loadButton);
    toolbar.appendChild(imageName);
    toolbar.appendChild(fileInput);

    const canvas = document.createElement("canvas");
    canvas.width = CANVAS_SIZE;
    canvas.height = CANVAS_SIZE;
    canvas.tabIndex = 0;
    canvas.style.cssText =
        "display:block;width:360px;max-width:100%;height:auto;aspect-ratio:1/1;" +
        "margin:0 auto;background:#222;border-radius:4px;touch-action:none;outline:none;";

    const roleRow = document.createElement("div");
    roleRow.style.cssText = "display:flex;align-items:center;gap:6px;";

    const roleSelect = document.createElement("select");
    roleSelect.style.cssText =
        "flex:1;height:24px;box-sizing:border-box;background:#1b1b1b;border:1px solid #444;" +
        "border-radius:3px;color:#ddd;font:12px sans-serif;";
    for (const value of ["obj", "text"]) {
        const option = document.createElement("option");
        option.value = value;
        option.textContent = value;
        roleSelect.appendChild(option);
    }

    const deleteButton = document.createElement("button");
    deleteButton.type = "button";
    deleteButton.textContent = "删除";
    deleteButton.style.cssText =
        "height:24px;padding:0 10px;border:1px solid #6a3a3a;border-radius:3px;" +
        "background:#3a2020;color:#f0b0b0;font:12px sans-serif;cursor:pointer;";

    roleRow.appendChild(roleSelect);
    roleRow.appendChild(deleteButton);

    function makeTextInput(placeholder) {
        const input = document.createElement("input");
        input.type = "text";
        input.placeholder = placeholder;
        input.style.cssText =
            "width:100%;height:24px;box-sizing:border-box;background:#1b1b1b;" +
            "border:1px solid #444;border-radius:3px;color:#ddd;font:12px sans-serif;padding:0 6px;";
        return input;
    }

    const enInput = makeTextInput("en 描述");
    const zhInput = makeTextInput("zh 描述");
    const tagsInput = makeTextInput("tags，逗号分隔");

    const hint = document.createElement("div");
    hint.textContent = "框：拖拽新建 / 点选 / Del 删除";
    hint.style.cssText = "font-size:11px;opacity:.6;";

    container.appendChild(toolbar);
    container.appendChild(canvas);
    container.appendChild(roleRow);
    container.appendChild(enInput);
    container.appendChild(zhInput);
    container.appendChild(tagsInput);
    container.appendChild(hint);

    if (typeof node.addDOMWidget === "function") {
        node.addDOMWidget("region_editor", "customtext", container, {
            serialize: false,
            hideOnZoom: false,
            getMinHeight: () => EDITOR_HEIGHT,
            getMaxHeight: () => EDITOR_HEIGHT,
            getHeight: () => EDITOR_HEIGHT,
        });
    } else {
        console.warn("[ComfyUI-Studio-Suite] Region Layout: addDOMWidget is unavailable");
    }

    let doc = blankDoc();
    let selectedId = null;
    let drag = null;
    let lastRect = null;
    let referenceImage = null;
    let readingDoc = false;

    function findWidget(name) {
        return (node.widgets || []).find((widget) => widget && widget.name === name) || null;
    }

    function widgetNumber(name, fallback) {
        const widget = findWidget(name);
        const value = Number(widget && widget.value);
        return Number.isFinite(value) && value > 0 ? Math.round(value) : fallback;
    }

    function canvasSize() {
        return {
            w: widgetNumber("canvas_width", 1024),
            h: widgetNumber("canvas_height", 1024),
        };
    }

    function selectedRegion() {
        return doc.regions.find((region) => region.id === selectedId) || null;
    }

    function updateFields() {
        const region = selectedRegion();
        const disabled = !region;
        for (const element of [roleSelect, deleteButton, enInput, zhInput, tagsInput]) {
            element.disabled = disabled;
        }
        if (!region) {
            roleSelect.value = "obj";
            enInput.value = "";
            zhInput.value = "";
            tagsInput.value = "";
            return;
        }
        roleSelect.value = region.role === "text" ? "text" : "obj";
        enInput.value = region.nl.en || "";
        zhInput.value = region.nl.zh || "";
        tagsInput.value = (region.tags || []).join(", ");
    }

    function readDocFromWidget() {
        if (readingDoc) return;
        readingDoc = true;
        try {
            const widget = findWidget("layout_json");
            let parsed = null;
            if (widget && typeof widget.value === "string" && widget.value.trim()) {
                try {
                    parsed = JSON.parse(widget.value);
                } catch (error) {
                    console.warn("[ComfyUI-Studio-Suite] Region Layout: layout_json 解析失败", error);
                    parsed = null;
                }
            }
            const size = canvasSize();
            doc = sanitizeDoc(parsed, size.w, size.h);
            if (selectedId && !doc.regions.some((region) => region.id === selectedId)) {
                selectedId = null;
            }
            updateFields();
            draw();
        } finally {
            readingDoc = false;
        }
    }

    function writeDoc() {
        const widget = findWidget("layout_json");
        if (!widget) return;
        const text = JSON.stringify(doc);
        if (widget.value === text) return;
        widget.value = text;
        const element = widget.inputEl || widget.element;
        if (element && typeof element.value === "string") {
            element.value = text;
        }
        node.graph?.setDirtyCanvas?.(true, true);
    }

    function nextRegionId() {
        const used = new Set(doc.regions.map((region) => region.id));
        let index = 1;
        while (used.has("r" + index)) index += 1;
        return "r" + index;
    }

    function createRegion(bbox) {
        return {
            id: nextRegionId(),
            bbox: bbox,
            role: "obj",
            literalText: "",
            nl: { zh: "", en: "" },
            tags: [],
            muted: false,
            linkedTo: null,
            colors: [],
        };
    }

    function computeLayoutRect() {
        const width = Math.max(1, doc.canvas.w);
        const height = Math.max(1, doc.canvas.h);
        const maxSize = CANVAS_SIZE - CANVAS_MARGIN * 2;
        const scale = Math.min(maxSize / width, maxSize / height);
        const drawWidth = width * scale;
        const drawHeight = height * scale;
        return {
            x: (CANVAS_SIZE - drawWidth) / 2,
            y: (CANVAS_SIZE - drawHeight) / 2,
            w: drawWidth,
            h: drawHeight,
        };
    }

    function draw() {
        const context = canvas.getContext("2d");
        if (!context) return;

        context.setTransform(1, 0, 0, 1, 0, 0);
        context.clearRect(0, 0, CANVAS_SIZE, CANVAS_SIZE);
        context.fillStyle = "#222";
        context.fillRect(0, 0, CANVAS_SIZE, CANVAS_SIZE);

        const rect = computeLayoutRect();
        lastRect = rect;

        context.fillStyle = "#1a1a1a";
        context.fillRect(rect.x, rect.y, rect.w, rect.h);

        if (referenceImage) {
            try {
                context.drawImage(referenceImage, rect.x, rect.y, rect.w, rect.h);
            } catch (error) {
                // Ignore a broken local preview; it never enters layout_json.
            }
        }

        context.strokeStyle = "rgba(255,255,255,0.08)";
        context.lineWidth = 1;
        for (let index = 1; index < 8; index += 1) {
            const x = rect.x + (rect.w * index) / 8;
            const y = rect.y + (rect.h * index) / 8;
            context.beginPath();
            context.moveTo(x, rect.y);
            context.lineTo(x, rect.y + rect.h);
            context.moveTo(rect.x, y);
            context.lineTo(rect.x + rect.w, y);
            context.stroke();
        }

        context.strokeStyle = "rgba(255,255,255,0.35)";
        context.strokeRect(rect.x + 0.5, rect.y + 0.5, rect.w - 1, rect.h - 1);

        doc.regions.forEach((region, index) => {
            const bbox = region.bbox;
            if (!bbox) return;
            const x = rect.x + bbox[0] * rect.w;
            const y = rect.y + bbox[1] * rect.h;
            const width = Math.max(0, (bbox[2] - bbox[0]) * rect.w);
            const height = Math.max(0, (bbox[3] - bbox[1]) * rect.h);
            const selected = region.id === selectedId;

            context.fillStyle = selected
                ? "rgba(255,140,0,0.18)"
                : region.muted
                    ? "rgba(160,160,160,0.10)"
                    : "rgba(74,163,255,0.12)";
            context.fillRect(x, y, width, height);

            context.setLineDash(region.muted ? [4, 3] : []);
            context.lineWidth = selected ? 2 : 1;
            context.strokeStyle = selected ? "#ff8c00" : region.muted ? "#888888" : "#4aa3ff";
            context.strokeRect(x + 0.5, y + 0.5, Math.max(0, width - 1), Math.max(0, height - 1));
            context.setLineDash([]);

            const label = String(index + 1);
            context.font = "11px sans-serif";
            const labelWidth = context.measureText(label).width + 8;
            const labelY = Math.max(rect.y, y - 14);
            context.fillStyle = selected ? "#ff8c00" : "rgba(0,0,0,0.65)";
            context.fillRect(x, labelY, labelWidth, 14);
            context.fillStyle = "#ffffff";
            context.textBaseline = "middle";
            context.fillText(label, x + 4, labelY + 7);
        });

        if (drag && drag.mode === "create") {
            const bbox = normalizeBbox([drag.start.x, drag.start.y, drag.current.x, drag.current.y]);
            if (bbox) {
                const x = rect.x + bbox[0] * rect.w;
                const y = rect.y + bbox[1] * rect.h;
                const width = (bbox[2] - bbox[0]) * rect.w;
                const height = (bbox[3] - bbox[1]) * rect.h;
                context.setLineDash([4, 3]);
                context.lineWidth = 1;
                context.strokeStyle = "#ff8c00";
                context.strokeRect(x + 0.5, y + 0.5, width, height);
                context.setLineDash([]);
            }
        }
    }

    function pointerToNorm(event) {
        if (!lastRect) return null;
        const bounds = canvas.getBoundingClientRect();
        if (!bounds.width || !bounds.height) return null;
        const x = ((event.clientX - bounds.left) * CANVAS_SIZE) / bounds.width;
        const y = ((event.clientY - bounds.top) * CANVAS_SIZE) / bounds.height;
        return {
            x: clamp01((x - lastRect.x) / lastRect.w),
            y: clamp01((y - lastRect.y) / lastRect.h),
        };
    }

    function hitTest(position) {
        for (let index = doc.regions.length - 1; index >= 0; index -= 1) {
            const region = doc.regions[index];
            const bbox = region.bbox;
            if (!bbox) continue;
            if (
                position.x >= bbox[0] &&
                position.x <= bbox[2] &&
                position.y >= bbox[1] &&
                position.y <= bbox[3]
            ) {
                return region;
            }
        }
        return null;
    }

    function deleteSelected() {
        if (!selectedId) return;
        const index = doc.regions.findIndex((region) => region.id === selectedId);
        if (index < 0) return;
        doc.regions.splice(index, 1);
        selectedId = null;
        updateFields();
        draw();
        writeDoc();
    }

    function finishGesture(event) {
        if (!drag) return;
        const gesture = drag;
        drag = null;
        if (canvas.hasPointerCapture?.(event.pointerId)) {
            canvas.releasePointerCapture(event.pointerId);
        }

        if (gesture.mode === "create") {
            const bbox = normalizeBbox([
                gesture.start.x,
                gesture.start.y,
                gesture.current.x,
                gesture.current.y,
            ]);
            if (bbox && lastRect) {
                const widthPx = (bbox[2] - bbox[0]) * lastRect.w;
                const heightPx = (bbox[3] - bbox[1]) * lastRect.h;
                if (widthPx >= MIN_BOX_PX && heightPx >= MIN_BOX_PX) {
                    const region = createRegion(bbox);
                    doc.regions.push(region);
                    selectedId = region.id;
                    updateFields();
                    writeDoc();
                }
            }
        } else if (gesture.mode === "move") {
            writeDoc();
        }
        draw();
    }

    canvas.addEventListener("pointerdown", (event) => {
        if (event.button !== 0) return;
        canvas.focus({ preventScroll: true });
        const position = pointerToNorm(event);
        if (!position) return;

        const hit = hitTest(position);
        if (hit) {
            selectedId = hit.id;
            drag = { mode: "move", id: hit.id, start: position, origin: hit.bbox.slice() };
        } else {
            selectedId = null;
            drag = { mode: "create", start: position, current: position };
        }
        updateFields();
        draw();
        canvas.setPointerCapture?.(event.pointerId);
        event.preventDefault();
        event.stopPropagation();
    });

    canvas.addEventListener("pointermove", (event) => {
        if (!drag) return;
        const position = pointerToNorm(event);
        if (!position) return;

        if (drag.mode === "create") {
            drag.current = position;
            draw();
            return;
        }

        const region = doc.regions.find((item) => item.id === drag.id);
        if (!region) return;
        const deltaX = position.x - drag.start.x;
        const deltaY = position.y - drag.start.y;
        const width = drag.origin[2] - drag.origin[0];
        const height = drag.origin[3] - drag.origin[1];
        const x1 = clampNumber(drag.origin[0] + deltaX, 0, 1 - width);
        const y1 = clampNumber(drag.origin[1] + deltaY, 0, 1 - height);
        region.bbox = [x1, y1, x1 + width, y1 + height];
        draw();
    });

    canvas.addEventListener("pointerup", finishGesture);
    canvas.addEventListener("pointercancel", finishGesture);

    container.addEventListener("keydown", (event) => {
        if (event.key !== "Delete" && event.key !== "Backspace") return;
        const tagName = String((event.target && event.target.tagName) || "").toLowerCase();
        if (tagName === "input" || tagName === "textarea" || tagName === "select") return;
        if (!selectedId) return;
        event.preventDefault();
        event.stopPropagation();
        deleteSelected();
    });

    roleSelect.addEventListener("change", () => {
        const region = selectedRegion();
        if (!region) return;
        region.role = roleSelect.value === "text" ? "text" : "obj";
        draw();
        writeDoc();
    });

    enInput.addEventListener("input", () => {
        const region = selectedRegion();
        if (!region) return;
        region.nl.en = enInput.value;
        writeDoc();
    });

    zhInput.addEventListener("input", () => {
        const region = selectedRegion();
        if (!region) return;
        region.nl.zh = zhInput.value;
        writeDoc();
    });

    tagsInput.addEventListener("input", () => {
        const region = selectedRegion();
        if (!region) return;
        region.tags = splitTags(tagsInput.value);
        writeDoc();
    });

    deleteButton.addEventListener("click", deleteSelected);

    function loadReferenceImage(dataUrl) {
        const image = new Image();
        image.onload = () => {
            referenceImage = image;
            draw();
        };
        image.onerror = () => {
            console.warn("[ComfyUI-Studio-Suite] Region Layout: 参考图读取失败（仅影响本地预览）");
        };
        image.src = dataUrl;
    }

    loadButton.addEventListener("click", () => fileInput.click());
    fileInput.addEventListener("change", () => {
        const file = fileInput.files && fileInput.files[0];
        if (!file) return;
        const reader = new FileReader();
        reader.onload = () => {
            const dataUrl = typeof reader.result === "string" ? reader.result : "";
            if (!dataUrl) return;
            container.dataset.regionImage = dataUrl;
            imageName.textContent = file.name || "参考图";
            loadReferenceImage(dataUrl);
        };
        reader.readAsDataURL(file);
        fileInput.value = "";
    });

    if (container.dataset.regionImage) {
        imageName.textContent = "已载入参考图";
        loadReferenceImage(container.dataset.regionImage);
    }

    const layoutWidget = findWidget("layout_json");
    if (layoutWidget) {
        const originalCallback = layoutWidget.callback;
        layoutWidget.callback = function (...args) {
            const result = originalCallback ? originalCallback.apply(this, args) : undefined;
            if (!readingDoc) readDocFromWidget();
            return result;
        };
    }

    for (const name of ["canvas_width", "canvas_height"]) {
        const widget = findWidget(name);
        if (!widget) continue;
        const originalCallback = widget.callback;
        widget.callback = function (...args) {
            const result = originalCallback ? originalCallback.apply(this, args) : undefined;
            if (readingDoc) return result;
            const size = canvasSize();
            doc.canvas = { w: size.w, h: size.h };
            draw();
            writeDoc();
            return result;
        };
    }

    node.__studioSuiteRegionRefresh = readDocFromWidget;
    readDocFromWidget();

    if (typeof node.setSize === "function") {
        const width = Math.max(Number(node.size?.[0]) || 0, MIN_NODE_WIDTH);
        const height = Math.max(Number(node.size?.[1]) || 0, MIN_NODE_HEIGHT);
        node.setSize([width, height]);
    }
}

app.registerExtension({
    name: "comfyui_studio_suite.region_layout_node",
    async beforeRegisterNodeDef(nodeType, nodeData) {
        if (!nodeData || nodeData.name !== NODE_NAME) return;

        const originalOnNodeCreated = nodeType.prototype.onNodeCreated;
        nodeType.prototype.onNodeCreated = function (...args) {
            const result = originalOnNodeCreated ? originalOnNodeCreated.apply(this, args) : undefined;
            installRegionEditor(this);
            return result;
        };

        const originalOnConfigure = nodeType.prototype.onConfigure;
        nodeType.prototype.onConfigure = function (...args) {
            const result = originalOnConfigure ? originalOnConfigure.apply(this, args) : undefined;
            this.__studioSuiteRegionRefresh?.();
            return result;
        };
    },
});
