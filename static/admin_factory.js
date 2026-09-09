(function(){
  'use strict';
  let state=null,editing=null,ready=false,sequence=0,receiptSequence=0,receiptBusy=false,lastReceiptAt=0;
  const groupStorageKey='factory-selected-group-v1';
  const $=id=>document.getElementById('fa-'+id);
  function node(tag,text,cls){const el=document.createElement(tag);if(text!==undefined)el.textContent=text;if(cls)el.className=cls;return el;}
  function notice(text,error=false){$('notice').textContent=text;$('notice').className='factory-notice'+(error?' factory-error':'');$('notice').hidden=!text;}
  function headers(){return typeof adminHeaders==='function'?adminHeaders(true):{'Content-Type':'application/json'};}
  async function call(path='',method='GET',body){
    const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),30000);
    try{const r=await fetch('/api/admin/factory'+path,{method,headers:headers(),body:body?JSON.stringify(body):undefined,cache:'no-store',signal:controller.signal});const data=await r.json();if(!r.ok||!data.ok)throw new Error(data.message||'操作失敗，請重新登入。');return data;}
    catch(e){if(e.name==='AbortError')throw new Error('連線逾時，請重新整理確認儲存結果。');throw e;}finally{clearTimeout(timer);}
  }
  function guard(button,operation){return async event=>{event?.preventDefault();if(button.disabled)return;button.disabled=true;try{await operation();}catch(e){notice(e.message,true);}finally{button.disabled=false;}};}
  function option(value,label){const o=node('option',label);o.value=value;return o;}
  function group(){return $('group').value;}
  function rememberedGroup(){try{return localStorage.getItem(groupStorageKey)||'';}catch(_){return '';}}
  function rememberGroup(){try{localStorage.setItem(groupStorageKey,group());}catch(_){}}
  function groupName(){return state?.groups.find(g=>g.id===group())?.name||'尚未選擇群組';}
  function syncReceiptGroup(){
    $('receipt-group').value=group();
    $('receipt-scope').textContent='查詢群組：'+groupName();
  }
  function changeGroup(value){
    $('group').value=value;rememberGroup();fillOptions();syncReceiptGroup();
    $('receipts').replaceChildren();loadStations().catch(e=>notice(e.message,true));
    loadReceipts().catch(e=>notice(e.message,true));
    loadMembers().catch(e=>notice(e.message,true));
  }
  function refreshVisibleReceipts(){
    if(!state||!group()||receiptBusy||document.visibilityState==='hidden'||Date.now()-lastReceiptAt<20000)return;
    const rect=$('receipts-section').getBoundingClientRect();
    if(rect.height>0&&rect.top<window.innerHeight&&rect.bottom>0)loadReceipts().catch(()=>{});
  }
  function init(){
    if(ready)return;ready=true;
    const root=document.getElementById('factory-admin-root');
    root.innerHTML=`<div class="factory-header"><span class="factory-eyebrow">LINE · 工廠協作</span><h1>工廠工具</h1><p>翻譯模式、設備資料、公告確認與選單數據</p></div>
<div id="fa-notice" class="factory-notice" role="status" aria-live="polite" hidden></div>
<section class="factory-card"><h2>連線與功能狀態</h2><div id="fa-health"></div><div class="factory-row"><button type="button" id="fa-refresh" class="factory-secondary">重新整理</button></div><p class="factory-hint">在 LINE 群組傳送 <strong>/factory</strong>，即可開啟掃碼與站別翻譯。掃碼、分享需先在 LINE Developers 啟用 LIFF 的 Scan QR 與分享選擇器，畫面大小設為 Full。</p></section>
<section class="factory-card"><h2>群組翻譯與互動</h2><label>群組<select id="fa-group"></select></label><form id="fa-options-form">
<label>文字翻譯模式<select id="fa-mode"><option value="all">自動翻譯所有文字</option><option value="mentioned">只有 @ 機器人時翻譯文字</option></select></label><p class="factory-hint">圖片、語音、文件維持各自的既有開關；管理指令仍可操作。</p>
<label><input type="checkbox" id="fa-edit">原文修改後補發更正翻譯</label><label><input type="checkbox" id="fa-mentions">譯文保留真正的 LINE @ 點名</label>
<p class="factory-hint">作業確認僅在群組輸入 <strong>/ack 通知內容</strong> 或 <strong>/確認 通知內容</strong> 時建立。確認按鈕、分享與工具入口統一於「快捷鍵」依群組設定。</p>
<label><input type="checkbox" id="fa-ack-reminder">自動 @ 提醒尚未回覆的人</label>
<label>提醒間隔（分鐘）<input id="fa-ack-minutes" type="number" min="1" max="10079" step="1" required></label>
<label><input type="checkbox" id="fa-ack-repeat">持續提醒，直到全員回覆或手動停止</label>
<p class="factory-hint">新通知按此間隔首次提醒；勾選持續提醒後，每輪會重新排除已回覆者，最長至通知 7 天有效期。取消勾選則提醒一次。可在下方單獨停止某筆通知；取消「自動 @ 提醒」會關閉整個群組的後續提醒。間隔設定適用於新通知。</p>
<p class="factory-hint">若 LINE 仍未提供完整名單，該輪會用 @All 提醒全體，已回覆者也會收到、可忽略；累積足夠身分後改為個別標記。</p>
<button id="fa-save-options" type="submit">儲存群組設定</button></form></section>
<section class="factory-card"><h2>已辨識的群組成員</h2><p class="factory-hint">成員發言、加入、按確認卡，或被 LINE 原生 @ 單獨標記時會自動記錄。尚未辨識者可在群組發一個字，或由您逐一 @；@All 不會提供每人的身分。</p><button id="fa-load-members" type="button" class="factory-secondary">重新讀取名單</button><div id="fa-members"></div></section>
<section class="factory-card"><h2>設備、站別與作業說明</h2><p class="factory-hint">既有設備詞庫可直接查閱；自訂資料可指定群組。作業說明請填入實際核准內容。</p><div id="fa-station-list" class="factory-table-wrap"></div>
<form id="fa-station-form"><h3 id="fa-editor-title">新增設備對照</h3><div class="factory-grid"><label>設備／站別代碼<input id="fa-code" maxlength="40" required placeholder="I5"></label><label>適用群組<select id="fa-station-group"></select></label><label>中文名稱<input id="fa-name-zh" maxlength="100" required></label><label>印尼文名稱<input id="fa-name-id" maxlength="200" required></label></div>
<label>簡稱與設備背景<textarea id="fa-context" maxlength="1200" rows="3" placeholder="說明這個代碼代表什麼，僅供翻譯辨識"></textarea></label><div class="factory-grid"><label>中文作業說明<textarea id="fa-sop-zh" maxlength="5000" rows="5"></textarea></label><label>印尼文作業說明<textarea id="fa-sop-id" maxlength="5000" rows="5"></textarea></label></div>
<label>相關表單<select id="fa-form-id"><option value="">不連結表單</option></select></label><div class="factory-row"><button id="fa-save-station" type="submit">儲存設備</button><button id="fa-reset-station" type="button" class="factory-secondary">取消編輯</button></div></form><div id="fa-qr-preview"></div></section>
<section class="factory-card" id="fa-receipts-section"><h2>作業確認紀錄</h2><label>查詢群組<select id="fa-receipt-group"></select></label><p id="fa-receipt-scope" class="factory-hint"></p><p class="factory-hint">這是同事主動回覆的紀錄，了解不代表作業完成。未回覆名單僅包含機器人已知成員。此區顯示時每 20 秒更新，也可按下方按鈕查詢。</p><button id="fa-load-receipts" type="button" class="factory-secondary">查看最新確認</button><p id="fa-receipt-status" class="factory-hint" role="status" aria-live="polite"></p><div id="fa-receipts"></div></section>
<section class="factory-card"><h2>圖文選單使用統計</h2><p class="factory-hint">LINE 以日本時間 UTC+9 統計，通常次日完成。少於 20 位點擊使用者時，官方可能不提供數據。</p><form id="fa-insight-form"><label>圖文選單 ID<input id="fa-menu" pattern="richmenu-[0-9a-f]{32}" placeholder="richmenu-…" required></label><div class="factory-grid"><label>開始日期<input type="date" id="fa-from" required></label><label>結束日期<input type="date" id="fa-to" required></label></div><label>統計方式<select id="fa-insight-mode"><option value="summary">期間彙總</option><option value="daily">每日統計</option></select></label><button id="fa-load-insight" type="submit">查詢官方統計</button></form><div id="fa-insight-result"></div></section>`;
    $('group').addEventListener('change',()=>changeGroup(group()));
    $('receipt-group').addEventListener('change',()=>changeGroup($('receipt-group').value));
    $('refresh').addEventListener('click',guard($('refresh'),load));
    $('options-form').addEventListener('submit',guard($('save-options'),async()=>{
      const data=await call('','PUT',{group_id:group(),expected_version:state.settings_version,expected_ack_version:state.ack_settings_version,options:{translation_mode:$('mode').value,edit_translation:$('edit').checked,native_mentions:$('mentions').checked,ack_reminder_enabled:$('ack-reminder').checked,ack_reminder_minutes:Number($('ack-minutes').value),ack_reminder_repeat:$('ack-repeat').checked}});
      state=data;notice('群組設定已儲存。');
    }));
    $('station-form').addEventListener('submit',guard($('save-station'),async()=>{
      const row={code:$('code').value.trim(),group_id:$('station-group').value,name_zh:$('name-zh').value.trim(),name_id:$('name-id').value.trim(),context:$('context').value.trim(),sop_zh:$('sop-zh').value.trim(),sop_id:$('sop-id').value.trim(),form_id:$('form-id').value};
      const rows=(state.settings.stations||[]).filter(x=>!editing||x.code!==editing.code||x.group_id!==editing.group_id);rows.push(row);
      state=await call('','PUT',{stations:rows,expected_version:state.settings_version});resetStation();await loadStations();notice('設備資料已儲存。');
    }));
    $('reset-station').addEventListener('click',resetStation);
    $('load-receipts').addEventListener('click',guard($('load-receipts'),loadReceipts));
    $('load-members').addEventListener('click',guard($('load-members'),loadMembers));
    window.setInterval(refreshVisibleReceipts,20000);
    document.addEventListener('visibilitychange',refreshVisibleReceipts);
    window.addEventListener('focus',refreshVisibleReceipts);
    $('insight-form').addEventListener('submit',guard($('load-insight'),loadInsight));
    const today=new Date(Date.now()+9*3600000),yesterday=new Date(today.getTime()-86400000),month=new Date(today.getTime()-30*86400000);
    $('from').value=month.toISOString().slice(0,10);$('to').value=yesterday.toISOString().slice(0,10);
  }
  async function load(){
    init();const seq=++sequence,selected=group()||rememberedGroup();notice('正在讀取設定…');const data=await call();if(seq!==sequence)return;
    state=data;$('group').replaceChildren();$('receipt-group').replaceChildren();$('station-group').replaceChildren(option('','所有群組'));
    data.groups.forEach(g=>{$('group').append(option(g.id,g.name));$('receipt-group').append(option(g.id,g.name));$('station-group').append(option(g.id,g.name));});
    if(data.groups.some(g=>g.id===selected))$('group').value=selected;
    rememberGroup();syncReceiptGroup();
    $('form-id').replaceChildren(option('','不連結表單'));(data.forms||[]).forEach(f=>$('form-id').append(option(f.id,f.title)));
    $('health').replaceChildren();const health=data.readiness;
    [['訊息編輯',health.edit_event_supported?'支援':'請更新 LINE SDK'],['原生點名',health.native_mentions_supported?'支援':'請更新 LINE SDK'],['LIFF 掃碼／分享',health.liff_configured?'已填 LIFF ID，需從手機驗證官方開關':'尚未設定 LIFF_ID'],['填表身分驗證',health.form_identity_configured?'已設定 LINE Login Channel ID':'尚未設定 LINE_LOGIN_CHANNEL_ID'],['互動紀錄儲存',health.persistent?'已連接持久儲存':health.storage_message||'請確認儲存設定']].forEach(([key,value])=>{$('health').append(node('p',key+'：'+value));});
    const reasons={missing_key:'未設定金鑰',quota_exhausted:'額度停用，請於 AI 頁重新測試',circuit_open:'連線暫停',eligible:'已設定，尚需實際呼叫確認',unsupported_capability:'不支援'};
    $('health').append(node('p','作業確認排程：'+(health.ack_worker_enabled===false?'未啟用，需啟用排程服務':health.ack_last_check_at?'最近檢查 '+new Date(health.ack_last_check_at*1000).toLocaleString('zh-TW',{hour12:false}):'已啟用，等待首次檢查')));
    if(health.ack_worker_error)$('health').append(node('p',health.ack_worker_error,'factory-error'));
    (health.provider?.providers||[]).forEach(p=>$('health').append(node('span',p.provider+' · '+(reasons[p.reason]||p.reason),'factory-pill')));
    fillOptions();await Promise.all([loadStations(),loadReceipts(),loadMembers()]);notice('');
  }
  function fillOptions(){
    const settings={...state.defaults,...(state.settings.groups||{})[group()],...(state.ack_settings?.groups||{})[group()]};
    $('mode').value=settings.translation_mode;$('edit').checked=settings.edit_translation;$('mentions').checked=settings.native_mentions;
    $('ack-reminder').checked=settings.ack_reminder_enabled;$('ack-minutes').value=settings.ack_reminder_minutes;
    $('ack-repeat').checked=settings.ack_reminder_repeat;
    $('save-options').disabled=!group();$('load-receipts').disabled=!group();
  }
  function resetStation(){editing=null;$('station-form').reset();$('editor-title').textContent='新增設備對照';}
  async function loadMembers(){
    const chosen=group();if(!chosen)return;
    const data=await call('/members?group_id='+encodeURIComponent(chosen));if(chosen!==group())return;
    $('members').replaceChildren(node('p','已辨識 '+data.members.length+' 位成員（含發起人）。'),node('p',data.members.map(row=>row.name).join('、')||'尚無名單'));
  }
  function editStation(row){
    const custom=(state.settings.stations||[]).find(x=>x.code===row.code&&x.group_id===group())||(state.settings.stations||[]).find(x=>x.code===row.code&&!x.group_id);editing=custom?{code:custom.code,group_id:custom.group_id}:null;
    const data=custom||{...row,group_id:group()};
    [['code','code'],['station-group','group_id'],['name-zh','name_zh'],['name-id','name_id'],['context','context'],['sop-zh','sop_zh'],['sop-id','sop_id'],['form-id','form_id']].forEach(([id,key])=>{$(id).value=data[key]||'';});
    $('editor-title').textContent=editing?'修改設備對照':'建立自訂設備對照';$('station-form').scrollIntoView({behavior:'smooth',block:'start'});
  }
  async function loadStations(){
    const chosen=group(),data=await call('/stations?group_id='+encodeURIComponent(chosen));if(chosen!==group())return;
    const table=node('table',undefined,'factory-table'),head=node('tr');['代碼／名稱','操作'].forEach(text=>head.append(node('th',text)));table.append(head);
    for(const row of data.stations){const tr=node('tr'),name=node('td');name.append(node('div',row.code+' · '+row.name_zh),node('small',row.name_id));const actions=node('td'),edit=node('button','編輯','factory-secondary'),qr=node('button','QR Code','factory-secondary');edit.type=qr.type='button';edit.addEventListener('click',()=>editStation(row));qr.addEventListener('click',guard(qr,()=>showQr(row)));actions.append(edit,qr);
      const custom=(state.settings.stations||[]).find(x=>x.code===row.code&&x.group_id===chosen)||(state.settings.stations||[]).find(x=>x.code===row.code&&!x.group_id);
      if(custom){const remove=node('button','刪除自訂','factory-danger');remove.type='button';remove.addEventListener('click',guard(remove,async()=>{if(!confirm('刪除 '+row.code+' 的自訂對照？'))return;state=await call('','PUT',{stations:state.settings.stations.filter(x=>x!==custom),expected_version:state.settings_version});await loadStations();notice('自訂對照已刪除；若有內建詞庫，將恢復內建資料。');}));actions.append(remove);}
      tr.append(name,actions);table.append(tr);
    }
    $('station-list').replaceChildren(table);
  }
  async function showQr(row){
    const url='/api/admin/factory/qr?'+new URLSearchParams({code:row.code,group_id:group()});
    const response=await fetch(url,{headers:headers(),cache:'no-store'});if(!response.ok){const d=await response.json();throw new Error(d.message||'無法產生 QR Code');}
    const blob=await response.blob(),objectUrl=URL.createObjectURL(blob),img=node('img',undefined,'factory-qr-image'),link=node('a','下載 '+row.code+' QR Code','factory-link');img.src=objectUrl;img.alt=row.code+' QR Code';link.href=objectUrl;link.download='station-'+row.code+'.png';
    const old=$('qr-preview').dataset.url;if(old)URL.revokeObjectURL(old);$('qr-preview').dataset.url=objectUrl;$('qr-preview').replaceChildren(node('h3',row.code+' · '+row.name_zh),img,link);$('qr-preview').scrollIntoView({behavior:'smooth',block:'center'});
  }
  async function loadReceipts(){
    const chosen=group(),seq=++receiptSequence;
    if(!chosen){$('receipt-status').textContent='請先選擇群組。';return;}
    receiptBusy=true;lastReceiptAt=Date.now();syncReceiptGroup();$('receipt-status').textContent='正在查詢「'+groupName()+'」…';
    const formatTime=seconds=>seconds?new Date(seconds*1000).toLocaleString('zh-TW',{hour12:false}):'—';
    try{
      const data=await call('/receipts?group_id='+encodeURIComponent(chosen));
      if(seq!==receiptSequence||chosen!==group())return;
      if(data.group_id&&data.group_id!==chosen)throw new Error('群組資料不一致，請重新查詢。');
      const opened=new Set([...$('receipts').querySelectorAll('details[open]')].map(el=>el.dataset.token));
      $('receipts').replaceChildren();
      $('receipt-status').textContent='「'+(data.group_name||groupName())+'」共 '+data.notices.length+' 則通知；更新時間：'+formatTime(data.checked_at||Date.now()/1000);
      if(!data.notices.length){$('receipts').append(node('p','「'+groupName()+'」目前沒有作業確認紀錄。請在此 LINE 群組輸入 /ack 通知內容，建立第一則確認。','factory-hint'));return;}
      for(const row of data.notices){
        const responses=Object.entries(row.responses||{}),understood=responses.filter(([,r])=>r.status==='understood'),help=responses.filter(([,r])=>r.status==='needs_help');
        const card=node('details'),title=node('summary',(row.current?'':row.expired?'［已過期］':'［原文已更新］')+(row.delivery_state==='delivered'?'':'［尚未確認送達］')+'✅ '+understood.length+'　❓ '+help.length+'　'+String(row.original||'').slice(0,80));
        card.dataset.token=row.token||'';card.open=opened.has(card.dataset.token)||data.notices.length===1;
        card.append(title,node('p','通知 #'+String(row.token||'').slice(0,6)+' · 發起人：'+(row.sender_name||'未取得姓名')+' · '+formatTime(row.created_at),'factory-hint'),node('p',row.original||'','factory-preserve'));
        if(row.translated)card.append(node('p',row.translated,'factory-preserve'));
        for(const [entries,label] of [[understood,'✅ 已了解'],[help,'❓ 需要說明']])card.append(node('p',label+'：'+(entries.map(([,r])=>r.name+'（'+formatTime(r.at)+'）').join('、')||'—')));
        const pending=row.pending_ids||Object.keys(row.expected||{}).filter(uid=>uid!==row.sender_id&&!row.responses?.[uid]);
        card.append(node('p','⏳ 已知成員未回覆：'+(pending.map(uid=>row.expected?.[uid]||'未取得姓名').join('、')||'—')));
        card.append(node('p','名單範圍：'+(row.roster_basis==='line_group_members'?'LINE 提供的群組成員':'機器人已知成員（可能不完整）')+'；不含發起人。','factory-hint'));
        const unknown=row.unknown_member_count,incomplete=unknown===null||Number(unknown)>0||(unknown===undefined&&row.roster_basis==='known_chat_members');
        if(incomplete)card.append(node('p','⚠️ 名單不完整：'+(Number(unknown)>0?'另有 '+unknown+' 人尚未取得身分。':'實際未回覆總人數尚無法確認。')+'已知 0 人不代表全員了解；需提醒時會以 @All 補提醒，已回覆者也可能收到。','factory-hint'));
        if(row.roster_checked_at)card.append(node('p','名單最近補查：'+formatTime(row.roster_checked_at),'factory-hint'));
        const reminderLabels={waiting_delivery:'等待通知送出',pending:'等待提醒',repeat_pending:'持續提醒中，等待下一輪',sending:'分批提醒中',sent:'已完成個別 @ 提醒',sent_all:'已用 @All 補提醒一次（名單不完整）',no_pending:incomplete?'舊紀錄名單不完整，未發送提醒；請重新發起通知':'無需提醒，沒有未回覆者',off:'未啟用',stopped:'此通知已手動停止提醒',cancelled:'已停止',retrying:'傳送未確認，稍後重試',failed:'傳送失敗',uncertain:'請到群組確認是否收到'};
        const next=row.next_reminder_at||row.reminder_due_at;
        card.append(node('p','自動提醒：'+(reminderLabels[row.reminder_state]||'舊通知未排程')+' · 已提醒 '+(row.reminder_count||0)+' 輪'+(row.wake_at&&next?' · 下次 '+formatTime(next):''),'factory-hint'));
        if(row.last_reminder_scope)card.append(node('p','上次方式：'+(row.last_reminder_scope==='all'?'@All（名單不完整）':'個別 @ 未回覆者'),'factory-hint'));
        if(row.current&&row.reminder_minutes&&!row.reminder_stopped_at&&row.wake_at){
          const stop=node('button','停止此通知提醒','factory-danger');stop.type='button';stop.dataset.stopToken=row.token;
          stop.addEventListener('click',guard(stop,async()=>{await call('/receipts/stop','POST',{group_id:chosen,token:row.token});await loadReceipts();notice('已停止這筆通知的後續提醒。');}));card.append(stop);
        }
        if(row.last_error)card.append(node('p',row.last_error,'factory-error'));
        $('receipts').append(card);
      }
    }catch(error){
      if(seq===receiptSequence&&chosen===group())$('receipt-status').textContent='查詢失敗：'+error.message+'；目前無法確認最新紀錄。';
      throw error;
    }finally{if(seq===receiptSequence)receiptBusy=false;}
  }
  async function loadInsight(){
    const data=await call('/insight?'+new URLSearchParams({menu_id:$('menu').value.trim(),from:$('from').value.replaceAll('-',''),to:$('to').value.replaceAll('-',''),mode:$('insight-mode').value}));
    const box=$('insight-result');box.replaceChildren(node('p',data.note,'factory-hint'));
    if(data.privacy_limited){box.append(node('p','官方未提供統計值，可能未達隱私門檻或尚未完成彙整；不是 0 次。'));return;}
    const impression=data.data.impression?.metrics;
    if(impression&&!Array.isArray(impression)){box.append(node('p','顯示次數：'+String(impression.count??'未提供')+'；顯示人數：'+String(impression.uniqueUsers??'未提供')));}
    function metricsTable(rows){const table=node('table',undefined,'factory-table'),head=node('tr');['日期／區域','次數','人數'].forEach(t=>head.append(node('th',t)));table.append(head);rows.forEach(r=>{const tr=node('tr');[r.name,r.count??'未提供',r.users??'未提供'].forEach(v=>tr.append(node('td',String(v))));table.append(tr);});box.append(table);}
    const clicks=data.data.clicks||[];if(clicks.length)metricsTable(clicks.flatMap((r,i)=>(Array.isArray(r.metrics)?r.metrics:[r.metrics||{}]).map(m=>({name:(m.date?m.date+' · ':'')+'區域 '+(i+1),count:m.count,users:m.uniqueUsers}))));
    const daily=Array.isArray(impression)?impression:[];
    if(daily.length)metricsTable(daily.map(r=>({name:r.date+' · 顯示',count:r.count,users:r.uniqueUsers})));
    if(!impression&&!clicks.length&&!daily.length)box.append(node('p','官方已回傳資料；此期間尚無可顯示的明細。'));
  }
  window.loadFactoryTools=()=>load().catch(error=>notice(error.message,true));
})();
