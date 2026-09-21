// Compatibility probe for the weilin/physton DOM contract, run against a REAL
// ComfyUI (no mocks). Asserts every third-party selector Studio Suite depends
// on still exists with expected visibility, and prints EXACTLY which selector
// changed on failure. Run this before upgrading ComfyUI or the physton
// prompt-all-in-one plugin. Env: PLAYWRIGHT_MODULE, CHROME_PATH, COMFY_URL,
// STUDIO_SUITE_TEST_OUTPUT (screenshots on failure).
//
// Selectors are NOT duplicated here. They are read from the deployed frontend's
// window.__studioSuiteWeilinSelectors, published by weilinAdapter.js, so this
// probe cannot drift from production. A missing map means the deployed frontend
// is older than this probe — deploy first, then re-run.
// Expected state on a live ComfyUI (2026-09-21): the `filled` checks report zero
// prompt tags. The explanation committed in 8771f74 — that our frontend never
// POSTs /weilin/physton_prompt/set_data — is WRONG and is corrected here: that
// route is the XML wizard persisting its own settings (newbieXmlWizard
// saveSettings, key=SETTINGS_KEY), not a prompt-text bridge.
//
// Rules out so far: the input path. input.fill() and real key-by-key typing both
// leave the tag lists empty. Rules in: the stubbed browser tests DO render tags,
// so the difference is what the environment provides, not how text arrives.
// Open question: whether the tag cloud needs a prompt snapshot pushed into the
// iframe by the node controller before it renders anything.
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const path = require('node:path');
const output = process.env.STUDIO_SUITE_TEST_OUTPUT || require('node:os').tmpdir();
const baseUrl = process.env.COMFY_URL || 'http://127.0.0.1:8188';

// `keys` are looked up in the adapter's WEILIN_SELECTORS; multiple keys form a
// descendant path. `literal` is for our own markup, which the adapter does not
// own. `min` = required element count; `visible: true` = at least one visible box.
const EXPECTATIONS = [
  { id: 'positive-textarea', literal: '#weilin_prompt_text_input', min: 1, visible: true, required: true, note: 'positive editor textarea (our own index.html, sanity anchor)' },
  { id: 'prompt-tags', keys: ['promptTags'], min: 1, required: true, note: 'physton tag cloud roots; bilingual annotations + A/B capture mount here' },
  { id: 'tags-list', keys: ['tagsList'], min: 1, required: true, note: 'tag list container watched by our MutationObserver' },
  { id: 'extend-translate', keys: ['translateButtons'], min: 1, required: true, note: 'legacy A/B translate button captured by studioSuiteLlmTools' },
  { id: 'group-tag-items', keys: ['groupTagItems'], min: 1, required: true, note: 'tag cloud items; existence required, visibility is node-type dependent (see positiveOnlyVisibility)' },
  { id: 'legacy-ai-icon', keys: ['legacyAiIcon'], required: false, note: 'legacy AI entrypoint we hide; absence is fine, presence must be hideable' },
  { id: 'legacy-ai-packages', keys: ['legacyAiPackages'], required: false, note: 'legacy AI entrypoint we hide' },
];
// Checks evaluated only AFTER text is entered and the tag cloud re-renders.
const AFTER_FILL_EXPECTATIONS = [
  { id: 'tag-items', keys: ['tag'], min: 1, required: true, note: 'rendered prompt tags; annotation targets' },
  { id: 'tag-values', keys: ['tag', 'tagValue'], min: 1, required: true, note: 'tag value nodes read by weilinReadPromptTagText' },
];

(async () => {
  const failures = [], warnings = [], report = [];

  const browser = await chromium.launch({ headless: true, ...(process.env.CHROME_PATH ? { executablePath: process.env.CHROME_PATH } : {}) });
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 1000 }, locale: 'zh-CN' });
    const errors = [];
    page.on('pageerror', e => errors.push(e.message));
    // The probe runs UNSTUBBED, so our frontend's real sync calls to physton are
    // the prime suspect when tags never render. Record them with their status.
    const weilinCalls = [];
    const consoleErrors = [];
    page.on('response', r => {
      if (r.url().includes('/weilin/')) weilinCalls.push(`${r.request().method()} ${new URL(r.url()).pathname} -> ${r.status()}`);
    });
    page.on('console', m => { if (m.type() === 'error') consoleErrors.push(m.text().slice(0, 200)); });
    await page.goto(baseUrl, { waitUntil: 'domcontentloaded', timeout: 90000 });
    await page.waitForFunction(() => !!window.LiteGraph?.registered_node_types?.PromptStudioPositiveOutput, null, { timeout: 90000 });
    await page.waitForTimeout(3000);
    await page.evaluate(() => {
      const app = comfyAPI.app.app;
      window.probeFullNode = LiteGraph.createNode('PromptStudioOutput'); app.graph.add(probeFullNode);
      window.probePosNode = LiteGraph.createNode('PromptStudioPositiveOutput'); app.graph.add(probePosNode);
    });
    const openEditor = async (posOnly) => {
      await page.evaluate(posOnly => {
        const n = posOnly ? probePosNode : probeFullNode;
        n.widgets.find(w => w.type === 'button' && w.name.includes('Prompt Studio')).callback();
      }, posOnly);
      return page.frameLocator('.studio-suite-prompt-iframe');
    };
    const measure = (frame, list) => frame.locator('body').evaluate((_element, list) => list.map(({ id, selector }) => {
      const els = [...document.querySelectorAll(selector)];
      const visible = els.filter(el => el.getClientRects().length > 0 && getComputedStyle(el).visibility !== 'hidden').length;
      return { id, selector, count: els.length, visible };
    }), list);
    const judge = (results, list, phase) => {
      for (const exp of list) {
        const got = results.find(r => r.id === exp.id) || { count: 0, visible: 0 };
        const entry = { phase, id: exp.id, selector: exp.selector, count: got.count, visible: got.visible, note: exp.note };
        report.push(entry);
        if (!exp.required) continue;
        if (got.count < (exp.min || 1)) failures.push(`${phase} ${exp.id}: selector "${exp.selector}" matched ${got.count} element(s), need >= ${exp.min || 1} — ${exp.note}`);
        else if (exp.visible && got.visible < 1) failures.push(`${phase} ${exp.id}: selector "${exp.selector}" matched ${got.count} element(s) but none visible — ${exp.note}`);
      }
    };
    // Turn adapter key references into concrete selectors. A missing key is a
    // real finding: the probe and the adapter no longer agree.
    const resolveList = (list) => {
      const out = [];
      for (const exp of list) {
        if (exp.literal) { out.push({ ...exp, selector: exp.literal }); continue; }
        const parts = [];
        for (const key of exp.keys) {
          const value = selectorMap?.[key];
          if (typeof value !== 'string' || !value) {
            failures.push(`SELECTOR-MAP ${exp.id}: weilinAdapter WEILIN_SELECTORS.${key} is missing or not a string — sync the probe with the adapter`);
            break;
          }
          parts.push(value);
        }
        if (parts.length === exp.keys.length) out.push({ ...exp, selector: parts.join(' ') });
      }
      return out;
    };

    // Phase 1: full node (PromptStudioOutput -> WeiLinPromptToString, both editors).
    let frame = await openEditor(false);
    // Contract source of truth: the deployed adapter publishes its selector map.
    const selectorMap = await frame.locator('body').evaluate(() => window.__studioSuiteWeilinSelectors || null);
    if (!selectorMap) {
      failures.push('STALE FRONTEND: window.__studioSuiteWeilinSelectors is undefined inside .studio-suite-prompt-iframe — the deployed frontend predates weilinAdapter.js, so it cannot be checked against the current contract. Deploy the current frontend, then re-run this probe.');
    }
    const expectations = resolveList(EXPECTATIONS);
    const afterFill = resolveList(AFTER_FILL_EXPECTATIONS);
    report.push({ phase: 'contract', id: 'selector-map', ok: Boolean(selectorMap), keys: selectorMap ? Object.keys(selectorMap).length : 0, note: 'selector map published by the deployed weilinAdapter.js' });
    try {
      await frame.locator('body[data-studio-suite-ready="1"]').waitFor({ timeout: 45000 });
      report.push({ phase: 'full', id: 'studio-suite-ready', ok: true, note: 'our own ready signal fired' });
    } catch {
      failures.push('full studio-suite-ready: body[data-studio-suite-ready="1"] never set — Studio Suite frontend failed to initialize (this is OUR signal; check js console, not weilin)');
    }
    // Our own ready signal fires when OUR modules initialized, which is earlier
    // than physton's first tag-cloud render. Wait (bounded) for the exact markup
    // asserted below — this gates the MEASUREMENT, never our readiness.
    if (selectorMap?.tagsList) {
      let cloudWait = 'ready';
      try {
        // state:'attached' — some roots live in the hidden negative editor, so
        // waiting for VISIBILITY here would always time out. Counts and
        // visibility are judged separately from the measurement below.
        await frame.locator(selectorMap.tagsList).first().waitFor({ state: 'attached', timeout: 30000 });
      } catch { cloudWait = 'timeout'; }
      report.push({ phase: 'full', id: 'tag-cloud-render', ok: cloudWait === 'ready', note: 'bounded wait for the physton tag cloud before measuring the full node' });
    }
    judge(await measure(frame, expectations), expectations, 'full');

    // Group-tab items visibility on the full node: informational. They live in
    // the negative editor container; on the full node that container is shown.
    const groupItems = report.find(r => r.phase === 'full' && r.id === 'group-tag-items');
    if (groupItems && groupItems.count > 0 && groupItems.visible === 0) {
      warnings.push('full group-tag-items: .group-tabs .tag-item exists but is 0x0 even on the full node — never use it as a readiness barrier');
    }

    if (selectorMap) {
      // Phase 2: fill the positive editor, physton re-renders the tag cloud.
      await frame.locator('#weilin_prompt_text_input').fill('1girl, blue eyes');
      try {
        await frame.locator('.prompt-tags-list .prompt-tag .prompt-tag-value').first().waitFor({ timeout: 20000 });
      } catch { /* judged below with exact counts */ }
      const fillDiag = await frame.locator('body').evaluate(() => {
        const ta = document.querySelector('#weilin_prompt_text_input');
        const lists = [...document.querySelectorAll('.prompt-tags-list')];
        return {
          textarea: ta ? ta.value : '(no #weilin_prompt_text_input)',
          phystonEditors: document.querySelectorAll('.physton-prompt').length,
          lists: lists.length,
          tagsPerList: lists.map(l => l.querySelectorAll('.prompt-tag').length),
          listChildren: lists.map(l => l.children.length),
        };
      });
      report.push({ phase: 'filled', id: 'fill-diagnostic', ...fillDiag, note: 'what the DOM actually holds right after the fill' });
      judge(await measure(frame, afterFill), afterFill, 'filled');
      const perTagTranslate = await frame.locator('body').evaluate(() => document.querySelectorAll('.prompt-tags-list .prompt-tag .translate-to-local').length);
      report.push({ phase: 'filled', id: 'translate-to-local', count: perTagTranslate, note: 'per-tag translate entries (informational; our capture degrades gracefully)' });
      if (perTagTranslate === 0) warnings.push('filled translate-to-local: no per-tag translate entries rendered — per-tag A/B capture will not trigger (may be a physton setting)');

      // Phase 3: positive-only node. The known trap: .group-tabs .tag-item must be
      // present-but-0x0 here. If that ever flips, review any code/tests that
      // assumed invisibility (and keep it unused as a barrier regardless).
      await page.evaluate(() => dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' })));
      await page.waitForTimeout(300);
      frame = await openEditor(true);
      await frame.locator('body[data-studio-suite-ready="1"]').waitFor({ timeout: 45000 }).catch(() => {
        failures.push('positive-only studio-suite-ready: ready signal missing after switching node');
      });
      const posExp = expectations.find(e => e.id === 'group-tag-items');
      if (posExp) {
        const posGroup = (await measure(frame, [posExp]))[0];
        report.push({ phase: 'positive-only', id: 'group-tag-items', selector: posGroup.selector, count: posGroup.count, visible: posGroup.visible, note: 'expected present-but-0x0 (negative editor hidden by index.html)' });
        if (posGroup.count === 0) {
          failures.push(`positive-only group-tag-items: selector "${posGroup.selector}" matched 0 elements — physton no longer renders tag cloud items for positive-only nodes; review tag-cloud dependent code`);
        } else if (posGroup.visible > 0) {
          warnings.push('positive-only group-tag-items: items are VISIBLE now (were 0x0 before) — the old trap changed; still do not use them as a readiness barrier');
        }
      }
    }

    const passed = failures.length === 0;
    if (!passed) await page.screenshot({ path: path.join(output, 'weilin-dom-probe-failure.png') }).catch(() => {});
    console.log(JSON.stringify({ passed, failures, warnings, report, pageErrors: errors, weilinCalls, consoleErrors }, null, 1));
    if (!passed) process.exitCode = 1;
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exitCode = 1; });
