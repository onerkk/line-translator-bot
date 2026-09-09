const assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path');
const {JSDOM,VirtualConsole}=require(process.env.JSDOM_PATH||'jsdom');
const windows=new Set();
const turn=()=>new Promise(resolve=>setImmediate(resolve));
async function until(fn,label){for(let i=0;i<150;i++){if(fn())return;await new Promise(r=>setTimeout(r,10));}throw new Error('Timeout: '+label);}
function deferred(){let resolve;const promise=new Promise(r=>resolve=r);return {promise,resolve};}
const station={code:'I5',name_zh:'研磨機',name_id:'Mesin grinding',context:'',sop_zh:'確認設備',sop_id:'Periksa mesin'};
const session={ok:true,group_name:'A 班',liff_id:'test',options:{station_tools:true,sharing:true},stations:[station]};
function open(kind,fetcher){
  const vc=new VirtualConsole(),errors=[];vc.on('jsdomError',e=>errors.push(e.message));
  const html=kind==='factory'?fs.readFileSync(path.join(__dirname,'../templates/line_factory.html'),'utf8'):'<main id="app"></main>';
  const dom=new JSDOM(html,{url:'http://localhost/liff/settings?session=test&id=f1',runScripts:'outside-only',virtualConsole:vc});
  const w=dom.window,d=w.document;windows.add(w);
  w.AbortController=AbortController;w.AbortSignal=AbortSignal;w.fetch=fetcher;w.LIFF_ID='test';
  w.liff={init:async()=>{},isLoggedIn:()=>true,getAccessToken:()=> 'fake-token'};w.alert=()=>{};
  w.eval(fs.readFileSync(path.join(__dirname,'../static/'+(kind==='factory'?'line_factory.js':'liff_forms.js')),'utf8'));
  return {w,d,errors};
}
const response=data=>({ok:true,json:async()=>data});
(async()=>{
  const late=deferred();
  const closing=open('factory',()=>late.promise);
  closing.w.close();late.resolve(response(session));await turn();await turn();assert.deepEqual(closing.errors,[]);
  const translating=deferred();let translationStarted=false;
  const member=open('factory',url=>{
    if(url.endsWith('/session'))return Promise.resolve(response(session));
    if(url.endsWith('/station'))return Promise.resolve(response({ok:true,station}));
    translationStarted=true;return translating.promise;
  });
  const {d,w}=member;
  await until(()=>d.querySelector('#factory-group').textContent==='A 班','member ready');
  d.querySelector('#factory-code').value='I5';d.querySelector('#factory-lookup button').click();
  await until(()=>!d.querySelector('#factory-station-detail').hidden,'station chosen');
  const input=d.querySelector('#factory-input');input.value='請停機';d.querySelector('#factory-translate').click();
  await until(()=>translationStarted,'translation started');
  input.value='可以開機';input.dispatchEvent(new w.Event('input',{bubbles:true}));
  translating.resolve(response({ok:true,original:'請停機',translated:'Hentikan mesin.'}));
  await until(()=>!d.querySelector('#factory-translate').disabled,'translation settled');
  assert(d.querySelector('#factory-station-output').hidden,'old translation shown for edited source');
  assert(d.querySelector('#factory-station-share-row').hidden,'old translation can be shared after edit');
  assert.deepEqual(member.errors,[]);
  const restored=deferred();let calls=0,signal;
  const resumed=open('factory',(url,options)=>{
    calls++;if(calls===1){signal=options.signal;return restored.promise;}
    return Promise.resolve(response(session));
  });
  resumed.w.dispatchEvent(new resumed.w.Event('pagehide'));assert(signal.aborted);
  resumed.w.dispatchEvent(new resumed.w.Event('pageshow'));
  await until(()=>resumed.d.querySelector('#factory-group').textContent==='A 班','restored member page');
  restored.resolve(response({...session,group_name:'Stale group'}));await turn();await turn();
  assert.equal(resumed.d.querySelector('#factory-group').textContent,'A 班');assert.deepEqual(resumed.errors,[]);
  const formResponse=deferred();let formRequested=false;
  const form=open('form',()=>{formRequested=true;return formResponse.promise;});
  await until(()=>formRequested,'form request');
  form.w.close();formResponse.resolve(response({form:{id:'f1',fields:[]}}));
  await turn();await turn();assert.deepEqual(form.errors,[]);
  console.log('PASS member lifecycle: closed pages, aborted navigation, restored state, edited input never displays or shares stale translation');
})().catch(e=>{console.error(e);process.exitCode=1;}).finally(()=>{for(const w of windows)w.close();});
