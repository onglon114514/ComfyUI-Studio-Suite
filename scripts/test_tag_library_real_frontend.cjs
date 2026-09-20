// Real read-only library HTTP service, current ComfyUI, independent browser.
const {chromium}=require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const root=path.resolve(__dirname,'..'),out=process.env.STUDIO_SUITE_TEST_OUTPUT || require('node:os').tmpdir();
const libraryBase=process.env.STUDIO_SUITE_LIBRARY_API || process.env.COMFY_URL || 'http://127.0.0.1:8188';
(async()=>{
 const browser=await chromium.launch({headless:true,...(process.env.CHROME_PATH?{executablePath:process.env.CHROME_PATH}:{})});
 const page=await browser.newPage({viewport:{width:1440,height:1000},locale:'zh-CN'});
 const errors=[];let calls=0,llm=0;
 page.on('pageerror',e=>errors.push(e.message));
 try{
 await page.route('**/*',async route=>{
      const r=route.request(),u=new URL(r.url(), 'http://studio-suite.invalid'),p=u.pathname;
  let file;
  if(p.endsWith('/ui/index.html'))file='prompt_studio/frontend/index.html';
  if(p.endsWith('/js/prompt_studio_controller.js'))file='web/js/prompt_studio_controller.js';
  for(const name of ['tagLibrary.js','studioSuiteLlmTools.js','bilingualState.js','newbieXmlWizard.js'])if(p.endsWith('/ui/js/'+name))file='prompt_studio/frontend/js/'+name;
  if(file)return route.fulfill({body:fs.readFileSync(path.join(root,file),'utf8'),contentType:file.endsWith('.html')?'text/html':'text/javascript'});
  if(p.endsWith('/manager/component/loads'))return route.fulfill({json:{}});
  if(p.endsWith('/llm_status'))return route.fulfill({json:{queue:{busy:true},worker:{alive:false},hy_mt:{available:true,alive:false},dictionary:{ready:false}}});
  if(p.endsWith('/llm_translate')||p.endsWith('/llm_preload')){llm++;return route.fulfill({status:409,json:{error:'read_only_test'}});}
  if(p.includes('/prompt-studio/library/')&&r.method()==='GET'){
   calls++;const response=await route.fetch({url:new URL(p+u.search,libraryBase).href});
   return route.fulfill({response});
  }
  if(!['GET','HEAD'].includes(r.method()))return route.fulfill({json:{success:true,data:{}}});
  return route.continue();
 });
 await page.goto(process.env.COMFY_URL||'http://127.0.0.1:8188',{waitUntil:'domcontentloaded',timeout:90000});
 await page.waitForFunction(()=>window.LiteGraph?.registered_node_types?.PromptStudioOutput,null,{timeout:90000});
 await page.waitForTimeout(3000);
 await page.evaluate(()=>{window.testNode=LiteGraph.createNode('PromptStudioOutput');comfyAPI.app.app.graph.add(testNode);testNode.widgets.find(w=>w.type==='button'&&w.name.includes('Prompt Studio')).callback();});
 const f=page.frameLocator('.studio-suite-prompt-iframe');
 await f.locator('.group-tabs .tag-item').first().waitFor({timeout:45000});
 await f.locator('[data-action="normal-mode"]').click();
 await f.locator('#weilin_prompt_text_input').fill('original,');
 const lib=f.locator('.ss-library'),cards=f.locator('[data-part="results"] .ss-library-card');
 assert.equal(calls,0);
 await f.locator('#ss-library-open').click();await cards.nth(39).waitFor();
 assert.equal(await cards.count(),40);
 const allPageText=await f.locator('[data-part="page"]').textContent();
 assert.match(allPageText,/1 \/ [\d,]+.*[\d,]+/);
 assert.equal(await f.locator('[data-part="sort"]').inputValue(),'popular');
 await f.locator('[data-part="category-root"]').selectOption('wiki:attire');
 await f.locator('[data-part="category-branch"]').selectOption('wiki:attire:shoes_and_footwear');
 await f.locator('[data-part="category"]').selectOption('wiki:section:shoes:high_heels');
 await cards.locator('code').getByText('high_heels',{exact:true}).waitFor();
 assert.match(await f.locator('[data-part="page"]').textContent(),/共 16 条/);
 await f.locator('[data-part="category-root"]').selectOption('');
 await page.waitForFunction(expected=>document.querySelector('.studio-suite-prompt-iframe')?.contentDocument?.querySelector('[data-part="page"]')?.textContent===expected,allPageText,{timeout:15000});
 await f.locator('[data-initial="a"]').click();
 await f.locator('[data-part="message"]').getByText('浏览不会修改提示词。',{exact:false}).waitFor();
 await cards.locator('code').first().waitFor();
 await page.waitForFunction(()=>{
  const frame=document.querySelector('.studio-suite-prompt-iframe');
  const value=frame?.contentDocument?.querySelector('[data-part="results"] .ss-library-card code')?.textContent||'';
  return value.toLowerCase().startsWith('a');
 },null,{timeout:15000});
 assert((await cards.locator('code').allTextContents()).every(value=>value.toLowerCase().startsWith('a')));
 await f.locator('[data-initial=""]').click();await cards.nth(39).waitFor();
 await f.locator('[data-part="work"]').fill('arknights');
 await page.waitForFunction(()=>document.querySelector('.studio-suite-prompt-iframe')?.contentDocument?.querySelector('[data-part="page"]')?.textContent?.includes('1,188'),null,{timeout:15000});
 assert.match(await f.locator('[data-part="page"]').textContent(),/1 \/ 30.*1,188/);
 await f.locator('[data-part="work"]').fill('');
 await page.waitForFunction(expected=>document.querySelector('.studio-suite-prompt-iframe')?.contentDocument?.querySelector('[data-part="page"]')?.textContent===expected,allPageText,{timeout:15000});
 await f.locator('[data-part="kind"]').selectOption('copyright');
 await page.waitForFunction(()=>document.querySelector('.studio-suite-prompt-iframe')?.contentDocument?.querySelector('[data-part="results"] .ss-library-card small')?.textContent?.includes('作品'),null,{timeout:15000});
 assert((await cards.locator('small').first().textContent()).includes('作品'));
 await f.locator('[data-part="kind"]').selectOption('');
 await page.waitForFunction(expected=>document.querySelector('.studio-suite-prompt-iframe')?.contentDocument?.querySelector('[data-part="page"]')?.textContent===expected,allPageText,{timeout:15000});
 await f.locator('[data-part="search"]').fill('blue_eyes');
 await cards.locator('code').getByText('blue_eyes',{exact:true}).waitFor();
 const card=cards.filter({has:f.locator('code').getByText('blue_eyes',{exact:true})});
 await card.getByRole('button',{name:'详情 / 相关'}).click();
 await f.locator('[data-part="detail"] .ss-library-card').nth(19).waitFor();
 await page.screenshot({path:path.join(out,'tag-library-real.png')});
 await card.getByRole('button',{name:'插入',exact:true}).click();
 assert.match(await f.locator('#weilin_prompt_text_input').inputValue(),/original,.*blue_eyes/);
 await lib.getByRole('button',{name:'关闭词典',exact:true}).click();
 const inserted=f.locator('.prompt-tags-list .prompt-tag').filter({hasText:/blue[ _]eyes/}).first();
 await inserted.hover();
 await inserted.getByRole('button',{name:'查词',exact:true}).click();
 assert.equal((await f.locator('[data-part="search"]').inputValue()).replaceAll('_',' '),'blue eyes');
 await cards.locator('code').getByText('blue_eyes',{exact:true}).waitFor();
 await lib.getByRole('button',{name:'关闭词典',exact:true}).click();
 const before=calls;await page.waitForTimeout(1200);assert.equal(calls,before);
 assert.deepEqual(errors,[]);assert.equal(llm,0);
 console.log(JSON.stringify({passed:true,realDictionary:true,calls,llm,errors}));
 }catch(e){await page.screenshot({path:path.join(out,'tag-library-real-failure.png')}).catch(()=>{});throw e;}
 finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
