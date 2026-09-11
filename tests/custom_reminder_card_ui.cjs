'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {JSDOM, VirtualConsole} = require(process.env.JSDOM_PATH || 'jsdom');
const errors = [], requests = [];
const consoleSink = new VirtualConsole();
consoleSink.on('jsdomError', error => errors.push(error.message));
const dom = new JSDOM(fs.readFileSync(process.argv[2], 'utf8'), {
  url: 'https://example.invalid/admin', runScripts: 'outside-only', virtualConsole: consoleSink
});
const w = dom.window, d = w.document;
const get = name => d.getElementById('reminder-' + name);
const emit = (name, event) => get(name).dispatchEvent(new w.Event(event, {bubbles: true}));
const groupId = 'C' + '1'.repeat(32), uid = 'U' + '2'.repeat(32);
w.AbortController = AbortController;
w.fetch = async (url, options) => {
  requests.push([url, options]);
  return {ok: true, json: async () => ({ok: true, reminders: [], next_offset: null,
    groups: [{id: groupId, name: '研磨 C 班', members: [{user_id: uid, name: '<img src=x onerror=alert(1)>'}]}],
    status: {ready: true, storage: 'upstash', worker_enabled: true, last_error: ''}})};
};
w.eval(fs.readFileSync(path.join(__dirname, '../static/admin_reminders.js'), 'utf8'));
(async () => {
  if (d.readyState === 'loading') await new Promise(resolve => d.addEventListener('DOMContentLoaded', resolve, {once: true}));
  await w.loadReminders();
  const initialRequests = requests.length;
  get('date').value = '2026-09-20'; get('time').value = '08:00'; emit('date', 'input');
  assert.equal(d.querySelector('.reminder-preview-time').textContent, '08:00');
  assert.equal(d.querySelector('.reminder-preview-date span').textContent, '週日 / Minggu');
  const literal = '<img src=x onerror=alert(1)>\n{m0} 開班股會議🙂';
  get('content').value = literal; emit('content', 'input');
  assert.equal(d.querySelector('.reminder-preview-content').textContent, literal);
  assert.equal(get('preview').querySelectorAll('img,script,iframe').length, 0);
  get('mode').value = 'all'; emit('mode', 'change');
  assert(d.querySelector('.reminder-preview-mention').textContent.includes('@所有人'));
  assert.equal(d.querySelector('.reminder-preview-footer strong').textContent, '全體成員 / Semua anggota');
  get('mode').value = 'users'; emit('mode', 'change');
  const member = get('members').querySelector('input'); member.checked = true;
  member.dispatchEvent(new w.Event('change', {bubbles: true}));
  assert(d.querySelector('.reminder-preview-mention').textContent.includes('@<img src=x onerror=alert(1)>'));
  assert.equal(get('preview').querySelectorAll('img').length, 0);
  assert.equal(d.querySelector('.reminder-preview-footer strong').textContent, '指定 1 位 / 1 anggota terpilih');
  get('mode').value = 'none'; emit('mode', 'change');
  assert.equal(d.querySelector('.reminder-preview-mention'), null);
  get('content').value = '🙂'.repeat(750); emit('content', 'input');
  assert.equal(d.querySelector('.reminder-preview-content').textContent, '🙂'.repeat(750));
  assert.equal(get('count').textContent, '1500 / 1500 字元');
  assert.equal(requests.length, initialRequests, 'live preview must not call an API');
  assert.deepEqual(errors, []);
  console.log('PASS custom reminder preview: actual form, weekday, all/users/none, literal input, full content, zero preview API calls');
})().catch(error => {console.error(error); process.exitCode = 1;}).finally(() => w.close());
