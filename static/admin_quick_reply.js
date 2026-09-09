(function(){
  'use strict';
  let ready=false,state=null,dirty=false,sequence=0,previewSequence=0,previewTimer=0,busy=false,previewPage=0,pages=[];
  const $=id=>document.getElementById('qr-'+id);
  const storageKey='quick-reply-selected-group-v1';
  const clone=value=>JSON.parse(JSON.stringify(value));
  function node(tag,text){const el=document.createElement(tag);if(text!==undefined)el.textContent=text;return el;}
  function option(value,label){const el=node('option',label);el.value=value;return el;}
  function message(text,error=false){$('status').textContent=text;$('status').className=error?'qr-status qr-error':'qr-status';}
  async function call(path,method='GET',body){
    const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),30000);
    try{
      const r=await fetch('/api/admin/quick-reply/'+path,{method,headers:adminHeaders(true),cache:'no-store',signal:controller.signal,body:body?JSON.stringify(body):undefined});
      const d=await r.json();if(!r.ok||!d.ok)throw new Error(d.error||'操作失敗，請重新登入。');return d;
    }catch(e){if(e.name==='AbortError')throw new Error('連線逾時，請重新載入確認結果。');throw e;}finally{clearTimeout(timer);}
  }
  function mark(){dirty=true;message('尚未儲存，請按「儲存此設定」。');previewPage=0;previewSoon();}
  function controls(){
    ['group','save','reload','inherit','add'].forEach(id=>{$(id).disabled=busy;});
    $('inherit').hidden=!state?.group_id;$('inherit').disabled=busy||!state?.customized;
    $('editor').disabled=busy;
  }
  function init(){
    if(ready)return;ready=true;
    document.getElementById('quickreply-admin-root').innerHTML=`
<div class="qr-panel"><h2>⚡ LINE 底部快捷選單</h2><p class="qr-hint">文字、圖片翻譯與公告操作都由這裡控制。調整後會套用於接下來送出的翻譯。</p>
<label>設定範圍<select id="qr-group"><option value="">全群組預設</option></select></label><p id="qr-scope" class="qr-hint"></p>
<div id="qr-status" class="qr-status" role="status" aria-live="polite"></div>
<fieldset id="qr-editor"><label class="qr-check"><input id="qr-enabled" type="checkbox">顯示底部快捷選單</label>
<label>作業確認觸發方式<select id="qr-notice"><option value="command">輸入 /ack 或 /確認 指令才建立</option><option value="off">關閉作業確認</option></select></label><p class="factory-hint">一般翻譯不會自動附確認卡。請在群組輸入 /ack 通知內容；未回覆提醒時間於「工廠工具」設定。</p>
<p class="qr-hint">啟用指令模式後，確認卡固定保留「了解」按鈕；關閉底部選單、取消勾選或移除快捷鍵，都不會停用指令。要停用請選「關閉作業確認」，再按「儲存此設定」。未回覆提醒的開關與間隔另於「工廠工具」設定。</p>
<div class="qr-preview"><h3>選單預覽</h3><label>預覽情境<select id="qr-kind"><option value="text">文字翻譯（含工單號）</option><option value="image">圖片翻譯（含工單號）</option><option value="ack">指令作業確認卡</option></select></label><div id="qr-preview" class="qr-preview-buttons"></div><p id="qr-count" class="qr-hint"></p><p class="qr-hint">工單查詢、圖片對照與語音重播，僅在訊息具備對應內容或功能時顯示。超過 13 顆會以「更多」換頁。</p></div>
<div id="qr-list"></div><div class="qr-add-row"><label>新增功能<select id="qr-add-kind"></select></label><button type="button" id="qr-add">＋ 新增按鈕</button></div></fieldset>
<div class="qr-toolbar"><button type="button" id="qr-save" class="qr-primary">儲存此設定</button><button type="button" id="qr-reload">重新載入</button><button type="button" id="qr-inherit" hidden>恢復使用全群組預設</button></div></div>`;
    $('enabled').addEventListener('change',()=>{state.profile.enabled=$('enabled').checked;mark();});
    $('notice').addEventListener('change',()=>{state.profile.acknowledgements=$('notice').value;mark();});
    $('kind').addEventListener('change',()=>{previewPage=0;previewSoon();});
    $('group').addEventListener('change',()=>{
      const selected=$('group').value;
      if(dirty&&!confirm('尚有未儲存的變更，確定放棄並切換群組？')){$('group').value=state.group_id;return;}
      load(true,selected);
    });
    $('reload').addEventListener('click',()=>{if(!dirty||confirm('放棄未儲存的變更並重新載入？'))load(true,state?.group_id||'');});
    $('save').addEventListener('click',()=>save(false));
    $('inherit').addEventListener('click',()=>{if(confirm('此群組將改用全群組預設，確定繼續？'))save(true);});
    $('add').addEventListener('click',add);
  }
  async function load(force=false,selected){
    init();if(busy||dirty&&!force)return;
    if(selected===undefined){selected=state?.group_id;try{if(selected===undefined)selected=localStorage.getItem(storageKey)||'';}catch(_){selected='';}}
    const seq=++sequence;busy=true;controls();message('正在讀取快捷選單…');
    try{
      const data=await call('list?group_id='+encodeURIComponent(selected||''));if(seq!==sequence)return;
      if(selected&&!data.groups.some(g=>g.id===selected)){selected='';state=await call('list');}else{state=data;}
      dirty=false;$('group').replaceChildren(option('','全群組預設（未自訂的群組）'));
      state.groups.forEach(g=>$('group').append(option(g.id,g.name)));$('group').value=state.group_id;
      try{localStorage.setItem(storageKey,state.group_id);}catch(_){}
      fill();message('已載入。');
    }catch(e){message(e.message,true);if(state)$('group').value=state.group_id;}
    finally{busy=false;controls();}
  }
  function fill(){
    $('scope').textContent=state.group_id?(state.customized?'此群組使用獨立設定，其他群組不受影響。':'此群組沿用全群組預設；修改並儲存後，會成為這個群組的獨立設定。'):'修改會套用到尚未自訂的群組與私人對話。';
    $('enabled').checked=state.profile.enabled;$('notice').value=state.profile.acknowledgements;
    render();previewPage=0;previewSoon();
  }
  function field(parent,title,value,change,tag='input'){
    const label=node('label',title),input=node(tag);if(tag==='input')input.type='text';input.value=value||'';
    input.addEventListener('input',()=>{change(input.value);mark();});label.append(input);parent.append(label);return input;
  }
  function render(){
    $('list').replaceChildren();
    state.profile.items.forEach((row,i)=>{
      const card=node('section');card.className='qr-item';card.dataset.itemId=row.id;
      const head=node('div');head.className='qr-item-head';
      const label=node('label'),enabled=node('input');label.className='qr-check';enabled.type='checkbox';enabled.checked=row.enabled;
      enabled.addEventListener('change',()=>{row.enabled=enabled.checked;mark();});label.append(enabled,node('span',row.label));
      head.append(label);
      for(const [text,delta] of [['↑',-1],['↓',1]]){
        const b=node('button',text);b.type='button';b.setAttribute('aria-label',(delta<0?'上移':'下移')+row.label);b.disabled=i+delta<0||i+delta>=state.profile.items.length;
        b.addEventListener('click',()=>{const rows=state.profile.items;[rows[i],rows[i+delta]]=[rows[i+delta],rows[i]];mark();render();});head.append(b);
      }
      const del=node('button','移除');del.type='button';del.addEventListener('click',()=>{if(confirm('移除「'+row.label+'」？儲存後生效。')){state.profile.items.splice(i,1);mark();render();}});head.append(del);card.append(head);
      if(row.type==='builtin'&&row.action==='factory_ack'){
        const hint=node('p','確認卡固定保留「了解」。此勾選與顯示情境只控制底部快捷鍵；移除後，確認卡會使用預設名稱。');hint.className='qr-hint';card.append(hint);
      }
      const detail=node('details'),summary=node('summary','編輯名稱與顯示情境');detail.append(summary);
      const name=field(detail,'按鈕名稱',row.label,v=>{row.label=v;label.lastChild.textContent=v;});name.maxLength=20;
      const kinds=node('div');kinds.className='qr-contexts';
      for(const [key,text] of [['text','文字／語音／文件翻譯'],['image','圖片翻譯']]){
        const l=node('label',text),input=node('input');input.type='checkbox';input.checked=row.contexts.includes(key);
        input.addEventListener('change',()=>{row.contexts=row.contexts.filter(x=>x!==key);if(input.checked)row.contexts.push(key);mark();});l.prepend(input);kinds.append(l);
      }
      detail.append(kinds);
      if(row.type==='builtin')detail.append(node('p','功能：'+state.builtins[row.action]));
      if(row.type==='external_link')detail.append(node('p','外連：'+((state.links.find(x=>x.key===row.link_key)||{}).label||row.link_key)+'（網址內容仍於外連管理）'));
      if(row.type==='message')field(detail,'按下後送出的文字或指令',row.text,v=>row.text=v,'textarea');
      if(row.type==='clipboard')field(detail,'要複製的內容',row.clipboard_text,v=>row.clipboard_text=v,'textarea');
      if(row.type==='uri')field(detail,'HTTPS 網址',row.uri,v=>row.uri=v);
      if(row.type==='message')field(detail,'對應的群組指令開關（可留白，例如 qry）',row.cmd_check,v=>row.cmd_check=v);
      card.append(detail);$('list').append(card);
    });
    if(!state.profile.items.length)$('list').append(node('p','尚無按鈕，可從下方新增。'));
    $('add-kind').replaceChildren();
    const used=new Set(state.profile.items.filter(r=>r.type==='builtin').map(r=>r.action));
    for(const [key,label] of Object.entries(state.builtins)){if(!used.has(key))$('add-kind').append(option('builtin:'+key,label));}
    for(const row of state.links){if(!state.profile.items.some(r=>r.type==='external_link'&&r.link_key===row.key))$('add-kind').append(option('link:'+row.key,'外連：'+row.label));}
    for(const [key,label] of [['message','自訂訊息／指令'],['camera','拍照'],['camera_roll','相簿'],['location','分享位置'],['clipboard','複製文字'],['uri','開啟網址']])$('add-kind').append(option(key,label));
  }
  function add(){
    if(!state)return;
    const choice=$('add-kind').value,[kind,key]=choice.split(':');
    const row={id:'custom_'+Date.now()+'_'+Math.random().toString(36).slice(2,7),type:kind,enabled:true,contexts:['text','image'],label:'新按鈕'};
    if(kind==='builtin'){row.action=key;row.label=state.builtins[key];if(key==='overlay')row.contexts=['image'];}
    else if(kind==='link'){row.type='external_link';row.link_key=key;row.label=state.links.find(x=>x.key===key).label;}
    else if(kind==='message')row.text='/help';
    else if(kind==='clipboard')row.clipboard_text='/qry';
    else if(kind==='uri')row.uri='https://';
    state.profile.items.push(row);mark();render();const last=$('list').lastElementChild;last.querySelector('details').open=true;last.scrollIntoView?.({block:'nearest'});
  }
  function previewSoon(){clearTimeout(previewTimer);const seq=++previewSequence;previewTimer=setTimeout(()=>preview(seq),220);}
  async function preview(seq){
    if(!state)return;
    try{
      const data=await call('preview','POST',{group_id:state.group_id,profile:clone(state.profile),kind:$('kind').value});if(seq!==previewSequence)return;
      pages=data.pages;previewPage=Math.min(previewPage,Math.max(0,pages.length-1));drawPreview();
      $('count').textContent='此情境共 '+data.count+' 顆按鈕'+(pages.length>1?'，分 '+pages.length+' 頁。':'。');
    }catch(e){if(seq===previewSequence){$('preview').replaceChildren();$('count').textContent=e.message;}}
  }
  function drawPreview(){
    $('preview').replaceChildren();for(const label of pages[previewPage]||[])$('preview').append(node('span',label));
    if(pages.length>1){const next=node('button','更多/Lain '+(previewPage+1)+'/'+pages.length);next.type='button';next.addEventListener('click',()=>{previewPage=(previewPage+1)%pages.length;drawPreview();});$('preview').append(next);}
  }
  async function save(reset){
    if(busy||!state)return;busy=true;controls();message('正在儲存…');
    try{
      const result=await call(reset?'reset':'save','POST',{group_id:state.group_id,profile:clone(state.profile),version:state.version});
      state=result;dirty=false;fill();message(reset?'此群組已恢復使用全群組預設。':'已儲存；接下來的新翻譯會使用此選單。');
    }catch(e){message(e.message,true);}finally{busy=false;controls();}
  }
  window.qrLoad=()=>load();
})();
