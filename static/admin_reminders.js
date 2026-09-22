(function () {
  'use strict';
  var groups = [], rows = [], editing = null, requestId = null, busy = false, ready = false;
  var selected = new Set(), nextOffset = null, timer = null, loadSequence = 0;
  var generated = null;
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
    if (!value) syncLanguage();
  }
  async function call(path, method, body, timeoutMs) {
    var headers = {'Content-Type':'application/json'};
    var key = window._ADMIN_KEY || (typeof KEY !== 'undefined' ? KEY : '');
    if (key) headers['X-Admin-Key'] = key;
    if (window._MANAGER_ID) headers['X-Manager-Id'] = window._MANAGER_ID;
    if (window._MANAGER_TOKEN) headers['X-Manager-Token'] = window._MANAGER_TOKEN;
    var options = {method:method || 'GET', headers:headers, cache:'no-store'};
    if (body) options.body = JSON.stringify(body);
    var response, controller = new AbortController();
    options.signal = controller.signal;
    var timeout = setTimeout(function () { controller.abort(); },timeoutMs || 20000);
    try { response = await fetch('/api/admin/reminders' + path, options); }
    catch (_) { throw new Error(path === '/translate' ? '翻譯連線未完成，內容已保留。請重試或自行填寫譯文。' : '連線中斷，請重新整理清單確認是否已儲存，再重試。'); }
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
  function syncLanguage() {
    var mode = el('language').value;
    el('content-wrap').hidden = mode === 'id';
    el('content-id-wrap').hidden = mode === 'zh';
    el('content').required = mode === 'zh';
    el('content-id').required = mode === 'id';
    el('translation-tools').hidden = mode !== 'bilingual';
    el('translate').disabled = busy || (!!el('content').value.trim() && !!el('content-id').value.trim());
  }
  function languageContent(row) {
    if (!row.language_mode || row.language_mode === 'original') {
      return [['original', '原文 / Teks asli', row.content]];
    }
    return [['zh','繁體中文',row.content_zh],['id','BAHASA INDONESIA',row.content_id]]
      .filter(function (part) { return (row.language_mode === 'bilingual' || row.language_mode === part[0]) && part[2]; });
  }
  async function fillTranslation() {
    var zh = el('content').value.trim(), id = el('content-id').value.trim();
    if (zh && id) return;
    if (!zh && !id) throw new Error('請先填寫中文或印尼文內容。');
    if (!group()) throw new Error('請先選擇提醒群組。');
    var source = zh ? 'zh' : 'id', target = zh ? 'id' : 'zh', content = zh || id;
    el('translation-note').textContent = '正在補齊' + (target === 'id' ? '印尼文' : '中文') + '…';
    var response;
    try {
      response = await call('/translate','POST',{group_id:el('group').value, source:source, target:target, content:content},65000);
      if (response.target !== target || typeof response.content !== 'string' || !response.content.trim() || response.content.length > 1500) {
        throw new Error('譯文未完成或過長，請重試或自行填寫。');
      }
    } catch (error) {
      el('translation-note').textContent = error.message;
      throw error;
    }
    el(target === 'id' ? 'content-id' : 'content').value = response.content;
    generated = {source:source, target:target, sourceText:content, targetText:response.content};
    el('translation-note').textContent = '雙語已補齊，可直接調整譯文並查看預覽。';
    preview();
  }
  function preview() {
    syncLanguage();
    var mention = el('mode').value === 'all' ? '@所有人' : el('mode').value === 'users' ? selectedNames().join(' ') : '';
    var box = el('preview'); box.replaceChildren();
    function node(tag, cls, text) {
      var item = document.createElement(tag); item.className = cls;
      if (text !== undefined) item.textContent = text;
      return item;
    }
    if (mention) box.append(node('div','reminder-preview-mention','⏰ 提醒 / Pengingat\n'+mention));
    var card = node('article','reminder-preview-card');
    var head = node('div','reminder-preview-head');
    var title = node('div','reminder-preview-title');
    title.append(node('strong','','提醒通知'),node('span','','PENGINGAT')); head.append(title);
    var body = node('div','reminder-preview-body');
    var mode = el('language').value;
    [['zh','繁體中文','content','填寫中文提醒內容'],['id','BAHASA INDONESIA','content-id','Isi pesan pengingat di sini']].forEach(function (part) {
      if (mode !== 'bilingual' && mode !== part[0]) return;
      var section = node('section','reminder-preview-language'); section.dataset.language = part[0];
      section.append(node('div','reminder-preview-label',part[1]));
      var value = el(part[2]).value.trim();
      var text = node('div','reminder-preview-content',value || part[3]); text.lang = part[0] === 'zh' ? 'zh-Hant' : 'id';
      if (!value) text.classList.add('reminder-preview-placeholder');
      section.append(text); body.append(section);
    });
    var dateValue = el('date').value;
    var date = dateValue ? new Date(dateValue+'T00:00:00+08:00') : null;
    var weekday = date && !Number.isNaN(date.getTime()) ? new Date(date.getTime()+8*3600000).getUTCDay() : null;
    var dayText = weekday === null ? '星期 / Hari' :
      ['週日','週一','週二','週三','週四','週五','週六'][weekday]+' / '+
      ['Minggu','Senin','Selasa','Rabu','Kamis','Jumat','Sabtu'][weekday];
    var schedule = node('div','reminder-preview-schedule');
    schedule.append(node('span','reminder-preview-date',dateValue ? dateValue.replace(/-/g,'.') : '日期 / Tanggal'),
      document.createTextNode('  '),node('span','reminder-preview-time',el('time').value || '--:--'),
      document.createTextNode(' · '),node('span','reminder-preview-weekday',dayText));
    var audience = el('mode').value === 'all' ? '全體成員 / Semua anggota' : el('mode').value === 'users' ?
      '指定 '+selected.size+' 位 / '+selected.size+' anggota terpilih' : '';
    var groupName = group() ? group().name : '群組 / Grup';
    if (groupName.length > 64) groupName = groupName.slice(0,63).replace(/[\uD800-\uDBFF]$/,'')+'…';
    var footer = node('div','reminder-preview-footer');
    footer.append(node('div','reminder-preview-meta-label','排程發送 / Jadwal kirim'),schedule,
      node('div','reminder-preview-zone','台灣時間 / Waktu Taiwan · UTC+8'),
      node('div','reminder-preview-group',groupName+(audience ? ' · '+audience : '')));
    card.append(head,body,footer); box.append(card);
    el('count').textContent = el('content').value.length + ' / 1500 字元';
    el('count-id').textContent = el('content-id').value.length + ' / 1500 字元';
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
    editing = null; requestId = uuid(); selected.clear(); generated = null;
    el('form').reset();
    el('date').value = new Date(Date.now() + 32 * 3600000).toISOString().slice(0,10);
    el('date').min = localNow().slice(0,10);
    el('time').value = '08:00';
    el('form-title').textContent = '新增提醒'; el('save').textContent = '儲存提醒';
    el('stop-edit').hidden = true; el('member-wrap').hidden = true;
    el('translation-note').textContent = '填寫一種語言即可補翻，譯文可直接修改；儲存時也會自動補齊空白的語言。';
    renderMembers(); notice('', false);
  }
  function edit(row) {
    editing = row; selected = new Set(row.user_ids); generated = null;
    el('group').value = row.group_id;
    el('date').value = row.local_time.slice(0,10); el('time').value = row.local_time.slice(11);
    el('mode').value = row.mention_mode; el('search').value = '';
    var legacy = !row.language_mode || row.language_mode === 'original';
    el('language').value = legacy ? 'bilingual' : row.language_mode;
    el('content').value = legacy ? (/[\u3400-\u9fff]/.test(row.content) ? row.content : '') : row.content_zh || '';
    el('content-id').value = legacy ? (/[\u3400-\u9fff]/.test(row.content) ? '' : row.content) : row.content_id || '';
    el('translation-note').textContent = legacy ? '這筆舊提醒只有原文，儲存時會補齊雙語；也可選擇僅發送一種語言。' : '中文與印尼文分開儲存；修改內容時請同步確認另一種語言。';
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
      card.append(head, meta);
      languageContent(row).forEach(function (part) {
        var content = document.createElement('div'); content.className = 'reminder-body';
        var language = document.createElement('div'); language.className = 'reminder-history-language'; language.textContent = part[1];
        var text = document.createElement('div'); text.textContent = part[2];
        content.append(language, text); card.append(content);
      });
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
    ['date','time'].forEach(function (id) { el(id).addEventListener('input',preview); });
    el('language').addEventListener('change',preview);
    [['zh','content'],['id','content-id']].forEach(function (part) {
      el(part[1]).addEventListener('input',function () {
        if (generated && part[0] === generated.source && el(part[1]).value.trim() !== generated.sourceText) {
          var target = el(generated.target === 'id' ? 'content-id' : 'content');
          if (target.value === generated.targetText) {
            target.value = '';
            el('translation-note').textContent = '原文已修改，儲存時會重新補翻另一種語言。';
          }
          generated = null;
        } else if (generated && part[0] === generated.target) {
          generated = null;
        } else if (el('content').value.trim() && el('content-id').value.trim()) {
          el('translation-note').textContent = '內容已修改，請同步確認另一種語言。';
        }
        preview();
      });
    });
    el('translate').addEventListener('click',async function () {
      if (busy) return;
      setBusy(true);
      try { await fillTranslation(); notice('雙語內容已補齊，可在預覽中查看。',false); }
      catch (error) { notice(error.message,true); }
      finally { setBusy(false); }
    });
    el('stop-edit').addEventListener('click',reset);
    el('reset').addEventListener('click',reset);
    el('refresh').addEventListener('click',function () { load(false); });
    el('more').addEventListener('click',function () { load(true); });
    el('form').addEventListener('submit',async function (event) {
      event.preventDefault(); if (busy || !ready) return;
      if (el('mode').value==='users' && !selected.size) { notice('請至少勾選 1 位成員。',true); return; }
      setBusy(true);
      var wasEdit=!!editing;
      try {
        if (el('language').value === 'bilingual') await fillTranslation();
        var language = el('language').value;
        var data = {group_id:el('group').value, local_time:el('date').value+'T'+el('time').value,
          language_mode:language, content_zh:language === 'id' ? '' : el('content').value.trim(),
          content_id:language === 'zh' ? '' : el('content-id').value.trim(), mention_mode:el('mode').value,
          user_ids:el('mode').value==='users' ? Array.from(selected) : []};
        if (editing) { data.revision=editing.revision; await call('/'+editing.id,'PUT',data); }
        else { data.request_id=requestId; await call('','POST',data); }
        reset(); await load(false); notice(wasEdit ? '提醒已更新。' : '提醒已儲存，到設定時間後派送。',false);
      } catch (e) { notice(e.message,true); }
      finally { setBusy(false); }
    });
    reset();
  });
}());
