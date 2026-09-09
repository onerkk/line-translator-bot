const assert=require('node:assert/strict');
const {JSDOM,VirtualConsole}=require(process.env.JSDOM_PATH||'jsdom');
const base=process.env.FACTORY_UI_URL||'http://127.0.0.1:8765';
console.log('CHECK unified menu: ci116-matched-ack');
const windows=new Set();
async function until(fn,label){for(let i=0;i<150;i++){if(fn())return;await new Promise(r=>setTimeout(r,30));}throw new Error('Timeout: '+label);}
const read=async group=>(await fetch(base+'/api/admin/quick-reply/list?group_id='+encodeURIComponent(group||''))).json();
(async()=>{
 const errors=[],vc=new VirtualConsole();vc.on('jsdomError',e=>errors.push(e.message));
 let failSave=false;
 const dom=await JSDOM.fromURL(base+'/preview-menu',{runScripts:'dangerously',resources:'usable',virtualConsole:vc,beforeParse(w){windows.add(w);
  w.AbortController=AbortController;w.AbortSignal=AbortSignal;w.confirm=()=>true;w.HTMLElement.prototype.scrollIntoView=()=>{};
  w.fetch=(url,options)=>{if(failSave&&String(url).endsWith('/save')){failSave=false;return Promise.resolve({ok:false,json:async()=>({error:'測試儲存失敗'})});}return fetch(new URL(url,w.location.href),options);};
 }});
 const w=dom.window,d=w.document,$=id=>d.getElementById('qr-'+id);
 const change=(el,type='change')=>el.dispatchEvent(new w.Event(type,{bubbles:true}));
 await until(()=>$('status')?.textContent==='已載入。','initial menu');
 const groups=[...$('group').options].map(x=>x.value).filter(Boolean);assert.equal(groups.length,2);
 // Exercise the real preview endpoint before waiting on the UI. A mixed old
 // backend must report its actual response instead of a generic DOM timeout.
 const initial=await read(''),ack=initial.profile.items.find(r=>r.action==='factory_ack');
 assert(ack,'The fixture must expose its default acknowledgement control');
 for(const [scenario,profile,labels] of [
  ['hidden',{enabled:true,acknowledgements:'command',items:[{...ack,enabled:false,label:'API了解/Paham'}]},['API了解/Paham']],
  ['deleted',{enabled:true,acknowledgements:'command',items:[]},[initial.builtins.factory_ack]],
  ['image-only',{enabled:true,acknowledgements:'command',items:[{...ack,contexts:['image'],label:'API了解/Paham'}]},['API了解/Paham']],
  ['menu-off',{enabled:false,acknowledgements:'command',items:[]},[initial.builtins.factory_ack]],
  ['explicit-off',{enabled:true,acknowledgements:'off',items:[ack]},[]],
 ]){
  const response=await fetch(base+'/api/admin/quick-reply/preview',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({group_id:groups[0],profile,kind:'ack'})});
  const data=await response.json(),detail='Acknowledgement preview '+scenario+': HTTP '+response.status+' '+JSON.stringify(data)+'. Check line_quick_reply.py and static/admin_quick_reply.js from the same update.';
  assert(response.ok&&data.ok,detail);assert.equal(data.count,labels.length,detail);assert.deepEqual(data.pages.flat(),labels,detail);
 }
 console.log('PASS acknowledgement API: hidden/deleted/image-only controls, menu off, explicit off');
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
 await scope('');
 assert.deepEqual([...$('notice').options].map(o=>o.value),['command','off']);
 const ackActions=new Set(['factory_ack','factory_help','factory_receipts']);
 const ackLabels=(await read('')).profile.items.filter(r=>r.enabled&&ackActions.has(r.action)&&r.contexts.includes('text')).map(r=>r.label);
 assert(ackLabels.length>0);
 $('enabled').checked=false;change($('enabled'));$('kind').value='text';change($('kind'));
 await until(()=>$('count').textContent.includes('共 0 顆按鈕'),'disabled translation preview');
 $('kind').value='ack';change($('kind'));
 await until(()=>$('count').textContent.includes('共 '+ackLabels.length+' 顆按鈕'),'explicit acknowledgement preview');
 assert.deepEqual([...$('preview').querySelectorAll('span')].map(el=>el.textContent),ackLabels);
 // Reproduce the saved-command / hidden-shortcut mismatch in the real editor.
 const defaults=await read(''),ackRow=defaults.profile.items.find(r=>r.action==='factory_ack');
 const ackCard=[...$('list').children].find(el=>el.dataset.itemId===ackRow.id);
 assert(ackCard.textContent.includes('確認卡固定保留'),'static/admin_quick_reply.js is missing the acknowledgement guidance; update the file inside static/, not only the test or a root-level copy.');
 const ackName=ackCard.querySelector('input[type=text]');ackName.value='測試了解/Paham';change(ackName,'input');
 for(const toggle of $('list').querySelectorAll('.qr-item-head input[type=checkbox]')){toggle.checked=false;change(toggle);}
 await until(()=>$('count').textContent.includes('共 1 顆按鈕')&&$('preview').textContent==='測試了解/Paham','hidden acknowledgement remains answerable');
 $('notice').value='off';change($('notice'));
 await until(()=>$('count').textContent.includes('共 0 顆按鈕'),'explicit acknowledgement off');
 $('notice').value='command';change($('notice'));
 await until(()=>$('preview').textContent==='測試了解/Paham','command mode restores acknowledgement');
 ackCard.querySelectorAll('.qr-item-head button')[2].click();
 await until(()=>$('count').textContent.includes('共 1 顆按鈕')&&$('preview').textContent===defaults.builtins.factory_ack,'deleted shortcut retains card default');
 await save();
 const persisted=await read('');assert.equal(persisted.profile.acknowledgements,'command');assert.equal(persisted.profile.enabled,false);
 assert(!persisted.profile.items.some(r=>r.action==='factory_ack'));
 $('reload').click();await until(()=>$('status').textContent==='已載入。','reload command without shortcut');
 assert.equal($('notice').value,'command');
 await until(()=>$('count').textContent.includes('共 1 顆按鈕')&&$('preview').textContent===defaults.builtins.factory_ack,'reloaded acknowledgement preview');
 // Pagination gets its own fixed fixture; default menu size can change as
 // features move between translation controls and command-only cards.
 $('enabled').checked=true;change($('enabled'));$('kind').value='image';change($('kind'));
 for(const toggle of $('list').querySelectorAll('.qr-item-head input[type=checkbox]')){toggle.checked=false;change(toggle);}
 for(let i=0;i<14;i++){
  $('add-kind').value='clipboard';$('add').click();
  const text=$('list').lastElementChild.querySelector('textarea');text.value='pagination-'+i;change(text,'input');
 }
 await until(()=>$('count').textContent.includes('共 14 顆按鈕')&&$('count').textContent.includes('分 2 頁'),'image pagination');
 assert.equal($('preview').querySelectorAll('span').length,12);$('preview').querySelector('button').click();
 assert.equal($('preview').querySelectorAll('span').length,2);assert($('preview').querySelector('button').textContent.includes('2/2'));
 const before=(await read('')).profile.items[0].label;
 const edit=$('list').firstElementChild.querySelector('input[type=text]');edit.value='未儲存測試';change(edit,'input');failSave=true;$('save').click();
 await until(()=>$('status').textContent==='測試儲存失敗','failed-save feedback');assert.equal((await read('')).profile.items[0].label,before);assert.equal(edit.value,'未儲存測試');
 assert.deepEqual(errors,[]);dom.window.close();
 console.log('PASS unified menu: independent groups, labels, text/photo conditions, reorder, deletion after reload, inheritance, mandatory acknowledgement after hide/delete/save/reload, explicit off, pagination, failed-save feedback');
})().catch(e=>{console.error(e);process.exitCode=1;}).finally(()=>{for(const w of windows)w.close();});
