// Region Layout ↔ Prompt Studio 文本桥测试（node >= 22，零依赖）
// 从 web/js/region_layout_node.js 抽取 window.RegionBoxBridge 块 eval，纯函数直测。
"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const jsPath = path.join(__dirname, "..", "web", "js", "region_layout_node.js");
const source = fs.readFileSync(jsPath, "utf8");
const match = source.match(/\/\/ == REGION_BOX_BRIDGE_BEGIN ==([\s\S]*?)\/\/ == REGION_BOX_BRIDGE_END ==/);
if (!match) {
  console.error("未能从 web/js/region_layout_node.js 提取 RegionBoxBridge 块（缺少 BEGIN/END 标记）");
  process.exit(1);
}

let bridge;
try {
  bridge = new Function("window", match[0] + "\nreturn window.RegionBoxBridge;")({});
} catch (err) {
  console.error("RegionBoxBridge 块 eval 失败: " + err.message);
  process.exit(1);
}
if (!bridge || typeof bridge.boxToEditorText !== "function" || typeof bridge.editorTextToBox !== "function") {
  console.error("RegionBoxBridge 未导出预期函数");
  process.exit(1);
}

function makeBox(overrides) {
  return Object.assign(
    {
      id: "r1",
      bbox: [0.1, 0.2, 0.4, 0.5],
      role: "obj",
      literalText: "",
      nl: { zh: "左侧的人", en: "a person on the left" },
      tags: ["person", "red_hood"],
      muted: false,
      linkedTo: null,
      colors: ["#ff0000"],
    },
    overrides
  );
}

const tests = [];
function test(name, fn) {
  tests.push({ name, fn });
}

test("round-trip：tags + nl.en", () => {
  const box = makeBox();
  const text = bridge.boxToEditorText(box);
  assert.strictEqual(text, "person, red_hood\n\na person on the left");
  const back = bridge.editorTextToBox(text, box);
  assert.deepStrictEqual(back.tags, ["person", "red_hood"]);
  assert.strictEqual(back.nl.en, "a person on the left");
  assert.strictEqual(back.nl.zh, "左侧的人");
});

test("round-trip：仅 tags（空行标记 tags 段）", () => {
  const box = makeBox({ nl: { zh: "左侧的人", en: "" } });
  const text = bridge.boxToEditorText(box);
  assert.strictEqual(text, "person, red_hood\n\n");
  const back = bridge.editorTextToBox(text, box);
  assert.deepStrictEqual(back.tags, ["person", "red_hood"]);
  assert.strictEqual(back.nl.en, "");
  assert.strictEqual(back.nl.zh, "左侧的人");
});

test("round-trip：仅 nl.en（无空行，整段视为 nl.en）", () => {
  const box = makeBox({ tags: [] });
  const text = bridge.boxToEditorText(box);
  assert.strictEqual(text, "a person on the left");
  const back = bridge.editorTextToBox(text, box);
  assert.deepStrictEqual(back.tags, []);
  assert.strictEqual(back.nl.en, "a person on the left");
});

test("round-trip：tags 与 nl.en 皆空", () => {
  const box = makeBox({ tags: [], nl: { zh: "中文", en: "" } });
  assert.strictEqual(bridge.boxToEditorText(box), "");
  const back = bridge.editorTextToBox("", box);
  assert.deepStrictEqual(back.tags, []);
  assert.strictEqual(back.nl.en, "");
  assert.strictEqual(back.nl.zh, "中文");
});

test("中文逗号作为 tags 分隔符", () => {
  const box = makeBox({ nl: { zh: "", en: "" } });
  const back = bridge.editorTextToBox("猫，狗，猫\n\n", box);
  assert.deepStrictEqual(back.tags, ["猫", "狗"]);
});

test("重复 tag 去重并保持首次出现顺序", () => {
  const box = makeBox({ nl: { zh: "", en: "" } });
  const back = bridge.editorTextToBox("cat, dog, cat, DOG, bird\n\n", box);
  assert.deepStrictEqual(back.tags, ["cat", "dog", "bird"]);
});

test("大小写与空白 normalize（小写、空白→下划线、去首尾下划线）", () => {
  const box = makeBox({ nl: { zh: "", en: "" } });
  const back = bridge.editorTextToBox("  Red  Hood ,\tTREE ,__Cat__\n\n", box);
  assert.deepStrictEqual(back.tags, ["red_hood", "tree", "cat"]);
});

test("text 角色：literalText 与 role 不被覆盖", () => {
  const box = makeBox({
    role: "text",
    literalText: 'HAPPY "DAY"',
    tags: ["sign"],
    nl: { zh: "招牌文字", en: "sign text" },
  });
  const initial = bridge.boxToEditorText(box);
  assert.ok(!initial.includes('HAPPY "DAY"'), "literalText 不进入编辑文本");
  assert.strictEqual(initial, "sign\n\nsign text");
  const back = bridge.editorTextToBox("new_sign\n\nnew sign text", box);
  assert.strictEqual(back.role, "text");
  assert.strictEqual(back.literalText, 'HAPPY "DAY"');
  assert.deepStrictEqual(back.tags, ["new_sign"]);
  assert.strictEqual(back.nl.en, "new sign text");
});

test("多行 nl.en 保留", () => {
  const box = makeBox({ tags: ["person"], nl: { zh: "", en: "line one\nline two\nline three" } });
  const text = bridge.boxToEditorText(box);
  assert.strictEqual(text, "person\n\nline one\nline two\nline three");
  const back = bridge.editorTextToBox(text, box);
  assert.strictEqual(back.nl.en, "line one\nline two\nline three");
  assert.deepStrictEqual(back.tags, ["person"]);
});

test("tags 行混中文时逐个 normalize", () => {
  const box = makeBox({ nl: { zh: "", en: "" } });
  const back = bridge.editorTextToBox("猫 耳，RED  Hat，猫_耳，Red Hat\n\n", box);
  assert.deepStrictEqual(back.tags, ["猫_耳", "red_hat"]);
});

test("无空行时整段作为 nl.en 且清空 tags", () => {
  const box = makeBox();
  const back = bridge.editorTextToBox("just english text", box);
  assert.strictEqual(back.nl.en, "just english text");
  assert.deepStrictEqual(back.tags, []);
  assert.strictEqual(back.nl.zh, "左侧的人");
});

test("回写保留 bbox/role/muted/linkedTo/colors，且不修改输入对象", () => {
  const box = makeBox({ muted: true, linkedTo: "r2" });
  const snapshot = JSON.parse(JSON.stringify(box));
  const back = bridge.editorTextToBox("cat\n\ncat desc", box);
  assert.deepStrictEqual(box, snapshot, "输入 box 必须保持不变");
  assert.notStrictEqual(back, box);
  assert.deepStrictEqual(back.bbox, box.bbox);
  assert.strictEqual(back.role, box.role);
  assert.strictEqual(back.muted, true);
  assert.strictEqual(back.linkedTo, "r2");
  assert.deepStrictEqual(back.colors, box.colors);
});

test("nl.en 前后空白被 trim，tags 行左右空白被忽略", () => {
  const box = makeBox({ nl: { zh: "", en: "" } });
  const back = bridge.editorTextToBox("  cat , dog  \n\n  a cat and a dog  ", box);
  assert.deepStrictEqual(back.tags, ["cat", "dog"]);
  assert.strictEqual(back.nl.en, "a cat and a dog");
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
