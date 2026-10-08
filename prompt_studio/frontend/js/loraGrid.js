// loraGrid.js — 高性能 LoRA 网格（虚拟化 + 懒加载 + 限并发）
// 替换旧的全量渲染网格（实测 272 张卡片 + 272 张缩略图一次性进 DOM），
// 改为：虚拟化（DOM 卡片 ≤ 40）+ 缩略图懒加载（IntersectionObserver）+ 限并发（≤ 4）。
// 复用现有类名与 DOM 层级，保证外观不变。
//
// 由下一个任务在 index.html 中接线：
//   <script type="module" src="./js/loraGrid.js?v=studio-suite-..."></script>
//   import { installLoraGrid } from "./loraGrid.js";
//   installLoraGrid();   // 需在旧网格渲染进 DOM 之后调用

const LORA_API = "/studio-suite/prompt-studio/autocomplete/loras";
const PREVIEW_BASE = "/studio-suite/prompt-studio/model-preview";
const MAX_DOM_CARDS = 40;     // 硬性：DOM 里卡片数 ≤ 40
const OVERSCAN = 6;           // 可见窗口上下各多渲染几行
const CARD_HEIGHT = 152;      // 兜底行高（img 高 120px + 信息区），运行时会被实测值覆盖
const MAX_IMG_CONCURRENT = 4; // 缩略图请求并发 ≤ 4
const PRELOAD_MARGIN = "300px 0px"; // 接近视口 300px 就开始加载

let box = null;         // 我们自己的 group-tags 容器
let scroller = null;    // 滚动容器
let items = [];         // 归一化后的 LoRA 列表
let cols = 1;           // 每行卡片数（实测）
let rowH = CARD_HEIGHT; // 每行高度（实测）
let totalRows = 0;
let ticking = false;
let activeLoads = 0;
const loadQueue = [];      // 等待加载缩略图的 img 队列
const observed = new Set();  // 当前被 IO 观察的 img
const loadedNames = new Set(); // 已成功加载过的 name，避免滚动来回时重复请求

function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text != null) node.textContent = text;
    return node;
}

// 找到旧容器：含最多 div.group-extra-network 的 div.group-tags
function findOldContainer() {
    const tags = document.querySelectorAll("div.group-tags");
    let best = null, bestCount = 0;
    for (const t of tags) {
        const n = t.querySelectorAll(":scope > div.group-extra-network").length;
        if (n > bestCount) { bestCount = n; best = t; }
    }
    return bestCount > 0 ? best : null;
}

// 向上找第一个可滚动祖先
function findScrollParent(node) {
    let el = node.parentElement;
    while (el && el !== document.body) {
        const s = getComputedStyle(el);
        if (/(auto|scroll)/.test(s.overflowY) || /(auto|scroll)/.test(s.overflow)) return el;
        el = el.parentElement;
    }
    return document.scrollingElement || document.documentElement;
}

function previewUrl(name) {
    return `${PREVIEW_BASE}?type=loras&name=${encodeURIComponent(name)}&w=192`;
}

// 缩略图加载队列：并发 ≤ MAX_IMG_CONCURRENT，且同一 name 只请求一次
function pumpLoads() {
    while (activeLoads < MAX_IMG_CONCURRENT && loadQueue.length) {
        const img = loadQueue.shift();
        if (!img.isConnected || img.dataset.loaded) continue; // 已被虚拟化回收
        const name = img.dataset.loraName;
        if (loadedNames.has(name)) { img.src = previewUrl(name); img.dataset.loaded = "1"; continue; }
        activeLoads++;
        const done = (ok) => { if (ok) loadedNames.add(name); activeLoads--; pumpLoads(); };
        img.onload = () => done(true);
        img.onerror = () => done(false);
        img.src = previewUrl(name);
        img.dataset.loaded = "1";
    }
}

const io = new IntersectionObserver((entries) => {
    for (const entry of entries) {
        if (entry.isIntersecting) {
            io.unobserve(entry.target);
            observed.delete(entry.target);
            loadQueue.push(entry.target);
            pumpLoads();
        }
    }
}, { rootMargin: PRELOAD_MARGIN });

// 每张卡片：严格复用现有类名与层级
function buildCard(item) {
    const name = item.name;
    const card = el("div", "group-extra-network");

    const info = el("div", "lora-infomation");
    info.title = "查看Lora信息";
    const icon = el("div", "icon-svg icon-svg-about");
    icon.dataset.name = "name"; // 沿用现有图标的 data-name="name" 惯例（见 index.html）
    info.appendChild(icon);
    // 名字文本：handoff 提到 CSS 里有 .extra-network-name 类；具体位置待下一任务用 capture_card_chain.cjs 确认
    info.appendChild(el("span", "extra-network-name", name));

    // 实测链：img 外包一层无类名 div
    const imgWrap = el("div");
    const img = el("img", "extra-network-preview");
    img.alt = name;
    img.title = name;
    img.dataset.loraName = name;
    img.loading = "lazy";
    imgWrap.appendChild(img);

    card.appendChild(info);
    card.appendChild(imgWrap);
    card.addEventListener("click", () => insertLora(name));
    return card;
}

// 点击插入：TODO——复用现有插入方式，文本格式/权重待确认
function insertLora(name) {
    const input = document.getElementById("weilin_prompt_text_input");
    if (!input) return;
    // TODO: 文本格式（<lora:name:weight>）与权重需对照现有实现确认，勿猜接口。
    const tag = `<lora:${name}:1>`;
    const prefix = input.value.trimEnd();
    input.value = prefix + (prefix && !/[,，\n]$/.test(prefix) ? ", " : "") + tag + ", ";
    input.dispatchEvent(new Event("input", { bubbles: true }));
    input.dispatchEvent(new Event("change", { bubbles: true }));
    window.syncPromptToHost?.(false);
}

// 实测列数与行高（用一批探针卡片，测完即删，同一帧内移除不闪烁）
function measure() {
    const probes = [];
    for (let i = 0; i < 40; i++) probes.push(buildCard({ name: "__probe__" }));
    box.append(...probes);
    const first = probes[0];
    const top0 = first.offsetTop;
    const cardH = first.offsetHeight || CARD_HEIGHT;
    let measuredCols = 0, measuredRowH = cardH;
    for (let i = 1; i < probes.length; i++) {
        if (probes[i].offsetTop > top0) { measuredCols = i; measuredRowH = probes[i].offsetTop - top0; break; }
    }
    if (!measuredCols) measuredCols = Math.max(1, Math.floor((box.clientWidth || 1000) / (first.offsetWidth || 128)));
    probes.forEach(p => p.remove());
    cols = Math.max(1, measuredCols);
    rowH = Math.max(cardH, measuredRowH);
}

// 可见行范围（含过扫描），并硬性保证卡片数 ≤ MAX_DOM_CARDS
function visibleRows() {
    const sRect = scroller.getBoundingClientRect();
    const bRect = box.getBoundingClientRect();
    const viewTop = Math.max(0, sRect.top - bRect.top);
    const viewBottom = Math.min(bRect.height, sRect.bottom - bRect.top);
    let sRow = Math.max(0, Math.floor(viewTop / rowH) - OVERSCAN);
    let eRow = Math.min(totalRows, Math.ceil(viewBottom / rowH) + OVERSCAN);
    const maxRows = Math.max(1, Math.floor(MAX_DOM_CARDS / cols));
    if (eRow - sRow > maxRows) {
        const mid = (sRow + eRow) / 2;
        sRow = Math.max(0, Math.floor(mid - maxRows / 2));
        eRow = Math.min(totalRows, sRow + maxRows);
        sRow = Math.max(0, eRow - maxRows);
    }
    return [sRow, eRow];
}

// 虚拟化渲染：回收离开窗口的卡片，重建可见卡片
function render() {
    if (!box || !items.length || !scroller) return;
    const [sRow, eRow] = visibleRows();
    const start = sRow * cols;
    const end = Math.min(items.length, eRow * cols);

    for (const img of observed) {
        io.unobserve(img);
        const i = loadQueue.indexOf(img);
        if (i >= 0) loadQueue.splice(i, 1);
    }
    observed.clear();

    const frag = document.createDocumentFragment();
    const top = el("div");
    top.style.cssText = `flex-basis:100%;width:100%;height:${sRow * rowH}px`;
    frag.appendChild(top);
    const newImgs = [];
    for (let i = start; i < end; i++) {
        const card = buildCard(items[i]);
        frag.appendChild(card);
        newImgs.push(card.querySelector(".extra-network-preview"));
    }
    const bottom = el("div");
    bottom.style.cssText = `flex-basis:100%;width:100%;height:${(totalRows - eRow) * rowH}px`;
    frag.appendChild(bottom);

    box.replaceChildren(frag);
    for (const img of newImgs) { io.observe(img); observed.add(img); }
}

async function loadLoras() {
    try {
        const res = await fetch(LORA_API);
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const payload = await res.json();
        // 先打印一次真实结构，再写解析（勿删此日志）
        console.log("[loraGrid] /autocomplete/loras 返回结构：", payload);
        items = extractLoras(payload);
        if (!items.length) { console.warn("[loraGrid] 未解析出任何 LoRA，请检查上面的结构日志"); return; }
        totalRows = Math.max(1, Math.ceil(items.length / cols));
        box.style.height = `${totalRows * rowH}px`;
        render();
    } catch (err) {
        console.warn("[loraGrid] 拉取 LoRA 列表失败：", err);
    }
}

// 兼容多种返回形状：数组 / {items} / {loras} / {extra_networks}；每项取 name
function extractLoras(payload) {
    const raw = Array.isArray(payload) ? payload
        : Array.isArray(payload?.items) ? payload.items
        : Array.isArray(payload?.loras) ? payload.loras
        : Array.isArray(payload?.extra_networks) ? payload.extra_networks
        : [];
    return raw.map(item => {
        if (typeof item === "string") return { name: item };
        const name = item?.name || item?.model_name || item?.filename || item?.prompt || "";
        return { name: String(name), raw: item };
    }).filter(x => x.name);
}

export function installLoraGrid() {
    if (window.__studioSuiteLoraGridInstalled) return;
    const oldBox = findOldContainer();
    if (!oldBox) return; // 未找到旧网格（可能尚未渲染），本次不动作
    window.__studioSuiteLoraGridInstalled = true;

    oldBox.style.display = "none"; // 隐藏旧卡片
    box = el("div", "group-tags");
    box.style.display = "flex";
    oldBox.insertAdjacentElement("afterend", box); // 同一位置新建容器

    scroller = findScrollParent(box);
    measure();
    const onScroll = () => {
        if (ticking) return;
        ticking = true;
        requestAnimationFrame(() => { ticking = false; render(); });
    };
    scroller.addEventListener("scroll", onScroll, { passive: true });
    window.addEventListener("resize", onScroll);
    loadLoras();
}
