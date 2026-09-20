// Run with Node and Playwright available (or set PLAYWRIGHT_MODULE).
// All status requests are mocked; no ComfyUI server or model is started.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');

(async () => {
  const source = fs.readFileSync(process.argv[2] || path.join(__dirname, '../web/js/task_agent_live_monitor.js'), 'utf8');
  const browser = await chromium.launch({ headless: true, ...(process.env.CHROME_PATH ? { executablePath: process.env.CHROME_PATH } : {}) });
  try {
    const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.clock.install();
    await page.addInitScript(() => {
      window.mock = { calls: 0, active: 0, maxActive: 0, aborted: 0, delay: 1800, fail: false, hidden: false };
      Object.defineProperty(document, 'hidden', { configurable: true, get: () => window.mock.hidden });
      window.fetch = (_url, options = {}) => new Promise((resolve, reject) => {
        const m = window.mock;
        m.calls++;
        m.active++;
        m.maxActive = Math.max(m.maxActive, m.active);
        let finished = false;
        const finish = () => { if (finished) return false; finished = true; m.active--; return true; };
        const abort = () => {
          if (!finish()) return;
          clearTimeout(timer);
          m.aborted++;
          reject(new DOMException('Aborted', 'AbortError'));
        };
        const timer = setTimeout(() => {
          if (!finish()) return;
          options.signal?.removeEventListener('abort', abort);
          resolve({ ok: !m.fail, status: m.fail ? 503 : 200, json: async () => ({
            active_backend: { status: { healthy: true, model_name: 'mock' } },
            task_agent: { active_task: { stream_text: 'Live tokens stay visible', task_type: 'test' } },
            latest_log: { tail: 'log ' + m.calls },
          }) });
        }, m.delay);
        options.signal?.addEventListener('abort', abort, { once: true });
        if (options.signal?.aborted) abort();
      });
    });
    await page.route('**/*', route => {
      const url = new URL(route.request().url());
      if (url.pathname === '/scripts/app.js') return route.fulfill({ contentType: 'text/javascript', body: 'export const app = { registerExtension(extension) { extension.setup(); } };' });
      if (url.pathname === '/monitor.js') return route.fulfill({ contentType: 'text/javascript', body: source });
      return route.fulfill({ contentType: 'text/html', body: '<html><body><script type="module" src="/monitor.js"></script></body></html>' });
    });
    await page.goto('http://studio-suite.test');
    await page.clock.runFor(2000);
    await page.waitForSelector('.ss-ta-monitor-bubble');
    await page.evaluate(() => {
      window.mock.calls = 0;
      document.querySelector('.ss-ta-monitor-bubble').click();
      for (let i = 0; i < 20; i++) document.querySelector('[data-action="refresh"]').click();
    });
    await page.clock.runFor(2000);
    assert.equal(await page.evaluate(() => mock.maxActive), 1, 'slow status requests must never overlap, even after repeated clicks');
    assert.equal(await page.evaluate(() => mock.calls), 1);
    assert.equal(await page.locator('[data-field="live_output"]').textContent(), 'Live tokens stay visible');

    await page.locator('[data-action="close"]').evaluate(el => el.click());
    await page.evaluate(() => {
      window.panelMutations = 0;
      new MutationObserver(records => { window.panelMutations += records.length; }).observe(document.querySelector('.ss-ta-monitor-panel'), { subtree: true, attributes: true, childList: true, characterData: true });
    });
    await page.clock.runFor(22000);
    assert.equal(await page.evaluate(() => panelMutations), 0, 'closed panel must not mutate');
    assert.ok(await page.evaluate(() => mock.calls > 1), 'bubble health still polls while panel is closed');

    await page.evaluate(() => { mock.delay = 20000; document.querySelector('.ss-ta-monitor-bubble').click(); });
    await page.clock.runFor(10020);
    assert.equal(await page.evaluate(() => mock.active), 0, 'timeout cancels hung request');
    assert.equal(await page.locator('.ss-ta-monitor-bubble').getAttribute('data-health'), 'error');
    await page.evaluate(() => { mock.delay = 100; document.querySelector('[data-action="refresh"]').click(); });
    await page.clock.runFor(120);
    assert.equal(await page.locator('.ss-ta-monitor-bubble').getAttribute('data-health'), 'healthy', 'recovers after timeout');
    await page.evaluate(() => { mock.fail = true; document.querySelector('[data-action="refresh"]').click(); });
    await page.clock.runFor(120);
    assert.match(await page.locator('[data-field="log_tail"]').textContent(), /HTTP 503/);

    await page.evaluate(() => {
      mock.fail = false;
      mock.delay = 20000;
      document.querySelector('[data-action="refresh"]').click();
      mock.hidden = true;
      document.dispatchEvent(new Event('visibilitychange'));
    });
    const callsWhenHidden = await page.evaluate(() => mock.calls);
    await page.clock.runFor(30000);
    assert.equal(await page.evaluate(() => mock.calls), callsWhenHidden, 'hidden tab stops polling');
    assert.equal(await page.evaluate(() => mock.active), 0, 'hiding tab aborts pending status request');
    await page.evaluate(() => { mock.hidden = false; mock.delay = 100; document.dispatchEvent(new Event('visibilitychange')); });
    await page.clock.runFor(120);
    assert.equal(await page.locator('.ss-ta-monitor-bubble').getAttribute('data-health'), 'healthy');
    await page.evaluate(() => window.dispatchEvent(new Event('pagehide')));
    const callsAfterHide = await page.evaluate(() => mock.calls);
    await page.clock.runFor(30000);
    assert.equal(await page.evaluate(() => mock.calls), callsAfterHide, 'pagehide stops timers');
    await page.evaluate(() => window.dispatchEvent(new Event('pageshow')));
    await page.clock.runFor(120);
    assert.equal(await page.evaluate(() => mock.calls), callsAfterHide + 1, 'back/forward restore resumes once');
    assert.deepEqual(errors, []);
    if (process.env.MONITOR_SCREENSHOT) await page.screenshot({ path: process.env.MONITOR_SCREENSHOT });
    console.log(JSON.stringify({ passed: true, checks: ['single-flight', 'stream-text', 'closed-DOM', 'timeout-recovery', 'HTTP-errors', 'hidden-abort', 'page-lifecycle'], stats: await page.evaluate(() => mock), errors }));
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
