// 区域布局适配器测试（node >= 22，零依赖）
// 从 frontend/index.html 抽取 window.RegionAdapters 块 eval，纯函数直测。
"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const htmlPath = path.join(__dirname, "..", "frontend", "index.html");
const html = fs.readFileSync(htmlPath, "utf8");
const match = html.match(/\/\/ == REGION_ADAPTERS_BEGIN ==([\s\S]*?)\/\/ == REGION_ADAPTERS_END ==/);
if (!match) {
  console.error("未能从 frontend/index.html 提取 RegionAdapters 块（缺少 BEGIN/END 标记）");
  process.exit(1);
}

let RegionAdapters;
try {
  RegionAdapters = new Function("window", match[0] + "\nreturn window.RegionAdapters;")({});
} catch (err) {
  console.error("RegionAdapters 块 eval 失败: " + err.message);
  process.exit(1);
}
if (!RegionAdapters || typeof RegionAdapters.toMoss !== "function") {
  console.error("RegionAdapters 未导出预期函数");
  process.exit(1);
}

// plan §4 P1 的 3 框样例：2 obj + 1 text；另附 1 个 muted、1 个零面积框用于跳过断言。
const doc = {
  schema: "prompt-studio/region-v1",
  canvas: { w: 1024, h: 1024 },
  global: { caption_zh: "戴红帽的女孩站在树旁", caption_en: "a girl in a red hood beside a tree" },
  image: "ref.png",
  regions: [
    {
      id: "a",
      bbox: [0.1, 0.1, 0.4, 0.5],
      role: "obj",
      literalText: "",
      nl: { zh: "左边的人", en: "a person on the left" },
      tags: [{ tag: "person", weight: 1, source: "manual" }, { tag: "red_hood", weight: 1, source: "manual" }],
      muted: false,
      linkedTo: null,
    },
    {
      id: "b",
      bbox: [0.55, 0.2, 0.9, 0.8],
      role: "obj",
      literalText: "",
      nl: { zh: "右侧的树", en: "a tree on the right" },
      tags: [{ tag: "tree", weight: 1, source: "manual" }],
      muted: false,
      linkedTo: null,
    },
    {
      id: "c",
      bbox: [0.2, 0.75, 0.8, 0.9],
      role: "text",
      literalText: 'HAPPY "DAY"',
      nl: { zh: "招牌文字", en: "sign text" },
      tags: [{ tag: "sign", weight: 1, source: "manual" }],
      muted: false,
      linkedTo: null,
    },
    {
      id: "m",
      bbox: [0, 0, 0.2, 0.2],
      role: "obj",
      literalText: "",
      nl: { zh: "静音框", en: "muted box" },
      tags: [{ tag: "ignored", weight: 1, source: "manual" }],
      muted: true,
      linkedTo: null,
    },
    {
      id: "z",
      bbox: [0.5, 0.5, 0.5, 0.7],
      role: "obj",
      literalText: "",
      nl: { zh: "零面积", en: "zero area" },
      tags: [],
      muted: false,
      linkedTo: null,
    },
  ],
};

const tests = [];
function test(name, fn) {
  tests.push({ name, fn });
}

test("toMoss 输出 schema 与画布级字段", () => {
  const moss = RegionAdapters.toMoss(doc);
  assert.deepStrictEqual(moss.summary, {
    meta: [],
    canvas: "single",
    caption: "a girl in a red hood beside a tree",
  });
  assert.strictEqual(moss.bbox_coord_space, "abs_centralized");
  assert.strictEqual(moss.bbox_format, "yxyx");
  assert.strictEqual(moss.items.length, 3);
});

test("toMoss bbox 为中心原点负值并取整", () => {
  const moss = RegionAdapters.toMoss(doc);
  assert.deepStrictEqual(moss.items.map((item) => item.bbox), [
    [-410, -410, 0, -102],
    [-307, 51, 307, 410],
    [256, -307, 410, 307],
  ]);
  moss.items.forEach((item) => item.bbox.forEach((v) => assert.ok(Number.isInteger(v), "bbox 必须是整数")));
});

test("toMoss 轴序为 yxyx（非方形画布可区分 x/y）", () => {
  const square = {
    canvas: { w: 1024, h: 768 },
    global: { caption_zh: "", caption_en: "" },
    regions: [{ id: "a", bbox: [0.25, 0.1, 0.75, 0.9], role: "obj", nl: { zh: "", en: "" }, tags: [] }],
  };
  const moss = RegionAdapters.toMoss(square);
  assert.deepStrictEqual(moss.items[0].bbox, [-307, -256, 307, 256]);
});

test("toMoss 画布宽度非 16 倍数时抛中文错误", () => {
  const bad = Object.assign({}, doc, { canvas: { w: 1000, h: 1024 } });
  assert.throws(() => RegionAdapters.toMoss(bad), /宽度是 16 的倍数/);
});

test("toMoss 画布高度非 16 倍数时抛中文错误", () => {
  const bad = Object.assign({}, doc, { canvas: { w: 1024, h: 1000 } });
  assert.throws(() => RegionAdapters.toMoss(bad), /高度是 16 的倍数/);
});

test("muted 与零面积框在所有导出中被跳过", () => {
  const moss = RegionAdapters.toMoss(doc);
  const ideo = RegionAdapters.toIdeogram(doc);
  const jsonl = JSON.parse(RegionAdapters.toGenericJsonl(doc));
  const tags = RegionAdapters.toTagLines(doc);
  assert.deepStrictEqual(moss.items.map((item) => item.caption), [
    "a person on the left",
    "a tree on the right",
    "sign text",
  ]);
  assert.strictEqual(ideo.compositional_deconstruction.elements.length, 3);
  assert.strictEqual(jsonl.regions.length, 3);
  assert.strictEqual(tags.split("\n").length, 3);
  assert.ok(!moss.items.some((item) => /muted|zero area/.test(item.caption)));
});

test("toMoss 角色映射与 text 框 literalText 隔离", () => {
  const moss = RegionAdapters.toMoss(doc);
  assert.deepStrictEqual(moss.items.map((item) => item.label), ["object", "object", "text"]);
  assert.strictEqual(moss.items[2].text, 'HAPPY "DAY"');
  assert.strictEqual(moss.items[0].text, undefined);
  assert.strictEqual(moss.items[1].text, undefined);
});

test("toMoss caption 在 nl 为空时回退 tags 拼接", () => {
  const tagsOnly = {
    canvas: { w: 1024, h: 1024 },
    global: { caption_zh: "", caption_en: "" },
    regions: [{
      id: "t1",
      bbox: [0.1, 0.1, 0.2, 0.2],
      role: "obj",
      literalText: "",
      nl: { zh: "", en: "" },
      tags: [{ tag: "cat" }, { tag: "cute" }],
    }],
  };
  assert.strictEqual(RegionAdapters.toMoss(tagsOnly).items[0].caption, "cat, cute");
});

test("toIdeogram bbox 映射到 0-1000 且保持 yxyx", () => {
  const ideo = RegionAdapters.toIdeogram(doc);
  assert.deepStrictEqual(ideo.high_level_description, "a girl in a red hood beside a tree");
  assert.deepStrictEqual(ideo.style_description, {
    aesthetics: "",
    lighting: "",
    photo: "",
    medium: "",
    color_palette: [],
  });
  assert.strictEqual(ideo.compositional_deconstruction.background, "");
  assert.deepStrictEqual(ideo.compositional_deconstruction.elements.map((el) => el.bbox), [
    [100, 100, 500, 400],
    [200, 550, 800, 900],
    [750, 200, 900, 800],
  ]);
  const flat = ideo.compositional_deconstruction.elements.flatMap((el) => el.bbox);
  flat.forEach((v) => assert.ok(Number.isInteger(v) && v >= 0 && v <= 1000, "0-1000 整数"));
});

test("toIdeogram text 元素写 text+desc，desc 不回退 tags", () => {
  const textOnlyTags = {
    canvas: { w: 1024, h: 1024 },
    global: { caption_zh: "", caption_en: "" },
    regions: [{
      id: "t1",
      bbox: [0.1, 0.1, 0.3, 0.3],
      role: "text",
      literalText: "HELLO",
      nl: { zh: "", en: "" },
      tags: [{ tag: "sign" }],
    }],
  };
  const element = RegionAdapters.toIdeogram(textOnlyTags).compositional_deconstruction.elements[0];
  assert.deepStrictEqual(element, { type: "text", bbox: [100, 100, 300, 300], text: "HELLO", desc: "" });
});

test("toGenericJsonl 默认一行一图 + regions 字段齐全", () => {
  const line = RegionAdapters.toGenericJsonl(doc);
  assert.strictEqual(line.split("\n").length, 1);
  const row = JSON.parse(line);
  assert.strictEqual(row.image, "ref.png");
  assert.strictEqual(row.width, 1024);
  assert.strictEqual(row.height, 1024);
  assert.strictEqual(row.caption, "a girl in a red hood beside a tree");
  assert.deepStrictEqual(row.regions.map((region) => region.bbox), [
    [0.1, 0.1, 0.4, 0.5],
    [0.55, 0.2, 0.9, 0.8],
    [0.2, 0.75, 0.8, 0.9],
  ]);
  assert.deepStrictEqual(row.regions.map((region) => region.prompt), [
    "a person on the left",
    "a tree on the right",
    "sign text",
  ]);
  assert.deepStrictEqual(row.regions[0].tags, ["person", "red_hood"]);
});

test("toGenericJsonl 的 prompt 不回退 tags", () => {
  const tagsOnly = {
    canvas: { w: 1024, h: 1024 },
    global: { caption_zh: "", caption_en: "" },
    regions: [{ id: "t1", bbox: [0.1, 0.1, 0.2, 0.2], role: "obj", nl: { zh: "", en: "" }, tags: [{ tag: "cat" }] }],
  };
  const row = JSON.parse(RegionAdapters.toGenericJsonl(tagsOnly));
  assert.strictEqual(row.regions[0].prompt, "");
  assert.deepStrictEqual(row.regions[0].tags, ["cat"]);
});

test("toGenericJsonl perRegion=true 时一框一行", () => {
  const lines = RegionAdapters.toGenericJsonl(doc, true).split("\n");
  assert.strictEqual(lines.length, 3);
  const rows = lines.map((line) => JSON.parse(line));
  rows.forEach((row) => {
    assert.deepStrictEqual(Object.keys(row).sort(), ["bbox", "image", "prompt", "tags"]);
    assert.strictEqual(row.image, "ref.png");
    assert.ok(Array.isArray(row.bbox) && row.bbox.length === 4);
  });
  assert.deepStrictEqual(rows[2].bbox, [0.2, 0.75, 0.8, 0.9]);
  assert.strictEqual(rows[2].prompt, "sign text");
  assert.deepStrictEqual(rows[2].tags, ["sign"]);
});

test("toTagLines 每框一行且 text 角色带 literalText 前缀", () => {
  const lines = RegionAdapters.toTagLines(doc).split("\n");
  assert.deepStrictEqual(lines, [
    "person, red_hood, a person on the left",
    "tree, a tree on the right",
    'text: "HAPPY \\"DAY\\"" | sign, sign text',
  ]);
});

test("fromIdeogram 往返：toIdeogram → fromIdeogram → toIdeogram 二次一致", () => {
  const ideo = RegionAdapters.toIdeogram(doc);
  const back = RegionAdapters.fromIdeogram(JSON.stringify(ideo));
  assert.deepStrictEqual(RegionAdapters.toIdeogram(back), ideo);
  assert.strictEqual(back.regions[0].role, "obj");
  assert.strictEqual(back.regions[2].role, "text");
  assert.strictEqual(back.regions[2].literalText, 'HAPPY "DAY"');
  assert.strictEqual(back.global.caption_en, ideo.high_level_description);
});

test("fromMoss 往返：toMoss → fromMoss → toMoss 二次一致", () => {
  const moss = RegionAdapters.toMoss(doc);
  const back = RegionAdapters.fromMoss(JSON.stringify(moss), { w: 1024, h: 1024 });
  assert.deepStrictEqual(RegionAdapters.toMoss(back), moss);
  assert.strictEqual(back.regions[2].role, "text");
  assert.strictEqual(back.regions[2].literalText, 'HAPPY "DAY"');
  assert.strictEqual(back.canvas.w, 1024);
  assert.strictEqual(back.canvas.h, 1024);
});

test("fromIdeogram 解析失败时抛错", () => {
  assert.throws(() => RegionAdapters.fromIdeogram("not json"), /解析失败/);
  assert.throws(() => RegionAdapters.fromIdeogram({}), /elements/);
});

test("fromMoss 缺少画布宽高或 bbox 格式不支持时抛错", () => {
  const moss = RegionAdapters.toMoss(doc);
  assert.throws(() => RegionAdapters.fromMoss(JSON.stringify(moss)), /画布宽高/);
  assert.throws(
    () => RegionAdapters.fromMoss(Object.assign({}, moss, { bbox_format: "xyxy" }), { w: 1024, h: 1024 }),
    /bbox_format/
  );
  assert.throws(() => RegionAdapters.fromMoss("{}"), /items/);
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
