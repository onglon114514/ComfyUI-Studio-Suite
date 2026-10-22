// Region Layout 选中框缩放测试（node >= 22，零依赖）
// 从 web/js/region_layout_node.js 抽取 window.RegionResize 块 eval，纯函数直测。
"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const jsPath = path.join(__dirname, "..", "web", "js", "region_layout_node.js");
const source = fs.readFileSync(jsPath, "utf8");
const match = source.match(/\/\/ == REGION_RESIZE_BEGIN ==([\s\S]*?)\/\/ == REGION_RESIZE_END ==/);
if (!match) {
  console.error("未能从 web/js/region_layout_node.js 提取 RegionResize 块（缺少 BEGIN/END 标记）");
  process.exit(1);
}

let RegionResize;
try {
  RegionResize = new Function("window", match[0] + "\nreturn window.RegionResize;")({});
} catch (err) {
  console.error("RegionResize 块 eval 失败: " + err.message);
  process.exit(1);
}
if (!RegionResize || typeof RegionResize.resizeBbox !== "function" || typeof RegionResize.handleAtPoint !== "function" || typeof RegionResize.badgeRect !== "function") {
  console.error("RegionResize 未导出预期函数");
  process.exit(1);
}

const { resizeBbox, handleAtPoint, badgeRect } = RegionResize;

function bbox(values) {
  return values.slice();
}

function near(actual, expected, message) {
  assert.ok(
    Math.abs(actual - expected) < 1e-9,
    (message || "数值不匹配") + ": 期望 " + expected + "，实际 " + actual
  );
}

function assertBbox(actual, expected, message) {
  assert.ok(Array.isArray(actual), (message || "bbox 不匹配") + ": 返回非数组");
  assert.strictEqual(actual.length, 4, (message || "bbox 不匹配") + ": 长度应为 4");
  for (let index = 0; index < 4; index += 1) {
    near(actual[index], expected[index], (message || "bbox 不匹配") + " [" + index + "]");
  }
}

const tests = [];
function test(name, fn) {
  tests.push({ name, fn });
}

// ---------- resizeBbox：八个方向 ----------

test("resize se：右/下边外扩，左上角固定", () => {
  const origin = bbox([0.2, 0.2, 0.6, 0.6]);
  assertBbox(resizeBbox(origin, "se", 0.1, 0.1, 0.01), [0.2, 0.2, 0.7, 0.7]);
  assertBbox(origin, [0.2, 0.2, 0.6, 0.6], "原 bbox 不应被修改");
});

test("resize nw：左/上边外扩，右下角固定", () => {
  assertBbox(resizeBbox([0.2, 0.2, 0.6, 0.6], "nw", -0.1, -0.1, 0.01), [0.1, 0.1, 0.6, 0.6]);
});

test("resize ne：右边外扩、上边外扩，左下角固定", () => {
  assertBbox(resizeBbox([0.2, 0.2, 0.6, 0.6], "ne", 0.1, -0.1, 0.01), [0.2, 0.1, 0.7, 0.6]);
});

test("resize sw：左边外扩、下边外扩，右上角固定", () => {
  assertBbox(resizeBbox([0.2, 0.2, 0.6, 0.6], "sw", -0.1, 0.1, 0.01), [0.1, 0.2, 0.6, 0.7]);
});

test("resize e：只改右边，纵向位移被忽略", () => {
  assertBbox(resizeBbox([0.2, 0.2, 0.6, 0.6], "e", 0.05, 0.4, 0.01), [0.2, 0.2, 0.65, 0.6]);
});

test("resize w：只改左边，纵向位移被忽略", () => {
  assertBbox(resizeBbox([0.2, 0.2, 0.6, 0.6], "w", -0.05, -0.4, 0.01), [0.15, 0.2, 0.6, 0.6]);
});

test("resize n：只改上边，横向位移被忽略", () => {
  assertBbox(resizeBbox([0.2, 0.2, 0.6, 0.6], "n", 0.4, -0.05, 0.01), [0.2, 0.15, 0.6, 0.6]);
});

test("resize s：只改下边，横向位移被忽略", () => {
  assertBbox(resizeBbox([0.2, 0.2, 0.6, 0.6], "s", -0.4, 0.05, 0.01), [0.2, 0.2, 0.6, 0.65]);
});

// ---------- 边界 clamp ----------

test("clamp 0：nw 拖出左上边界，固定右下角并停在 0", () => {
  assertBbox(resizeBbox([0.2, 0.2, 0.6, 0.6], "nw", -5, -5, 0.01), [0, 0, 0.6, 0.6]);
});

test("clamp 1：se 拖出右下边界，固定左上角并停在 1", () => {
  assertBbox(resizeBbox([0.2, 0.2, 0.6, 0.6], "se", 5, 5, 0.01), [0.2, 0.2, 1, 1]);
});

test("clamp 0/1：w 边拖到左侧外、s 边拖到下方外", () => {
  assertBbox(resizeBbox([0.2, 0.2, 0.6, 0.6], "w", -5, 0, 0.01), [0, 0.2, 0.6, 0.6]);
  assertBbox(resizeBbox([0.2, 0.2, 0.6, 0.6], "s", 0, 5, 0.01), [0.2, 0.2, 0.6, 1]);
});

// ---------- 最小尺寸 ----------

test("最小尺寸：w 向右拖过头时 x1 停在 x2 - minSize", () => {
  assertBbox(resizeBbox([0.2, 0.2, 0.6, 0.6], "w", 0.5, 0, 0.05), [0.55, 0.2, 0.6, 0.6]);
});

test("最小尺寸：e 向左拖过头时 x2 停在 x1 + minSize", () => {
  assertBbox(resizeBbox([0.2, 0.2, 0.6, 0.6], "e", -0.5, 0, 0.05), [0.2, 0.2, 0.25, 0.6]);
});

test("最小尺寸：n/s 纵向同样生效", () => {
  assertBbox(resizeBbox([0.2, 0.2, 0.6, 0.6], "n", 0, 0.5, 0.05), [0.2, 0.55, 0.6, 0.6]);
  assertBbox(resizeBbox([0.2, 0.2, 0.6, 0.6], "s", 0, -0.5, 0.05), [0.2, 0.2, 0.6, 0.25]);
});

test("最小尺寸：缺省值 0.01", () => {
  assertBbox(resizeBbox([0.2, 0.2, 0.6, 0.6], "w", 1, 0), [0.59, 0.2, 0.6, 0.6]);
});

// ---------- handleAtPoint ----------

const box = [0.25, 0.25, 0.75, 0.75];
const rect = { w: 400, h: 200 };
// 归一化 → 内部像素：左上 (100,50)，右上 (300,50)，右下 (300,150)，左下 (100,150)
const cornerPoints = {
  nw: { x: 0.25, y: 0.25 },
  ne: { x: 0.75, y: 0.25 },
  se: { x: 0.75, y: 0.75 },
  sw: { x: 0.25, y: 0.75 },
};
const edgePoints = {
  n: { x: 0.5, y: 0.25 },
  e: { x: 0.75, y: 0.5 },
  s: { x: 0.5, y: 0.75 },
  w: { x: 0.25, y: 0.5 },
};

test("handleAtPoint：四角命中", () => {
  for (const [handle, point] of Object.entries(cornerPoints)) {
    assert.strictEqual(handleAtPoint(box, point, rect, 7), handle, handle + " 应命中");
  }
});

test("handleAtPoint：四边中点命中", () => {
  for (const [handle, point] of Object.entries(edgePoints)) {
    assert.strictEqual(handleAtPoint(box, point, rect, 7), handle, handle + " 应命中");
  }
});

test("handleAtPoint：框中心不命中任何 handle", () => {
  assert.strictEqual(handleAtPoint(box, { x: 0.5, y: 0.5 }, rect, 7), null);
});

test("handleAtPoint：容差边界（<= 7px 命中，7.01px 不命中）", () => {
  // nw 像素位置 (100, 50)；x + 7px / y + 7px 仍在边界上
  assert.strictEqual(handleAtPoint(box, { x: 107 / 400, y: 57 / 200 }, rect, 7), "nw");
  assert.strictEqual(handleAtPoint(box, { x: 107.01 / 400, y: 50 / 200 }, rect, 7), null);
  assert.strictEqual(handleAtPoint(box, { x: 100 / 400, y: 42.99 / 200 }, rect, 7), null);
});

test("handleAtPoint：容差为 0 时只命中精确点", () => {
  const exact = { x: 0.75, y: 0.75 };
  assert.strictEqual(handleAtPoint(box, exact, rect, 0), "se");
  assert.strictEqual(handleAtPoint(box, { x: 0.751, y: 0.75 }, rect, 0), null);
});

test("handleAtPoint：小框重叠时四角优先于四边", () => {
  const tiny = [0.5, 0.5, 0.5001, 0.5001];
  assert.strictEqual(handleAtPoint(tiny, { x: 0.5, y: 0.5 }, rect, 7), "nw");
});

test("handleAtPoint：支持 {width,height} 形式的 rect", () => {
  assert.strictEqual(handleAtPoint(box, { x: 0.75, y: 0.5 }, { width: 400, height: 200 }, 7), "e");
});

test("handleAtPoint：非法 bbox / rect / point 返回 null", () => {
  assert.strictEqual(handleAtPoint(null, { x: 0.5, y: 0.5 }, rect, 7), null);
  assert.strictEqual(handleAtPoint([0.1, 0.1, 0.2], { x: 0.5, y: 0.5 }, rect, 7), null);
  assert.strictEqual(handleAtPoint([0.1, NaN, 0.2, 0.3], { x: 0.5, y: 0.5 }, rect, 7), null);
  assert.strictEqual(handleAtPoint(box, { x: 0.5, y: 0.5 }, { w: 0, h: 200 }, 7), null);
  assert.strictEqual(handleAtPoint(box, { x: NaN, y: 0.5 }, rect, 7), null);
});

// ---------- 非法输入 ----------

test("resizeBbox：非法 handle 抛错", () => {
  assert.throws(() => resizeBbox(box, "center", 0.1, 0.1, 0.01), /unknown handle/);
  assert.throws(() => resizeBbox(box, "nwe", 0.1, 0.1, 0.01), /unknown handle/);
  assert.throws(() => resizeBbox(box, null, 0.1, 0.1, 0.01), /unknown handle/);
});

test("resizeBbox：非法 origin 返回 null", () => {
  assert.strictEqual(resizeBbox(null, "se", 0.1, 0.1, 0.01), null);
  assert.strictEqual(resizeBbox([0, 0, 1], "se", 0.1, 0.1, 0.01), null);
  assert.strictEqual(resizeBbox([0, 0, 1, Infinity], "se", 0.1, 0.1, 0.01), null);
});

test("resizeBbox：非有限 dx/dy 按 0 处理，不移动", () => {
  assertBbox(resizeBbox([0.2, 0.2, 0.6, 0.6], "se", NaN, undefined, 0.01), [0.2, 0.2, 0.6, 0.6]);
});

test("badgeRect：位于框左上角上方", () => {
  const rect = { x: 10, y: 20, w: 200, h: 100 };
  const badge = badgeRect([0.25, 0.25, 0.75, 0.5], rect, 1, 10);
  near(badge.x, 60);
  near(badge.y, 20 + 25 - 14);
  near(badge.w, 18);
  near(badge.h, 14);
});

test("badgeRect：触顶时夹在画布内", () => {
  const rect = { x: 0, y: 0, w: 100, h: 100 };
  const badge = badgeRect([0, 0, 0.5, 0.5], rect, 1, 8);
  near(badge.y, 0);
});

test("badgeRect：随 scale 放大", () => {
  const rect = { x: 0, y: 0, w: 300, h: 300 };
  const badge = badgeRect([0.5, 0.5, 1, 1], rect, 2, 10);
  near(badge.h, 28);
  near(badge.w, 26);
});

test("badgeRect：非法输入返回 null", () => {
  assert.strictEqual(badgeRect(null, { x: 0, y: 0, w: 10, h: 10 }, 1, 5), null);
  assert.strictEqual(badgeRect([0.1, 0.1, 0.2, 0.2], null, 1, 5), null);
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
