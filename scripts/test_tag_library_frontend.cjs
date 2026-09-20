// Separate browser; fixture dictionary and blocked persistence/model calls.
const {chromium}=require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const root=path.resolve(__dirname,'..'),out=process.env.STUDIO_SUITE_TEST_OUTPUT || require('node:os').tmpdir();
(async()=>{
 const browser=await chromium.launch({headless:true,...(process.env.CHROME_PATH?{executablePath:process.env.CHROME_PATH}:{})});
 const page=await browser.newPage({viewport:{width:1440,height:1000},locale:'zh-CN'});
 let ready=true,slow=false,calls=0,writes=0,llm=0;
 const errors=[],checks=[];
 page.on('pageerror',e=>errors.push(e.message));
 const item=n=>({canonical:'tag_'+String(n).padStart(3,'0'),label_zh:'词条 '+n,kind:'unknown'});
 try{
 await page.route('**/*',async route=>{
  const r=route.request(),u=new URL(r.url()),p=u.pathname;
  let file;
  if(p.endsWith('/ui/index.html'))file='prompt_studio/frontend/index.html';
  if(p.endsWith('/js/prompt_studio_controller.js'))file='web/js/prompt_studio_controller.js';
  for(const name of ['tagLibrary.js','studioSuiteLlmTools.js','bilingualState.js','newbieXmlWizard.js','weilinAdapter.js'])if(p.endsWith('/ui/js/'+name))file='prompt_studio/frontend/js/'+name;
  if(file)return route.fulfill({body:fs.readFileSync(path.join(root,file),'utf8'),contentType:file.endsWith('.html')?'text/html':'text/javascript'});
  if(p.endsWith('/manager/component/loads'))return route.fulfill({json:{}});
  if(p.endsWith('/llm_status'))return route.fulfill({json:{queue:{busy:true},worker:{alive:false},hy_mt:{available:true,alive:false},dictionary:{ready:false}}});
  if(p.endsWith('/llm_translate')||p.endsWith('/llm_preload')){llm++;return route.fulfill({status:409,json:{error:'comfy_queue_busy'}});}
  if(p.includes('/prompt-studio/library/')){
   calls++;const action=p.split('/').pop();
   if(slow&&action==='search')await new Promise(resolve=>setTimeout(resolve,800));
   let data={success:true};
   if(action==='status')Object.assign(data,{ready,version:'fixture',counts:{tag_count:716365,work_count:22000}});
   if(action==='categories')Object.assign(data,{items:[
    {id:'wiki:attire',label_zh:'服装与配饰（Danbooru Wiki）',parent_id:null,count:632},
    {id:'wiki:attire:footwear',label_zh:'鞋与足部穿着',parent_id:'wiki:attire',count:90},
    {id:'wiki:shoes:high_heels',label_zh:'高跟鞋',parent_id:'wiki:attire:footwear',count:16},
    {id:'clothing',label_zh:'旧服装分类',parent_id:null,count:632}
   ],kinds:[{kind:'copyright',count:1200},{kind:'general',count:42000}]});
   if(action==='works')data.items=[{name:'arknights',character_count:1186,post_count:165743}];
   if(action==='search'){
    const q=u.searchParams.get('q'),work=u.searchParams.get('work'),kind=u.searchParams.get('kind'),initial=u.searchParams.get('initial'),category=u.searchParams.get('category');
    const current=Number(u.searchParams.get('page')||1),start=(current-1)*40;
    if(category==='wiki:shoes:high_heels')data.items=[{canonical:'stiletto_heels',label_zh:'细高跟鞋',kind:'general'}];
    else if(q)data.items=[{canonical:q,label_zh:'查询 '+q,kind:'unknown'}];
    else if(work)data.items=[{canonical:'amiya_(arknights)',label_zh:'阿米娅',kind:'character'}];
    else if(kind==='copyright')data.items=[{canonical:'arknights',label_zh:'明日方舟',kind:'copyright'}];
    else if(initial==='a')data.items=[{canonical:'a_tag',label_zh:'A 标签',kind:'general'}];
    else data.items=Array.from({length:Math.min(40,120-start)},(_,i)=>item(start+i));
    data.total_count=q||work||kind||initial?data.items.length:120;
    data.total_pages=Math.max(1,Math.ceil(data.total_count/40));data.page=current;data.page_size=40;
   }
   if(action==='detail')data.item={canonical:u.searchParams.get('canonical'),label_zh:'详情译名',aliases:['别名'],groups:[{label_zh:'服装'}],sources:[{id:'fixture'}]};
   if(action==='related')data.items=[{...item(888),count:100}];
   if(action==='quick-groups')Object.assign(data,{items:[{key:'我的分组',group:'常用'}],revision:'fixture'});
   if(action==='quick-add'){writes++;}
   return route.fulfill({json:data});
  }
  if(!['GET','HEAD'].includes(r.method()))return route.fulfill({json:{success:true,data:{}}});
  return route.continue();
 });
 await page.goto(process.env.COMFY_URL||'http://127.0.0.1:8188',{waitUntil:'domcontentloaded',timeout:90000});
 await page.waitForFunction(()=>window.LiteGraph?.registered_node_types?.PromptStudioOutput,null,{timeout:90000});
 await page.waitForTimeout(3000);
 await page.evaluate(()=>{window.testNode=LiteGraph.createNode('PromptStudioOutput');comfyAPI.app.app.graph.add(testNode);testNode.widgets.find(w=>w.type==='button'&&w.name.includes('Prompt Studio')).callback();});
 const f=page.frameLocator('.studio-suite-prompt-iframe');
 // Ready barrier: Studio Suite's own signal (body[data-studio-suite-ready="1"]),
 // never third-party DOM. `.group-tabs .tag-item` is NOT a barrier: those items
 // render only inside the negative editor and stay 0x0 for positive-only nodes.
 // waitStudioSuiteReady keeps a bounded fallback on OUR OWN DOM only.
 const readyPath=await waitStudioSuiteReady(f);
 await f.locator('[data-action="normal-mode"]').click();
 const lib=f.locator('.ss-library'),cards=f.locator('[data-part="results"] .ss-library-card'); assert.equal(calls,0);assert.equal(llm,0);
 await f.locator('#weilin_prompt_text_input').fill('original,');
 await f.locator('#ss-library-open').click();await cards.nth(39).waitFor();
 assert.equal(await cards.count(),40);assert.equal(await f.locator('#weilin_prompt_text_input').inputValue(),'original,');
 assert.match(await f.locator('[data-part="page"]').textContent(),/1 \/ 3.*120/);
 await f.locator('[data-part="next"]').click();await cards.first().getByText('tag_040',{exact:true}).waitFor();
 await f.locator('[data-part="previous"]').click();await cards.first().getByText('tag_000',{exact:true}).waitFor();
 await f.locator('[data-part="jump"]').fill('3');await lib.getByRole('button',{name:'跳转',exact:true}).click();await cards.first().getByText('tag_080',{exact:true}).waitFor();
 await f.locator('[data-part="first"]').click();await cards.first().getByText('tag_000',{exact:true}).waitFor();
 await f.locator('[data-initial="a"]').click();await cards.first().getByText('a_tag',{exact:true}).waitFor();
 await f.locator('[data-initial=""]').click();await cards.nth(39).waitFor();
 await f.locator('[data-part="work"]').fill('ark');await cards.first().getByText('amiya_(arknights)',{exact:true}).waitFor();
 await f.locator('[data-part="work"]').fill('');await cards.nth(39).waitFor();
 await f.locator('[data-part="kind"]').selectOption('copyright');await cards.first().getByText('arknights',{exact:true}).waitFor();
 await f.locator('[data-part="kind"]').selectOption('');await cards.nth(39).waitFor();
 await f.locator('[data-part="category-root"]').selectOption('wiki:attire');
 await f.locator('[data-part="category-branch"]').selectOption('wiki:attire:footwear');
 await f.locator('[data-part="category"]').selectOption('wiki:shoes:high_heels');
 await cards.first().getByText('stiletto_heels',{exact:true}).waitFor();
 await f.locator('[data-part="category-root"]').selectOption('');await cards.nth(39).waitFor();
 checks.push('bounded pages, hierarchical category, page jump, initial, type, and work filters');
 await cards.first().getByRole('button',{name:'插入',exact:true}).click();
 assert.match(await f.locator('#weilin_prompt_text_input').inputValue(),/tag_000/);
 await f.locator('[data-part="target"]').selectOption('negative');
 await cards.nth(1).getByRole('button',{name:'插入',exact:true}).click();
 assert.match(await f.locator('#weilin_prompt_text_neg_input').inputValue(),/tag_001/);
 await cards.first().getByRole('button',{name:'详情 / 相关'}).click();
 await f.locator('[data-part="detail"]').getByText('tag_888',{exact:true}).waitFor();
 assert.equal(writes,0);
 await f.locator('[data-part="detail"]').getByRole('button',{name:'加入我的快捷标签…'}).click();
 await f.locator('.ss-library-quick').waitFor();assert.equal(writes,0);
 await f.locator('.ss-library-quick').getByRole('button').click();
 await assertSoon(()=>assert.equal(writes,1));
 checks.push('explicit positive/negative insert, related detail, confirmed-only quick-add');
 slow=true;
 await f.locator('[data-part="search"]').fill('old');await page.waitForTimeout(250);
 await f.locator('[data-part="search"]').fill('new');
 await cards.first().getByText('new',{exact:true}).waitFor();await page.waitForTimeout(900);
 assert.equal(await cards.locator('code').first().textContent(),'new');slow=false;
 checks.push('cancel and sequence guard suppress stale searches');
 await f.locator('[data-part="search"]').fill('');await cards.nth(39).waitFor();
 await page.screenshot({path:path.join(out,'tag-library-desktop.png')});
 await page.setViewportSize({width:720,height:900});
 assert(await lib.evaluate(n=>n.scrollWidth<=n.clientWidth+2));
 const narrowBox=await lib.boundingBox();
 assert(narrowBox.x>=0 && narrowBox.x+narrowBox.width<=720,'outer iframe must not clip the dictionary');
 await lib.getByRole('button',{name:'关闭词典',exact:true}).scrollIntoViewIfNeeded();
 await page.screenshot({path:path.join(out,'tag-library-narrow.png')});
 await page.setViewportSize({width:1440,height:1000});
 await lib.getByRole('button',{name:'关闭词典',exact:true}).click();
 let before=calls;await page.waitForTimeout(1300);assert.equal(calls,before);assert.equal(await lib.count(),0);
 checks.push('closed panel removes DOM and makes zero library requests');
 await waitWizardReady(f);
 await f.locator('[data-action="newbie-mode"]').click();
 await f.locator('[data-step="2"]').click();
 await f.locator('[data-action="open-section"]').first().click();
 await f.locator('[data-action="open-editor"]').first().click();
 await f.locator('[data-editor-input]').fill('field text');
 await f.locator('#ss-library-open').click();await cards.nth(39).waitFor();
 assert.equal(await f.locator('[data-part="target"]').inputValue(),'xml');
 await cards.nth(2).getByRole('button',{name:'插入',exact:true}).click();
 await lib.getByRole('button',{name:'关闭词典',exact:true}).click();
 assert.match(await f.locator('[data-editor-input]').inputValue(),/field text.*tag_002/);
 await f.locator('[data-action="normal-mode"]').click();
 assert(!/tag_002/.test(await f.locator('#weilin_prompt_text_input').inputValue()));
 checks.push('XML field insert preserves original prompt bridge');
 const baseline=await settleDom(page,f);
 for(let i=0;i<50;i++){
   await f.locator('#ss-library-open').click();await cards.nth(39).waitFor();
   await lib.getByRole('button',{name:'关闭词典',exact:true}).click();
 }
 const after=await f.locator('body').evaluate(n=>n.querySelectorAll('*').length);
 assert(after<=baseline+15,`DOM leak ${baseline} -> ${after}`);
 assert.equal(llm,0);
 checks.push('50 open/close cycles bounded DOM; zero model starts while queue busy');
 ready=false;await f.locator('#ss-library-open').click();
 await lib.getByText('尚未安装词典索引。此页面不会自动下载或构建。').waitFor();
 await lib.getByRole('button',{name:'关闭词典',exact:true}).click();
 checks.push('missing index shows install guidance without auto-build');
 assert.deepEqual(errors,[]);
 console.log(JSON.stringify({passed:true,checks,calls,writes,llm,baseline,after,readyPath,errors}));
 }catch(e){await page.screenshot({path:path.join(out,'tag-library-failure.png')}).catch(()=>{});console.log(JSON.stringify({errors,calls,writes,llm}));throw e;}
 finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
async function assertSoon(fn){for(let i=0;i<100;i++){try{return fn();}catch(e){if(i===99)throw e;await new Promise(r=>setTimeout(r,50));}}}
// Own ready signal first; bounded fallback on Studio Suite's own DOM only.
async function waitStudioSuiteReady(frame){
 try{await frame.locator('body[data-studio-suite-ready="1"]').waitFor({timeout:20000});return 'signal';}
 catch(e){
  await frame.locator('#weilin_prompt_text_input').waitFor({timeout:25000});
  await frame.locator('#ss-library-open').waitFor({timeout:25000});
  return 'fallback';
 }
}
// The general ready signal fires when our frontend mounts; the XML wizard finishes its
// async init later and publishes its own signal. Everything below drives the wizard, so
// wait for that one instead of racing it.
async function waitWizardReady(frame){
 try{await frame.locator('body[data-studio-suite-wizard-ready="1"]').waitFor({timeout:20000});return 'signal';}
 catch(e){return 'timeout';}
}
// Measure a node count only once it stops changing, so async wizard rendering is never
// mistaken for a DOM leak. The leak budget itself (<= baseline+15 over 50 cycles) is unchanged.
async function settleDom(page,frame,rounds=10){
 let last=-1;
 for(let i=0;i<rounds;i++){
  const n=await frame.locator('body').evaluate(b=>b.querySelectorAll('*').length);
  if(n===last)return n;
  last=n;
  await page.waitForTimeout(300);
 }
 return last;
}
