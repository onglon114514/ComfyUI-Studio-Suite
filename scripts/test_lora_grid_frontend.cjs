// test_lora_grid_frontend.cjs — LoRA 网格前端验收测试（虚拟化 + 懒加载 + 限并发）
//
// 在实机 ComfyUI 的提示词编辑器中，用 page.route 伪造 200+ 条 LoRA 列表，把仓库里的
// loraGrid.js 注入页面并调用 installLoraGrid()，然后测量并断言：
//   1) 我们新建容器（div.group-tags）里的卡片数 <= MAX_DOM_CARDS(40)
//   2) 页面 model-preview 请求总数 << 200（记录实际值）
//   3) model-preview 同时在飞请求峰值 <= MAX_IMG_CONCURRENT(4)
//   4) 被隐藏的旧容器是否仍然产生 model-preview 请求（如实记录）
//
// 说明：实装目录尚未接线 loraGrid.js，故本测试把仓库源文件当作被测对象注入实机页面，
// 不部署、不修改任何其它文件。旧渲染器仍在渲染被隐藏的容器，故其请求会与新网格并存，
// 用 URL 参数区分：新网格带 w=192，旧渲染器带 v=OLD。
//
// Env: PLAYWRIGHT_MODULE, CHROME_PATH, COMFY_URL
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const fs = require('fs');
const path = require('path');

const baseUrl = process.env.COMFY_URL || 'http://127.0.0.1:8188';
const MODULE_PATH = path.resolve(__dirname, '..', 'prompt_studio', 'frontend', 'js', 'loraGrid.js');

const MAX_DOM_CARDS = 40;
const MAX_IMG_CONCURRENT = 4;
const FAKE_COUNT = 240;              // 200+ 假条目
const OLD_PREVIEW_TAG = 'v=OLD0001'; // 旧渲染器预览图 URL 标记（无 w= 参数）

// FrameLocator 没有 waitForFunction，用轮询 evaluate 代替
async function waitForInFrame(frame, fn, timeoutMs, label) {
  const deadline = Date.now() + timeoutMs;
  let last;
  while (Date.now() < deadline) {
    try {
      last = await frame.locator('body').evaluate(fn);
      if (last) return last;
    } catch (e) {
      last = e && e.message ? e.message : String(e);
    }
    await new Promise((r) => setTimeout(r, 300));
  }
  throw new Error(`timeout waiting for ${label}; last=${JSON.stringify(last)}`);
}
const LORAS_API = '**/studio-suite/prompt-studio/autocomplete/loras*';
const EXTRA_NETWORKS_API = '**/weilin/physton_prompt/get_extra_networks*';
const PREVIEW_API = '**/studio-suite/prompt-studio/model-preview*';

// 1x1 透明 PNG（用于快速响应 model-preview，避免压真实缩略图渲染）
const TINY_PNG = Buffer.from(
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII=',
  'base64'
);

// ---- 假数据 ----
const fakeNames = Array.from({ length: FAKE_COUNT }, (_, i) => `fake_lora_${String(i + 1).padStart(4, '0')}`);

// autocomplete/loras 返回字符串名数组（与实机一致）
function fakeLorasArray() {
  return JSON.stringify(fakeNames);
}

// get_extra_networks 返回 { extra_networks: [{ name:'lora', items:[...] }] }（旧渲染器吃这个）
function fakeExtraNetworks() {
  const items = fakeNames.map((name) => ({
    basename: `${name}.safetensors`,
    name: `${name}.safetensors`,
    dirname: 'E:\\fake\\loras',
    filename: `E:\\fake\\loras\\${name}.safetensors`,
    description: '',
    preview: `/studio-suite/prompt-studio/model-preview?type=loras&name=${encodeURIComponent(name)}&${OLD_PREVIEW_TAG}`,
    model_name: name,
    model_type: 'loras',
    model_filename: `${name}.safetensors`,
    output_name: '',
    prompt: '',
    local_info: '',
  }));
  return JSON.stringify({ extra_networks: [{ name: 'lora', title: 'Lora', items }] });
}

// ---- 请求分类与计数 ----
function classify(url) {
  if (!url.includes('model-preview')) return null;
  return url.includes('w=192') ? 'new' : 'old';
}

// ---- 页面内测量 ----
const MEASURE = () => {
  const tags = [...document.querySelectorAll('div.group-tags')];
  const buckets = { newCards: null, newDisplay: null, oldCards: null, oldDisplay: null };
  for (const t of tags) {
    const display = getComputedStyle(t).display;
    const cards = t.querySelectorAll(':scope > div.group-extra-network').length;
    if (display === 'none') {
      // 被隐藏的旧容器（可能有多个 none，取卡片最多的）
      if (buckets.oldCards === null || cards > buckets.oldCards) {
        buckets.oldCards = cards;
        buckets.oldDisplay = display;
      }
    } else {
      // 可见容器 = 我们新建的
      if (buckets.newCards === null || cards > buckets.newCards) {
        buckets.newCards = cards;
        buckets.newDisplay = display;
      }
    }
  }
  return {
    ...buckets,
    installed: !!window.__studioSuiteLoraGridInstalled,
    allGroupTags: tags.length,
  };
};

(async () => {
  const counters = {
    total: 0, newCount: 0, oldCount: 0,
    inFlight: 0, peak: 0,
    newInFlight: 0, newPeak: 0,
    oldInFlight: 0, oldPeak: 0,
  };

  const browser = await chromium.launch({
    headless: true,
    ...(process.env.CHROME_PATH ? { executablePath: process.env.CHROME_PATH } : {}),
  });

  try {
    const page = await browser.newPage({ viewport: { width: 1600, height: 1000 }, locale: 'zh-CN' });

    // 计数须在面板打开前注册（旧渲染器在面板打开时就开始发请求）
    const onReq = (r) => {
      const c = classify(r.url());
      if (!c) return;
      counters.total++;
      if (c === 'new') counters.newCount++; else counters.oldCount++;
      counters.inFlight++;
      counters.peak = Math.max(counters.peak, counters.inFlight);
      if (c === 'new') { counters.newInFlight++; counters.newPeak = Math.max(counters.newPeak, counters.newInFlight); }
      else { counters.oldInFlight++; counters.oldPeak = Math.max(counters.oldPeak, counters.oldInFlight); }
    };
    const onDone = (r) => {
      const c = classify(r.url());
      if (!c) return;
      counters.inFlight--;
      if (c === 'new') counters.newInFlight--; else counters.oldInFlight--;
    };
    page.on('request', onReq);
    page.on('requestfinished', onDone);
    page.on('requestfailed', onDone);

    // 伪造三个接口
    await page.route(LORAS_API, (route) =>
      route.fulfill({ status: 200, contentType: 'application/json', body: fakeLorasArray() })
    );
    await page.route(EXTRA_NETWORKS_API, (route) =>
      route.fulfill({ status: 200, contentType: 'application/json', body: fakeExtraNetworks() })
    );
    await page.route(PREVIEW_API, (route) =>
      route.fulfill({ status: 200, contentType: 'image/png', body: TINY_PNG })
    );

    // 打开 ComfyUI，等 LiteGraph 就绪
    await page.goto(baseUrl, { waitUntil: 'domcontentloaded', timeout: 90000 });
    await page.waitForFunction(
      () => !!window.LiteGraph?.registered_node_types?.PromptStudioPositiveOutput,
      null,
      { timeout: 90000 }
    );
    await page.waitForTimeout(2500);

    // 打开提示词编辑器面板（沿用参考探针的做法）
    await page.evaluate(() => {
      const app = comfyAPI.app.app;
      window.__lgNode = LiteGraph.createNode('PromptStudioOutput');
      app.graph.add(window.__lgNode);
      window.__lgNode.widgets
        .find((w) => w.type === 'button' && w.name.includes('Prompt Studio'))
        .callback();
    });

    const frame = page.frameLocator('.studio-suite-prompt-iframe');
    await frame.locator('body[data-studio-suite-ready="1"]').waitFor({ timeout: 45000 });

    // 等旧渲染器把 240 张卡片物化进 DOM（这是 installLoraGrid 找旧容器的前提）
    await frame
      .locator('div.group-tags > div.group-extra-network')
      .first()
      .waitFor({ timeout: 30000 });
    await waitForInFrame(
      frame,
      () => document.querySelectorAll('div.group-tags > div.group-extra-network').length >= 200,
      30000,
      'old grid 200+ cards'
    );
    await page.waitForTimeout(3000); // 让旧渲染器把 preview 请求发出去

    // 注入仓库 loraGrid.js 作为真实 ES module 并调用 installLoraGrid()
    const moduleSource = fs.readFileSync(MODULE_PATH, 'utf8');
    const frameObj = page.frames().find((f) => f.url().includes('prompt-studio'));
    if (!frameObj) throw new Error('prompt-studio iframe frame not found');
    await frameObj.addScriptTag({
      content: moduleSource + '\ninstallLoraGrid();',
      type: 'module',
    });

    // 等我们新建的可见容器出现卡片
    await waitForInFrame(
      frame,
      () => {
        const tags = [...document.querySelectorAll('div.group-tags')];
        return tags.some(
          (t) =>
            getComputedStyle(t).display !== 'none' &&
            t.querySelectorAll(':scope > div.group-extra-network').length > 0
        );
      },
      30000,
      'new visible container with cards'
    );
    await page.waitForTimeout(5000); // 让新网格的懒加载队列跑完（并发 <=4 依次加载）

    const measured = await frame.locator('body').evaluate(MEASURE);

    // ---- 断言 ----
    const cardsOk = measured.newCards !== null && measured.newCards <= MAX_DOM_CARDS;
    const totalOk = counters.total < 200;             // "≪ 200"
    const peakOk = counters.newPeak <= MAX_IMG_CONCURRENT; // 新网格自身并发峰值
    const oldStillProduced = counters.oldCount > 0;
    const passed = cardsOk && totalOk && peakOk;

    const summary = {
      test: 'lora-grid-frontend',
      comfyUrl: baseUrl,
      fakeCount: FAKE_COUNT,
      module: MODULE_PATH,
      maxDomCards: MAX_DOM_CARDS,
      maxImgConcurrent: MAX_IMG_CONCURRENT,
      measured: {
        newContainer: { cards: measured.newCards, display: measured.newDisplay },
        oldContainerHidden: { cards: measured.oldCards, display: measured.oldDisplay },
        installed: measured.installed,
        allGroupTags: measured.allGroupTags,
      },
      requests: {
        modelPreviewTotal: counters.total,
        fromNewGrid_w192: counters.newCount,
        fromOldRenderer_vOLD: counters.oldCount,
        peakInFlightTotal: counters.peak,
        peakInFlightNewGrid: counters.newPeak,
        peakInFlightOld: counters.oldPeak,
      },
      assertions: {
        cards_le_40: cardsOk,
        total_model_preview_lt_200: totalOk,
        new_grid_peak_in_flight_le_4: peakOk,
        hidden_old_container_still_produced_requests: oldStillProduced,
      },
      passed,
    };

    console.log(JSON.stringify(summary, null, 2));
    if (!passed) process.exitCode = 1;
  } finally {
    await browser.close();
  }
})().catch((e) => {
  console.error('FATAL:', e && e.message ? e.message : e);
  process.exitCode = 1;
});
