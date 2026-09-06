(function () {
  'use strict';
  var groups = [], rows = [], editing = null, requestId = null, busy = false, ready = false;
  var selected = new Set(), nextOffset = null, timer = null, loadSequence = 0;
  var labels = {pending:'等待提醒', sending:'派送中', retrying:'等待重試', sent:'LINE 已接受',
    failed:'派送失敗', uncertain:'需要確認收件', cancelled:'已取消'};
  function el(id) { return document.getElementById('reminder-' + id); }
  function notice(message, error) {
    el('notice').textContent = message;
    el('notice').className = 'reminder-notice ' + (error ? 'reminder-error' : 'reminder-success');
    el('notice').hidden = !message;
  }
  function setBusy(value) {
    busy = value;
    el('form').querySelectorAll('input,select,textarea,button').forEach(function (input) { input.disabled=value; });
    el('save').disabled = value || !ready;
  }
  async function call(path, method, body) {
    var headers = {'Content-Type':'application/json'};
    var key = window._ADMIN_KEY || (typeof KEY !== 'undefined' ? KEY : '');
    if (key) headers['X-Admin-Key'] = key;
    if (window._MANAGER_ID) headers['X-Manager-Id'] = window._MANAGER_ID;
    if (window._MANAGER_TOKEN) headers['X-Manager-Token'] = window._MANAGER_TOKEN;
    var options = {method:method || 'GET', headers:headers, cache:'no-store'};
    if (body) options.body = JSON.stringify(body);
    var response, controller = new AbortController();
    options.signal = controller.signal;
    var timeout = setTimeout(function () { controller.abort(); },20000);
    try { response = await fetch('/api/admin/reminders' + path, options); }
    catch (_) { throw new Error('連線中斷，請重新整理清單確認是否已儲存，再重試。'); }
    finally { clearTimeout(timeout); }
    var data;
    try { data = await response.json(); }
    catch (_) { throw new Error('伺服器未回傳確認結果，請重新整理清單後再操作。'); }
    if (!response.ok || !data.ok) throw new Error(data.message || '操作失敗，請重新登入後再試。');
    return data;
  }
  function uuid() {
    if (crypto.randomUUID) return crypto.randomUUID();
    var bytes = new Uint8Array(16); crypto.getRandomValues(bytes);
    bytes[6] = (bytes[6] & 15) | 64; bytes[8] = (bytes[8] & 63) | 128;
    var h = Array.from(bytes, function (b) { return b.toString(16).padStart(2, '0'); }).join('');
    return h.slice(0,8)+'-'+h.slice(8,12)+'-'+h.slice(12,16)+'-'+h.slice(16,20)+'-'+h.slice(20);
  }
  function localNow() { return new Date(Date.now() + 8 * 3600000).toISOString().slice(0,16); }
  function group() { return groups.find(function (g) { return g.id === el('group').value; }); }
  function selectedNames() {
    var g = group();
    return (g ? g.members : []).filter(function (u) { return selected.has(u.user_id); })
      .map(function (u) { return '@' + u.name; });
  }
  function preview() {
    var mention = el('mode').value === 'all' ? '@所有人' : el('mode').value === 'users' ? selectedNames().join(' ') : '';
    el('preview').textContent = '⏰ 自訂提醒\n' + (el('date').value || '日期') + ' ' +
      (el('time').value || '時間') + '（台灣時間）\n' + (mention ? mention + '\n' : '') +
      (el('content').value.trim() || '你的提醒內容會顯示在這裡');
    el('count').textContent = el('content').value.length + ' / 1500 字元';
    el('selected-count').textContent = '已選 ' + selected.size + ' / 20 位';
  }
  function renderMembers() {
    var g = group(), box = el('members'); box.replaceChildren();
    var query = el('search').value.trim().toLocaleLowerCase();
    var members = (g ? g.members : []).filter(function (u) { return u.name.toLocaleLowerCase().includes(query); });
    if (!members.length) { box.textContent = '沒有符合的成員。未列出者請先在群組發言，再重新整理。'; }
    members.forEach(function (u) {
      var label = document.createElement('label'); label.className = 'reminder-member';
      var input = document.createElement('input'); input.type = 'checkbox'; input.checked = selected.has(u.user_id);
      input.addEventListener('change', function () {
        if (input.checked && selected.size >= 20) { input.checked = false; notice('一次最多指定 20 位成員。', true); return; }
        if (input.checked) selected.add(u.user_id); else selected.delete(u.user_id);
        preview();
      });
      var name = document.createElement('span'); name.textContent = u.name;
      label.append(input, name); box.append(label);
    });
    preview();
  }
  function reset() {
    editing = null; requestId = uuid(); selected.clear();
    el('form').reset();
    el('date').value = new Date(Date.now() + 32 * 3600000).toISOString().slice(0,10);
    el('date').min = localNow().slice(0,10);
    el('time').value = '08:00';
    el('form-title').textContent = '新增提醒'; el('save').textContent = '儲存提醒';
    el('stop-edit').hidden = true; el('member-wrap').hidden = true;
    renderMembers(); notice('', false);
  }
  function edit(row) {
    editing = row; selected = new Set(row.user_ids);
    el('group').value = row.group_id;
    el('date').value = row.local_time.slice(0,10); el('time').value = row.local_time.slice(11);
    el('mode').value = row.mention_mode; el('content').value = row.content; el('search').value = '';
    el('member-wrap').hidden = row.mention_mode !== 'users';
    el('form-title').textContent = '修改提醒'; el('save').textContent = '儲存修改';
    el('stop-edit').hidden = false; renderMembers(); notice('', false);
    el('form').scrollIntoView({behavior:'smooth', block:'start'});
  }
  function renderRows() {
    var box = el('list'); box.replaceChildren();
    if (!rows.length) { var empty = document.createElement('p'); empty.className='empty'; empty.textContent='尚無提醒。'; box.append(empty); }
    rows.forEach(function (row) {
      var card = document.createElement('article'); card.className = 'card';
      var head = document.createElement('div'); head.className = 'reminder-item-head';
      var title = document.createElement('strong'); title.textContent = row.local_time.replace('T',' ') + '（台灣）';
      var status = document.createElement('span'); status.className = 'reminder-status reminder-status-' + row.status;
      status.textContent = labels[row.status] || row.status; head.append(title, status);
      var meta = document.createElement('div'); meta.className = 'reminder-meta';
      var names = row.mention_mode === 'all' ? '@所有人' : row.mention_mode === 'users' ? row.user_ids.map(function (uid) { return '@'+(row.user_names[uid] || '指定成員'); }).join(' ') : '不標註';
      meta.textContent = row.group_name + ' · ' + names;
      var content = document.createElement('div'); content.className = 'reminder-body'; content.textContent = row.content;
      card.append(head, meta, content);
      if (row.last_error) { var error = document.createElement('p'); error.className='reminder-meta reminder-error'; error.textContent=row.last_error; card.append(error); }
      if (row.sent_at) { var sent = document.createElement('div'); sent.className='reminder-meta'; sent.textContent='LINE 接受時間：'+new Date(row.sent_at*1000).toLocaleString('zh-TW',{timeZone:'Asia/Taipei',hour12:false}); card.append(sent); }
      var actions = document.createElement('div'); actions.className = 'reminder-actions';
      if (row.status === 'pending' && row.attempts === 0) {
        var modify = document.createElement('button'); modify.type='button'; modify.className='btn btn-primary btn-sm'; modify.textContent='修改';
        modify.addEventListener('click', function () { if (!busy) edit(row); }); actions.append(modify);
      }
      if (row.status === 'pending' || row.status === 'retrying') {
        var cancel = document.createElement('button'); cancel.type='button'; cancel.className='btn btn-red btn-sm'; cancel.textContent='取消提醒';
        cancel.addEventListener('click', async function () {
          if (busy) return;
          if (!window.confirm('取消這筆提醒？'+(row.attempts ? '\n已送達的訊息不會收回，請另確認群組。' : ''))) return;
          cancel.disabled = true; setBusy(true);
          try { await call('/'+row.id+'/cancel','POST',{revision:row.revision}); if (editing && editing.id===row.id) reset(); await load(false); notice('提醒已取消。',false); }
          catch (e) { notice(e.message,true); cancel.disabled=false; }
          finally { setBusy(false); }
        }); actions.append(cancel);
      }
      card.append(actions); box.append(card);
    });
    el('more').hidden = nextOffset === null;
  }
  async function load(append) {
    var sequence = ++loadSequence;
    try {
      var data = await call(append && nextOffset !== null ? '?offset='+nextOffset : '');
      if (sequence !== loadSequence) return;
      groups = data.groups;
      var old = el('group').value;
      el('group').replaceChildren(new Option('請選擇群組',''));
      groups.forEach(function (g) { el('group').add(new Option(g.name || g.id,g.id)); });
      el('group').value = old;
      if (!old && groups.length === 1) el('group').value = groups[0].id;
      if (old && !group()) selected.clear();
      ready = data.status.ready;
      el('save').disabled = busy || !ready;
      el('health').textContent = ready ? (data.status.storage === 'upstash' ? '提醒已連接雲端儲存。' : '提醒使用本機資料庫，請確認主機有持久磁碟。') : data.status.message;
      if (!data.status.worker_enabled && !data.status.cron_configured) el('health').textContent += '\n排程執行未啟用，請檢查主機設定。';
      if (data.status.last_error) el('health').textContent += '\n'+data.status.last_error;
      el('health').className = 'reminder-notice' + (ready ? '' : ' reminder-error');
      rows = append ? rows.concat(data.reminders.filter(function (r) { return !rows.some(function (oldRow) { return oldRow.id===r.id; }); })) : data.reminders;
      nextOffset = data.next_offset; renderRows(); renderMembers();
    } catch (e) { notice(e.message,true); }
    if (timer) clearTimeout(timer);
    timer = setTimeout(function () {
      var panel=document.getElementById('panel-reminders');
      if (panel.classList.contains('active') && !document.hidden && !busy) load(false);
    },30000);
  }
  window.loadReminders = function () { return load(false); };
  document.addEventListener('DOMContentLoaded', function () {
    if (!el('form')) return;
    el('group').addEventListener('change',function () { selected.clear(); el('search').value=''; renderMembers(); });
    el('mode').addEventListener('change',function () { el('member-wrap').hidden=el('mode').value!=='users'; preview(); });
    el('search').addEventListener('input',renderMembers);
    ['date','time','content'].forEach(function (id) { el(id).addEventListener('input',preview); });
    el('stop-edit').addEventListener('click',reset);
    el('reset').addEventListener('click',reset);
    el('refresh').addEventListener('click',function () { load(false); });
    el('more').addEventListener('click',function () { load(true); });
    el('form').addEventListener('submit',async function (event) {
      event.preventDefault(); if (busy || !ready) return;
      var data = {group_id:el('group').value, local_time:el('date').value+'T'+el('time').value,
        content:el('content').value.trim(), mention_mode:el('mode').value,
        user_ids:el('mode').value==='users' ? Array.from(selected) : []};
      if (data.mention_mode==='users' && !data.user_ids.length) { notice('請至少勾選 1 位成員。',true); return; }
      setBusy(true);
      var wasEdit=!!editing;
      try {
        if (editing) { data.revision=editing.revision; await call('/'+editing.id,'PUT',data); }
        else { data.request_id=requestId; await call('','POST',data); }
        reset(); await load(false); notice(wasEdit ? '提醒已更新。' : '提醒已儲存，到設定時間後派送。',false);
      } catch (e) { notice(e.message,true); }
      finally { setBusy(false); }
    });
    reset();
  });
}());
