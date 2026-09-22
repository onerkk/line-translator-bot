'use strict';
// Real DOM events and actual admin form; no browser, network or LINE connection.
const assert = require('node:assert/strict');
const fs = require('node:fs'), path = require('node:path');
const {JSDOM,VirtualConsole} = require(process.env.JSDOM_PATH || 'jsdom');
const root=path.resolve(__dirname,'..'), errors=[], requests=[], saves=[], translations=[], rows=[];
let failTranslation=false, failSave=false, translationText;
const sink=new VirtualConsole(); sink.on('jsdomError',e=>errors.push(e.message));
const dom=new JSDOM(fs.readFileSync(process.argv[2],'utf8'),{
  url:'https://example.invalid/admin',runScripts:'outside-only',virtualConsole:sink
});
const w=dom.window,d=w.document,get=name=>d.getElementById('reminder-'+name);
const emit=(name,event)=>get(name).dispatchEvent(new w.Event(event,{bubbles:true,cancelable:true}));
const input=(name,value)=>{get(name).value=value;emit(name,'input');};
const select=(name,value)=>{get(name).value=value;emit(name,'change');};
const gid='C'+'1'.repeat(32),uid='U'+'2'.repeat(32);
const zh='明天班股會議，早上0750會議室集合\n（台灣同仁就好）';
const idn='Besok ada rapat regu dan bagian. Berkumpul di ruang rapat pukul 07.50 pagi.\nKhusus rekan kerja Taiwan.';
translationText=idn;
w.AbortController=AbortController; w._ADMIN_KEY='offline';
w.HTMLElement.prototype.scrollIntoView=function(){};
w.fetch=async(url,options)=>{
  requests.push([url,options]);
  assert.equal(options.headers['X-Admin-Key'],'offline');
  const body=options.body?JSON.parse(options.body):null;
  const response=(value,status=200)=>({ok:status<400,json:async()=>value});
  if(url.endsWith('/translate')){
    translations.push(body);
    return failTranslation?response({ok:false,message:'翻譯未完成，請重試或手動填寫。'},502):
      response({ok:true,source:body.source,target:body.target,content:translationText});
  }
  if(options.method==='POST'||options.method==='PUT'){
    saves.push(body);
    if(failSave)return response({ok:false,message:'儲存未確認，請重試。'},503);
    const row={...rows[0],...body,id:body.request_id||(rows[0]||{}).id,revision:((rows[0]||{}).revision||0)+1,
      group_name:'研磨C班',user_names:{[uid]:'Irwan'},status:'pending',attempts:0,content:body.content_zh||body.content_id};
    rows.splice(0,rows.length,row);return response({ok:true,reminder:row},options.method==='POST'?201:200);
  }
  return response({ok:true,reminders:rows,next_offset:null,
    groups:[{id:gid,name:'研磨C班',members:[{user_id:uid,name:'<img src=x onerror=alert(1)>'}]}],
    status:{ready:true,storage:'upstash',worker_enabled:true,last_error:''}});
};
const style=d.createElement('style');style.textContent=fs.readFileSync(path.join(root,'static/admin_reminders.css'),'utf8');d.head.append(style);
w.eval(fs.readFileSync(path.join(root,'static/admin_reminders.js'),'utf8'));
async function settle(){
  for(let n=0;n<100;n++){await new Promise(resolve=>setImmediate(resolve));if(!get('save').disabled)return;}
  throw new Error('Form did not finish its operation');
}
const schedule=()=>{input('date','2030-09-22');input('time','18:00');};
const reset=()=>{get('reset').click();select('group',gid);};
async function save(){assert(get('form').checkValidity());emit('form','submit');await settle();}
(async()=>{
  if(d.readyState==='loading')await new Promise(resolve=>d.addEventListener('DOMContentLoaded',resolve,{once:true}));
  await w.loadReminders();
  assert.equal(get('language').value,'bilingual');
  input('date','2026-09-22');input('time','18:00');input('content',zh);
  get('translate').click();await settle();
  assert.equal(translations.length,1);assert.equal(translations[0].source,'zh');
  assert.equal(get('content-id').value,idn);
  assert.equal(d.querySelector('.reminder-preview-weekday').textContent,'週二 / Selasa');
  assert.equal(d.querySelector('[data-language=zh] .reminder-preview-content').textContent,zh);
  assert.equal(d.querySelector('[data-language=id] .reminder-preview-content').textContent,idn);
  assert(!d.querySelector('.reminder-preview-head').textContent.includes('18:00'));
  assert(d.querySelector('.reminder-preview-footer').textContent.includes('排程發送'));
  assert.equal(w.getComputedStyle(d.querySelector('.reminder-preview-content')).fontSize,'18px');
  assert.equal(w.getComputedStyle(d.querySelector('.reminder-preview-schedule')).fontSize,'11px');
  if(process.argv[3]){
    fs.mkdirSync(process.argv[3],{recursive:true});
    const styles=Array.from(d.querySelectorAll('style')).map(s=>s.outerHTML).join('\n');
    const rendered='<!doctype html><meta charset="utf-8">'+styles+
      '<style>@page{size:340px 900px;margin:20px}body{padding:0;margin:0;background:white;font-family:"Noto Sans CJK TC",sans-serif}</style>'+
      '<div id="panel-reminders">'+d.querySelector('.reminder-preview-card').outerHTML+'</div>';
    fs.writeFileSync(path.join(process.argv[3],'reminder-card.html'),rendered);
    fs.writeFileSync(path.join(process.argv[3],'reminder-editor.html'),dom.serialize());
  }
  input('content',zh+'請準時。');assert.equal(get('content-id').value,'');
  assert.equal(translations.length,1,'typing cannot call a model');
  translationText=idn+' Mohon tepat waktu.';get('translate').click();await settle();
  assert.equal(translations.length,2);
  input('content-id','Terjemahan yang diperiksa manual.');input('content',zh);
  assert.equal(get('content-id').value,'Terjemahan yang diperiksa manual.');
  const beforeSave=translations.length;
  schedule();await save();
  assert.equal(saves.at(-1).content_zh,zh);assert.equal(saves.at(-1).content_id,'Terjemahan yang diperiksa manual.');
  assert.equal(translations.length,beforeSave);
  assert(get('list').textContent.includes('BAHASA INDONESIA'));
  Array.from(get('list').querySelectorAll('button')).find(b=>b.textContent==='修改').click();
  assert.equal(get('content').value,zh);assert.equal(get('content-id').value,'Terjemahan yang diperiksa manual.');
  select('language','id');assert.equal(d.querySelectorAll('.reminder-preview-language').length,1);
  assert(get('content-wrap').hidden&&!get('content').required);
  await save();assert.equal(saves.at(-1).language_mode,'id');assert.equal(saves.at(-1).content_zh,'');
  assert.equal(translations.length,beforeSave);
  reset();schedule();input('content',zh);translationText=idn;
  await save();assert.equal(saves.at(-1).content_id,idn);assert.equal(translations.length,beforeSave+1);
  reset();schedule();input('content',zh);failTranslation=true;
  const saveCount=saves.length;
  await save();assert.equal(saves.length,saveCount);assert.equal(get('content').value,zh);assert.equal(get('content-id').value,'');
  failTranslation=false;failSave=true;await save();
  const retryId=saves.at(-1).request_id, translatedCount=translations.length;
  failSave=false;await save();assert.equal(saves.at(-1).request_id,retryId);assert.equal(translations.length,translatedCount);
  reset();select('language','zh');input('content',zh);schedule();
  await save();assert.equal(saves.at(-1).content_id,'');assert.equal(translations.length,translatedCount);
  reset();input('content-id',idn);translationText=zh;
  get('translate').click();await settle();assert.equal(translations.at(-1).source,'id');assert.equal(get('content').value,zh);
  reset();
  const literal='<img src=x onerror=alert(1)>\n{m0} 開班股會議🙂';
  input('content',literal);
  assert.equal(d.querySelector('[data-language=zh] .reminder-preview-content').textContent,literal);
  assert.equal(get('preview').querySelectorAll('img,script,iframe').length,0);
  select('mode','all');assert(d.querySelector('.reminder-preview-mention').textContent.includes('@所有人'));
  select('mode','users');const member=get('members').querySelector('input');member.checked=true;
  member.dispatchEvent(new w.Event('change',{bubbles:true}));
  assert(d.querySelector('.reminder-preview-mention').textContent.includes('@<img src=x onerror=alert(1)>'));
  assert.equal(get('preview').querySelectorAll('img').length,0);
  select('mode','none');assert.equal(d.querySelector('.reminder-preview-mention'),null);
  const count=requests.length;input('content','🙂'.repeat(750));
  assert.equal(d.querySelector('[data-language=zh] .reminder-preview-content').textContent,'🙂'.repeat(750));
  assert.equal(get('count').textContent,'1500 / 1500 字元');assert.equal(requests.length,count);
  assert.deepEqual(errors,[]);
  console.log('PASS: actual bilingual form, content-first style, preview, both directions, single languages, stale translations, save retries, literal input and native mentions');
})().catch(e=>{console.error(e);process.exitCode=1;}).finally(()=>w.close());
