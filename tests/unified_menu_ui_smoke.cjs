const assert=require('node:assert/strict');
const {JSDOM,VirtualConsole}=require(process.env.JSDOM_PATH||'jsdom');
const base=process.env.FACTORY_UI_URL||'http://127.0.0.1:8765';
async function until(fn,label){for(let i=0;i<150;i++){if(fn())return;await new Promise(r=>setTimeout(r,30));}throw new Error('Timeout: '+label);}
const read=async group=>(await fetch(base+'/api/admin/quick-reply/list?group_id='+encodeURIComponent(group||''))).json();
(async()=>{
 const errors=[],vc=new VirtualConsole();vc.on('jsdomError',e=>errors.push(e.message));
 let failSave=false;
 const dom=await JSDOM.fromURL(base+'/preview-menu',{runScripts:'dangerously',resources:'usable',virtualConsole:vc,beforeParse(w){
  w.AbortController=AbortController;w.AbortSignal=AbortSignal;w.confirm=()=>true;w.HTMLElement.prototype.scrollIntoView=()=>{};
  w.fetch=(url,options)=>{if(failSave&&String(url).endsWith('/save')){failSave=false;return Promise.resolve({ok:false,json:async()=>({error:'測試儲存失敗'})});}return fetch(new URL(url,w.location.href),options);};
 }});
 const w=dom.window,d=w.document,$=id=>d.getElementById('qr-'+id);
 const change=(el,type='change')=>el.dispatchEvent(new w.Event(type,{bubbles:true}));
 await until(()=>$('status')?.textContent==='已載入。','initial menu');
 const groups=[...$('group').options].map(x=>x.value).filter(Boolean);assert.equal(groups.length,2);
 async function scope(group){$('group').value=group;change($('group'));await until(()=>$('status').textContent==='已載入。'&&!$('group').disabled,'group load');}
 async function save(){ $('save').click();await until(()=>$('status').textContent.startsWith('已儲存；'),'save menu'); }
 await scope(groups[0]);
 const first=$('list').firstElementChild,originalId=first.dataset.itemId;
 const name=first.querySelector('input[type=text]');name.value='A 班自然';change(name,'input');
 const photo=first.querySelectorAll('.qr-contexts input')[1];photo.checked=false;change(photo);
 await save();let a=await read(groups[0]);assert.equal(a.profile.items[0].label,'A 班自然');assert.deepEqual(a.profile.items[0].contexts,['text']);
 await scope(groups[1]);assert.notEqual($('list').firstElementChild.querySelector('input[type=text]').value,'A 班自然');
 $('enabled').checked=false;change($('enabled'));await save();assert.equal((await read(groups[1])).profile.enabled,false);assert.equal((await read(groups[0])).profile.enabled,true);
 await scope(groups[0]);assert.equal($('list').firstElementChild.querySelector('input[type=text]').value,'A 班自然');
 $('list').firstElementChild.querySelectorAll('.qr-item-head button')[1].click();await save();a=await read(groups[0]);assert.equal(a.profile.items[1].id,originalId);
 [...$('list').children].find(e=>e.dataset.itemId===originalId).querySelectorAll('.qr-item-head button')[2].click();await save();
 $('reload').click();await until(()=>$('status').textContent==='已載入。','reload deleted button');assert(!(await read(groups[0])).profile.items.some(x=>x.id===originalId));
 $('inherit').click();await until(()=>$('status').textContent.includes('已恢復使用'),'inherit default');assert.equal((await read(groups[0])).customized,false);
 await scope('');$('kind').value='image';change($('kind'));await until(()=>$('count').textContent.includes('分 2 頁'),'image pagination');
 assert($('preview').querySelectorAll('span').length<=12);$('preview').querySelector('button').click();assert($('preview').querySelector('button').textContent.includes('2/2'));
 const before=(await read('')).profile.items[0].label;
 const edit=$('list').firstElementChild.querySelector('input[type=text]');edit.value='未儲存測試';change(edit,'input');failSave=true;$('save').click();
 await until(()=>$('status').textContent==='測試儲存失敗','failed-save feedback');assert.equal((await read('')).profile.items[0].label,before);assert.equal(edit.value,'未儲存測試');
 assert.deepEqual(errors,[]);dom.window.close();
 console.log('PASS unified menu: independent groups, labels, text/photo conditions, reorder, deletion after reload, inheritance, pagination, failed-save feedback');
})().catch(e=>{console.error(e);process.exitCode=1;});
