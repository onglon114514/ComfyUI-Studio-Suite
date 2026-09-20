// Requires a running ComfyUI with Studio Suite and Playwright (or PLAYWRIGHT_MODULE).
// Uses a separate browser; model calls and persistence POSTs are mocked.
// Tests iframe teardown/reopen, workflow metadata, both languages, A/B fallback,
// queue protection and idle rendering. COMFY_URL/CHROME_PATH are optional.
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const fs = require('node:fs');
const assert = require('node:assert/strict');
const path = require('node:path');
const root = path.resolve(process.env.STUDIO_SUITE_SOURCE || path.join(__dirname, '..')) + path.sep;
const output = process.env.STUDIO_SUITE_TEST_OUTPUT || require('node:os').tmpdir();
const baseUrl = process.env.COMFY_URL || 'http://127.0.0.1:8188';

(async () => {
  const browser = await chromium.launch({ headless: true, ...(process.env.CHROME_PATH ? {executablePath: process.env.CHROME_PATH} : {}) });
  const page = await browser.newPage({viewport: {width: 1440, height: 1000}, locale: 'zh-CN'});
  const errors = [], requests = [], checks = [];
  let queueBusy = false, glossaryMissing = false, delay = 0;
  page.on('pageerror', e => errors.push(e.message));
  page.on('crash', () => errors.push('renderer crash'));
  await page.addInitScript(() => {
    window.unhandled = [];
    addEventListener('unhandledrejection', e => unhandled.push(String(e.reason)));
  });
  try {
    await page.route('**/*', async route => {
      const r = route.request(), url = new URL(r.url());
      const pathname = url.pathname;
      if (pathname.endsWith('/manager/component/loads')) return route.fulfill({json:{}});
      let file;
      if (pathname.endsWith('/js/prompt_studio_controller.js')) file = 'web/js/prompt_studio_controller.js';
      if (pathname.endsWith('/ui/index.html')) file = 'prompt_studio/frontend/index.html';
      for (const name of ['studioSuiteLlmTools.js', 'bilingualState.js', 'tagLibrary.js', 'newbieXmlWizard.js']) if (pathname.endsWith('/ui/js/' + name)) file = 'prompt_studio/frontend/js/' + name;
      if (file) return route.fulfill({contentType: file.endsWith('.html') ? 'text/html' : 'text/javascript', body: fs.readFileSync(root + file, 'utf8')});
      if (pathname.endsWith('/llm_status')) return route.fulfill({json:{queue:{busy:queueBusy}, worker:{alive:false}, hy_mt:{available:true,alive:true}, dictionary:{ready:false,manual_aliases:2}}});
      if (pathname.endsWith('/glossary_lookup')) {
        requests.push({type:'glossary', ...r.postDataJSON()});
        return route.fulfill({status:glossaryMissing?404:200,json:{success:!glossaryMissing,matches:glossaryMissing?[]:[{text:'1girl',local:'一个女孩'},{text:'blue eyes',local:'蓝眼睛'}]}});
      }
      if (pathname.endsWith('/llm_translate')) {
        const data = r.postDataJSON(); requests.push({type:'llm',...data});
        if (delay) await new Promise(resolve=>setTimeout(resolve,delay));
        const outputs = {'女孩拿着雨伞。':'A girl holds an umbrella.', 'A girl holds an umbrella.':'女孩拿着雨伞。', 'silver ribbon':'银色丝带', 'red hat':'红色帽子','green scarf':'绿色围巾'};
        return route.fulfill({json:{success:true, translated_text:outputs[data.text] || '对照：'+data.text, source_text:data.text,engine:'hy_mt',runtime:{hy_mt:{alive:true}}}});
      }
      if (!['GET','HEAD'].includes(r.method())) {requests.push({type:'legacy',path:pathname}); return route.fulfill({json:{success:true,data:{}}});}
      return route.continue();
    });
    await page.goto(baseUrl, {waitUntil:'domcontentloaded',timeout:90000});
    await page.waitForFunction(()=>!!window.LiteGraph?.registered_node_types?.PromptStudioPositiveOutput, null, {timeout:90000});
    await page.waitForTimeout(4000);
    await page.evaluate(()=>{
      const app = comfyAPI.app.app;
      window.testNode = LiteGraph.createNode('PromptStudioOutput'); app.graph.add(testNode);
      window.otherNode = LiteGraph.createNode('PromptStudioPositiveOutput'); app.graph.add(otherNode);
    });
    const open = async (other=false) => {
      await page.evaluate(other=>{const n=other?otherNode:testNode;n.widgets.find(w=>w.type==='button'&&w.name.includes('Prompt Studio')).callback();},other);
      const frame = page.frameLocator('.studio-suite-prompt-iframe');
      // Ready barrier: the positive editor textarea. `.group-tabs .tag-item` cannot be used:
      // the physton tag cloud renders its items only in the negative editor (component-5178),
      // which index.html hides for positive-only nodes (PromptStudioPositiveOutput ->
      // WeiLinComfyUIPromptAllInOneGreat), so those items stay 0x0 there forever.
      await frame.locator('#weilin_prompt_text_input').waitFor({timeout:45000});
      await frame.locator('[data-action="normal-mode"]').click();
      await frame.locator('[data-studio-suite-llm-translate="positive"]').waitFor();
      await page.waitForTimeout(1000);
      return frame;
    };
    const close = async ()=>{await page.evaluate(()=>dispatchEvent(new KeyboardEvent('keydown',{key:'Escape'})));await page.waitForTimeout(150);};
    let frame = await open();
    const text = ()=>frame.locator('#weilin_prompt_text_input');
    const local = ()=>frame.locator('.prompt-tags').first().locator('.studio-suite-local-language .local-language');
    await text().fill('女孩拿着雨伞。');
    await frame.locator('[data-studio-suite-llm-translate="positive"]').click();
    await assertEventually(async()=>assert.equal(await text().inputValue(),'A girl holds an umbrella.'));
    await assertEventually(async()=>assert((await local().allTextContents()).includes('女孩拿着雨伞。')));
    await assertEventually(async()=>assert((await page.evaluate(()=>testNode.properties.studio_suite_bilingual?.positive))?.length));
    await close(); frame=await open();
    await assertEventually(async()=>assert((await local().allTextContents()).includes('女孩拿着雨伞。')));
    checks.push('Chinese source survives iframe destruction/reopen');
    await close();frame=await open(true);
    assert(!(await local().allTextContents()).includes('女孩拿着雨伞。'));
    await close();frame=await open();
    checks.push('node isolation');
    await text().fill('1girl, blue eyes, silver ribbon');
    await page.waitForTimeout(700);
    const ab = frame.locator('.extend-btn-item:has(.icon-svg-translate)').first();
    await ab.click();
    await page.waitForTimeout(400);
    await assertEventually(async()=>assert((await local().allTextContents()).includes('银色丝带')));
    assert.equal((await text().inputValue()).split(',').map(s=>s.trim()).filter(Boolean).join(','),'1girl,blue eyes,silver ribbon');
    assert.equal(requests.filter(r=>r.type==='llm'&&['1girl','blue eyes'].includes(r.text)).length,0);
    checks.push('A/B dictionary hits + HY-MT fallback; output unchanged');
    let count=requests.filter(r=>r.type==='llm').length;
    await ab.click();await page.waitForTimeout(500);
    assert.equal(requests.filter(r=>r.type==='llm').length,count);
    checks.push('already annotated terms not retranslated');
    glossaryMissing=true;
    await text().fill('red hat');await page.waitForTimeout(700);await ab.click();
    await assertEventually(async()=>assert((await local().allTextContents()).includes('红色帽子')));
    assert.equal(await frame.locator('.toast-error').count(),0);
    checks.push('missing dictionary falls back without red error toast');
    glossaryMissing=false;
    await frame.locator('#weilin_prompt_text_neg_input').fill('red hat');
    await frame.locator('[data-studio-suite-reference="negative"]').click();
    await assertEventually(async()=>assert((await frame.locator('.prompt-tags').nth(1).locator('.studio-suite-local-language .local-language').allTextContents()).includes('红色帽子')));
    assert.equal((await text().inputValue()).replace(/,$/,''),'red hat');
    checks.push('negative comparison kept separate');
    await frame.locator('#studio-suite-reference-language').selectOption('en');
    await text().fill('女孩拿着雨伞。');
    await frame.locator('[data-studio-suite-reference="positive"]').click();
    await page.waitForTimeout(2300);
    await assertEventually(async()=>assert((await local().allTextContents()).includes('A girl holds an umbrella.')));
    assert.equal((await text().inputValue()).replace(/[。,]+$/,''),'女孩拿着雨伞');
    await frame.locator('#studio-suite-reference-language').selectOption('zh');
    checks.push('English comparison leaves Chinese model input unchanged');
    queueBusy=true;await page.waitForTimeout(3000);
    assert.equal(await frame.locator('[data-studio-suite-reference="positive"]').isDisabled(),true);
    count=requests.filter(r=>r.type==='llm').length;
    await ab.click({force:true});await page.waitForTimeout(400);
    assert.equal(requests.filter(r=>r.type==='llm').length,count);
    checks.push('queue guard on new and old buttons');
    queueBusy=false;await page.waitForTimeout(3000);
    delay=1000;await text().fill('green scarf');await page.waitForTimeout(500);await ab.click();
    await page.waitForTimeout(150);await text().fill('user changed input');
    await page.waitForTimeout(1400);
    assert.equal((await text().inputValue()).replace(/,$/,''),'user changed input');
    checks.push('pending comparison never overwrites edits');
    delay=0;
    await text().fill('1girl, blue eyes, silver ribbon');await page.waitForTimeout(700);
    await ab.click();await page.waitForTimeout(500);
    const runtime = await frame.locator('body').evaluate(body=>{
      const active=document.getAnimations().filter(a=>a.playState==='running').length;
      window.bilingualMutations=0;
      const observer=new MutationObserver(r=>window.bilingualMutations+=r.length);
      observer.observe(body,{subtree:true,childList:true,attributes:true,characterData:true});
      return {activeAnimations:active,dom:body.querySelectorAll('*').length};
    });
    await page.waitForTimeout(3000);
    const mutations=await frame.locator('body').evaluate(()=>window.bilingualMutations);
    assert.equal(runtime.activeAnimations,0);
    assert(mutations<300,'idle mutation loop: '+mutations);
    checks.push('no running decorative animations or idle mutation feedback loop');
    await page.screenshot({path:path.join(output, 'prompt-bilingual-pass.png')});
    await close();
    await page.evaluate(()=>{
      const saved=testNode.serialize();testNode.configure(JSON.parse(JSON.stringify(saved)));
    });
    frame=await open();
    await assertEventually(async()=>assert((await local().allTextContents()).includes('银色丝带')));
    checks.push('workflow JSON serialization preserves comparison');
    assert.deepEqual(errors,[]);
    assert.deepEqual(await page.evaluate(()=>unhandled),[]);
    console.log(JSON.stringify({
      passed:true,checks,runtime,mutations,
      requests:requests.filter(r=>r.type!=='legacy'),errors
    }));
  } catch(e) {
    console.log(JSON.stringify({errors,requests}));
    await page.screenshot({path:path.join(output, 'prompt-bilingual-failure.png')}).catch(()=>{});
    throw e;
  } finally {await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});

async function assertEventually(fn){let error;for(let i=0;i<80;i++){try{return await fn();}catch(e){error=e;await new Promise(r=>setTimeout(r,100));}}throw error;}
