// 区域布局存档测试（node >= 22，零依赖）
// 从 web/js/region_layout_node.js 抽取 window.RegionLayoutArchive 块 eval，纯函数直测。
"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const jsPath = path.join(__dirname, "..", "web", "js", "region_layout_node.js");
const source = fs.readFileSync(jsPath, "utf8");
const match = source.match(/\/\/ == REGION_LAYOUT_ARCHIVE_BEGIN ==([\s\S]*?)\/\/ == REGION_LAYOUT_ARCHIVE_END ==/);
if (!match) {
  console.error("未能从 web/js/region_layout_node.js 提取 RegionLayoutArchive 块（缺少 BEGIN/END 标记）");
  process.exit(1);
}

let archive;
try {
  archive = new Function("window", match[0] + "\nreturn window.RegionLayoutArchive;")({});
} catch (err) {
  console.error("RegionLayoutArchive 块 eval 失败: " + err.message);
  process.exit(1);
}
if (
  !archive ||
  typeof archive.listArchives !== "function" ||
  typeof archive.saveArchive !== "function" ||
  typeof archive.deleteArchive !== "function" ||
  typeof archive.applyArchive !== "function" ||
  typeof archive.loadStore !== "function" ||
  typeof archive.saveStore !== "function" ||
  typeof archive.suggestName !== "function"
) {
  console.error("RegionLayoutArchive 未导出预期函数");
  process.exit(1);
}

function makeDoc(overrides) {
  return Object.assign(
    {
      schema: "prompt-studio/region-v1",
      canvas: { w: 1024, h: 768 },
      global: { caption_zh: "", caption_en: "a cat" },
      regions: [
        { id: "r1", bbox: [0.1, 0.1, 0.4, 0.4], muted: false, tags: ["cat"] },
        { id: "r2", bbox: [0.5, 0.2, 0.9, 0.8], muted: false, tags: [] },
      ],
    },
    overrides || {}
  );
}

function fakeStorage(initial) {
  const data = Object.assign({}, initial);
  return {
    getItem(key) {
      return Object.prototype.hasOwnProperty.call(data, key) ? data[key] : null;
    },
    setItem(key, value) {
      data[key] = String(value);
    },
    _data: data,
  };
}

const tests = [];
function test(name, fn) {
  tests.push({ name, fn });
}

test("listArchives：空/非法 store 返回空列表", () => {
  assert.deepStrictEqual(archive.listArchives({}), []);
  assert.deepStrictEqual(archive.listArchives(null), []);
  assert.deepStrictEqual(archive.listArchives(undefined), []);
  assert.deepStrictEqual(archive.listArchives([]), []);
});

test("saveArchive：列表返回 name/savedAt/活跃框数/w/h", () => {
  const doc = makeDoc({
    regions: [
      { id: "r1", bbox: [0.1, 0.1, 0.4, 0.4] },
      { id: "r2", bbox: [0.5, 0.1, 0.9, 0.4], muted: true },
      { id: "r3", bbox: [0.5, 0.5, 0.5, 0.8] },
      { id: "r4", bbox: [0.6, 0.6, 0.9, 0.9] },
    ],
  });
  const store = archive.saveArchive({}, "scene_a", doc);
  const list = archive.listArchives(store);
  assert.strictEqual(list.length, 1);
  const entry = list[0];
  assert.strictEqual(entry.name, "scene_a");
  assert.strictEqual(typeof entry.savedAt, "number");
  assert.ok(Number.isFinite(entry.savedAt));
  assert.strictEqual(entry.regions, 2);
  assert.strictEqual(entry.w, 1024);
  assert.strictEqual(entry.h, 768);
});

test("saveArchive：同名覆盖，只保留一条且 savedAt 更新", () => {
  const stale = { scene: { savedAt: 1, doc: makeDoc({ canvas: { w: 256, h: 256 } }) } };
  const next = archive.saveArchive(stale, "scene", makeDoc({ canvas: { w: 512, h: 512 } }));
  assert.deepStrictEqual(Object.keys(next), ["scene"]);
  assert.ok(next.scene.savedAt > 1);
  assert.deepStrictEqual(next.scene.doc.canvas, { w: 512, h: 512 });
  assert.strictEqual(stale.scene.savedAt, 1, "入参 store 不被修改");
  assert.deepStrictEqual(stale.scene.doc.canvas, { w: 256, h: 256 });
});

test("saveArchive：上限 50 条，超出删除 savedAt 最旧", () => {
  assert.strictEqual(archive.MAX_ARCHIVES, 50);
  const store = {};
  for (let index = 0; index < archive.MAX_ARCHIVES; index += 1) {
    store["a" + String(index).padStart(2, "0")] = { savedAt: 1000 + index, doc: makeDoc() };
  }
  const next = archive.saveArchive(store, "newest", makeDoc());
  assert.strictEqual(Object.keys(next).length, 50);
  assert.ok(!next.a00, "最旧条目被删除");
  assert.ok(next.a01);
  assert.ok(next.newest);
  assert.strictEqual(Object.keys(store).length, 50);
  assert.ok(store.a00, "入参 store 不被修改");
});

test("deleteArchive：返回删除后的新 store，不修改入参", () => {
  let store = archive.saveArchive({}, "a", makeDoc());
  store = archive.saveArchive(store, "b", makeDoc());
  const next = archive.deleteArchive(store, " a ");
  assert.deepStrictEqual(Object.keys(next), ["b"]);
  assert.ok(store.a, "入参 store 仍保留 a");
  assert.deepStrictEqual(archive.deleteArchive(store, "missing"), store);
  assert.deepStrictEqual(archive.deleteArchive(store, "  "), store);
  assert.deepStrictEqual(archive.deleteArchive(null, "a"), {});
});

test("saveArchive：名称 trim，空名抛中文错误", () => {
  const store = archive.saveArchive({}, "  scene_a  ", makeDoc());
  assert.deepStrictEqual(Object.keys(store), ["scene_a"]);
  assert.throws(() => archive.saveArchive({}, "   ", makeDoc()), /存档名称不能为空/);
  assert.throws(() => archive.saveArchive({}, "", makeDoc()), /名称/);
  assert.throws(() => archive.saveArchive({}, null, makeDoc()), /名称/);
  assert.throws(() => archive.saveArchive({}, 42, makeDoc()), /名称/);
});

test("saveArchive：深拷贝 doc，只保留 schema/canvas/global/regions", () => {
  const doc = makeDoc({ image: "internal.png", extra: 1 });
  const store = archive.saveArchive({}, "snap", doc);
  doc.canvas.w = 1;
  doc.regions[0].tags.push("dog");
  doc.regions[0].bbox[0] = 0.9;
  doc.global.caption_en = "changed";
  const saved = store.snap.doc;
  assert.strictEqual(saved.canvas.w, 1024);
  assert.deepStrictEqual(saved.regions[0].tags, ["cat"]);
  assert.strictEqual(saved.regions[0].bbox[0], 0.1);
  assert.strictEqual(saved.global.caption_en, "a cat");
  assert.notStrictEqual(saved.regions[0], doc.regions[0]);
  assert.deepStrictEqual(Object.keys(saved).sort(), ["canvas", "global", "regions", "schema"]);
  assert.ok(!("image" in saved));
  assert.ok(!("extra" in saved));
});

test("applyArchive：回写 canvas 宽高并返回规范 doc 副本", () => {
  const entry = { name: "s", savedAt: 1, doc: makeDoc({ canvas: { w: 800, h: 600 } }) };
  const widgets = { canvas_width: { value: 0 }, canvas_height: { value: 0 } };
  const doc = archive.applyArchive(entry, widgets);
  assert.strictEqual(widgets.canvas_width.value, 800);
  assert.strictEqual(widgets.canvas_height.value, 600);
  assert.deepStrictEqual(doc.canvas, { w: 800, h: 600 });
  assert.notStrictEqual(doc, entry.doc, "返回副本，避免改动画布污染存档");
  doc.regions[0].tags.push("x");
  assert.deepStrictEqual(entry.doc.regions[0].tags, ["cat"]);
  assert.deepStrictEqual(archive.applyArchive(entry, null).canvas, { w: 800, h: 600 });
  assert.deepStrictEqual(archive.applyArchive(entry, {}).canvas, { w: 800, h: 600 });
  assert.deepStrictEqual(archive.applyArchive(entry.doc, {}).canvas, { w: 800, h: 600 });
});

test("listArchives：损坏或缺字段条目被跳过", () => {
  const store = {
    good: { savedAt: 5, doc: makeDoc() },
    no_saved: { doc: makeDoc() },
    bad_saved: { savedAt: "abc", doc: makeDoc() },
    no_doc: { savedAt: 3 },
    no_canvas: { savedAt: 3, doc: { schema: "prompt-studio/region-v1", regions: [] } },
    zero_w: { savedAt: 3, doc: makeDoc({ canvas: { w: 0, h: 100 } }) },
    no_regions: { savedAt: 3, doc: { canvas: { w: 100, h: 100 } } },
    null_entry: null,
    text_entry: "oops",
    "  ": { savedAt: 3, doc: makeDoc() },
  };
  assert.deepStrictEqual(archive.listArchives(store).map((entry) => entry.name), ["good"]);
});

test("listArchives：按 savedAt 降序排列", () => {
  const store = {
    old: { savedAt: 10, doc: makeDoc() },
    newest: { savedAt: 30, doc: makeDoc() },
    mid: { savedAt: 20, doc: makeDoc() },
  };
  assert.deepStrictEqual(
    archive.listArchives(store).map((entry) => entry.name),
    ["newest", "mid", "old"]
  );
});

test("loadStore/saveStore：localStorage 往返、损坏 JSON 与不可用 storage 回退", () => {
  assert.strictEqual(archive.STORAGE_KEY, "studio_suite_region_layouts");
  const storage = fakeStorage();
  const store = archive.saveArchive({}, "scene", makeDoc());
  assert.strictEqual(archive.saveStore(storage, store), true);
  assert.ok(storage._data.studio_suite_region_layouts);
  assert.deepStrictEqual(Object.keys(archive.loadStore(storage)), ["scene"]);

  const broken = fakeStorage({ studio_suite_region_layouts: "{not json" });
  assert.deepStrictEqual(archive.loadStore(broken), {});
  const wrongShape = fakeStorage({ studio_suite_region_layouts: "[1,2,3]" });
  assert.deepStrictEqual(archive.loadStore(wrongShape), {});
  const empty = fakeStorage({ studio_suite_region_layouts: "" });
  assert.deepStrictEqual(archive.loadStore(empty), {});

  const throwing = {
    getItem() {
      throw new Error("blocked");
    },
    setItem() {
      throw new Error("blocked");
    },
  };
  assert.deepStrictEqual(archive.loadStore(throwing), {});
  assert.strictEqual(archive.saveStore(throwing, store), false);
  assert.deepStrictEqual(archive.loadStore(null), {});
  assert.strictEqual(archive.saveStore(null, store), false);
});

test("loadStore：部分损坏条目被丢弃，其余可用", () => {
  const storage = fakeStorage({
    studio_suite_region_layouts: JSON.stringify({
      keep: { savedAt: 2, doc: makeDoc() },
      drop: { savedAt: "x", doc: makeDoc() },
    }),
  });
  assert.deepStrictEqual(Object.keys(archive.loadStore(storage)), ["keep"]);
});

test("suggestName：layout_YYYYMMDD_HHMM", () => {
  assert.strictEqual(archive.suggestName(new Date(2026, 0, 2, 3, 4, 5)), "layout_20260102_0304");
  assert.match(archive.suggestName(), /^layout_\d{8}_\d{4}$/);
});

let passed = 0;
const failures = [];
for (const item of tests) {
  try {
    item.fn();
    passed += 1;
    console.log("PASS  " + item.name);
  } catch (err) {
    failures.push({ name: item.name, err });
    console.error("FAIL  " + item.name);
    console.error("      " + String((err && err.stack) || err).split("\n").slice(0, 4).join("\n      "));
  }
}

console.log("\n" + passed + " passed, " + failures.length + " failed, " + tests.length + " total");
if (failures.length) process.exit(1);
