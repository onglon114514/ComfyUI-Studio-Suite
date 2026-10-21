import { app } from "/scripts/app.js";
import { getLegacyPromptStudioController } from "./prompt_studio_controller.js";

const NODE_NAME = "StudioSuiteRegionLayout";
const SCHEMA = "prompt-studio/region-v1";
const CANVAS_SIZE = 360; // 仅作为画布最小/兜底像素尺寸，实际尺寸随节点宽度变化
const CANVAS_MARGIN = 4;
const MIN_BOX_PX = 8;
const MIN_BOX = 0.01;
const HANDLE_SIZE = 8;
const HANDLE_TOLERANCE = 7;
const EDITOR_HEIGHT = 548;
const MIN_EDITOR_HEIGHT = 260;
const MAX_EDITOR_HEIGHT = 2000;
const MIN_NODE_WIDTH = 420;
const MIN_NODE_HEIGHT = 800;
const TOKEN_BUDGET = 4000;
const MAX_CANVAS_WIDTH = 720; // 画布显示宽度上限
const MAX_CANVAS_HEIGHT = 640; // 画布显示高度上限（竖图按高限制，不再被裁切）
const MAX_DPR = 2; // backing store 的 DPR 上限，控制高分屏下的像素总量

// == REGION_BOX_BRIDGE_BEGIN ==
const RegionBoxBridge = (() => {
    function tagText(tag) {
        return typeof tag === "string" ? tag : (tag && tag.tag) || "";
    }

    function normalizeTag(tag) {
        return String(tag || "")
            .trim()
            .toLowerCase()
            .replace(/\s+/g, "_")
            .replace(/^_+|_+$/g, "");
    }

    function normalizeTags(text) {
        const seen = new Set();
        const tags = [];
        for (const part of String(text || "").split(/[,，]/)) {
            const tag = normalizeTag(part);
            if (!tag || seen.has(tag)) continue;
            seen.add(tag);
            tags.push(tag);
        }
        return tags;
    }

    function boxToEditorText(box) {
        const source = box && typeof box === "object" ? box : {};
        const tags = Array.isArray(source.tags) ? source.tags.map(tagText).filter(Boolean) : [];
        const en = source.nl && typeof source.nl.en === "string" ? source.nl.en : "";
        const tagLine = tags.join(", ");
        if (!tagLine) return en;
        return en ? tagLine + "\n\n" + en : tagLine + "\n\n";
    }

    function editorTextToBox(text, box) {
        const source = box && typeof box === "object" ? box : {};
        const raw = typeof text === "string" ? text : "";
        const separator = raw.match(/\n\s*\n/);
        const tagsText = separator ? raw.slice(0, separator.index) : "";
        const enText = separator ? raw.slice(separator.index + separator[0].length) : raw;
        const nl = source.nl && typeof source.nl === "object" ? source.nl : {};
        return Object.assign({}, source, {
            nl: Object.assign({}, nl, { en: enText.trim() }),
            tags: normalizeTags(tagsText),
        });
    }

    return { boxToEditorText, editorTextToBox };
})();
window.RegionBoxBridge = RegionBoxBridge;
// == REGION_BOX_BRIDGE_END ==

// == REGION_RESIZE_BEGIN ==
// 纯几何工具：不依赖模块内其它符号，供画布交互与 node 测试共用。
const RegionResize = (() => {
    const DEFAULT_MIN_BOX = 0.01;
    const DEFAULT_TOLERANCE_PX = 7;
    const HANDLE_ORDER = ["nw", "ne", "se", "sw", "n", "e", "s", "w"];

    function clamp(value, lo, hi) {
        return Math.min(hi, Math.max(lo, value));
    }

    function normalizeBox(raw) {
        if (!Array.isArray(raw) || raw.length !== 4) return null;
        const values = raw.map((value) => Number(value));
        if (values.some((value) => !Number.isFinite(value))) return null;
        return values;
    }

    function handlePoints(box, width, height) {
        const x1 = box[0] * width;
        const x2 = box[2] * width;
        const y1 = box[1] * height;
        const y2 = box[3] * height;
        const midX = (x1 + x2) / 2;
        const midY = (y1 + y2) / 2;
        return [
            { handle: "nw", x: x1, y: y1 },
            { handle: "ne", x: x2, y: y1 },
            { handle: "se", x: x2, y: y2 },
            { handle: "sw", x: x1, y: y2 },
            { handle: "n", x: midX, y: y1 },
            { handle: "e", x: x2, y: midY },
            { handle: "s", x: midX, y: y2 },
            { handle: "w", x: x1, y: midY },
        ];
    }

    // 拖动某个 handle：只移动该 handle 对应的边，对边/对角保持固定。
    // 非法 handle 抛错；origin 非法返回 null。
    function resizeBbox(origin, handle, dx, dy, minSize) {
        if (!HANDLE_ORDER.includes(handle)) {
            throw new Error('RegionResize: unknown handle "' + String(handle) + '"');
        }
        const box = normalizeBox(origin);
        if (!box) return null;
        const minimum = Number.isFinite(Number(minSize)) && Number(minSize) > 0
            ? Math.min(1, Number(minSize))
            : DEFAULT_MIN_BOX;
        const moveX = Number.isFinite(Number(dx)) ? Number(dx) : 0;
        const moveY = Number.isFinite(Number(dy)) ? Number(dy) : 0;
        let x1 = box[0];
        let y1 = box[1];
        let x2 = box[2];
        let y2 = box[3];

        if (handle.includes("w")) {
            x1 = clamp(x1 + moveX, 0, Math.max(0, x2 - minimum));
        }
        if (handle.includes("e")) {
            x2 = clamp(x2 + moveX, Math.min(1, x1 + minimum), 1);
        }
        if (handle.includes("n")) {
            y1 = clamp(y1 + moveY, 0, Math.max(0, y2 - minimum));
        }
        if (handle.includes("s")) {
            y2 = clamp(y2 + moveY, Math.min(1, y1 + minimum), 1);
        }
        return [x1, y1, x2, y2];
    }

    // bbox / point 为 0..1 归一化坐标，rect 为画布内部像素尺寸（{w,h} 或 {width,height}）。
    // 容差包含边界（<=），四角优先于四边。
    function handleAtPoint(bbox, point, rect, tolerancePx) {
        const box = normalizeBox(bbox);
        if (!box) return null;
        const width = Number(rect && (rect.w !== undefined ? rect.w : rect.width));
        const height = Number(rect && (rect.h !== undefined ? rect.h : rect.height));
        if (!(width > 0) || !(height > 0)) return null;
        const pointX = Number(point && point.x);
        const pointY = Number(point && point.y);
        if (!Number.isFinite(pointX) || !Number.isFinite(pointY)) return null;
        let tolerance = DEFAULT_TOLERANCE_PX;
        if (tolerancePx !== undefined && tolerancePx !== null) {
            const value = Number(tolerancePx);
            if (Number.isFinite(value)) tolerance = Math.max(0, value);
        }
        const px = pointX * width;
        const py = pointY * height;
        for (const candidate of handlePoints(box, width, height)) {
            if (
                Math.abs(px - candidate.x) <= tolerance &&
                Math.abs(py - candidate.y) <= tolerance
            ) {
                return candidate.handle;
            }
        }
        return null;
    }

    return { resizeBbox, handleAtPoint };
})();
window.RegionResize = RegionResize;
// == REGION_RESIZE_END ==

// == REGION_LAYOUT_ARCHIVE_BEGIN ==
const RegionLayoutArchive = (() => {
    const MAX_ARCHIVES = 50;
    const STORAGE_KEY = "studio_suite_region_layouts";
    const DEFAULT_SCHEMA = "prompt-studio/region-v1";
    const DEFAULT_CANVAS = 1024;

    function isPlainObject(value) {
        return !!value && typeof value === "object" && !Array.isArray(value);
    }

    function cloneJson(value) {
        return JSON.parse(JSON.stringify(value));
    }

    function positiveInt(value, fallback) {
        const number = Math.round(Number(value));
        return Number.isFinite(number) && number > 0 ? number : fallback;
    }

    function hasArea(bbox) {
        if (!Array.isArray(bbox) || bbox.length !== 4) return false;
        const values = bbox.map((value) => Number(value));
        if (values.some((value) => !Number.isFinite(value))) return false;
        return values[2] > values[0] && values[3] > values[1];
    }

    function countActiveRegions(regions) {
        if (!Array.isArray(regions)) return 0;
        let total = 0;
        for (const region of regions) {
            if (!isPlainObject(region) || region.muted) continue;
            if (hasArea(region.bbox)) total += 1;
        }
        return total;
    }

    function normalizeDoc(raw) {
        const source = isPlainObject(raw) ? raw : {};
        const canvas = isPlainObject(source.canvas) ? source.canvas : {};
        const global = isPlainObject(source.global) ? source.global : {};
        const regions = [];
        if (Array.isArray(source.regions)) {
            for (const region of source.regions) {
                if (!isPlainObject(region)) continue;
                try {
                    regions.push(cloneJson(region));
                } catch (error) {
                    continue;
                }
            }
        }
        return {
            schema: typeof source.schema === "string" && source.schema ? source.schema : DEFAULT_SCHEMA,
            canvas: {
                w: positiveInt(canvas.w, DEFAULT_CANVAS),
                h: positiveInt(canvas.h, DEFAULT_CANVAS),
            },
            global: {
                caption_zh: typeof global.caption_zh === "string" ? global.caption_zh : "",
                caption_en: typeof global.caption_en === "string" ? global.caption_en : "",
            },
            regions: regions,
        };
    }

    function entryInfo(name, entry) {
        if (!isPlainObject(entry)) return null;
        const savedAt = Number(entry.savedAt);
        if (!Number.isFinite(savedAt)) return null;
        if (!isPlainObject(entry.doc)) return null;
        const canvas = entry.doc.canvas;
        if (!isPlainObject(canvas)) return null;
        const width = Number(canvas.w);
        const height = Number(canvas.h);
        if (!Number.isFinite(width) || width <= 0) return null;
        if (!Number.isFinite(height) || height <= 0) return null;
        if (!Array.isArray(entry.doc.regions)) return null;
        return {
            name: name,
            savedAt: savedAt,
            regions: countActiveRegions(entry.doc.regions),
            w: Math.round(width),
            h: Math.round(height),
        };
    }

    function listArchives(store) {
        const result = [];
        if (!isPlainObject(store)) return result;
        for (const rawName of Object.keys(store)) {
            const name = String(rawName).trim();
            if (!name) continue;
            const info = entryInfo(name, store[rawName]);
            if (info) result.push(info);
        }
        result.sort((a, b) => (b.savedAt - a.savedAt) || a.name.localeCompare(b.name));
        return result;
    }

    function cloneStore(store) {
        const next = {};
        if (!isPlainObject(store)) return next;
        for (const name of Object.keys(store)) {
            const entry = store[name];
            if (!isPlainObject(entry) || !isPlainObject(entry.doc)) continue;
            try {
                next[name] = { savedAt: entry.savedAt, doc: cloneJson(entry.doc) };
            } catch (error) {
                continue;
            }
        }
        return next;
    }

    function saveArchive(store, name, doc) {
        const key = typeof name === "string" ? name.trim() : "";
        if (!key) throw new Error("布局存档名称不能为空");
        const next = cloneStore(store);
        next[key] = { savedAt: Date.now(), doc: normalizeDoc(doc) };
        const names = Object.keys(next);
        if (names.length > MAX_ARCHIVES) {
            names.sort((a, b) => {
                const diff = (Number(next[a].savedAt) || 0) - (Number(next[b].savedAt) || 0);
                return diff !== 0 ? diff : a.localeCompare(b);
            });
            for (const stale of names.slice(0, names.length - MAX_ARCHIVES)) {
                delete next[stale];
            }
        }
        return next;
    }

    function deleteArchive(store, name) {
        const next = cloneStore(store);
        const key = typeof name === "string" ? name.trim() : "";
        if (key) delete next[key];
        return next;
    }

    function applyArchive(entry, widgets) {
        const source = isPlainObject(entry) && isPlainObject(entry.doc) ? entry.doc : entry;
        const doc = normalizeDoc(source);
        const targets = isPlainObject(widgets) ? widgets : {};
        const values = [
            ["canvas_width", doc.canvas.w],
            ["canvas_height", doc.canvas.h],
        ];
        for (const [key, value] of values) {
            const widget = targets[key];
            if (isPlainObject(widget)) widget.value = value;
        }
        return doc;
    }

    function defaultStorage() {
        try {
            return typeof localStorage !== "undefined" ? localStorage : null;
        } catch (error) {
            return null;
        }
    }

    function loadStore(storage) {
        const target = storage === undefined ? defaultStorage() : storage;
        if (!target) return {};
        let text = null;
        try {
            text = target.getItem(STORAGE_KEY);
        } catch (error) {
            return {};
        }
        if (typeof text !== "string" || !text) return {};
        let parsed = null;
        try {
            parsed = JSON.parse(text);
        } catch (error) {
            return {};
        }
        if (!isPlainObject(parsed)) return {};
        const next = {};
        for (const rawName of Object.keys(parsed)) {
            const name = String(rawName).trim();
            if (!name) continue;
            const info = entryInfo(name, parsed[rawName]);
            if (!info) continue;
            next[name] = { savedAt: info.savedAt, doc: normalizeDoc(parsed[rawName].doc) };
        }
        return next;
    }

    function saveStore(storage, store) {
        const target = storage === undefined ? defaultStorage() : storage;
        if (!target) return false;
        try {
            target.setItem(STORAGE_KEY, JSON.stringify(isPlainObject(store) ? store : {}));
            return true;
        } catch (error) {
            return false;
        }
    }

    function pad2(value) {
        return String(value).padStart(2, "0");
    }

    function suggestName(date) {
        const now = date instanceof Date && !Number.isNaN(date.getTime()) ? date : new Date();
        return (
            "layout_" +
            now.getFullYear() +
            pad2(now.getMonth() + 1) +
            pad2(now.getDate()) +
            "_" +
            pad2(now.getHours()) +
            pad2(now.getMinutes())
        );
    }

    return {
        MAX_ARCHIVES,
        STORAGE_KEY,
        listArchives,
        saveArchive,
        deleteArchive,
        applyArchive,
        loadStore,
        saveStore,
        suggestName,
    };
})();
window.RegionLayoutArchive = RegionLayoutArchive;
// == REGION_LAYOUT_ARCHIVE_END ==

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

    function makeModeButton(text) {
        const button = document.createElement("button");
        button.type = "button";
        button.textContent = text;
        button.setAttribute("aria-pressed", "false");
        button.style.cssText =
            "flex:none;height:24px;padding:0 10px;border:1px solid #555;border-radius:3px;" +
            "background:#2a2a2a;color:#ddd;font:12px sans-serif;cursor:pointer;white-space:nowrap;";
        return button;
    }

    const selectModeButton = makeModeButton("选择");
    const drawModeButton = makeModeButton("新建框");

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

    toolbar.appendChild(selectModeButton);
    toolbar.appendChild(drawModeButton);
    toolbar.appendChild(loadButton);
    toolbar.appendChild(imageName);
    toolbar.appendChild(fileInput);

    const canvas = document.createElement("canvas");
    canvas.width = CANVAS_SIZE;
    canvas.height = CANVAS_SIZE;
    canvas.tabIndex = 0;
    canvas.style.cssText =
        "display:block;flex:none;margin:0 auto;background:#222;border-radius:4px;" +
        "touch-action:none;outline:none;";

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

    const editPromptButton = document.createElement("button");
    editPromptButton.type = "button";
    editPromptButton.textContent = "打开 Prompt Studio 编辑选中框";
    editPromptButton.disabled = true;
    editPromptButton.style.cssText =
        "height:24px;padding:0 10px;border:1px solid #3f5f8a;border-radius:3px;" +
        "background:#1d2b3d;color:#bcd7ff;font:12px sans-serif;cursor:pointer;";

    const hint = document.createElement("div");
    hint.textContent =
        "选择：点选/拖动/拖角缩放 · 新建框：任意位置拖拽（可叠框） · Del 删除";
    hint.style.cssText = "font-size:11px;opacity:.6;";

    const tokenHint = document.createElement("span");
    tokenHint.textContent = "总 tokens: 0 / " + TOKEN_BUDGET;
    tokenHint.style.cssText = "margin-left:8px;";
    hint.appendChild(tokenHint);

    const filterHint = document.createElement("div");
    filterHint.textContent = "禁词过滤已启用";
    filterHint.style.cssText = "font-size:10px;opacity:.5;";

    function makeArchiveButton(text, theme) {
        const button = document.createElement("button");
        button.type = "button";
        button.textContent = text;
        button.style.cssText =
            "flex:none;height:24px;padding:0 8px;border:1px solid " + theme.border + ";" +
            "border-radius:3px;background:" + theme.background + ";color:" + theme.color + ";" +
            "font:12px sans-serif;cursor:pointer;white-space:nowrap;";
        return button;
    }

    const archiveSaveRow = document.createElement("div");
    archiveSaveRow.style.cssText = "display:flex;align-items:center;gap:6px;";

    const archiveNameInput = makeTextInput("存档名称");
    archiveNameInput.style.width = "auto";
    archiveNameInput.style.flex = "1";
    archiveNameInput.style.minWidth = "0";
    archiveNameInput.value = RegionLayoutArchive.suggestName();

    const archiveSaveButton = makeArchiveButton("保存布局", {
        border: "#3f5f8a",
        background: "#1d2b3d",
        color: "#bcd7ff",
    });

    archiveSaveRow.appendChild(archiveNameInput);
    archiveSaveRow.appendChild(archiveSaveButton);

    const archiveLoadRow = document.createElement("div");
    archiveLoadRow.style.cssText = "display:flex;align-items:center;gap:6px;";

    const archiveSelect = document.createElement("select");
    archiveSelect.style.cssText =
        "flex:1;min-width:0;height:24px;box-sizing:border-box;background:#1b1b1b;" +
        "border:1px solid #444;border-radius:3px;color:#ddd;font:12px sans-serif;";

    const archiveLoadButton = makeArchiveButton("载入", {
        border: "#555",
        background: "#2a2a2a",
        color: "#ddd",
    });
    const archiveDeleteButton = makeArchiveButton("删除存档", {
        border: "#6a3a3a",
        background: "#3a2020",
        color: "#f0b0b0",
    });

    archiveLoadRow.appendChild(archiveSelect);
    archiveLoadRow.appendChild(archiveLoadButton);
    archiveLoadRow.appendChild(archiveDeleteButton);

    const archiveStatus = document.createElement("div");
    archiveStatus.style.cssText =
        "min-height:12px;font-size:10px;opacity:.5;overflow:hidden;" +
        "text-overflow:ellipsis;white-space:nowrap;";

    const globalLabel = document.createElement("div");
    globalLabel.textContent = "全局描述（场景 / 风格 / 光线 / 镜头 / 氛围）";
    globalLabel.style.cssText = "font-size:10px;opacity:.6;";

    const globalInput = document.createElement("textarea");
    globalInput.placeholder =
        "A cluttered anime bedroom in a medium shot, soft even indoor lighting, cel-shaded anime style…";
    globalInput.style.cssText =
        "width:100%;height:40px;box-sizing:border-box;background:#1b1b1b;border:1px solid #444;" +
        "border-radius:3px;color:#ddd;font:12px sans-serif;padding:4px 6px;resize:vertical;";
    globalInput.addEventListener("input", () => {
        doc.global.caption_en = globalInput.value;
        writeDoc();
    });

    const globalRow = document.createElement("div");
    globalRow.style.cssText = "display:flex;flex-direction:column;gap:3px;";
    globalRow.appendChild(globalLabel);
    globalRow.appendChild(globalInput);

    container.appendChild(toolbar);
    container.appendChild(canvas);
    container.appendChild(globalRow);
    container.appendChild(editPromptButton);
    container.appendChild(roleRow);
    container.appendChild(enInput);
    container.appendChild(zhInput);
    container.appendChild(tagsInput);
    container.appendChild(archiveSaveRow);
    container.appendChild(archiveLoadRow);
    container.appendChild(archiveStatus);
    container.appendChild(hint);
    container.appendChild(filterHint);

    if (typeof node.addDOMWidget === "function") {
        node.addDOMWidget("region_editor", "customtext", container, {
            serialize: false,
            hideOnZoom: false,
            getMinHeight: () => MIN_EDITOR_HEIGHT,
            getMaxHeight: () => MAX_EDITOR_HEIGHT,
            getHeight: () => Math.max(MIN_EDITOR_HEIGHT, container.offsetHeight || EDITOR_HEIGHT),
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
    let editorMode = "select";

    function applyModeButtonStyle(button, active) {
        button.style.background = active ? "#7a3f00" : "#2a2a2a";
        button.style.borderColor = active ? "#ff8c00" : "#555";
        button.style.color = active ? "#ffe0b0" : "#ddd";
        button.setAttribute("aria-pressed", active ? "true" : "false");
    }

    // 只切换交互模式；不改动 doc、layout_json 或选中状态。
    function setEditorMode(mode) {
        editorMode = mode === "draw" ? "draw" : "select";
        applyModeButtonStyle(selectModeButton, editorMode === "select");
        applyModeButtonStyle(drawModeButton, editorMode === "draw");
        canvas.style.cursor = editorMode === "draw" ? "crosshair" : "";
    }

    selectModeButton.addEventListener("click", () => setEditorMode("select"));
    drawModeButton.addEventListener("click", () => setEditorMode("draw"));
    setEditorMode("select");

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
        const canEditPrompt = !!region && !region.muted;
        editPromptButton.disabled = !canEditPrompt;
        editPromptButton.title = canEditPrompt
            ? "把选中框的 tags / en 带入 Prompt Studio 编辑，关闭时回写"
            : region
                ? "选中框已静音，不能编辑"
                : "请先在画布中选中 1 个框";
        editPromptButton.style.opacity = canEditPrompt ? "1" : "0.45";
        editPromptButton.style.cursor = canEditPrompt ? "pointer" : "default";
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
            globalInput.value = doc.global.caption_en || doc.global.caption_zh || "";
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
        updateTokenHint();
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

    function estimateTokens(text) {
        let total = 0;
        for (const char of String(text || "")) {
            total += char.charCodeAt(0) > 127 ? 1 : 0.3;
        }
        return Math.round(total);
    }

    function computeTokenTotal() {
        let total = 0;
        for (const region of doc.regions) {
            if (!region || region.muted) continue;
            const bbox = region.bbox;
            if (!Array.isArray(bbox) || bbox[2] <= bbox[0] || bbox[3] <= bbox[1]) continue;
            const tags = Array.isArray(region.tags)
                ? region.tags.map(regionTagValue).filter(Boolean)
                : [];
            const nl = region.nl && typeof region.nl === "object" ? region.nl : {};
            const caption = String(nl.en || "").trim() || String(nl.zh || "").trim();
            const parts = [];
            if (tags.length) parts.push(tags.join(", "));
            if (caption) parts.push(caption);
            total += estimateTokens(parts.join(", "));
        }
        return total;
    }

    function updateTokenHint() {
        const total = computeTokenTotal();
        const overBudget = total > TOKEN_BUDGET;
        tokenHint.textContent = "总 tokens: " + total + " / " + TOKEN_BUDGET;
        tokenHint.style.color = overBudget ? "#ff6b6b" : "";
        tokenHint.style.opacity = overBudget ? "1" : "";
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

    // 画布内部像素 = 显示宽度 × devicePixelRatio；画布高度按 doc.canvas 宽高比推导。
    function canvasPixelScale() {
        const bounds = canvas.getBoundingClientRect();
        if (!bounds.width) return 1;
        return Math.max(0.01, canvas.width / bounds.width);
    }

    let lastCanvasDisplay = { w: 0, h: 0 };
    let widgetFitFrame = 0;

    function scheduleWidgetFit() {
        if (typeof requestAnimationFrame !== "function") return;
        if (typeof node.computeSize !== "function" || typeof node.setSize !== "function") return;
        if (widgetFitFrame) return;
        widgetFitFrame = requestAnimationFrame(() => {
            widgetFitFrame = 0;
            const width = Number(node.size?.[0]) || MIN_NODE_WIDTH;
            const currentHeight = Number(node.size?.[1]) || 0;
            const targetHeight = Math.max(MIN_EDITOR_HEIGHT, node.computeSize()[1]);
            // Grow-only: never fight the user when they made the node taller.
            if (targetHeight > currentHeight + 2) {
                node.setSize([width, targetHeight]);
                node.graph?.setDirtyCanvas?.(true, true);
            }
        });
    }

    function syncCanvasSize() {
        const size = canvasSize();
        const ratioWidth = Math.max(1, size.w);
        const ratioHeight = Math.max(1, size.h);
        const containerWidth = Math.max(CANVAS_SIZE, Number(container.clientWidth) || CANVAS_SIZE);
        const maxWidth = Math.min(containerWidth, MAX_CANVAS_WIDTH);
        // Fit inside both caps: wide nodes stop at MAX_CANVAS_WIDTH, portrait
        // canvases stop at MAX_CANVAS_HEIGHT instead of growing past the node.
        const fit = Math.min(maxWidth / ratioWidth, MAX_CANVAS_HEIGHT / ratioHeight);
        const displayWidth = Math.max(1, Math.floor(ratioWidth * fit));
        const displayHeight = Math.max(1, Math.floor(ratioHeight * fit));
        if (displayWidth !== lastCanvasDisplay.w || displayHeight !== lastCanvasDisplay.h) {
            lastCanvasDisplay = { w: displayWidth, h: displayHeight };
            canvas.style.width = displayWidth + "px";
            canvas.style.height = displayHeight + "px";
            scheduleWidgetFit();
        }
        const rawDpr = Number(window.devicePixelRatio) > 0 ? Number(window.devicePixelRatio) : 1;
        const dpr = Math.min(rawDpr, MAX_DPR);
        const pixelWidth = Math.max(1, Math.round(displayWidth * dpr));
        const pixelHeight = Math.max(1, Math.round(displayHeight * dpr));
        if (canvas.width !== pixelWidth || canvas.height !== pixelHeight) {
            canvas.width = pixelWidth;
            canvas.height = pixelHeight;
        }
    }

    function rectHandlePoints(bbox, rect) {
        const x1 = rect.x + bbox[0] * rect.w;
        const x2 = rect.x + bbox[2] * rect.w;
        const y1 = rect.y + bbox[1] * rect.h;
        const y2 = rect.y + bbox[3] * rect.h;
        const midX = (x1 + x2) / 2;
        const midY = (y1 + y2) / 2;
        return [
            { handle: "nw", x: x1, y: y1 },
            { handle: "ne", x: x2, y: y1 },
            { handle: "se", x: x2, y: y2 },
            { handle: "sw", x: x1, y: y2 },
            { handle: "n", x: midX, y: y1 },
            { handle: "e", x: x2, y: midY },
            { handle: "s", x: midX, y: y2 },
            { handle: "w", x: x1, y: midY },
        ];
    }

    function drawSelectionHandles(context, rect) {
        const region = selectedRegion();
        if (!region || !Array.isArray(region.bbox)) return;
        const scale = canvasPixelScale();
        const size = Math.max(4, HANDLE_SIZE * scale);
        const half = size / 2;
        const lineWidth = Math.max(1, scale);
        for (const point of rectHandlePoints(region.bbox, rect)) {
            context.fillStyle = "#1a1a1a";
            context.fillRect(point.x - half, point.y - half, size, size);
            context.strokeStyle = "#ff8c00";
            context.lineWidth = lineWidth;
            context.strokeRect(
                point.x - half + lineWidth / 2,
                point.y - half + lineWidth / 2,
                Math.max(1, size - lineWidth),
                Math.max(1, size - lineWidth)
            );
        }
    }

    function computeLayoutRect(displayScale) {
        const pixelWidth = Math.max(1, canvas.width || CANVAS_SIZE);
        const pixelHeight = Math.max(1, canvas.height || CANVAS_SIZE);
        const width = Math.max(1, doc.canvas.w);
        const height = Math.max(1, doc.canvas.h);
        const margin = Math.min(
            CANVAS_MARGIN * Math.max(1, displayScale || 1),
            Math.floor(Math.min(pixelWidth, pixelHeight) / 4)
        );
        const maxWidth = Math.max(1, pixelWidth - margin * 2);
        const maxHeight = Math.max(1, pixelHeight - margin * 2);
        const scale = Math.min(maxWidth / width, maxHeight / height);
        const drawWidth = width * scale;
        const drawHeight = height * scale;
        return {
            x: (pixelWidth - drawWidth) / 2,
            y: (pixelHeight - drawHeight) / 2,
            w: drawWidth,
            h: drawHeight,
        };
    }

    function draw() {
        syncCanvasSize();
        const context = canvas.getContext("2d");
        if (!context) return;
        const pixelWidth = Math.max(1, canvas.width);
        const pixelHeight = Math.max(1, canvas.height);
        const scale = canvasPixelScale();

        context.setTransform(1, 0, 0, 1, 0, 0);
        context.clearRect(0, 0, pixelWidth, pixelHeight);
        context.fillStyle = "#222";
        context.fillRect(0, 0, pixelWidth, pixelHeight);

        const rect = computeLayoutRect(scale);
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
        context.lineWidth = Math.max(1, scale);
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
            context.lineWidth = (selected ? 2 : 1) * Math.max(1, scale);
            context.strokeStyle = selected ? "#ff8c00" : region.muted ? "#888888" : "#4aa3ff";
            context.strokeRect(x + 0.5, y + 0.5, Math.max(0, width - 1), Math.max(0, height - 1));
            context.setLineDash([]);

            const label = String(index + 1);
            context.font = (11 * Math.max(1, scale)) + "px sans-serif";
            const labelWidth = context.measureText(label).width + 8 * Math.max(1, scale);
            const labelHeight = 14 * Math.max(1, scale);
            const labelY = Math.max(rect.y, y - labelHeight);
            context.fillStyle = selected ? "#ff8c00" : "rgba(0,0,0,0.65)";
            context.fillRect(x, labelY, labelWidth, labelHeight);
            context.fillStyle = "#ffffff";
            context.textBaseline = "middle";
            context.fillText(label, x + 4 * Math.max(1, scale), labelY + labelHeight / 2);
        });

        if (drag && drag.mode === "create") {
            const bbox = normalizeBbox([drag.start.x, drag.start.y, drag.current.x, drag.current.y]);
            if (bbox) {
                const x = rect.x + bbox[0] * rect.w;
                const y = rect.y + bbox[1] * rect.h;
                const width = (bbox[2] - bbox[0]) * rect.w;
                const height = (bbox[3] - bbox[1]) * rect.h;
                context.setLineDash([4, 3]);
                context.lineWidth = Math.max(1, scale);
                context.strokeStyle = "#ff8c00";
                context.strokeRect(x + 0.5, y + 0.5, width, height);
                context.setLineDash([]);
            }
        }

        drawSelectionHandles(context, rect);

        updateTokenHint();
    }

    function pointerToNorm(event) {
        if (!lastRect) return null;
        const bounds = canvas.getBoundingClientRect();
        if (!bounds.width || !bounds.height) return null;
        const pixelWidth = Math.max(1, canvas.width);
        const pixelHeight = Math.max(1, canvas.height);
        const x = ((event.clientX - bounds.left) * pixelWidth) / bounds.width;
        const y = ((event.clientY - bounds.top) * pixelHeight) / bounds.height;
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

    function hitHandleAt(position) {
        const region = selectedRegion();
        if (!region || !lastRect) return null;
        return RegionResize.handleAtPoint(
            region.bbox,
            position,
            { w: lastRect.w, h: lastRect.h },
            HANDLE_TOLERANCE * canvasPixelScale()
        );
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
                const minBoxPx = MIN_BOX_PX * canvasPixelScale();
                if (widthPx >= minBoxPx && heightPx >= minBoxPx) {
                    const region = createRegion(bbox);
                    doc.regions.push(region);
                    selectedId = region.id;
                    updateFields();
                    writeDoc();
                }
            }
        } else if (gesture.mode === "move" || gesture.mode === "resize") {
            writeDoc();
        }
        draw();
    }

    const HANDLE_CURSORS = {
        n: "ns-resize",
        s: "ns-resize",
        e: "ew-resize",
        w: "ew-resize",
        nw: "nwse-resize",
        se: "nwse-resize",
        ne: "nesw-resize",
        sw: "nesw-resize",
    };

    function updateHandleCursor(event) {
        if (editorMode === "draw") {
            canvas.style.cursor = "crosshair";
            return;
        }
        const position = pointerToNorm(event);
        const handle = position ? hitHandleAt(position) : null;
        canvas.style.cursor = handle ? HANDLE_CURSORS[handle] : "";
    }

    canvas.addEventListener("pointerdown", (event) => {
        if (event.button !== 0) return;
        if (drag) return;
        canvas.focus({ preventScroll: true });
        const position = pointerToNorm(event);
        if (!position) return;

        // select：handle → resize，命中框 → 选中+move，空白 → create。
        // draw：忽略 handle / 命中框，任意位置一律 create（因此可以叠框）。
        const drawMode = editorMode === "draw";
        const handle = drawMode ? null : hitHandleAt(position);
        if (handle) {
            const region = selectedRegion();
            if (region) {
                drag = {
                    mode: "resize",
                    id: region.id,
                    handle: handle,
                    start: position,
                    origin: region.bbox.slice(),
                };
            }
        }

        if (!drag) {
            const hit = drawMode ? null : hitTest(position);
            if (hit) {
                selectedId = hit.id;
                drag = { mode: "move", id: hit.id, start: position, origin: hit.bbox.slice() };
            } else {
                selectedId = null;
                drag = { mode: "create", start: position, current: position };
            }
        }
        updateFields();
        draw();
        canvas.setPointerCapture?.(event.pointerId);
        event.preventDefault();
        event.stopPropagation();
    });

    canvas.addEventListener("pointermove", (event) => {
        if (!drag) {
            updateHandleCursor(event);
            return;
        }
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

        if (drag.mode === "resize") {
            const next = RegionResize.resizeBbox(
                drag.origin,
                drag.handle,
                deltaX,
                deltaY,
                MIN_BOX
            );
            if (next) region.bbox = next;
            draw();
            return;
        }

        const width = drag.origin[2] - drag.origin[0];
        const height = drag.origin[3] - drag.origin[1];
        const x1 = clampNumber(drag.origin[0] + deltaX, 0, 1 - width);
        const y1 = clampNumber(drag.origin[1] + deltaY, 0, 1 - height);
        region.bbox = [x1, y1, x1 + width, y1 + height];
        draw();
    });

    canvas.addEventListener("pointerleave", () => {
        if (!drag) canvas.style.cursor = editorMode === "draw" ? "crosshair" : "";
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

    editPromptButton.addEventListener("click", () => {
        const region = selectedRegion();
        if (!region || region.muted) return;
        getLegacyPromptStudioController().openRegionEditor({
            initialText: RegionBoxBridge.boxToEditorText(region),
            title: "Region Prompt Studio",
            onSave: (text) => {
                const index = doc.regions.findIndex((item) => item.id === region.id);
                if (index < 0) return;
                doc.regions[index] = RegionBoxBridge.editorTextToBox(text, doc.regions[index]);
                writeDoc();
                updateFields();
                draw();
            },
        });
    });

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
        // Internal storage only: hide the textarea entirely. ComfyUI multiline
        // widgets grow with the node and would eat the editor's space.
        layoutWidget.computeSize = () => [0, -4];
        layoutWidget.type = "hidden";
        if (layoutWidget.inputEl) {
            layoutWidget.inputEl.style.display = "none";
            layoutWidget.inputEl.style.height = "0";
            layoutWidget.inputEl.style.minHeight = "0";
        }
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

    let archiveStore = RegionLayoutArchive.loadStore();

    function refreshArchiveButtons() {
        const hasSelection = !!archiveSelect.value && !!archiveStore[archiveSelect.value];
        for (const button of [archiveLoadButton, archiveDeleteButton]) {
            button.disabled = !hasSelection;
            button.style.opacity = hasSelection ? "1" : "0.45";
            button.style.cursor = hasSelection ? "pointer" : "default";
        }
    }

    function renderArchiveOptions(preferredName) {
        const entries = RegionLayoutArchive.listArchives(archiveStore);
        const previous = typeof preferredName === "string" ? preferredName : archiveSelect.value;
        archiveSelect.textContent = "";
        if (!entries.length) {
            const option = document.createElement("option");
            option.value = "";
            option.textContent = "（无存档）";
            archiveSelect.appendChild(option);
            archiveSelect.disabled = true;
            refreshArchiveButtons();
            return;
        }
        archiveSelect.disabled = false;
        for (const entry of entries) {
            const option = document.createElement("option");
            option.value = entry.name;
            option.textContent = entry.name + " · " + entry.regions + "区 · " + entry.w + "x" + entry.h;
            archiveSelect.appendChild(option);
        }
        archiveSelect.value = entries.some((entry) => entry.name === previous)
            ? previous
            : entries[0].name;
        refreshArchiveButtons();
    }

    function setArchiveStatus(text, isError) {
        archiveStatus.textContent = text || "";
        archiveStatus.style.color = isError ? "#ff8080" : "";
        archiveStatus.style.opacity = isError ? "1" : ".5";
    }

    function syncCanvasWidget(name, value) {
        const widget = findWidget(name);
        if (widget) widget.value = value;
        const element = widget && (widget.inputEl || widget.element);
        if (element && typeof element.value === "string") element.value = String(value);
    }

    function saveCurrentArchive() {
        const name = archiveNameInput.value.trim();
        let next;
        try {
            next = RegionLayoutArchive.saveArchive(archiveStore, name, doc);
        } catch (error) {
            setArchiveStatus(String((error && error.message) || "保存失败"), true);
            archiveNameInput.focus();
            return;
        }
        archiveStore = next;
        RegionLayoutArchive.saveStore(undefined, archiveStore);
        archiveNameInput.value = name;
        renderArchiveOptions(name);
        setArchiveStatus("已保存 " + name);
    }

    function loadSelectedArchive() {
        const name = archiveSelect.value;
        const entry = name ? archiveStore[name] : null;
        if (!entry) {
            setArchiveStatus("请先选择存档", true);
            return;
        }
        doc = RegionLayoutArchive.applyArchive(entry, {
            canvas_width: findWidget("canvas_width"),
            canvas_height: findWidget("canvas_height"),
        });
        selectedId = null;
        syncCanvasWidget("canvas_width", doc.canvas.w);
        syncCanvasWidget("canvas_height", doc.canvas.h);
        updateFields();
        draw();
        writeDoc();
        archiveNameInput.value = name;
        setArchiveStatus("已载入 " + name);
    }

    function deleteSelectedArchive() {
        const name = archiveSelect.value;
        if (!name || !archiveStore[name]) {
            setArchiveStatus("请先选择存档", true);
            return;
        }
        archiveStore = RegionLayoutArchive.deleteArchive(archiveStore, name);
        RegionLayoutArchive.saveStore(undefined, archiveStore);
        renderArchiveOptions();
        setArchiveStatus("已删除 " + name);
    }

    archiveSaveButton.addEventListener("click", saveCurrentArchive);
    archiveLoadButton.addEventListener("click", loadSelectedArchive);
    archiveDeleteButton.addEventListener("click", deleteSelectedArchive);
    archiveSelect.addEventListener("change", () => {
        refreshArchiveButtons();
        setArchiveStatus("");
    });
    archiveNameInput.addEventListener("keydown", (event) => {
        if (event.key !== "Enter") return;
        event.preventDefault();
        event.stopPropagation();
        saveCurrentArchive();
    });

    renderArchiveOptions();

    function resizeCanvasToNode() {
        syncCanvasSize();
        draw();
    }

    let resizeFrame = 0;
    function scheduleCanvasResize() {
        if (typeof requestAnimationFrame !== "function") {
            resizeCanvasToNode();
            return;
        }
        if (resizeFrame) return;
        resizeFrame = requestAnimationFrame(() => {
            resizeFrame = 0;
            resizeCanvasToNode();
        });
    }

    if (typeof ResizeObserver === "function") {
        const observer = new ResizeObserver(scheduleCanvasResize);
        observer.observe(container);
        node.__studioSuiteRegionStopResize = () => {
            observer.disconnect();
            if (resizeFrame && typeof cancelAnimationFrame === "function") {
                cancelAnimationFrame(resizeFrame);
                resizeFrame = 0;
            }
        };
    } else if (typeof window.addEventListener === "function") {
        window.addEventListener("resize", scheduleCanvasResize);
    }

    const originalOnRemoved = node.onRemoved;
    node.onRemoved = function (...args) {
        node.__studioSuiteRegionStopResize?.();
        return typeof originalOnRemoved === "function"
            ? originalOnRemoved.apply(this, args)
            : undefined;
    };

    node.__studioSuiteRegionRefresh = readDocFromWidget;
    node.__studioSuiteRegionResize = () => {
        resizeCanvasToNode();
        scheduleCanvasResize();
    };
    readDocFromWidget();
    resizeCanvasToNode();

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
            this.__studioSuiteRegionResize?.();
            return result;
        };
    },
});
