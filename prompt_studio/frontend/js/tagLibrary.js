const API = "/studio-suite/prompt-studio/library/";
const kinds = { unknown: "未确认类别", general: "普通标签", character: "角色", copyright: "作品", artist: "画师", meta: "元标签" };
let dialog, opener, life, searchRequest, detailRequest, timer, workTimer, tagLookup, lookupTag, epoch = 0;
let searchEpoch = 0, detailEpoch = 0, workEpoch = 0, page = 1, totalPages = 1, initial = "", session = null;
let hostKind = "positive";
let categoryItems = [], categoryById = new Map(), categoryChildren = new Map();

function el(tag, attrs = {}, text = "") {
    const node = document.createElement(tag);
    for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, value);
    node.textContent = text;
    return node;
}
function button(text, action) {
    const node = el("button", { type: "button" }, text);
    node.addEventListener("click", action);
    return node;
}
function part(name) { return dialog?.querySelector(`[data-part="${name}"]`); }
function message(text) { if (part("message")) part("message").textContent = text; }
async function get(name, params = {}, signal = life?.signal) {
    const response = await fetch(API + name + "?" + new URLSearchParams(params), { signal });
    const data = await response.json().catch(() => ({}));
    if (!response.ok || data.success === false) throw new Error(data.error || `HTTP ${response.status}`);
    return data;
}
function failure(error) {
    if (error.name !== "AbortError" && dialog) message(`查询未完成：${error.message}`);
}
function closeLibrary() {
    if (!dialog) return;
    epoch++; searchEpoch++; detailEpoch++;
    clearTimeout(timer); clearTimeout(workTimer);
    life?.abort(); searchRequest?.abort(); detailRequest?.abort();
    dialog.close(); dialog.remove(); dialog = null;
    opener?.focus(); opener = null;
    page = 1; totalPages = 1; initial = "";
    categoryItems = []; categoryById = new Map(); categoryChildren = new Map();
}

function categoryLabel(group) {
    return `${group.label_zh || group.id} (${Number(group.count || 0).toLocaleString()})`;
}
function categoryDescendants(groupId) {
    const result = [];
    const walk = (parentId, depth) => {
        for (const child of categoryChildren.get(parentId) || []) {
            result.push({ group: child, depth });
            walk(child.id, depth + 1);
        }
    };
    walk(groupId, 0);
    return result;
}
function rebuildCategoryDetails(runSearch = true) {
    const root = part("category-root"), branch = part("category-branch"), category = part("category");
    const selected = branch.value || root.value;
    category.replaceChildren();
    if (!selected) {
        category.append(el("option", { value: "" }, "全部细分类"));
        category.disabled = true;
    } else {
        const base = categoryById.get(selected);
        category.append(el("option", { value: selected }, `整个：${categoryLabel(base)}`));
        for (const { group, depth } of categoryDescendants(selected)) {
            category.append(el("option", { value: group.id }, `${"　".repeat(depth)}${categoryLabel(group)}`));
        }
        category.disabled = false;
        category.value = selected;
    }
    if (runSearch) search(true);
}
function rebuildCategoryBranches(runSearch = true) {
    const root = part("category-root"), branch = part("category-branch");
    branch.replaceChildren();
    if (!root.value) {
        branch.append(el("option", { value: "" }, "全部分类页"));
        branch.disabled = true;
    } else {
        const base = categoryById.get(root.value);
        branch.append(el("option", { value: root.value }, `整个：${categoryLabel(base)}`));
        for (const child of categoryChildren.get(root.value) || []) {
            branch.append(el("option", { value: child.id }, categoryLabel(child)));
        }
        branch.disabled = false;
        branch.value = root.value;
    }
    rebuildCategoryDetails(runSearch);
}
function mountCategoryTree(items) {
    categoryItems = Array.isArray(items) ? items.slice(0, 4000) : [];
    categoryById = new Map(categoryItems.map(group => [group.id, group]));
    categoryChildren = new Map();
    for (const group of categoryItems) {
        const parent = categoryById.has(group.parent_id) ? group.parent_id : "";
        if (!categoryChildren.has(parent)) categoryChildren.set(parent, []);
        categoryChildren.get(parent).push(group);
    }
    for (const children of categoryChildren.values()) {
        children.sort((a, b) => (a.label_zh || a.id).localeCompare(b.label_zh || b.id, "zh-CN"));
    }
    const root = part("category-root");
    root.replaceChildren(el("option", { value: "" }, "全部分类库"));
    for (const group of categoryChildren.get("") || []) root.append(el("option", { value: group.id }, categoryLabel(group)));
    rebuildCategoryBranches(false);
}

function selectedTarget() {
    const target = part("target")?.value;
    const xml = window.studioSuiteXmlEditor?.current();
    if (target === "xml") {
        if (!xml?.active || !xml.editing) throw new Error("请先打开要写入的 XML 字段，再选择当前 XML 字段");
        return { xml: true };
    }
    if (xml?.active) throw new Error("XML 模式下请选择当前 XML 字段，或先切换回普通模式");
    const input = document.getElementById(target === "negative" ? "weilin_prompt_text_neg_input" : "weilin_prompt_text_input");
    if (!input || !input.getClientRects().length) throw new Error("当前节点不提供这个输入，请选择可用的目标");
    return { input };
}
function insert(tag, targetSession) {
    if (targetSession !== session) { message("节点已改变，请重新打开词典"); return; }
    try {
        const target = selectedTarget();
        if (target.xml) {
            if (!window.studioSuiteXmlEditor.insertTag(tag)) throw new Error("字段正在输入或已关闭，未插入");
        } else {
            const prefix = target.input.value.trimEnd();
            const value = tag.replace(/\\?([()])/g, "\\$1");
            target.input.value = prefix + (prefix && !/[,，\n]$/.test(prefix) ? ", " : "") + value + ", ";
            target.input.dispatchEvent(new Event("input", { bubbles: true }));
            target.input.dispatchEvent(new Event("change", { bubbles: true }));
            window.syncPromptToHost?.(false);
        }
        message(`已插入：${tag}`);
    } catch (error) { failure(error); }
}
function tagCard(item, compact = false) {
    const card = el("article", { class: "ss-library-card" });
    card.append(el("strong", {}, item.label_zh || "暂无译名"), el("code", {}, item.canonical));
    card.append(el("small", {}, compact ? `共现次数 ${item.count ?? "未知"}` : (kinds[item.kind] || item.kind || "未确认类别")));
    const actions = el("div", { class: "ss-library-actions" });
    const targetSession = session;
    actions.append(button("插入", () => insert(item.canonical, targetSession)), button("详情 / 相关", () => showDetail(item.canonical)));
    card.append(actions);
    return card;
}
async function search(reset = false) {
    if (!dialog) return;
    if (reset) page = 1;
    searchRequest?.abort(); searchRequest = new AbortController();
    const request = ++searchEpoch, openEpoch = epoch;
    message("正在查询本地索引…");
    part("previous").disabled = true; part("next").disabled = true;
    try {
        const data = await get("search", {
            q: part("search").value.trim(),
            category: part("category").value,
            kind: part("kind").value,
            work: part("work").value.trim(),
            sort: part("sort").value,
            initial,
            page,
            limit: 40,
        }, searchRequest.signal);
        if (!dialog || request !== searchEpoch || openEpoch !== epoch) return;
        const items = Array.isArray(data.items) ? data.items.slice(0, 40) : [];
        part("results").replaceChildren(...items.map(item => tagCard(item)));
        if (!items.length) part("results").append(el("p", {}, "没有匹配项。可尝试英文名称开头、中文译名开头或别名；待分类不代表条目不存在。"));
        page = Math.max(1, Number(data.page) || 1);
        totalPages = Math.max(1, Number(data.total_pages) || 1);
        const total = Math.max(0, Number(data.total_count) || 0);
        part("first").disabled = part("previous").disabled = page <= 1;
        part("last").disabled = part("next").disabled = page >= totalPages;
        part("jump").value = String(page);
        part("jump").max = String(totalPages);
        part("page").textContent = `第 ${page.toLocaleString()} / ${totalPages.toLocaleString()} 页 · 共 ${total.toLocaleString()} 条`;
        message("浏览不会修改提示词。共现只是关联参考，不会自动追加。");
    } catch (error) { if (request === searchEpoch) failure(error); }
}

function setInitial(value) {
    initial = value;
    dialog?.querySelectorAll("[data-initial]").forEach(node => {
        node.classList.toggle("is-active", node.dataset.initial === initial);
    });
}

async function suggestWorks() {
    if (!dialog) return;
    const request = ++workEpoch;
    try {
        const data = await get("works", { q: part("work").value.trim(), limit: 50 });
        if (!dialog || request !== workEpoch) return;
        part("work-options").replaceChildren(...(data.items || []).map(item => {
            const option = el("option", { value: item.name });
            option.label = `${item.label_zh || item.name} · ${Number(item.character_count || 0).toLocaleString()} 角色`;
            return option;
        }));
    } catch (error) { failure(error); }
}

function goToPage(value) {
    const target = Math.min(totalPages, Math.max(1, Number.parseInt(value, 10) || 1));
    if (target === page) return;
    page = target;
    search();
}
async function showDetail(canonical) {
    if (!dialog) return;
    detailRequest?.abort(); detailRequest = new AbortController();
    const request = ++detailEpoch, openEpoch = epoch;
    const area = part("detail");
    area.replaceChildren(el("p", {}, "读取详情…"));
    try {
        const data = await get("detail", { canonical }, detailRequest.signal);
        if (!dialog || request !== detailEpoch || openEpoch !== epoch) return;
        const item = data.item || data;
        area.replaceChildren(el("h3", {}, item.label_zh || item.canonical), el("code", {}, item.canonical));
        if (item.description) area.append(el("p", {}, item.description));
        const aliases = (item.aliases || []).slice(0, 40).map(x => typeof x === "string" ? x : x.alias || x.name || "");
        area.append(el("p", {}, `别名：${aliases.join("、") || "未提供"}${item.aliases_truncated ? "（仅显示前 40 项）" : ""}`));
        area.append(el("p", {}, `分类：${(item.groups || []).map(x => typeof x === "string" ? x : x.label_zh || x.id).join("、") || "待分类"}`));
        area.append(el("small", {}, `来源：${(item.sources || []).map(x => typeof x === "string" ? x : x.name || x.path || x.id).join("、") || "未确认"}`));
        const actions = el("div", { class: "ss-library-actions" });
        const targetSession = session;
        actions.append(button("插入当前目标", () => insert(item.canonical, targetSession)), button("加入我的快捷标签…", () => quickAdd(item, area, request)));
        area.append(actions, el("h4", {}, "常一起出现（不是译名或必然属性）"));
        const related = await get("related", { canonical, limit: 20 }, detailRequest.signal);
        if (!dialog || request !== detailEpoch || openEpoch !== epoch) return;
        const items = Array.isArray(related.items) ? related.items.slice(0, 20) : [];
        area.append(...items.map(x => tagCard(x, true)));
        if (!items.length) area.append(el("p", {}, "暂无共现数据，或未安装共现资源。"));
    } catch (error) { if (request === detailEpoch) failure(error); }
}
async function quickAdd(item, area, request) {
    try {
        const data = await get("quick-groups");
        if (!dialog || request !== detailEpoch) return;
        area.querySelector(".ss-library-quick")?.remove();
        const form = el("div", { class: "ss-library-quick" });
        const select = el("select", { "aria-label": "我的快捷标签目标分组" });
        for (const group of (data.items || []).slice(0, 5000)) select.append(el("option", { value: JSON.stringify([group.key, group.group]) }, `${group.key} / ${group.group}`));
        const save = button("确认加入（不覆盖重名）", async () => {
            save.disabled = true;
            try {
                if (!select.value) throw new Error("没有可写分组，请先在原快捷库创建一个分组");
                const [key, group] = JSON.parse(select.value);
                const response = await fetch(API + "quick-add", { method: "POST", headers: {"Content-Type":"application/json"}, body: JSON.stringify({ canonical: item.canonical, key, group, revision: data.revision }), signal: life.signal });
                const result = await response.json();
                if (!response.ok) throw new Error(result.error || "加入失败");
                message("已加入快捷标签。重新打开 Prompt Studio 后可见；没有覆盖其他标签。");
                form.remove();
            } catch (error) { failure(error); save.disabled = false; }
        });
        form.append(el("p", {}, "仅此操作会写入你的快捷库。请选择已有分组："), select, save);
        area.prepend(form);
    } catch (error) { failure(error); }
}

async function openLibrary(query = "") {
    if (dialog) { if (query) { part("search").value = query; await search(true); } return; }
    const openEpoch = ++epoch;
    opener = document.activeElement;
    life = new AbortController();
    dialog = el("dialog", { class: "ss-library", "aria-labelledby": "ss-library-title" });
    const header = el("header");
    header.append(el("h2", { id: "ss-library-title" }, "公共词典库"), button("关闭词典", closeLibrary));
    const summary = el("p", { "data-part": "summary" }, "正在检查本地资源…");
    const filters = el("div", { class: "ss-library-filters" });
    const input = el("input", { "data-part": "search", placeholder: "英文 / 中文 / 别名前缀搜索", "aria-label": "搜索词典", maxlength: "160" }); input.value = query;
    input.addEventListener("input", () => {
        clearTimeout(timer); searchEpoch++; searchRequest?.abort(); setInitial("");
        timer = setTimeout(() => search(true), 180);
    });
    const categoryRoot = el("select", { "data-part": "category-root", "aria-label": "分类库" });
    categoryRoot.append(el("option", { value: "" }, "全部分类库"));
    categoryRoot.addEventListener("change", () => rebuildCategoryBranches(true));
    const categoryBranch = el("select", { "data-part": "category-branch", "aria-label": "分类页", disabled: "disabled" });
    categoryBranch.append(el("option", { value: "" }, "全部分类页"));
    categoryBranch.addEventListener("change", () => rebuildCategoryDetails(true));
    const category = el("select", { "data-part": "category", "aria-label": "细分类", disabled: "disabled" }); category.append(el("option", { value: "" }, "全部细分类"));
    category.addEventListener("change", () => search(true));
    const kind = el("select", { "data-part": "kind", "aria-label": "Danbooru 类型" }); kind.append(el("option", { value: "" }, "全部 Danbooru 类型"));
    for (const [id, name] of Object.entries(kinds)) kind.append(el("option", { value: id }, name));
    kind.addEventListener("change", () => search(true));
    const sort = el("select", { "data-part": "sort", "aria-label": "排序方式" });
    sort.append(el("option", { value: "popular" }, "按热度（默认）"), el("option", { value: "name" }, "按英文名称"));
    sort.addEventListener("change", () => search(true));
    const workOptions = el("datalist", { id: "ss-library-work-options", "data-part": "work-options" });
    const work = el("input", { "data-part": "work", list: "ss-library-work-options", placeholder: "作品/系列英文前缀（如 arknights）", "aria-label": "按作品筛选", maxlength: "160" });
    work.addEventListener("input", () => {
        clearTimeout(workTimer); workEpoch++;
        workTimer = setTimeout(async () => { await suggestWorks(); search(true); }, 180);
    });
    const target = el("select", { "data-part": "target", "aria-label": "插入目标" });
    for (const [value, text] of [["positive","插入正向"],["negative","插入反向"],["xml","插入当前 XML 字段"]]) target.append(el("option",{value},text));
    target.value = window.studioSuiteXmlEditor?.current()?.active ? "xml" : hostKind;
    filters.append(input, categoryRoot, categoryBranch, category, kind, sort, work, workOptions, target);
    const alphabet = el("nav", { class: "ss-library-alphabet", "aria-label": "按首字母筛选" });
    for (const [value, text] of [["", "全部"], ["0-9", "0-9"], ...Array.from("ABCDEFGHIJKLMNOPQRSTUVWXYZ", letter => [letter.toLowerCase(), letter])]) {
        const item = button(text, () => { setInitial(value); search(true); });
        item.dataset.initial = value;
        item.classList.toggle("is-active", value === initial);
        alphabet.append(item);
    }
    setInitial("");
    const layout = el("div", { class: "ss-library-layout" });
    const results = el("section", { "data-part": "results", class: "ss-library-results", "aria-label": "词条结果" });
    const detail = el("aside", { "data-part": "detail", class: "ss-library-detail", "aria-label": "词条详情" }, "选择一条词条的“详情 / 相关”。个人快捷库保持原样。使用标签旁的“查词”可直接定位。");
    layout.append(results, detail);
    const footer = el("footer");
    const first = button("首页", () => goToPage(1)); first.dataset.part = "first"; first.disabled = true;
    const previous = button("上一页", () => goToPage(page - 1)); previous.dataset.part = "previous"; previous.disabled = true;
    const jump = el("input", { type: "number", min: "1", value: "1", "data-part": "jump", "aria-label": "跳转页码" });
    jump.addEventListener("keydown", event => { if (event.key === "Enter") goToPage(jump.value); });
    const jumpButton = button("跳转", () => goToPage(jump.value));
    const next = button("下一页", () => goToPage(page + 1)); next.dataset.part = "next"; next.disabled = true;
    const last = button("末页", () => goToPage(totalPages)); last.dataset.part = "last"; last.disabled = true;
    footer.append(first, previous, el("span", { "data-part": "page" }), jump, jumpButton, next, last);
    dialog.append(header, summary, filters, alphabet, el("p", { "data-part": "message", role: "status" }), layout, footer);
    dialog.addEventListener("cancel", event => { event.preventDefault(); closeLibrary(); });
    document.body.append(dialog); dialog.showModal(); input.focus();
    try {
        const status = await get("status");
        if (!dialog || openEpoch !== epoch) return;
        if (!status.ready) {
            summary.textContent = "尚未安装词典索引。此页面不会自动下载或构建。";
            results.append(el("p", {}, "请在节点目录显式执行下列命令，或安装提供的词典资源包。构建期间请暂停跑图。"), el("code", {}, "python scripts/build_tag_library.py"));
            message(status.error || "原有快捷标签和翻译仍可使用。");
            return;
        }
        const count = status.counts?.tags ?? status.counts?.tag_count ?? status.tag_count;
        const workCount = status.counts?.work_count;
        summary.textContent = `本地候选词典 ${count != null ? Number(count).toLocaleString() + " 词条 · " : ""}${workCount != null ? Number(workCount).toLocaleString() + " 作品 · " : ""}版本 ${status.version || "未知"}。类型来自快照，语义细分仍含待审核项。`;
        const groups = await get("categories");
        if (!dialog || openEpoch !== epoch) return;
        mountCategoryTree(groups.items || []);
        const kindCounts = Object.fromEntries((groups.kinds || []).map(item => [item.kind, item.count]));
        for (const option of kind.options) {
            if (option.value && kindCounts[option.value] != null) option.textContent = `${kinds[option.value]} (${Number(kindCounts[option.value]).toLocaleString()})`;
        }
        await suggestWorks();
        await search(true);
    } catch (error) { failure(error); }
}

function mount() {
    if (document.getElementById("ss-library-open")) return;
    const open = button("词典库", () => openLibrary()); open.id = "ss-library-open";
    (document.querySelector(".topBar .topBox") || document.body).append(open);
    const style = el("style");
    style.textContent = `
      #ss-library-open{border:1px solid #69634a;background:#353329;color:#ffe0a0;border-radius:18px;padding:7px 14px;cursor:pointer}
      .ss-library{box-sizing:border-box;color:#eee;background:#222329;border:1px solid #666;border-radius:12px;width:min(1250px,96vw);height:min(860px,94vh);max-width:96vw;max-height:94vh;padding:18px;overflow:hidden;font-family:inherit;font-size:14px;line-height:1.45}
      .ss-library[open]{display:flex;flex-direction:column;gap:10px}.ss-library::backdrop{background:#0009}
      .ss-library *{box-sizing:border-box;animation:none!important;transition:none!important}.ss-library header,.ss-library footer,.ss-library-actions{display:flex;gap:8px;align-items:center;flex-wrap:wrap}.ss-library header{justify-content:space-between}.ss-library-filters{display:grid;grid-template-columns:minmax(220px,2fr) minmax(180px,1.4fr) minmax(150px,1fr) minmax(150px,1fr);gap:8px}.ss-library-filters [data-part=search],.ss-library-filters [data-part=work]{grid-column:span 2}
       .ss-library h2,.ss-library h3,.ss-library p{margin:0}.ss-library button,.ss-library select,.ss-library input{font:inherit;color:inherit;background:#30323a;border:1px solid #666;border-radius:6px;padding:6px 9px;max-width:100%}.ss-library button{cursor:pointer}.ss-library button:disabled{opacity:.4;cursor:default}.ss-library button:hover{border-color:#eab64b}.ss-library input{flex:1;min-width:180px}.ss-library select[data-part^=category]{border-color:#766542}.ss-library code{overflow-wrap:anywhere;white-space:normal;color:#d8c792}.ss-library small{color:#bbb}.ss-library [data-part=message]{min-height:20px;color:#dec894}
      .ss-library-alphabet{display:flex;gap:4px;overflow-x:auto;scrollbar-width:thin;padding:2px 0 5px}.ss-library-alphabet button{flex:0 0 auto;padding:4px 7px;min-width:28px}.ss-library-alphabet button.is-active{color:#17130a;background:#eab64b;border-color:#f0c96e}.ss-library footer [data-part=page]{margin-right:auto}.ss-library footer [data-part=jump]{flex:0 0 84px;min-width:72px;width:84px}
      .ss-library-layout{display:grid;grid-template-columns:minmax(0,1fr) 300px;gap:14px;min-height:0;flex:1}.ss-library-results{display:grid;grid-template-columns:repeat(auto-fill,minmax(190px,1fr));gap:8px;align-content:start;overflow:auto;padding-right:5px}.ss-library-detail{overflow:auto;border-left:1px solid #4b4c54;padding-left:14px;overflow-wrap:anywhere}.ss-library-detail>*{margin-bottom:10px}.ss-library-card{border:1px solid #484a53;background:#292a31;border-radius:7px;padding:10px;display:flex;flex-direction:column;gap:5px;min-width:0}.ss-library-card .ss-library-actions{margin-top:auto}.ss-library-quick{border:1px solid #d2a64a;padding:10px;display:grid;gap:8px}
      .prompt-tag{position:relative}.ss-library-tag-lookup{position:absolute;left:0;right:auto;top:0;z-index:3;border:1px solid #897046;background:#343028;color:#ffe0a0;font-size:10px;border-radius:4px;cursor:pointer;display:none}.prompt-tag:hover>.ss-library-tag-lookup,.prompt-tag:focus-within>.ss-library-tag-lookup{display:block}
      @media(max-width:760px){.ss-library{padding:10px}.ss-library-filters{grid-template-columns:1fr 1fr}.ss-library-filters [data-part=search],.ss-library-filters [data-part=work]{grid-column:1/-1}.ss-library-layout{grid-template-columns:1fr}.ss-library-detail{max-height:32vh;border-left:0;border-top:1px solid #555;padding:10px 0}.ss-library footer [data-part=page]{flex-basis:100%;order:-1}}
    `;
    document.head.append(style);
}
document.addEventListener("keydown", event => {
    if (dialog && event.key === "Escape") { event.preventDefault(); event.stopImmediatePropagation(); closeLibrary(); }
}, true);
document.addEventListener("mouseover", event => {
    const tag = event.target.closest?.(".prompt-tags-list .prompt-tag");
    if (!tag) return;
    lookupTag = tag;
    if (!tagLookup) {
        tagLookup = button("查词", click => {
            click.preventDefault(); click.stopPropagation();
            const value = lookupTag?.querySelector(".prompt-tag-value")?.cloneNode(true);
            value?.querySelectorAll("button, svg, .btn-tag-delete, .translate-to-local").forEach(node => node.remove());
            const text = value?.textContent?.replace(/[×✕]$/g, "");
            if (text) openLibrary(text.trim());
        });
        tagLookup.className = "ss-library-tag-lookup";
        tagLookup.addEventListener("pointerdown", click => click.stopPropagation());
        tagLookup.addEventListener("mousedown", click => click.stopPropagation());
    }
    if (tagLookup.parentElement !== tag) tag.append(tagLookup);
});
window.addEventListener("message", event => {
    if (event.source !== window.parent || event.origin !== location.origin) return;
    const data = event.data;
    if (!["openWeiLinPrompt", "responeseWeiLinPrompt"].includes(data?.handel) || !data.randomid) return;
    if (session && session !== data.randomid) closeLibrary();
    session = data.randomid;
    hostKind = data.nodeName === "WeiLinComfyUIPromptAllInOneNeg" ? "negative" : "positive";
});
window.addEventListener("pagehide", closeLibrary);
if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", mount, { once: true }); else mount();
