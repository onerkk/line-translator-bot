const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const {JSDOM,VirtualConsole}=require(process.env.JSDOM_PATH||'jsdom');
const source=fs.readFileSync(path.join(__dirname,'../static/admin_factory.js'),'utf8');
const windows=new Set();
const turn=()=>new Promise(resolve=>setImmediate(resolve));
async function until(fn,label){for(let i=0;i<100;i++){if(fn())return;await new Promise(resolve=>setTimeout(resolve,10));}throw new Error('Timeout: '+label);}
function deferred(){let resolve,reject;const promise=new Promise((yes,no)=>{resolve=yes;reject=no;});return {promise,resolve,reject};}
function open(intercept=()=>{}){
 const errors=[],requests=[],vc=new VirtualConsole();vc.on('jsdomError',error=>errors.push(error.message));
 const dom=new JSDOM('<main id="factory-admin-root"></main>',{url:'http://localhost/preview-admin',runScripts:'outside-only',virtualConsole:vc});
 const w=dom.window,d=w.document;windows.add(w);
 const data={ok:true,groups:[{id:'group-a',name:'A 班'}],defaults:{translation_mode:'all',edit_translation:true,native_mentions:true,ack_reminder_enabled:false,ack_reminder_minutes:30,ack_reminder_repeat:true},settings:{groups:{},stations:[]},ack_settings:{groups:{}},forms:[],readiness:{},settings_version:1,ack_settings_version:1};
 const payload=url=>url.includes('/stations?')?{ok:true,stations:[]}:url.includes('/receipts?')?{ok:true,group_id:'group-a',notices:[]}:url.includes('/members?')?{ok:true,members:[{name:'Adi'}]}:data;
 w.AbortController=AbortController;w.AbortSignal=AbortSignal;
 w.fetch=async(url,options)=>{requests.push({url:String(url),options});return intercept(String(url),options)||{ok:true,json:async()=>payload(String(url))};};
 w.HTMLElement.prototype.scrollIntoView=()=>{};
 w.eval(source);
 return {dom,w,d,errors,requests,load:w.loadFactoryTools()};
}
(async()=>{
 // The receipt panel can finish while another branch of load() is still
 // parsing its response. Closing at this point used to reproduce CI #96.
 const late=deferred();let started=false;
 const page=open(url=>{if(url.includes('/members?'))return {ok:true,json:()=>{started=true;return late.promise;}};});
 await until(()=>started&&page.d.querySelector('#fa-receipt-status').textContent.includes('共 0 則'),'partial page before close');
 page.w.close();late.resolve({ok:true,members:[{name:'Adi'}]});
 await page.load;await turn();assert.deepEqual(page.errors,[]);

 const rejected=deferred();let requested=false;
 const closing=open(()=>{requested=true;return rejected.promise;});
 await until(()=>requested,'pending request before close');
 closing.w.close();rejected.reject(new Error('network disconnected after leaving'));
 await closing.load;await turn();assert.deepEqual(closing.errors,[]);

 // A cached page can be restored before an old response settles. Its old
 // request must stay cancelled even though this document is active again.
 const stale=deferred();let staleSignal,held=false;
 const restored=open((url,options)=>{
  if(url.includes('/members?')&&!held){held=true;staleSignal=options.signal;return {ok:true,json:()=>stale.promise};}
 });
 await until(()=>held,'member request before pagehide');
 assert.equal(restored.d.querySelector('#factory-admin-root').getAttribute('aria-busy'),'true');
 restored.w.dispatchEvent(new restored.w.Event('pagehide'));
 assert.equal(staleSignal.aborted,true);
 const count=restored.requests.length;restored.w.dispatchEvent(new restored.w.Event('focus'));
 await turn();assert.equal(restored.requests.length,count);
 restored.w.dispatchEvent(new restored.w.Event('pageshow'));
 await until(()=>restored.d.querySelector('#factory-admin-root').getAttribute('aria-busy')==='false','restored page ready');
 assert(restored.d.querySelector('#fa-members').textContent.includes('Adi'));
 stale.resolve({ok:true,members:[{name:'Outdated member'}]});await restored.load;await turn();
 assert(!restored.d.body.textContent.includes('Outdated member'));
 assert(!restored.d.querySelector('#fa-notice').classList.contains('factory-error'));
 assert.deepEqual(restored.errors,[]);

 // Cancellation on navigation must not suppress errors on a live page.
 const failed=open(url=>url.includes('/members?')?{ok:false,json:async()=>({ok:false,message:'名單讀取失敗，請重試。'})}:undefined);
 await failed.load;
 assert.equal(failed.d.querySelector('#fa-notice').textContent,'名單讀取失敗，請重試。');
 assert(failed.d.querySelector('#fa-notice').classList.contains('factory-error'));
 assert.equal(failed.d.querySelector('#factory-admin-root').getAttribute('aria-busy'),'false');
 const retry=failed.d.querySelector('#fa-load-members');retry.click();
 await until(()=>!retry.disabled,'failed member request releases button');
 assert.equal(failed.d.querySelector('#fa-notice').textContent,'名單讀取失敗，請重試。');
 assert.deepEqual(failed.errors,[]);

 const timeout=open(()=>{const error=new Error('request timeout');error.name='AbortError';return Promise.reject(error);});
 await timeout.load;assert(timeout.d.querySelector('#fa-notice').textContent.includes('連線逾時'));
 assert.deepEqual(timeout.errors,[]);
 console.log('PASS lifecycle: late response/rejection after close, pending-load state, navigation abort, restored page ignores stale response, live errors and timeouts stay visible');
})().catch(error=>{console.error(error);process.exitCode=1;}).finally(()=>{for(const w of windows)w.close();});
