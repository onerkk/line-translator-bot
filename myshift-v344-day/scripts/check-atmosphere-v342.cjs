'use strict';
// node scripts/check-atmosphere-v342.cjs  (needs playwright + chromium; pass --swiftshader for software WebGL)
// Boots the app with demo weather, walks every preview, season, screen and theme,
// and asserts the engine keeps running without page errors or runaway frame cost.
const {chromium}=require('playwright'),assert=require('node:assert/strict'),fs=require('node:fs'),http=require('node:http'),path=require('node:path');
(async()=>{
const base=path.resolve(__dirname,'..');
const server=http.createServer((req,res)=>{let name=decodeURIComponent(new URL(req.url,'http://localhost').pathname);if(name.endsWith('/'))name+='index.html';fs.readFile(path.join(base,name),(err,data)=>{if(err){res.writeHead(404);res.end();return}res.setHeader('Content-Type',({'.html':'text/html','.js':'text/javascript','.css':'text/css','.png':'image/png','.webp':'image/webp','.json':'application/json','.mp3':'audio/mpeg'})[path.extname(name)]||'application/octet-stream');res.end(data)})});
await new Promise(r=>server.listen(8768,'127.0.0.1',r));server.unref();
const browser=await chromium.launch({headless:true,args:['--no-sandbox','--disable-dev-shm-usage',...(process.argv.includes('--swiftshader')?['--use-gl=angle','--use-angle=swiftshader','--enable-unsafe-swiftshader']:[])]});
const context=await browser.newContext({timezoneId:'Asia/Taipei',viewport:{width:393,height:852},deviceScaleFactor:2,serviceWorkers:'block'});
const page=await context.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));page.on('console',m=>{if(/nature fx/.test(m.text()))errors.push(m.text())});
await page.route('**/*',async r=>{const u=new URL(r.request().url());if(u.origin!=='http://127.0.0.1:8768')return r.abort();if(u.pathname.includes('/vendor/firebase/'))return r.fulfill({contentType:'text/javascript',body:`window.firebase={initializeApp(){},auth:Object.assign(()=>({setPersistence:()=>Promise.resolve(),onAuthStateChanged(cb){setTimeout(()=>cb(null),30)},getRedirectResult:()=>Promise.resolve(null)}),{Auth:{Persistence:{LOCAL:'local'}}}),firestore:Object.assign(()=>({collection:()=>({doc:()=>({get:async()=>({exists:false}),set:async()=>{}}),where(){return this},get:async()=>({forEach(){},docs:[],empty:true})})}),{FieldValue:{serverTimestamp:()=>0}})};`});return r.continue();});
await page.addInitScript(fs.readFileSync(path.join(__dirname,'glass-contrast-audit.js'),'utf8'));
await page.addInitScript(()=>{localStorage.setItem('sb_c',JSON.stringify({rt:'4on2off',pos:0,unit:'示範單位'}));localStorage.setItem('_appearance','auto');localStorage.setItem('_userPrefs',JSON.stringify({visualFx:true}));});
await page.goto('http://127.0.0.1:8768/',{waitUntil:'networkidle'});await page.waitForTimeout(2000);
const demo=code=>({code});
await page.evaluate(({code})=>{fbAuthReady=true;S.step='cal';S.pos=0;S.rt='4on2off';S.unit='示範單位';S.instH=true;const now=new Date(),start=new Date(now);start.setMinutes(0,0,0);const hTime=Array.from({length:240},(_,i)=>{const d=new Date(start.getTime()+i*3600000);return `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}T${String(d.getHours()).padStart(2,'0')}:00`});wxData={lat:23.32,lon:120.27,temp:29,code,provider:'open-meteo',source:'示範資料',updatedAt:Date.now(),sourceTime:Date.now(),currentCloud:48,currentPrecip:0,place:{display:'臺南市 鹽水區'},days:[],hTime,hTemp:hTime.map(()=>29),hPrec:hTime.map(()=>20),hRain:hTime.map(()=>0),hCloud:hTime.map(()=>48),hCode:hTime.map(()=>code),hWind:hTime.map(()=>12),hGust:hTime.map(()=>18),hHum:hTime.map(()=>72)};_syncWeatherFx();render();},demo(2));
await page.waitForTimeout(800);
let checks=0;
// Model fallback drives the live scene and is labelled as such.
let dbg=await page.evaluate(()=>WxFx._debug());assert.equal(dbg.skyGL&&dbg.skyGL.ok,true,'WebGL sky active');checks++;assert.equal(dbg.active,'cloud');assert.equal(dbg.evidence.reason,'model-forecast');checks++;
for(const code of [0,61,65,95,45,71]){await page.evaluate(c=>{wxData.code=c;wxData.hCode=wxData.hCode.map(()=>c);_syncWeatherFx()},code);await page.waitForTimeout(150);dbg=await page.evaluate(()=>WxFx._debug());assert.equal(dbg.active,NatureMode(code));checks++}
function NatureMode(c){return c===0?'clear':c===61?'rain':c===65?'heavy':c===95?'storm':c===45?'fog':'snow'}
// Stale weather falls back to light-only scene.
await page.evaluate(()=>{wxData.updatedAt=Date.now()-5*3600000;wxData.sourceTime=wxData.updatedAt;_syncWeatherFx()});await page.waitForTimeout(100);dbg=await page.evaluate(()=>WxFx._debug());assert.equal(dbg.active,'none');checks++;
await page.evaluate(()=>{wxData.updatedAt=Date.now();wxData.sourceTime=Date.now();wxData.code=2;wxData.hCode=wxData.hCode.map(()=>2);_syncWeatherFx()});
// Every preview renders, shows the HUD, isolates the app and ends on its own; each looks different.
const skySigs=new Set();
for(const mode of await page.evaluate(()=>WxFx.previewModes())){
  await page.evaluate(m=>WxFx.preview(m),mode);await page.waitForTimeout(700);
  dbg=await page.evaluate(()=>WxFx._debug());assert.equal(dbg.active,mode);assert.equal(dbg.running,true);
  await page.locator('.nature-preview-hud').waitFor({state:'visible',timeout:4000});assert.equal(await page.locator('.nature-preview-hud').isVisible(),true);assert.equal(await page.evaluate(()=>document.querySelector('#app').inert),true);
  // v342: in rain the drops live on the GL glass plane (#nature-glass-gl); count its coverage too, right after a synchronous redraw.
  const ink=await page.evaluate(()=>{WxFx.refresh();const c=document.querySelector('#wxfx'),d=c.getContext('2d').getImageData(0,0,c.width,c.height).data;let n=0;for(let i=3;i<d.length;i+=64)if(d[i]>8)n++;const g=SkyGL&&SkyGL.glassActive&&SkyGL.glassActive()?SkyGL.glassCanvas():null;if(g){const gl=g.getContext('webgl'),px=new Uint8Array(g.width*g.height*4);gl.readPixels(0,0,g.width,g.height,gl.RGBA,gl.UNSIGNED_BYTE,px);for(let i=3;i<px.length;i+=64)if(px[i]>8)n++}return n});
  if(['drizzle','rain','heavy','storm','typhoon','hail','cold','heat','fog'].includes(mode))assert(ink>20,mode+' draws on the glass');
  // v342: the sky lives on the WebGL plane; sample it right after a synchronous redraw (buffer is not preserved across frames).
  skySigs.add(await page.evaluate(()=>{WxFx.refresh();const g=SkyGL&&SkyGL.ok?SkyGL.canvas():null;let s='';if(g){const gl=g.getContext('webgl'),px=new Uint8Array(4);for(let i=1;i<8;i++)for(let j=1;j<8;j++){gl.readPixels(g.width*i/8|0,g.height*j/8|0,1,1,gl.RGBA,gl.UNSIGNED_BYTE,px);s+=(px[0]>>4).toString(16)+(px[1]>>4).toString(16)+(px[2]>>4).toString(16)}}const c=document.querySelector('#nature-sky'),x=c.getContext('2d');for(let i=1;i<8;i++)for(let j=1;j<8;j++){const d=x.getImageData(c.width*i/8|0,c.height*j/8|0,1,1).data;s+=(d[0]>>4).toString(16)+(d[1]>>4).toString(16)+(d[2]>>4).toString(16)}return s}));
  checks++;
}
assert(skySigs.size>=12,'distinct scenes '+skySigs.size);
await page.evaluate(()=>WxFx.stopPreview());await page.waitForTimeout(100);dbg=await page.evaluate(()=>WxFx._debug());assert.equal(dbg.preview,false);assert.equal(dbg.active,'cloud');assert.equal(await page.evaluate(()=>document.querySelector('#app').inert),false);checks++;
// Seasons change plates/accents and the shell reacts.
for(const season of ['spring','summer','autumn','winter']){await page.evaluate(s=>WxFx.previewSeason(s),season);await page.waitForTimeout(400);assert.equal(await page.evaluate(()=>document.body.dataset.season),season);checks++}
await page.evaluate(()=>WxFx.stopPreview());
// Theme "auto" follows the sun at the weather location (Changhua, 18:23 in October is after dusk).
await page.evaluate(()=>{wxData.lat=24.08;wxData.lon=120.54;MyShiftAppearance.set('auto')});
const sunTheme=await page.evaluate(()=>{const sun=NatureEffects.solarPosition(Date.now(),24.08,120.54);return {dark:document.documentElement.dataset.theme==='dark',expect:sun.elevation< -3}});assert.equal(sunTheme.dark,sunTheme.expect);checks++;
// Worst-case WCAG contrast over the darkest and brightest sky behind every glass pane, every screen, both themes.
// Theme/tab switches animate panel colours; freeze transitions so the audit reads settled styles (slow software GL made this flaky).
await page.addStyleTag({content:'*,*::before,*::after{transition:none!important}'});
for(const theme of ['light','dark'])for(const tab of ['today','calendar','pay','weather','more']){
  await page.evaluate(({theme,tab})=>{MyShiftAppearance.set(theme);UI_TAB=tab;render();document.querySelectorAll('#app details').forEach(d=>d.open=true)},{theme,tab});await page.waitForTimeout(600);
  const fails=await page.evaluate(()=>__glassAudit([{r:6,g:14,b:26,a:1},{r:214,g:226,b:232,a:1}]));
  assert.deepEqual(fails.map(f=>f.sel+' '+f.k),[],theme+' '+tab+' contrast');checks++;
}
// Dock pill sits under the active item.
await page.evaluate(()=>{MyShiftAppearance.set('light');UI_TAB='weather';render()});await page.waitForTimeout(1600);
// The pill follows the active tab on the engine's 0.5 s chrome tick; allow it a moment on slow (software GL) runs.
let pill=null;for(let i=0;i<15&&!(pill!==null&&pill<3);i++){await page.waitForTimeout(400);pill=await page.evaluate(()=>{const p=document.querySelector('.dock-pill'),a=document.querySelector('.bottom-nav-item.active');if(!p||!a)return null;const pr=p.getBoundingClientRect(),ar=a.getBoundingClientRect();return Math.abs((pr.left+pr.width/2)-(ar.left+ar.width/2))})}assert(pill!==null&&pill<3,'dock pill aligned '+pill);checks++;
// Frame cost stays bounded across screens and themes; the glass shell leaves text legible.
for(const theme of ['light','dark'])for(const tab of ['today','calendar','pay','weather','more']){
  await page.evaluate(({theme,tab})=>{MyShiftAppearance.set(theme);UI_TAB=tab;render()},{theme,tab});await page.waitForTimeout(900);
  dbg=await page.evaluate(()=>WxFx._debug());assert(dbg.running,'running on '+tab);assert(dbg.frameMs<40,'frame cost '+dbg.frameMs+' on '+tab);assert(dbg.protectedRects>5,'text mask on '+tab);
  const nav=await page.locator('.bottom-nav').evaluate(e=>getComputedStyle(e).backdropFilter);assert(/blur/.test(nav));
  checks++;
}
// Reduced motion removes the foreground plane; quality switch resizes without errors.
await page.emulateMedia({reducedMotion:'reduce'});await page.waitForTimeout(200);assert.equal(await page.locator('#wxfx').evaluate(e=>getComputedStyle(e).display),'none');checks++;
await page.emulateMedia({reducedMotion:'no-preference'});
for(const q of ['subtle','balanced','rich']){await page.evaluate(v=>WxFx.setQuality(v),q);await page.waitForTimeout(300);dbg=await page.evaluate(()=>WxFx._debug());assert.equal(dbg.quality,q);assert(dbg.dpr.fx<=(q==='rich'?1.5:q==='balanced'?1.25:1)+1e-9);checks++}
assert.equal(await page.evaluate(()=>document.body.dataset.fxQuality),'rich');
dbg=await page.evaluate(()=>WxFx._debug());assert(dbg.spriteCount<80,'sprite cache bounded');assert(dbg.plateCacheSize<=5);
assert.deepEqual(errors,[]);
console.log('atmosphere v342 verified: '+checks+' checks, frames '+dbg.frames+', last frame cost '+dbg.frameMs+' ms');
await browser.close();
})().catch(e=>{console.error(e);process.exit(1)});
