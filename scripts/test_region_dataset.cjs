// 区域布局数据集测试（node >= 22，零依赖）
// 从 frontend/index.html 抽取 RegionDataset 块 eval；该块依赖同一文件中的 RegionAdapters.asDoc。
"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const htmlPath = path.join(__dirname, "..", "frontend", "index.html");
const html = fs.readFileSync(htmlPath, "utf8");

const adaptersMatch = html.match(/\/\/ == REGION_ADAPTERS_BEGIN ==([\s\S]*?)\/\/ == REGION_ADAPTERS_END ==/);
const datasetMatch = html.match(/\/\/ == REGION_DATASET_BEGIN ==([\s\S]*?)\/\/ == REGION_DATASET_END ==/);
if (!adaptersMatch) {
  console.error("未能从 frontend/index.html 提取 RegionAdapters 块（缺少 BEGIN/END 标记）");
  process.exit(1);
}
if (!datasetMatch) {
  console.error("未能从 frontend/index.html 提取 RegionDataset 块（缺少 BEGIN/END 标记）");
  process.exit(1);
}

// 数据集块嵌在适配器块内：从适配器源码中去掉它再单独拼接，确保测试直测该块本身。
const adaptersSource = adaptersMatch[1].replace(datasetMatch[0], "");
let loaded;
try {
  loaded = new Function(
    "window",
    adaptersSource + "\n" + datasetMatch[0]
      + "\nreturn { adapters: window.RegionAdapters, dataset: window.RegionDataset };"
  )({});
} catch (err) {
  console.error("RegionDataset 块 eval 失败: " + err.message);
  process.exit(1);
}
const RegionAdapters = loaded.adapters;
const RegionDataset = loaded.dataset;
if (!RegionAdapters || typeof RegionAdapters.toGenericJsonl !== "function") {
  console.error("RegionAdapters 未导出预期函数");
  process.exit(1);
}
if (!RegionDataset || typeof RegionDataset.mergeToJsonl !== "function") {
  console.error("RegionDataset 未导出预期函数");
  process.exit(1);
}

function region(id, bbox, overrides) {
  return Object.assign({
    id: id,
    bbox: bbox,
    role: "obj",
    literalText: "",
    nl: { zh: "", en: "" },
    tags: [],
    muted: false,
    linkedTo: null,
  }, overrides || {});
}

const docA = {
  schema: "prompt-studio/region-v1",
  canvas: { w: 1024, h: 768 },
  global: { caption_zh: "", caption_en: "two cats" },
  image: "internal-name.png",
  regions: [
    region("a1", [0.1, 0.1, 0.4, 0.4], { nl: { zh: "", en: "left cat" }, tags: [{ tag: "cat", weight: 1, source: "manual" }] }),
    region("a2", [0.5, 0.2, 0.9, 0.8], { role: "text", literalText: "MEOW" }),
    region("a3", [0, 0, 0.2, 0.2], { muted: true, nl: { zh: "", en: "muted cat" } }),
    region("a4", [0.4, 0.4, 0.4, 0.6], { nl: { zh: "", en: "zero area cat" } }),
  ],
};

const docB = {
  schema: "prompt-studio/region-v1",
  canvas: { w: 512, h: 512 },
  global: { caption_zh: "", caption_en: "" },
  image: "second.png",
  regions: [
    region("b1", [0.2, 0.3, 0.6, 0.7], { nl: { zh: "", en: "dog" } }),
  ],
};

const mutedOnly = {
  schema: "prompt-studio/region-v1",
  canvas: { w: 256, h: 256 },
  global: { caption_zh: "", caption_en: "nothing" },
  regions: [
    region("m1", [0, 0, 0.5, 0.5], { muted: true }),
    region("z1", [0.5, 0.5, 0.5, 0.8]),
  ],
};

const entries = [
  { image: "cat_a.png", doc: docA },
  { image: "dog_b.png", doc: docB },
];

const tests = [];
function test(name, fn) {
  tests.push({ name, fn });
}

test("mergeToJsonl 两条目合并为两行，image 取条目名", () => {
  const lines = RegionDataset.mergeToJsonl(entries).split("\n");
  assert.strictEqual(lines.length, 2);
  const rows = lines.map((line) => JSON.parse(line));
  assert.strictEqual(rows[0].image, "cat_a.png");
  assert.strictEqual(rows[0].width, 1024);
  assert.strictEqual(rows[0].height, 768);
  assert.strictEqual(rows[0].caption, "two cats");
  assert.strictEqual(rows[0].regions.length, 2);
  assert.strictEqual(rows[1].image, "dog_b.png");
  assert.strictEqual(rows[1].width, 512);
  assert.strictEqual(rows[1].regions.length, 1);
});

test("mergeToJsonl perRegion=true 时每个 region 一行且 image 字段正确", () => {
  const lines = RegionDataset.mergeToJsonl(entries, true).split("\n");
  assert.strictEqual(lines.length, 3);
  const rows = lines.map((line) => JSON.parse(line));
  assert.deepStrictEqual(rows.map((row) => row.image), ["cat_a.png", "cat_a.png", "dog_b.png"]);
  rows.forEach((row) => {
    assert.deepStrictEqual(Object.keys(row).sort(), ["bbox", "image", "prompt", "tags"]);
    assert.ok(Array.isArray(row.bbox) && row.bbox.length === 4);
  });
  assert.deepStrictEqual(rows[0].bbox, [0.1, 0.1, 0.4, 0.4]);
  assert.strictEqual(rows[0].prompt, "left cat");
});

test("mergeToJsonl 跳过空 doc 与无 region 的条目", () => {
  const mixed = [
    { image: "ok.png", doc: docA },
    { image: "empty.png", doc: {} },
    { image: "null.png", doc: null },
    { image: "no-regions.png", doc: { canvas: { w: 100, h: 100 }, regions: [] } },
  ];
  const lines = RegionDataset.mergeToJsonl(mixed).split("\n");
  assert.strictEqual(lines.length, 1);
  assert.strictEqual(JSON.parse(lines[0]).image, "ok.png");
});

test("mergeToJsonl 跳过静音框与零面积框组成的空条目", () => {
  const mixed = [{ image: "muted.png", doc: mutedOnly }];
  assert.strictEqual(RegionDataset.mergeToJsonl(mixed), "");
  assert.strictEqual(RegionDataset.mergeToJsonl(mixed, true), "");
});

test("upsertEntry 同 image 覆盖且不修改原数组", () => {
  const base = [{ image: "cat_a.png", doc: docA }];
  const next = RegionDataset.upsertEntry(base, { image: " cat_a.png ", doc: docB });
  assert.strictEqual(next.length, 1);
  assert.strictEqual(next[0].image, "cat_a.png");
  assert.strictEqual(next[0].doc, docB);
  assert.strictEqual(base.length, 1);
  assert.strictEqual(base[0].doc, docA);
});

test("upsertEntry 上限 200 条，超出时丢弃最旧条目", () => {
  assert.strictEqual(RegionDataset.MAX_ENTRIES, 200);
  const base = [];
  for (let i = 0; i < 200; i += 1) base.push({ image: "img_" + i + ".png", doc: docB });
  const next = RegionDataset.upsertEntry(base, { image: "img_200.png", doc: docA });
  assert.strictEqual(next.length, 200);
  assert.strictEqual(next[0].image, "img_1.png");
  assert.strictEqual(next[199].image, "img_200.png");
  assert.strictEqual(base.length, 200);
});

test("summarizeEntries 返回 images/regions/missingCaption", () => {
  const stats = RegionDataset.summarizeEntries([
    { image: "cat_a.png", doc: docA },
    { image: "dog_b.png", doc: docB },
    { image: "empty.png", doc: {} },
  ]);
  assert.deepStrictEqual(stats, { images: 3, regions: 3, missingCaption: 1 });
  assert.deepStrictEqual(RegionDataset.summarizeEntries([]), { images: 0, regions: 0, missingCaption: 0 });
});

test("docFromRegionJson 规范化 fromIdeogram 的输出", () => {
  const ideogram = RegionAdapters.toIdeogram(docA);
  const back = RegionAdapters.fromIdeogram(JSON.stringify(ideogram));
  const doc = RegionDataset.docFromRegionJson(back);
  assert.strictEqual(doc.schema, "prompt-studio/region-v1");
  assert.deepStrictEqual(doc.canvas, { w: 1024, h: 1024 });
  assert.strictEqual(doc.global.caption_en, "two cats");
  assert.strictEqual(doc.regions.length, 2);
  assert.strictEqual(doc.regions[0].nl.en, "left cat");
  assert.strictEqual(doc.regions[1].role, "text");
  assert.strictEqual(doc.regions[1].literalText, "MEOW");
  assert.deepStrictEqual(RegionDataset.docFromRegionJson(JSON.stringify(doc)), doc);
});

test("docFromRegionJson 规范化 fromMoss 的输出并保留画布", () => {
  const moss = RegionAdapters.toMoss(docA);
  const back = RegionAdapters.fromMoss(JSON.stringify(moss), { w: 1024, h: 768 });
  const doc = RegionDataset.docFromRegionJson(back);
  assert.deepStrictEqual(doc.canvas, { w: 1024, h: 768 });
  assert.strictEqual(doc.global.caption_en, "two cats");
  assert.strictEqual(doc.regions.length, 2);
  assert.strictEqual(doc.regions[1].literalText, "MEOW");
});

test("docFromRegionJson 非法输入抛中文错误", () => {
  assert.throws(() => RegionDataset.docFromRegionJson("not json"), /解析失败/);
  assert.throws(() => RegionDataset.docFromRegionJson(null), /需要对象/);
  assert.throws(() => RegionDataset.docFromRegionJson({}), /regions/);
  assert.throws(() => RegionDataset.docFromRegionJson({ regions: [] }), /画布宽高/);
  assert.throws(
    () => RegionDataset.docFromRegionJson({ canvas: { w: 64, h: 64 }, regions: [{ bbox: [0, 0, 1] }] }),
    /bbox 需要 4 项/
  );
});

test("单条目输出与 toGenericJsonl 字段完全一致", () => {
  const single = [{ image: docA.image, doc: docA }];
  assert.strictEqual(RegionDataset.mergeToJsonl(single), RegionAdapters.toGenericJsonl(docA));
  assert.strictEqual(
    RegionDataset.mergeToJsonl(single, true),
    RegionAdapters.toGenericJsonl(docA, true)
  );
});

test("mergeToJsonl / summarizeEntries 对空输入安全", () => {
  assert.strictEqual(RegionDataset.mergeToJsonl([]), "");
  assert.strictEqual(RegionDataset.mergeToJsonl(null), "");
  assert.deepStrictEqual(RegionDataset.summarizeEntries(null), { images: 0, regions: 0, missingCaption: 0 });
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
