/* v343 (stage 3: Taiwan ecology via nature-taiwan.js, all effects on the front plane) / v342 sky-gl / v341 atmosphere engine.
   Observed/modelled weather, solar geometry and season drive one scene:
   background plate (behind the app) + foreground particles (in front, text masked).
   Everything is local, offline-safe, sprite based; no per-frame gradient or filter
   allocation; adaptive frame budget so a weak phone degrades instead of hanging.
   No weather API, account data, or pay state is written by this module. */
(function(root){
  'use strict';
  const clamp=(v,a,b)=>Math.max(a,Math.min(b,v)),rand=(a,b)=>a+Math.random()*(b-a),TAU=Math.PI*2;
  const known=v=>v!==null&&v!==undefined&&v!==''&&Number.isFinite(Number(v));
  function classify(code,temp,wind){
    if(!known(code))return 'none';
    code=Number(code);
    if([96,99].includes(code))return 'hail';
    if(code===95)return 'storm';
    if([65,67,82].includes(code))return 'heavy';
    if([51,53].includes(code))return 'drizzle';
    if([55,56,57,61,63,66,80,81].includes(code))return 'rain';
    if([71,73,75,77,85,86].includes(code))return 'snow';
    if([45,48].includes(code))return 'fog';
    if(wind>=35)return 'wind';
    if(temp>=34)return 'heat';
    if(temp<=10)return 'cold';
    return code>=2?'cloud':'clear';
  }
  // NOAA fractional-year solar position (elevation + azimuth, degrees).
  function solarPosition(stamp,lat,lon){
    if(!known(lat)||!known(lon)||Math.abs(lat)>90||Math.abs(lon)>180)return null;
    const d=new Date(stamp);if(!Number.isFinite(d.getTime()))return null;
    const rad=Math.PI/180,day=(Date.UTC(d.getUTCFullYear(),d.getUTCMonth(),d.getUTCDate())-Date.UTC(d.getUTCFullYear(),0,0))/86400000;
    const minutes=d.getUTCHours()*60+d.getUTCMinutes()+d.getUTCSeconds()/60,g=TAU/365*(day-1+(minutes/60-12)/24);
    const eq=229.18*(.000075+.001868*Math.cos(g)-.032077*Math.sin(g)-.014615*Math.cos(2*g)-.040849*Math.sin(2*g));
    const dec=.006918-.399912*Math.cos(g)+.070257*Math.sin(g)-.006758*Math.cos(2*g)+.000907*Math.sin(2*g)-.002697*Math.cos(3*g)+.00148*Math.sin(3*g);
    const solarMinutes=((minutes+eq+4*Number(lon))%1440+1440)%1440,ha=(solarMinutes/4-180)*rad,phi=Number(lat)*rad;
    const sinEl=clamp(Math.sin(phi)*Math.sin(dec)+Math.cos(phi)*Math.cos(dec)*Math.cos(ha),-1,1),elevation=Math.asin(sinEl)/rad;
    const azimuth=((Math.atan2(Math.sin(ha),Math.cos(ha)*Math.sin(phi)-Math.tan(dec)*Math.cos(phi))/rad+180)%360+360)%360;
    return {elevation,azimuth,hourAngle:ha,declination:dec,solarHour:solarMinutes/60,daylight:elevation>0,twilight:elevation>-6&&elevation<=0};
  }
  // Mean synodic lunar phase (0 new, .5 full); backdrop illustration only.
  function moonPhase(stamp){const p=((stamp-947182440000)/2551442877)%1;return p<0?p+1:p}
  function visualState(code,details={}){
    const mode=classify(code,20,0);
    const cloud=known(details.cloud)?clamp(Number(details.cloud)/100,0,1):({clear:.1,cloud:code===2?.55:.88,drizzle:.7,rain:.88,heavy:.98,storm:1,fog:.98,snow:.95}[mode]??.35);
    const rain=known(details.rain)?clamp(Number(details.rain),0,100):null;
    const intensity=['drizzle','rain','heavy','storm','hail'].includes(mode)?clamp(rain===null?({drizzle:.16,rain:.32,heavy:.75,storm:.9,hail:.75}[mode]??.32):.12+Math.sqrt(rain/15),.12,1):0;
    return {cloud,intensity,rain,mode};
  }
  function actorPose(kind,index,t,W,H,wind=0){
    const bird=kind==='bird',period=bird?31+index*7:43+index*11;
    const progress=((t+index*13)%period)/period,travel=bird?progress:progress+.055*Math.sin(t*.8+index);
    const x=travel*(W+100)-50,y=H*(bird?.17+index*.13:.30+index*.16)+Math.sin(t*(bird?.35:.65)+index)*(bird?16:31);
    const fold=bird?.38+.62*Math.abs(Math.sin(t*5.2+index)):.16+.84*Math.abs(Math.sin(t*9.4+index));
    return {x,y,fold,bank:Math.sin(t*.55+index)*(bird?.10:.3),size:bird?32+index*6:30+index*9};
  }

  // ---- sprite store: every bitmap is decoded once and shrunk to a small canvas ----
  const SPRITES={
    'cloud-0':['images/fx/cloud/cloud-01.png',512],'cloud-1':['images/fx/cloud/cloud-02.png',512],'cloud-2':['images/fx/cloud/cloud-03.png',512],
    'sun-0':['images/fx/sun/sun-01.png',256],'sun-1':['images/fx/sun/sun-02.png',256],'sun-2':['images/fx/sun/sun-03.png',256],
    'bolt-0':['images/fx/lightning/bolt-01.png',512],'bolt-1':['images/fx/lightning/bolt-02.png',512],'bolt-2':['images/fx/lightning/bolt-03.png',512],'bolt-3':['images/fx/lightning/bolt-04.png',512],
    'snow-0':['images/fx/frost/snow-01.png',48],'snow-1':['images/fx/frost/snow-02.png',48],'snow-2':['images/fx/frost/snow-03.png',48],'snow-3':['images/fx/frost/snow-04.png',48],
    'blossom-0':['images/fx/blossom/blossom-01.png',64],'blossom-1':['images/fx/blossom/blossom-02.png',64],'blossom-2':['images/fx/blossom/blossom-03.png',64],'blossom-3':['images/fx/blossom/blossom-04.png',64],'blossom-4':['images/fx/blossom/blossom-05.png',64],'blossom-5':['images/fx/blossom/blossom-06.png',64],
    'maple-0':['images/fx/maple/maple-01.png',96],'maple-1':['images/fx/maple/maple-02.png',96],'maple-2':['images/fx/maple/maple-03.png',96],'maple-3':['images/fx/maple/maple-04.png',96],
    'debris-0':['images/fx/debris/leaf-01.png',80],'debris-1':['images/fx/debris/leaf-02.png',80],'debris-2':['images/fx/debris/leaf-03.png',96],
    'pleaf-0':['images/fx/pleaf/leaf-01.png',200],'pleaf-1':['images/fx/pleaf/leaf-02.png',200],'pleaf-2':['images/fx/pleaf/leaf-03.png',200],
    'flower-0':['images/fx/flower/flower-01.png',120],'flower-1':['images/fx/flower/flower-02.png',120],'flower-2':['images/fx/flower/flower-03.png',120],'flower-3':['images/fx/flower/flower-04.png',120],
    'monarch-0':['images/fx/butterfly/monarch-01.png',112],'monarch-1':['images/fx/butterfly/monarch-02.png',112],'monarch-2':['images/fx/butterfly/monarch-03.png',112],'monarch-3':['images/fx/butterfly/monarch-04.png',112],
    'purple-0':['images/fx/butterfly/purple-01.png',112],'purple-1':['images/fx/butterfly/purple-02.png',112],'purple-2':['images/fx/butterfly/purple-03.png',112],'purple-3':['images/fx/butterfly/purple-04.png',112],
    'swallowtail-0':['images/fx/butterfly/swallowtail-01.png',112],'swallowtail-1':['images/fx/butterfly/swallowtail-02.png',112],'swallowtail-2':['images/fx/butterfly/swallowtail-03.png',112],'swallowtail-3':['images/fx/butterfly/swallowtail-04.png',112],
    'dfly-0':['images/fx/dragonfly/dfly-01.png',128],'dfly-1':['images/fx/dragonfly/dfly-02.png',128],'dfly-2':['images/fx/dragonfly/dfly-03.png',128],
    'firefly-0':['images/fx/firefly/firefly-01.png',40],'firefly-1':['images/fx/firefly/firefly-02.png',40],'firefly-2':['images/fx/firefly/firefly-03.png',40],'firefly-3':['images/fx/firefly/firefly-04.png',40],
    'frog-0':['images/fx/frog/frog-01.png',160],'frog-1':['images/fx/frog/frog-02.png',160],'lilypad':['images/fx/lilypad/pad-01.png',240],
    'drop':['images/fx/rain/drop.png',64],
    'martin':['images/nature-photo/martin.png',160],'morpho':['images/nature-photo/butterfly.png',120],'pear':['images/nature-photo/pear-leaf.png',240],'acer':['images/nature-photo/maple-leaf.png',200],
    'frost':['images/atmosphere/frost-glass.webp',768],'impact':['images/atmosphere/glass-impact.webp',512]
  };
  const PLATES={day:'images/atmosphere/lagoon-day.webp',night:'images/atmosphere/lagoon-night.webp',overcast:'images/atmosphere/lagoon-overcast.webp',storm:'images/atmosphere/lagoon-storm.webp',winter:'images/atmosphere/lagoon-winter.webp'};

  root.NatureEffects={classify,solarPosition,moonPhase,visualState,actorPose,create:function(){
    let canvas,sky,ctx,bg,w=0,h=0,raf=0,last=0,time=0,frameCount=0,accum=0,dprFx=1,dprSky=1;
    let details={},weatherState=visualState(null),solar=null;
    let code=null,temp=0,wind=0,mode='none',preview=null,previewUntil=0,quiet=[],layoutDirty=true,scrollDirty=true,maskTick=-1,maskScale=.25;
    let signatureMode='',signatureStart=0,theatre=true,sceneCaption=null,returnFocus=null,hud=null,previewStarted=0,hudTimer=0,sceneLighting='auto';
    let previewTimer=0,previewToken=0,seasonPreview=null,thunderTimer=0,lastIceStrike=-1;
    let viewTick=-1,gust=0,cloudBlend=.3,lightning=0,boltIndex=0,boltX=.5,nextLightning=25,interactionWind=0,lastLightUpdate=-1,failures=0;
    const tilt={x:0,y:0,tx:0,ty:0};let pill=null,chromeTick=-10,realFrame={ema:33,slow:0};
    let plateKey='',plateFrom='',plateFade=1,gradeKey='',gradeCanvas=null,budget={ema:12,low:0,high:0},sessionLevel='';
    const previewModes=['clear','cloud','drizzle','rain','heavy','storm','typhoon','hail','snow','wind','fog','heat','cold','earthquake'];
    const labels={clear:['晴空','Cerah'],cloud:['雲層','Awan'],drizzle:['細雨','Gerimis'],rain:['降雨','Hujan'],heavy:['豪雨','Hujan lebat'],storm:['雷雨','Badai petir'],typhoon:['颱風','Topan'],hail:['冰雹','Hujan es'],snow:['飄雪','Salju'],wind:['強風','Angin kencang'],fog:['濃霧','Kabut'],heat:['熱浪','Panas'],cold:['寒流','Dingin'],earthquake:['地震情境','Simulasi gempa']};
    const reduced=matchMedia('(prefers-reduced-motion: reduce)');
    const sprites=new Map(),plates=new Map(),tints=new Map(),glows=new Map(),streaks=new Map();
    let mask,maskCtx,pageRects=[],stickyEls=[],views=[],starsA=null,starsB=null,moonCanvas=null,moonKey='',raysCanvas=null;
    const rain=[],snow=[],hail=[],petals=[],beads=[],flies=[],splashes=[];
    let level='rich';try{level=localStorage.getItem('nature_quality')||level}catch(e){}
    // Low-memory phones start one step lighter for this session; the budget loop can still move either way.
    try{const mem=navigator.deviceMemory;if(mem&&mem<=2)sessionLevel=level==='rich'?'balanced':level==='balanced'?'subtle':'';}catch(e){}
    const allowed=k=>typeof root.isFxEnabled!=='function'||root.isFxEnabled(k);
    const effLevel=()=>sessionLevel||level;
    const rainModes=['drizzle','rain','heavy','storm','typhoon'];
    const activeMode=()=>preview&&performance.now()<previewUntil?preview:details.context&&(!details.context.expiresAt||Date.now()>details.context.expiresAt)?'none':mode;
    const rainMode=()=>rainModes.includes(activeMode());

    const tw=key=>root.TaiwanFlora?root.TaiwanFlora.sprite(key):null; // v343 Taiwan ecology sprites (procedural)
    // ---- asset loading ----
    function sprite(key){
      if(!SPRITES[key])return null;let e=sprites.get(key);if(e)return e.ready?e.cv:null;
      e={ready:false,cv:null};sprites.set(key,e);
      const [src,max]=SPRITES[key],im=new Image();im.decoding='async';
      im.onload=()=>{try{const s=Math.min(1,max/Math.max(im.naturalWidth,im.naturalHeight)),cv=document.createElement('canvas');cv.width=Math.max(1,Math.round(im.naturalWidth*s));cv.height=Math.max(1,Math.round(im.naturalHeight*s));cv.getContext('2d').drawImage(im,0,0,cv.width,cv.height);e.cv=cv;e.ready=true}catch(_){sprites.delete(key)}};
      im.onerror=()=>{sprites.delete(key)};im.src='./'+src;return null;
    }
    function plate(key){
      if(!PLATES[key])return null;let e=plates.get(key);if(e)return e.ready?e.im:null;
      e={ready:false,im:null};plates.set(key,e);const im=new Image();im.decoding='async';
      im.onload=()=>{e.im=im;e.ready=true};im.onerror=()=>plates.delete(key);im.src='./'+PLATES[key];return null;
    }
    // Pre-tinted copies of a sprite (night / storm / dusk) instead of per-frame compositing.
    function tinted(key,tint,alpha){
      if(!tint)return sprite(key);const id=key+'|'+tint;let cv=tints.get(id);if(cv)return cv;const src=sprite(key);if(!src)return null;
      cv=document.createElement('canvas');cv.width=src.width;cv.height=src.height;const c=cv.getContext('2d');c.drawImage(src,0,0);c.globalCompositeOperation='source-atop';c.globalAlpha=alpha;c.fillStyle=tint;c.fillRect(0,0,cv.width,cv.height);tints.set(id,cv);return cv;
    }
    // Radial glow sprite drawn scaled; one gradient per colour for the whole session.
    function glow(c,x,y,r,rgb,alpha){
      let cv=glows.get(rgb);if(!cv){cv=document.createElement('canvas');cv.width=cv.height=128;const g=cv.getContext('2d'),gr=g.createRadialGradient(64,64,0,64,64,64);gr.addColorStop(0,`rgba(${rgb},1)`);gr.addColorStop(.22,`rgba(${rgb},.42)`);gr.addColorStop(.6,`rgba(${rgb},.08)`);gr.addColorStop(1,`rgba(${rgb},0)`);g.fillStyle=gr;g.fillRect(0,0,128,128);glows.set(rgb,cv)}
      if(alpha<=0||r<=0)return;c.globalAlpha=clamp(alpha,0,1);c.drawImage(cv,x-r,y-r,r*2,r*2);c.globalAlpha=1;
    }
    // Rain streak sprite per (length bucket, brightness) — drawn with setTransform, no gradients per drop.
    function streak(len,bright,dark){
      const id=Math.round(len/4)*4+'|'+(bright?1:0)+(dark?1:0);let cv=streaks.get(id);if(cv)return cv;
      const L=Math.max(4,Math.round(len/4)*4),W=4;cv=document.createElement('canvas');cv.width=W;cv.height=L+2;const c=cv.getContext('2d');
      const body=c.createLinearGradient(0,0,0,L);body.addColorStop(0,'rgba(56,92,108,0)');body.addColorStop(.7,`rgba(56,92,108,${dark?.55:.8})`);body.addColorStop(1,'rgba(56,92,108,0)');
      c.strokeStyle=body;c.lineWidth=bright?1.6:1;c.lineCap='round';c.beginPath();c.moveTo(2,1);c.lineTo(2,L);c.stroke();
      const core=c.createLinearGradient(0,0,0,L);core.addColorStop(0,'rgba(236,247,251,0)');core.addColorStop(.8,`rgba(236,247,251,${bright?.95:.65})`);core.addColorStop(1,'rgba(236,247,251,0)');
      c.strokeStyle=core;c.lineWidth=bright?.75:.5;c.beginPath();c.moveTo(2.3,1);c.lineTo(2.3,L);c.stroke();streaks.set(id,cv);return cv;
    }
    function starField(seed){const cv=document.createElement('canvas');cv.width=512;cv.height=512;const c=cv.getContext('2d');let s=seed;const r=()=>{s=(s*16807)%2147483647;return s/2147483647};
      for(let i=0;i<70;i++){const x=r()*512,y=r()*512,rad=.5+r()*1.2;c.fillStyle=`rgba(${225+r()*30|0},${235+r()*20|0},255,${.5+r()*.5})`;c.beginPath();c.arc(x,y,rad,0,TAU);c.fill()}return cv}
    function moon(stamp){
      const p=moonPhase(stamp),key=Math.round(p*60);if(moonCanvas&&moonKey===key)return moonCanvas;moonKey=key;
      const cv=moonCanvas||document.createElement('canvas');cv.width=cv.height=96;const c=cv.getContext('2d'),R=30,cx=48,cy=48;
      c.clearRect(0,0,96,96);c.save();c.beginPath();c.arc(cx,cy,R,0,TAU);c.clip();
      const disc=c.createRadialGradient(cx-8,cy-8,4,cx,cy,R);disc.addColorStop(0,'#fbfaf1');disc.addColorStop(.7,'#e9e6d3');disc.addColorStop(1,'#bdbba7');c.fillStyle=disc;c.fillRect(0,0,96,96);
      c.fillStyle='rgba(165,165,150,.35)';for(const [mx,my,mr] of [[-9,-6,7],[6,4,9],[-3,10,4],[10,-10,4]]){c.beginPath();c.arc(cx+mx,cy+my,mr,0,TAU);c.fill()}
      // Terminator: fraction lit = (1-cos(2πp))/2; waxing lights the right limb.
      const lit=(1-Math.cos(TAU*p))/2,waxing=p<.5,k=1-2*lit;
      c.fillStyle='rgba(8,14,28,.93)';c.beginPath();
      if(waxing){c.arc(cx,cy,R,Math.PI/2,-Math.PI/2);c.ellipse(cx,cy,Math.abs(k)*R,R,0,-Math.PI/2,Math.PI/2,k<0)}
      else{c.arc(cx,cy,R,-Math.PI/2,Math.PI/2);c.ellipse(cx,cy,Math.abs(k)*R,R,0,Math.PI/2,-Math.PI/2,k<0)}
      c.fill();c.restore();moonCanvas=cv;return cv;
    }

    // ---- lifecycle ----
    function init(){
      if(canvas)return;
      sky=document.createElement('canvas');sky.id='nature-sky';sky.setAttribute('aria-hidden','true');document.body.appendChild(sky);bg=sky.getContext('2d',{alpha:true});
      if(root.SkyGL)try{root.SkyGL.init()}catch(e){}
      canvas=document.createElement('canvas');canvas.id='wxfx';canvas.setAttribute('aria-hidden','true');document.body.appendChild(canvas);ctx=canvas.getContext('2d');
      mask=document.createElement('canvas');maskCtx=mask.getContext('2d');
      starsA=starField(7);starsB=starField(91);
      for(let i=0;i<360;i++)rain.push({x:Math.random(),y:Math.random(),z:rand(.15,1),jitter:rand(-1,1)});
      for(let i=0;i<110;i++)snow.push({x:Math.random(),y:Math.random(),z:rand(.2,1),phase:rand(0,TAU),kind:i%4,spin:rand(-.6,.6)});
      for(let i=0;i<80;i++)hail.push({x:Math.random(),y:Math.random(),z:rand(.2,1),bounce:0});
      for(let i=0;i<34;i++)petals.push({x:Math.random(),y:Math.random(),z:rand(.3,1),phase:rand(0,TAU),rot:rand(0,TAU),spin:rand(-1.2,1.2),kind:i%6,swing:rand(.6,1.4)});
      for(let i=0;i<10;i++)beads.push({x:rand(.03,.97),y:rand(-.2,1),r:rand(2.4,5.5),v:rand(.004,.02),wob:rand(0,TAU),trail:0});
      for(let i=0;i<16;i++)flies.push({x:Math.random(),y:rand(.35,.9),phase:rand(0,TAU),kind:i%4,speed:rand(.4,1)});
      for(let i=0;i<14;i++)splashes.push({t:rand(0,1),x:Math.random()});
      document.addEventListener('keydown',e=>{if(e.key==='Escape'&&preview)stopPreview()});
      const dirty=()=>{layoutDirty=true};
      const observer=new MutationObserver(dirty);
      for(const el of document.querySelectorAll('#app,#mr'))observer.observe(el,{childList:true,subtree:true,characterData:true,attributes:true,attributeFilter:['class','style','hidden','open']});
      resize();addEventListener('resize',resize,{passive:true});addEventListener('scroll',()=>{scrollDirty=true},{passive:true,capture:true});
      document.addEventListener('transitionend',dirty,{passive:true});document.addEventListener('animationend',dirty,{passive:true});
      if(document.fonts)document.fonts.addEventListener('loadingdone',dirty);
      document.addEventListener('visibilitychange',()=>{if(document.hidden){clearTimeout(thunderTimer);lightning=0}resume()});reduced.addEventListener('change',resume);
      document.addEventListener('pointermove',e=>{if(e.pointerType==='mouse'){tilt.tx=clamp(e.clientX/w*2-1,-1,1);tilt.ty=clamp(e.clientY/h*2-1,-1,1);if(e.target.closest&&e.target.closest('.atmosphere-view'))interactionWind=3}},{passive:true});
      addEventListener('deviceorientation',e=>{if(e.gamma==null)return;tilt.tx=clamp(e.gamma/30,-1,1);tilt.ty=clamp((e.beta-45)/30,-1,1)},{passive:true});
      // Touch light: mark the pressed control and place the bloom under the finger.
      document.addEventListener('pointerdown',e=>{const b=e.target.closest&&e.target.closest('#app button,#app [role=button],#app .day');if(!b||reduced.matches)return;const r=b.getBoundingClientRect();b.style.setProperty('--tx',(e.clientX-r.left)+'px');b.style.setProperty('--ty',(e.clientY-r.top)+'px');b.classList.remove('fx-touch');void b.offsetWidth;b.classList.add('fx-touch');setTimeout(()=>b.classList.remove('fx-touch'),600)},{passive:true});
      setInterval(()=>{syncSound();if(!raf)resume()},15000);resume();
    }
    function capDpr(){const d=devicePixelRatio||1,l=effLevel();return {fx:Math.min(d,l==='subtle'?1:l==='balanced'?1.25:1.5),sky:Math.min(d,1)}}
    function resize(){
      if(!canvas)return;w=innerWidth;h=innerHeight;const d=capDpr();dprFx=d.fx;dprSky=d.sky;
      canvas.width=Math.round(w*dprFx);canvas.height=Math.round(h*dprFx);canvas.style.width=w+'px';canvas.style.height=h+'px';ctx.setTransform(dprFx,0,0,dprFx,0,0);
      sky.width=Math.round(w*dprSky);sky.height=Math.round(h*dprSky);sky.style.width=w+'px';sky.style.height=h+'px';bg.setTransform(dprSky,0,0,dprSky,0,0);
      if(root.SkyGL&&root.SkyGL.ok)root.SkyGL.resize(w,h,effLevel());
      mask.width=Math.ceil(w*maskScale);mask.height=Math.ceil(h*maskScale);gradeKey='';raysCanvas=null;layoutDirty=true;readViews();if(!raf)draw(0);
    }

    // ---- text protection mask (quarter resolution, rebuilt only when layout changes) ----
    const fullSelector='.bottom-nav,.wk-row,.calendar-month-grid,.calendar-view-switch,.screen-heading,.page-returnbar,.modal-sheet,.wx-detail-sheet,.nature-preview-hud,input,select,textarea';
    const stickySelector='.bottom-nav,.wk-row,.nature-preview-hud,.page-returnbar,.modal-sheet,.wx-detail-sheet';
    function scanLayout(){
      pageRects=[];const sy=scrollY||0,sx=scrollX||0;
      const push=(r,solid)=>{if(r.width<=0||r.height<=0)return;pageRects.push({l:r.left+sx,t:r.top+sy,w:r.width,h:r.height,solid})};
      document.querySelectorAll(fullSelector).forEach(e=>{if(!e.matches(stickySelector))push(e.getBoundingClientRect(),true)});
      document.querySelectorAll('.status-switch,.pref-switch,.header-actions,.brand-mark,.account-avatar,#app button svg,#app [role="button"] svg,.dial-face svg').forEach(e=>push(e.getBoundingClientRect(),false));
      for(const scope of document.querySelectorAll('#app,#mr')){
        const walker=document.createTreeWalker(scope,NodeFilter.SHOW_TEXT,{acceptNode:node=>!node.textContent.trim()||!node.parentElement||node.parentElement.closest('script,style,svg,[aria-hidden="true"],'+fullSelector)?NodeFilter.FILTER_REJECT:NodeFilter.FILTER_ACCEPT});
        const range=document.createRange();let node;
        while((node=walker.nextNode())){range.selectNodeContents(node);for(const rect of range.getClientRects())push(rect,false)}
      }
      stickyEls=[...document.querySelectorAll(stickySelector)];layoutDirty=false;readViews();
    }
    function buildMask(){
      const sy=scrollY||0,sx=scrollX||0,m=maskScale,c=maskCtx;c.setTransform(1,0,0,1,0,0);c.clearRect(0,0,mask.width,mask.height);c.fillStyle='#000';quiet=[];
      for(const r of pageRects){const top=r.t-sy,left=r.l-sx;if(top+r.h<0||top>h)continue;const pad=r.solid?5:4;c.fillRect((left-pad)*m,(top-pad)*m,(r.w+pad*2)*m,(r.h+pad*2)*m);quiet.push(r)}
      for(const el of stickyEls){const r=el.getBoundingClientRect();if(r.width<=0||r.height<=0||r.bottom<0||r.top>h)continue;c.fillRect((r.left-6)*m,(r.top-6)*m,(r.width+12)*m,(r.height+12)*m);quiet.push(r)}
      scrollDirty=false;maskTick=time;if(root.SkyGL&&root.SkyGL.ok)root.SkyGL.setMask(mask);
    }

    // ---- scene state helpers ----
    function sceneIsNight(fallback){return preview&&sceneLighting!=='auto'?sceneLighting==='night':solar?solar.elevation< -6:fallback}
    function seasonName(){return seasonPreview&&performance.now()<previewUntil?seasonPreview:root.NatureContext?root.NatureContext.season(Date.now(),details.lat,details.lon):'spring'}
    function sceneContext(m,season,night){
      const raw=details.context||{mode:m,temp:null,wind:null,cloud:null,lat:details.lat,lon:details.lon};
      const state=preview?{...raw,mode:m,season,temp:m==='cold'?5:m==='heat'?37:m==='snow'?-1:26,wind:['wind','typhoon'].includes(m)?80:8,cloud:['clear','heat'].includes(m)?12:['cloud','fog'].includes(m)?88:100}:{...raw,mode:m,season};
      return root.NatureContext?root.NatureContext.ecology(state,night):{plant:'reeds',animals:[],wet:false,cloudType:'unknown'};
    }
    // Sun elevation the sky should show: real solar geometry, or an equivalent when a preview
    // forces day/night or no location is known.
    function sceneElevation(phase,hour){
      if(solar&&!(preview&&sceneLighting!=='auto'))return solar.elevation;
      if(phase==='night')return -18;if(phase==='dusk')return -2;if(phase==='golden')return 8;
      const t=clamp((hour-6)/12,0,1);return Math.max(12,Math.sin(t*Math.PI)*62);
    }
    function fallbackAzimuth(hour){return clamp((hour-6)/12,0,1)*180+90}
    function lightPhase(hour){
      if(preview&&sceneLighting!=='auto')return sceneLighting==='night'?'night':'day';
      if(solar)return solar.elevation< -6?'night':solar.elevation<0?'dusk':solar.elevation<12?'golden':'day';
      return hour<5||hour>=19?'night':hour<6||hour>=18?'dusk':hour<8||hour>16?'golden':'day';
    }
    function sceneTemp(m){if(preview)return m==='cold'?5:m==='heat'?37:m==='snow'?-1:26;const t=details.context?.temp;return known(t)?Number(t):null}
    function sceneHumidity(){const v=details.context?.humidity;return known(v)?Number(v):null}
    function windDir(){const v=details.context?.windDirection;return preview||!known(v)?1:Math.sin((Number(v)+180)*Math.PI/180)}
    function cloudCover(m){
      if(preview)return ['cloud','drizzle','rain','heavy','storm','typhoon','hail','fog','snow'].includes(m)?.92:m==='cold'?.55:.14;
      return weatherState.cloud;
    }
    function rainIntensity(m){return preview?(m==='drizzle'?.18:m==='typhoon'?1:m==='heavy'?.92:m==='storm'?.8:.5):weatherState.intensity}
    function plateFor(m,phase,season){
      if(phase==='night')return 'night';
      if(m==='snow')return 'winter';
      if(['storm','typhoon','hail','heavy'].includes(m))return 'storm';
      if(['rain','drizzle','fog'].includes(m)||(m==='cloud'&&cloudCover(m)>.72)||(season==='winter'&&m!=='clear'&&m!=='heat'&&cloudCover(m)>.45))return 'overcast';
      return 'day';
    }
    function coverRect(im,W,H){const s=Math.max(W/im.naturalWidth,H/im.naturalHeight),dw=im.naturalWidth*s,dh=im.naturalHeight*s;return [(W-dw)/2,H-dh+(dh-H)*.12,dw,dh]}

    // ---- background: plate, grade, celestial bodies, clouds, fog ----
    function drawPlates(m,phase,season,dt){
      const want=plateFor(m,phase,season);
      if(want!==plateKey){plateFrom=plateKey;plateKey=want;plateFade=plateFrom?0:1}
      plateFade=Math.min(1,plateFade+dt/2.4);
      const cur=plate(plateKey),prev=plateFrom?plate(plateFrom):null;
      if(!cur&&!prev){const g=bg.createLinearGradient(0,0,0,h);const c=phase==='night'?['#0b1a2c','#1b2f44']:want==='storm'?['#3f4e5c','#7f8c95']:want==='overcast'?['#8795a0','#c3ccd0']:['#5e97c3','#cfe0e4'];g.addColorStop(0,c[0]);g.addColorStop(1,c[1]);bg.fillStyle=g;bg.fillRect(0,0,w,h);return}
      const px=tilt.x*14,py=tilt.y*10,R=im=>{const r=coverRect(im,w+28,h+20);return [r[0]-14+px,r[1]-10+py,r[2],r[3]]};
      if(prev&&plateFade<1){bg.globalAlpha=1;bg.drawImage(prev,...R(prev))}
      if(cur){bg.globalAlpha=prev?plateFade:1;bg.drawImage(cur,...R(cur));bg.globalAlpha=1}
      else if(!prev){bg.fillStyle='#6d8da3';bg.fillRect(0,0,w,h)}
    }
    // Colour grading overlay cached per (season, phase, weather family); seasons differ strongly.
    function drawGrade(m,phase,season){
      const wet=rainModes.includes(m)||m==='hail',key=[season,phase,wet?'wet':m==='fog'?'fog':m==='snow'||m==='cold'?'cold':m==='heat'?'heat':'dry',w,h].join('|');
      if(key!==gradeKey){
        gradeKey=key;gradeCanvas=gradeCanvas||document.createElement('canvas');gradeCanvas.width=64;gradeCanvas.height=96;const c=gradeCanvas.getContext('2d');c.clearRect(0,0,64,96);
        const top={spring:'255,214,228',summer:'186,232,214',autumn:'255,186,110',winter:'168,196,224'}[season],bottom={spring:'214,236,206',summer:'255,240,190',autumn:'168,110,70',winter:'206,218,228'}[season];
        let a=phase==='night'?.22:phase==='golden'||phase==='dusk'?.34:.28;if(wet)a*=.6;if(m==='fog')a*=.7;
        const g=c.createLinearGradient(0,0,0,96);g.addColorStop(0,`rgba(${top},${a})`);g.addColorStop(.72,`rgba(${bottom},${a*.55})`);g.addColorStop(1,`rgba(${bottom},${a*.9})`);c.fillStyle=g;c.fillRect(0,0,64,96);
        if(phase==='golden'||phase==='dusk'){const sun=c.createLinearGradient(64,0,0,96);sun.addColorStop(0,'rgba(255,170,90,.42)');sun.addColorStop(.6,'rgba(255,140,80,.1)');sun.addColorStop(1,'rgba(90,60,110,.18)');c.fillStyle=sun;c.fillRect(0,0,64,96)}
        if(phase==='night'){c.fillStyle='rgba(10,20,44,.34)';c.fillRect(0,0,64,96)}
        if(m==='heat'){c.fillStyle='rgba(255,150,60,.16)';c.fillRect(0,0,64,96)}
        if(m==='cold'||m==='snow'){c.fillStyle='rgba(150,190,230,.2)';c.fillRect(0,0,64,96)}
        if(wet){c.fillStyle='rgba(40,60,80,.18)';c.fillRect(0,0,64,96)}
      }
      bg.globalCompositeOperation='soft-light';bg.drawImage(gradeCanvas,0,0,w,h);bg.globalCompositeOperation='source-over';
      if(season==='winter'&&phase!=='night'){bg.globalCompositeOperation='saturation';bg.fillStyle='rgba(120,130,140,.45)';bg.fillRect(0,0,w,h);bg.globalCompositeOperation='source-over'}
      if(season==='summer'&&phase==='day'&&!wet){bg.globalCompositeOperation='saturation';bg.fillStyle='rgba(60,200,180,.18)';bg.fillRect(0,0,w,h);bg.globalCompositeOperation='source-over'}
    }
    function sunScreen(hour){
      if(solar){const el=clamp(solar.elevation,-10,80),az=solar.azimuth*Math.PI/180;return {x:w*(.5-.36*Math.sin(az)),y:h*.72-(el/80)*h*.66,el}}
      const t=clamp((hour-6)/12,0,1);return {x:w*(.12+.76*t),y:h*.72-Math.sin(t*Math.PI)*h*.6,el:Math.sin(t*Math.PI)*60};
    }
    function drawCelestial(m,phase,hour,cover,dt){
      if(phase==='night'){
        const dark=1-clamp(cover,0,1)*.85;if(starsA){bg.globalAlpha=(.5+.35*Math.sin(time*.9))*dark;bg.drawImage(starsA,0,0,w,h*.7);bg.globalAlpha=(.5+.35*Math.sin(time*1.3+2))*dark;bg.drawImage(starsB,0,0,w,h*.7);bg.globalAlpha=1}
        const p=moonPhase(Date.now()),ha=(solar?solar.hourAngle:(hour-12)*Math.PI/12)+TAU*p,el=Math.cos(ha)*.78,mx=w*(.5-.4*Math.sin(ha)),my=h*.58-el*h*.5;
        if(el>-.05){const mc=moon(Date.now()),mr=Math.max(22,Math.min(w,h)*.045);glow(bg,mx,my,mr*4.5,'190,206,236',.22*dark);bg.globalAlpha=.92*dark+.08;bg.drawImage(mc,mx-mr,my-mr,mr*2,mr*2);bg.globalAlpha=1;if(cover>.3)glow(bg,mx,my,mr*2.4,'214,224,240',cover*.25)}
        return;
      }
      const s=sunScreen(hour),warm=phase!=='day',wet=rainModes.includes(m)||m==='hail',hidden=cover>.86||wet;
      const sunAlpha=hidden?.08:clamp(1-(cover-.3)/.55,.12,1);
      glow(bg,s.x,s.y,Math.max(220,w*(warm?.75:.6)),warm?'255,170,96':'255,236,186',(warm?.5:.34)*sunAlpha*(hidden?.5:1));
      if(hidden)return;
      const key=m==='heat'?'sun-1':warm?'sun-2':'sun-0',sun=sprite(key);
      const r=Math.max(34,Math.min(w,h)*(m==='heat'?.11:.085))*(warm?1.25:1);
      if(sun){bg.globalAlpha=sunAlpha;bg.drawImage(sun,s.x-r,s.y-r,r*2,r*2);bg.globalAlpha=1}else glow(bg,s.x,s.y,r,'255,250,230',sunAlpha);
      if(cover<.5&&!reduced.matches){
        if(!raysCanvas){raysCanvas=document.createElement('canvas');const R=360;raysCanvas.width=raysCanvas.height=R*2;const c=raysCanvas.getContext('2d');c.translate(R,R);for(let i=0;i<9;i++){const a=i*TAU/9,g=c.createLinearGradient(0,0,Math.cos(a)*R,Math.sin(a)*R);g.addColorStop(0,'rgba(255,240,200,.26)');g.addColorStop(.45,'rgba(255,240,200,.06)');g.addColorStop(1,'rgba(255,240,200,0)');c.strokeStyle=g;c.lineWidth=14+i%3*8;c.beginPath();c.moveTo(0,0);c.lineTo(Math.cos(a)*R,Math.sin(a)*R);c.stroke()}}
        bg.save();bg.translate(s.x,s.y);bg.rotate(time*.018);bg.globalAlpha=(.5-cover)*1.1*sunAlpha*(m==='heat'?1.4:1);bg.drawImage(raysCanvas,-h,-h,h*2,h*2);bg.restore();
      }
    }
    function drawClouds(m,phase,cover,dt){
      const l=effLevel(),count=l==='subtle'?3:l==='balanced'?5:7,wet=rainModes.includes(m)||m==='hail',stormy=['storm','typhoon','hail','heavy'].includes(m);
      const tint=phase==='night'?'#1a2436':stormy?'#2b3744':wet?'#6b7784':phase==='golden'||phase==='dusk'?'#f0b482':null,tintA=phase==='night'?.82:stormy?.72:wet?.5:.38;
      const speed=8+gust*2.2;
      for(let i=0;i<count;i++){
        const kind=cover>.75?i%2:cover>.35?(i%3):2,cv=tinted('cloud-'+kind,tint,tintA);if(!cv)continue;
        const depth=.35+(i%4)*.22,size=Math.min(w*(.55+depth*.75),760),aspect=cv.height/cv.width;
        const x=(((i*.37+.11)*w+time*speed*depth*(1+i%3*.3))%(w+size*1.2))-size*.6+tilt.x*22*depth,y=h*(.03+i*.085+(cover>.6?0:.02))+Math.sin(time*.05+i)*6+tilt.y*14*depth;
        let a=(cover<.2?cover*2:clamp(.35+cover*.7,0,1))*(.45+depth*.55);if(m==='fog')a*=.5;if(cover<.12&&kind!==2)continue;
        bg.globalAlpha=clamp(a,0,1);if(i%2){bg.save();bg.translate(x+size,y);bg.scale(-1,1);bg.drawImage(cv,0,0,size,size*aspect);bg.restore()}else bg.drawImage(cv,x,y,size,size*aspect);
      }bg.globalAlpha=1;
      if(cover>.78){const dark=phase==='night'?'10,16,28':stormy?'36,46,58':'130,140,150';glow(bg,w*.3,-h*.1,w*1.1,dark,(cover-.78)*(stormy?1.4:.9));glow(bg,w*.85,h*.05,w*.9,dark,(cover-.78)*(stormy?1.1:.6))}
    }
    function drawFogBanks(m,phase,hum){
      const base=m==='fog'?1:hum!==null&&hum>=92&&!rainModes.includes(m)?.45:0;if(base<=0)return;
      const rgb=phase==='night'?'140,160,184':'214,226,232';
      for(let i=0;i<5;i++){const x=Math.sin(time*.06+i*1.7)*w*.55+w*.5,y=h*(.3+i*.16);bg.save();bg.scale(1,.42);glow(bg,x,y/.42,w*.9,rgb,.3*base);bg.restore()}
      bg.fillStyle=`rgba(${rgb},${.22*base})`;bg.fillRect(0,h*.4,w,h*.6);
    }

    // ---- foreground particles ----
    function vignette(x,y){return (.66+.34*clamp(Math.abs(x/w-.5)*2,0,1))*(y>h-120?.6:1)}
    function drawRain(dt,m,phase){
      if(!allowed('weather')||!rainMode())return;
      const l=effLevel(),drizzle=m==='drizzle',intensity=rainIntensity(m),glRain=root.SkyGL&&root.SkyGL.ok,count=Math.round((l==='subtle'?70:l==='balanced'?170:280)*Math.max(.18,intensity)*(glRain?.55:1));
      const tilt=windDir()*clamp(gust/34+(m==='typhoon'?Math.sin(time*1.4)*.22:0),.03,m==='typhoon'?1.6:.9),angle=Math.atan(tilt),cos=Math.cos(angle),sin=Math.sin(angle),dark=phase==='night';
      const f=dprFx;
      for(let i=0;i<count;i++){
        const p=rain[i],speed=(drizzle?120+p.z*170:230+p.z*480)*(m==='typhoon'?1.6:m==='heavy'?1.25:1);
        p.y+=dt*speed/h;p.x+=dt*(speed*tilt+p.jitter*6)/w;
        if(p.y>1.03){p.y=-.04;p.x=Math.random()}if(p.x>1.12)p.x=-.12;if(p.x<-.12)p.x=1.12;
        const x=p.x*w,y=p.y*h,len=drizzle?3+9*p.z:9+30*p.z*(m==='heavy'?1.25:1),a=(.22+.5*p.z)*vignette(x,y),cv=streak(len,p.z>.6,dark);
        ctx.globalAlpha=a;ctx.setTransform(cos*f,-sin*f,sin*f,cos*f,x*f,y*f);ctx.drawImage(cv,-2,0);
      }
      ctx.setTransform(f,0,0,f,0,0);ctx.globalAlpha=1;
      // Splash rings on the lower band (near ground / sill).
      if(!drizzle){const n=l==='subtle'?4:intensity>.6?12:7;ctx.lineWidth=.8;for(let i=0;i<n;i++){const s=splashes[i];s.t+=dt*1.6;if(s.t>1){s.t=0;s.x=Math.random()}const x=s.x*w,y=h-18-i*3,k=s.t;ctx.strokeStyle=`rgba(224,243,250,${(1-k)*.5})`;ctx.beginPath();ctx.ellipse(x,y,2+k*16,.8+k*4,0,0,TAU);ctx.stroke();if(k<.4){ctx.fillStyle=`rgba(236,250,255,${(1-k*2.5)*.7})`;ctx.beginPath();ctx.arc(x+Math.sin(i)*3,y-k*18,1,0,TAU);ctx.fill()}}}
      if(m==='heavy'||m==='typhoon'){ctx.fillStyle=`rgba(190,208,218,${.05+intensity*.07})`;ctx.fillRect(0,0,w,h)}
    }
    // Beads live on the glass in front of the app; the rain field lives outside it.
    function drawBeads(dt,m){
      if(!allowed('weather')||!rainMode())return;
      if(root.SkyGL&&root.SkyGL.glassActive&&root.SkyGL.glassActive())return; // v342: refracting drops live on the GL glass plane
      const l=effLevel(),drizzle=m==='drizzle',intensity=rainIntensity(m),tilt=windDir()*clamp(gust/34,.03,.9);
      const drop=sprite('drop'),nb=drizzle?6:l==='subtle'?4:intensity>.7?10:7;
      for(let i=0;i<nb;i++){
        const b=beads[i];b.y+=dt*(b.v*(.6+intensity))*(1+.4*Math.sin(time*2+b.wob));b.x+=dt*tilt*.004*b.r;if(b.y>1.08){b.y=rand(-.25,-.05);b.x=rand(.03,.97);b.r=rand(2.2,5.5)}
        const x=b.x*w+Math.sin(time*1.7+b.wob)*.6,y=b.y*h,r=b.r;
        ctx.strokeStyle='rgba(206,228,236,.22)';ctx.lineWidth=r*.55;ctx.beginPath();ctx.moveTo(x,y-r*6-40*intensity);ctx.lineTo(x,y);ctx.stroke();
        if(drop){ctx.globalAlpha=.78;ctx.drawImage(drop,x-r*1.2,y-r*2.4,r*2.4,r*2.4*drop.height/drop.width)}
        else{ctx.fillStyle='rgba(220,236,244,.4)';ctx.beginPath();ctx.ellipse(x,y,r,r*1.5,0,0,TAU);ctx.fill()}
      }ctx.globalAlpha=1;
    }
    function drawSnow(dt,m,phase){
      if(m!=='snow'||!allowed('weather'))return;
      const l=effLevel(),count=l==='subtle'?30:l==='balanced'?60:100,f=dprFx,drift=windDir()*gust*.4;
      for(let i=0;i<count;i++){
        const p=snow[i],cv=sprite('snow-'+p.kind);p.y+=dt*(.03+.05*p.z);p.x+=dt*(Math.sin(time*.7+p.phase)*.02+drift*.0015)*p.z;if(p.y>1.05){p.y=-.05;p.x=Math.random()}if(p.x>1.05)p.x=-.05;if(p.x<-.05)p.x=1.05;
        const x=p.x*w,y=p.y*h,s=3+p.z*11,a=(.35+p.z*.5)*vignette(x,y);ctx.globalAlpha=a;
        if(cv){const rot=time*p.spin+p.phase,c=Math.cos(rot),sn=Math.sin(rot);ctx.setTransform(c*f,sn*f,-sn*f,c*f,x*f,y*f);ctx.drawImage(cv,-s,-s,s*2,s*2)}
        else{ctx.setTransform(f,0,0,f,0,0);ctx.fillStyle='#f3f9ff';ctx.beginPath();ctx.arc(x,y,s*.3,0,TAU);ctx.fill()}
      }
      ctx.setTransform(f,0,0,f,0,0);ctx.globalAlpha=1;
      ctx.fillStyle=phase==='night'?'rgba(170,190,214,.06)':'rgba(236,244,250,.08)';ctx.fillRect(0,0,w,h);
    }
    function drawHail(dt,m,phase){
      if(m!=='hail'||!allowed('weather'))return;
      const l=effLevel(),count=l==='subtle'?24:l==='balanced'?44:70,f=dprFx;
      for(let i=0;i<count;i++){const p=hail[i],z=p.z,r=.8+z*2.6,speed=360+z*600;p.y+=dt*speed/h;p.x+=dt*(60+gust*4)/w;if(p.y>1.12){p.y=-.08;p.x=Math.random()}if(p.x>1.1)p.x=-.1;
        let x=p.x*w,y=p.y*h;const floor=h*(.84+(i%5)*.026);if(y>floor){const t=(y-floor)/(h-floor);y=floor-Math.sin(t*Math.PI)*(12+z*24);x+=t*24}
        const rot=time*.8+i,c=Math.cos(rot),s=Math.sin(rot);ctx.setTransform(c*f,s*f,-s*f,c*f,x*f,y*f);
        ctx.fillStyle=phase==='night'?'#cfe5f1':'#eef8fc';ctx.strokeStyle=phase==='night'?'#8bb2c6':'#5c8aa0';ctx.lineWidth=.6;ctx.beginPath();for(let j=0;j<7;j++){const a=j*TAU/7,rr=r*(j%2?.86:1);j?ctx.lineTo(Math.cos(a)*rr,Math.sin(a)*rr):ctx.moveTo(Math.cos(a)*rr,Math.sin(a)*rr)}ctx.closePath();ctx.fill();ctx.stroke();
        ctx.fillStyle='rgba(255,255,255,.8)';ctx.beginPath();ctx.arc(-r*.3,-r*.35,r*.3,0,TAU);ctx.fill();
      }ctx.setTransform(f,0,0,f,0,0);
    }
    // Season particles: spring petals and autumn leaves drift even in calm air; winter dry leaves and
    // generic debris need measured wind. Spin and sway follow gust strength.
    // v343: Taiwan lowland seasons. Spring 油桐/木棉, summer 鳳凰木, autumn 欒樹/芒草, winter 落羽松 + north-east monsoon.
    function drawSeasonDrift(dt,m,season,phase){
      if(!allowed('seasonal')||reduced.matches)return;
      const windy=['wind','typhoon'].includes(m),speedW=preview?(windy?60:m==='cold'?26:8):Math.max(0,Number(details.context?.wind)||0);
      const monsoon=season==='winter'&&(m==='cold'||speedW>=20||windy);
      const calmDrift=season==='spring'||season==='autumn'||season==='summer';
      if(!calmDrift&&speedW<8&&!windy&&!monsoon)return;
      const l=effLevel(),q=l==='subtle'?.35:l==='balanced'?.65:1;
      const base=Math.round((season==='spring'?18:season==='summer'?9:season==='autumn'?16:monsoon?14:5)*q);
      const count=Math.min(petals.length,windy?base+10:base),dir=windDir()||1,f=dprFx,g=Math.min(1,speedW/40),wet=rainMode();
      for(let i=0;i<count;i++){
        const p=petals[i];let key,size,fall,spin=1,heavy=false,float=false;
        if(season==='spring'){if(p.kind<4){key='tung';size=9}else{key='kapok';size=14;heavy=true}}
        else if(season==='summer'){key='flame-petal';size=9}
        else if(season==='autumn'){if(p.kind<3){key='rain-tree-flower';size=6}else if(p.kind<5){key='rain-tree-capsule';size=9;heavy=true}else{key='fluff';size=6;float=true}}
        else{if(p.kind<4||monsoon&&p.kind<5){key='cypress-needle';size=9}else{key='dry-leaf';size=11}}
        const cv=tw(key);
        fall=(heavy?.07:float?.012:.03)*(wet?1.5:1);
        const sway=float?Math.sin(time*p.swing*.6+p.phase)*2:Math.sin(time*p.swing+p.phase);
        const gust=monsoon?.18+g*.2:g*.11;
        p.y+=dt*(fall+(float?-.004*(1-g):.03*g))*(.5+p.z);p.x+=dt*(sway*.03*(heavy?.4:1)+dir*(.01+gust)*(.4+p.z));p.rot+=dt*(p.spin*spin*(1+g*3)*(heavy?.5:1));
        if(p.y>1.08){p.y=rand(-.3,-.05);p.x=Math.random()}if(p.y<-.3)p.y=1.05;if(p.x>1.1)p.x=-.1;if(p.x<-.1)p.x=1.1;
        if(!cv)continue;const x=p.x*w,y=p.y*h,sz=size*(.6+p.z*.7),c=Math.cos(p.rot),sn=Math.sin(p.rot),tumble=heavy?.75+.25*Math.abs(Math.cos(time+p.phase)):.45+.55*Math.abs(Math.cos(time*1.3+p.phase));
        ctx.globalAlpha=(phase==='night'?.45:.85)*vignette(x,y);ctx.setTransform(c*f*tumble,sn*f*tumble,-sn*f,c*f,x*f,y*f);ctx.drawImage(cv,-sz,-sz*cv.height/cv.width,sz*2,sz*2*cv.height/cv.width);
      }ctx.setTransform(f,0,0,f,0,0);ctx.globalAlpha=1;
      // wind made visible: faint streaks when it blows hard (typhoon, strong wind, NE monsoon)
      if(windy||monsoon){const n=monsoon?7:9,night=phase==='night';ctx.lineWidth=1;for(let i=0;i<n;i++){const x=((time*(260+i*40)+i*131)%(w+260))-130,y=(i*97+Math.sin(time*.7+i)*12)%h,len=70+i%3*40;ctx.strokeStyle=night?`rgba(190,205,215,${.12+i%2*.06})`:`rgba(255,255,255,${.18+i%2*.08})`;ctx.beginPath();ctx.moveTo(x,y);ctx.quadraticCurveTo(x+len*.5*dir,y-4,x+len*dir,y+2);ctx.stroke()}}
    }
    // Rooted foreground botany on both lower sides; sway follows measured wind only.
    function drawBotany(m,season,phase){
      if(!allowed('seasonal'))return;
      const speed=preview?(['wind','typhoon'].includes(m)?70:8):Math.max(0,Number(details.context?.wind)||0),dir=windDir()||1,night=phase==='night',f=dprFx;
      const bottom=h-96,sw=Math.min(.6,speed*.014),set=season==='spring'?[['tung-branch',150]]:season==='summer'?[['flame-branch',160]]:season==='autumn'?[['miscanthus',70],['rain-tree-branch',130]]:[['cypress-branch',150]];
      for(let side=0;side<2;side++)for(let i=0;i<set.length;i++){
        const [key,size]=set[i],cv=tw(key)||sprite(key);if(!cv)continue;
        const rootX=side?w+6:-6,rootY=bottom-i*30+(key==='miscanthus'?70:0),motion=reduced.matches?0:Math.sin(time*(.6+speed*.02)+i*1.1+side*2)*sw;
        const rot=(side?1:-1)*(key==='miscanthus'?.05:-.1+i*.1)+motion+dir*Math.min(.3,speed*.006),c=Math.cos(rot),s=Math.sin(rot),sx=side?-1:1;
        ctx.globalAlpha=(night?.4:.86)*(season==='winter'?.75:1);ctx.setTransform(c*f*sx,s*f,-s*f*sx,c*f,rootX*f,rootY*f);
        ctx.drawImage(cv,key==='miscanthus'?-size*.4:0,key==='miscanthus'?-size*1.6*cv.height/cv.width:-size*1.3*cv.height/cv.width,size*1.3,size*1.3*cv.height/cv.width);
      }
      ctx.setTransform(f,0,0,f,0,0);ctx.globalAlpha=1;
      // Summer evenings and rain: a tree frog on a lily pad, bottom left, above the navigation.
      if(season==='summer'&&(night||rainMode())&&effLevel()!=='subtle'){const pad=sprite('lilypad'),frog=sprite(Math.sin(time*.3)>.92?'frog-1':'frog-0');if(pad&&frog){ctx.globalAlpha=night?.55:.85;ctx.drawImage(pad,-20,h-150,120,120*pad.height/pad.width);ctx.drawImage(frog,6,h-126,60,60*frog.height/frog.width);ctx.globalAlpha=1}}
    }
    function drawAnimals(m,season,phase,dt){
      if(!allowed('animals')||reduced.matches)return;
      const night=phase==='night',eco=sceneContext(m,season,night),f=dprFx,l=effLevel();
      if(night&&season==='summer'&&!rainMode()&&m!=='fog'){
        const n=l==='subtle'?5:l==='balanced'?9:14;
        for(let i=0;i<n;i++){const p=flies[i];p.x+=dt*Math.sin(time*.5+p.phase)*.02*p.speed;p.y+=dt*Math.cos(time*.4+p.phase*2)*.012;if(p.x<0)p.x=1;if(p.x>1)p.x=0;p.y=clamp(p.y,.3,.92);
          const x=p.x*w,y=p.y*h,pulse=Math.max(0,Math.sin(time*(1.3+p.speed)+p.phase*3)),cv=sprite('firefly-'+p.kind);glow(ctx,x,y+5,12+pulse*10,'214,255,120',pulse*.75);if(cv){ctx.globalAlpha=.4+pulse*.6;ctx.drawImage(cv,x-9,y-9,18,18)}}
        ctx.globalAlpha=1;
      }
      if(!eco.animals.length)return;
      const temp=sceneTemp(m);
      // Dragonflies hover over water in warm summer afternoons.
      if(season==='summer'&&temp!==null&&temp>=26&&l!=='subtle'){for(let i=0;i<2;i++){const cv=sprite('dfly-'+(Math.floor(time*14+i)%3));if(!cv)continue;const x=w*(.3+i*.35)+Math.sin(time*(.8+i*.3))*w*.14+Math.sin(time*5+i)*2,y=h*(.52+i*.1)+Math.cos(time*.6+i)*22,dir=Math.cos(time*.8+i*.3)>0?1:-1;ctx.globalAlpha=.9;ctx.setTransform(f*dir,0,0,f,x*f,y*f);ctx.drawImage(cv,-26,-13,52,26)}}
      // Crag martin: photograph split into wings and body; glides between flapping bursts.
      const bird=sprite('martin');
      if(bird&&eco.animals.includes('bird')){for(let i=0;i<(l==='subtle'?1:2);i++){const pose=actorPose('bird',i,time,w,h,wind),iw=bird.width,ih=bird.height,glide=Math.sin(time*.43+i)>.65,fold=glide?.92:pose.fold,scale=pose.size/iw;
        ctx.setTransform(f,0,0,f,0,0);ctx.save();ctx.translate(pose.x,pose.y);ctx.rotate(pose.bank);ctx.globalAlpha=.9;ctx.scale(scale,scale);
        ctx.save();ctx.scale(1,fold);ctx.drawImage(bird,0,0,iw,ih*.48,-iw*.5,-ih*.48,iw,ih*.48);ctx.restore();ctx.save();ctx.scale(1,fold);ctx.drawImage(bird,0,ih*.59,iw,ih*.41,-iw*.5,ih*.11,iw,ih*.41);ctx.restore();ctx.drawImage(bird,0,ih*.48,iw,ih*.11,-iw*.5,0,iw,ih*.11);ctx.restore()}}
      ctx.setTransform(f,0,0,f,0,0);ctx.globalAlpha=1;
    }
    // Butterflies come close to the glass, so they are the only fauna in front of the app.
    function drawButterflies(m,season,phase){
      if(!allowed('animals')||reduced.matches)return;
      const eco=sceneContext(m,season,phase==='night');if(!eco.animals.length)return;
      const f=dprFx,l=effLevel(),species=season==='spring'?'swallowtail':season==='autumn'?'monarch':'purple',nb=l==='subtle'?1:2;
      for(let i=0;i<nb;i++){
        const pose=actorPose('butterfly',i,time,w,h,wind),frame=[0,1,2,3,2,1][Math.floor((time*9+i*2)%6)],cv=sprite(species+'-'+frame);if(!cv)continue;
        const size=28+i*8,c=Math.cos(pose.bank),s=Math.sin(pose.bank),dir=Math.cos(time*.03+i)>0?1:-1;
        ctx.globalAlpha=.92;ctx.setTransform(c*f*dir,s*f,-s*f*dir,c*f,pose.x*f,pose.y*f);ctx.drawImage(cv,-size/2,-size/2,size,size);
      }
      ctx.setTransform(f,0,0,f,0,0);ctx.globalAlpha=1;
    }
    // Two out-of-focus near leaves/petals cross the lens now and then (pre-blurred once).
    function drawNearBokeh(dt,m,season,phase){
      if(!allowed('seasonal')||reduced.matches||effLevel()==='subtle'||rainMode())return;
      const key=season==='spring'?'tung':season==='summer'?'flame-petal':season==='autumn'?'rain-tree-capsule':'cypress-spray',cv=blurred(key);if(!cv)return;const f=dprFx;
      for(let i=0;i<2;i++){
        const cycle=(time*.035+i*.5)%1;if(cycle>.42)continue;const k=cycle/.42,x=(i?w*1.1-k*w*1.3:-w*.1+k*w*1.3),y=h*(.18+i*.42)+Math.sin(k*Math.PI*2+i)*40,size=season==='winter'?90:season==='autumn'?60:70,rot=k*4+i;
        ctx.globalAlpha=(phase==='night'?.35:.6)*Math.sin(k*Math.PI);const c=Math.cos(rot),sn=Math.sin(rot);ctx.setTransform(c*f,sn*f,-sn*f,c*f,x*f,y*f);ctx.drawImage(cv,-size/2,-size/2,size,size);
      }
      ctx.setTransform(f,0,0,f,0,0);ctx.globalAlpha=1;
    }
    function blurred(key){let cv=tints.get('blur|'+key);if(cv)return cv;const src=tw(key)||sprite(key);if(!src)return null;cv=document.createElement('canvas');cv.width=src.width+24;cv.height=src.height+24;const c=cv.getContext('2d');try{c.filter='blur(6px)'}catch(_){}c.drawImage(src,12,12);tints.set('blur|'+key,cv);return cv}
    function drawLightning(m,phase){
      if(lightning<=0)return;const pulse=lightning*(.7+.3*Math.sin(lightning*19)**2);
      bg.globalCompositeOperation='screen';bg.fillStyle=`rgba(190,210,240,${pulse*.28})`;bg.fillRect(0,0,w,h);bg.globalCompositeOperation='source-over';
      glow(bg,w*boltX,h*.1,w*.8,'205,222,255',pulse*.3);
    }
    // Glass / optical signatures: hail cracks, frost growth, heat shimmer, fog breath.
    function drawSignature(m,phase,season){
      if(signatureMode!==m){signatureMode=m;signatureStart=time;lastIceStrike=-1}
      if(!allowed('weather')||reduced.matches)return;
      const t=time-signatureStart,c=ctx,night=phase==='night',seed=n=>{const v=Math.sin(n*127.1+311.7)*43758.5453;return v-Math.floor(v)};
      const temp=sceneTemp(m);
      if(m==='hail'){
        const cycle=Math.floor(t/6.4),age=t%6.4-1.25,x=w*(cycle%2?.73:.24),y=h*(.28+seed(cycle+4)*.35),radius=Math.min(w*.55,225),impact=sprite('impact');
        if(age>-.45&&age<0){const q=(age+.45)/.45,r=3+q*q*23;glow(c,x,y,r*1.8,'211,239,255',.35);c.fillStyle='#e8f6fb';c.beginPath();c.arc(x,y,r,0,TAU);c.fill();c.fillStyle='rgba(255,255,255,.9)';c.beginPath();c.arc(x-r*.3,y-r*.35,r*.3,0,TAU);c.fill()}
        if(age>=0&&cycle!==lastIceStrike){lastIceStrike=cycle;if(root.WxSfx&&root.WxSfx.triggerIceImpact)root.WxSfx.triggerIceImpact((x/w-.5)*1.4)}
        if(age>=0&&age<4.6){
          const grow=Math.min(1,age*8),fade=Math.min(1,(4.6-age)/1.3);
          if(impact){c.save();c.beginPath();c.arc(x,y,radius*1.7*grow,0,TAU);c.clip();c.globalAlpha=fade*.85;c.drawImage(impact,x-radius*.75,y-radius,radius*2,radius*2);c.restore()}
          if(age<.9)for(let k=0;k<14;k++){const a=k*TAU/14+seed(cycle)*2,dist=age*(45+seed(k+7)*120),sx=x+Math.cos(a)*dist,sy=y+Math.sin(a)*dist+age*age*110;c.globalAlpha=(1-age/.9)*.8;c.fillStyle='#d9f2ff';c.beginPath();c.arc(sx,sy,2,0,TAU);c.fill()}
          c.globalAlpha=fade*Math.max(0,1-age*2.7);c.strokeStyle='#e8faff';c.lineWidth=2;c.beginPath();c.arc(x,y,12+age*120,0,TAU);c.stroke();c.globalAlpha=1;
        }
      }
      const frosty=m==='cold'||m==='snow'||(season==='winter'&&temp!==null&&temp<=12);
      if(frosty){
        const growth=Math.min(1,t/9),frost=sprite('frost');
        if(frost){c.save();c.globalAlpha=(m==='snow'?.55:.8)*Math.min(1,t/2)*(m==='cold'||m==='snow'?1:.55);const reach=w*(.3+.7*growth);c.beginPath();c.rect(0,0,reach*.5,h);c.rect(w-reach*.5,0,reach*.5,h);c.clip();c.drawImage(frost,0,0,w,h);c.restore()}
        if(m==='cold'){const breath=(t%8)/8;glow(c,w*.48,h*.81-breath*60,w*(.2+breath*.45),'211,236,244',Math.sin(breath*Math.PI)*.2)}
      }
      if(m==='heat'||(season==='summer'&&temp!==null&&temp>=32&&!rainMode())){
        const d=sky.width/w,strength=m==='heat'?1:.5,rows=effLevel()==='subtle'?10:20;
        for(let j=0;j<rows;j++){const y=h*.5+j*h*.023,dy=h*.024,offset=Math.sin(t*1.8+j*.7)*2.2*strength;c.globalAlpha=.34;c.drawImage(sky,0,y*d,sky.width,dy*d,offset,y,w,dy+1)}c.globalAlpha=1;
        if(m==='heat')glow(c,w*.8,h*.05,w,'255,153,57',.12);
      }
      if(m==='typhoon'){const surge=Math.pow((Math.sin(t*1.25)+1)/2,3);c.fillStyle=`rgba(24,47,61,${.1+surge*.16})`;c.fillRect(0,0,w,h);if(!(root.SkyGL&&root.SkyGL.ok))for(let i=0;i<7;i++){const x=((t*(190+i*17)+i*163)%(w+650))-650,y=(i*139)%h;c.save();c.translate(x,y);c.rotate(-.24);c.fillStyle=`rgba(193,218,229,${.07+surge*.12})`;c.fillRect(0,20,650,45);c.restore()}}
      if(m==='fog'){for(let i=0;i<4;i++){const x=Math.sin(t*.07+i*1.7)*w*.6+w*.5,y=h*(.15+i*.22);c.save();c.scale(1,.5);glow(c,x,y*2,w*.9,night?'153,185,206':'229,241,246',.22);c.restore()}}
      if(m==='earthquake'&&preview){
        const elapsed=(performance.now()-previewStarted)/1000,envelope=Math.min(1,elapsed/1.5)*Math.max(0,1-elapsed/20),amp=(effLevel()==='subtle'?1:effLevel()==='rich'?5.5:3.5)*envelope;
        document.documentElement.style.setProperty('--quake-x',(Math.sin(time*31)+Math.sin(time*43)*.35)*amp+'px');document.documentElement.style.setProperty('--quake-y',Math.sin(time*37)*amp*.55+'px');
        for(let i=0;i<30;i++){const z=(i%9+1)/9,x=(i*81+Math.sin(time*.6+i)*18)%w,y=(h-i*29-time*(3+z*11))%h;glow(c,x,y,1.5+z*4,night?'170,182,194':'132,118,99',.34*envelope)}
      }
    }

    // ---- HUD / captions / evidence ----
    function evidenceLabel(){
      if(preview)return '手動預覽 · 非當地實況';
      const state=details.context,zh=document.documentElement.lang!=='id';
      if(!state||activeMode()==='none'){return state?.reason==='measured-dry'&&Date.now()<=state.expiresAt?'日夜與季節光景運作中 · 近站無雨；其他天氣待確認':'日夜與季節光景運作中 · 等待當地天氣資料'}
      const date=new Date(state.sourceTime),hm=date.toLocaleTimeString([], {hour:'2-digit',minute:'2-digit'});
      if(state.reason==='model-forecast')return (zh?'氣象模式推估 ':'Model ')+hm+(zh?' · 非測站實測，生態為季節示意':' · bukan observasi stasiun');
      if(state.reason==='measured-dry-model')return (zh?'近站無雨 ':'Stasiun kering ')+hm+(zh?' · 其他天氣依氣象模式推估':' · lainnya dari model');
      return (zh?'近站實測 ':'Observasi ')+hm+(zh?' · 生態為季節示意':' · satwa ilustrasi musim');
    }
    function updateHud(){
      const perf=sessionLevel&&sessionLevel!==level?(document.documentElement.lang!=='id'?' · 已自動降低動畫密度':' · kepadatan diturunkan otomatis'):'';
      document.querySelectorAll('[data-nature-evidence]').forEach(el=>{const text=evidenceLabel()+perf;if(el.textContent!==text)el.textContent=text});
      const active=!!preview&&performance.now()<previewUntil,zh=document.documentElement.lang!=='id';
      if(!hud){hud=document.createElement('aside');hud.className='nature-preview-hud';hud.setAttribute('aria-label',zh?'情境預覽控制':'Kontrol pratinjau');hud.innerHTML='<span></span><button type="button"></button>';hud.querySelector('button').onclick=stopPreview;
        const toggle=document.createElement('label');toggle.className='theatre-toggle';toggle.innerHTML='<input type="checkbox" aria-label="顯示操作介面"><i></i><em></em>';toggle.querySelector('input').onchange=e=>{theatre=!e.target.checked;updateHud();layoutDirty=true};hud.insertBefore(toggle,hud.lastChild);
        const lighting=document.createElement('select');lighting.className='scene-lighting';lighting.setAttribute('aria-label','預覽光線');lighting.innerHTML='<option value="auto">目前光線</option><option value="day">白天</option><option value="night">夜晚</option>';lighting.onchange=e=>{sceneLighting=e.target.value;gradeKey='';syncSound();resume()};hud.insertBefore(lighting,hud.lastChild);document.body.appendChild(hud);
        sceneCaption=document.createElement('div');sceneCaption.className='theatre-caption';sceneCaption.setAttribute('aria-hidden','true');document.body.appendChild(sceneCaption)}
      hud.hidden=!active;const pure=active&&theatre&&!seasonPreview;
      document.body.classList.toggle('scene-theatre',pure);
      const app=document.getElementById('app');if(app)app.inert=pure;
      if(sceneCaption)sceneCaption.hidden=!pure;
      if(!active)return;
      hud.querySelector('.scene-lighting').value=sceneLighting;const optionText=zh?['目前光線','白天','夜晚']:['Saat ini','Siang','Malam'];hud.querySelectorAll('.scene-lighting option').forEach((o,i)=>o.textContent=optionText[i]);hud.querySelector('input').checked=!theatre;hud.querySelector('em').textContent=zh?'顯示介面':'Antarmuka';
      const descriptions={hail:'冰粒逼近 · 撞擊 · 玻璃裂隙',cold:'冷凝水氣 · 霜晶緩慢蔓延',typhoon:'低雲疾行 · 陣風雨牆',heavy:'密集降雨 · 水珠匯流 · 濺水',storm:'積雨雲 · 閃電與延遲雷聲',snow:'遠近雪晶 · 緩慢飄落',fog:'低空霧帶 · 景深消退',heat:'灼熱日輪 · 熱氣擾動',clear:'日輪光束 · 雲影緩移',cloud:'多層雲影 · 光線明滅',wind:'陣風 · 近景飛葉',drizzle:'細雨落下 · 玻璃凝珠',rain:'斜落雨絲 · 水珠滑落',earthquake:'地面震動 · 微塵揚起'};
      if(sceneIsNight(new Date().getHours()<5||new Date().getHours()>=19)){descriptions.clear='月相與星空 · 夜間景深';descriptions.heat='暖夜 · 熱氣擾動'}
      sceneCaption.innerHTML='<small>ATMOSPHERE / '+(zh?'情境模擬':'SIMULASI')+'</small><strong>'+(labels[activeMode()]||labels.clear)[zh?0:1]+'</strong><p>'+(zh?(descriptions[activeMode()]||''): 'Cahaya · gerakan · suara')+'</p><span>'+(zh?'合成景片與攝影去背素材 × 模擬動態，非當地即時攝影':'Latar sintetis & foto; bukan kamera langsung')+'</span>';
      const label=(labels[activeMode()]||labels.clear)[zh?0:1],seconds=Math.max(0,Math.ceil((previewUntil-performance.now())/1000));
      const text=zh?`${label} · 情境示意 ${seconds}s${activeMode()==='earthquake'?' · 非即時警報':''}`:`${label} · Simulasi ${seconds}s${activeMode()==='earthquake'?' · Bukan peringatan langsung':''}`;
      if(hud.firstChild.textContent!==text)hud.firstChild.textContent=text;hud.lastChild.textContent=zh?'結束預覽':'Akhiri';
    }

    // ---- frame ----
    function draw(dt){
      if(!ctx)return;
      const on=['weather','animals','seasonal'].some(allowed);
      if(preview&&(!on||!seasonPreview&&!allowed('weather'))){stopPreview();return}
      canvas.style.display=on?'':'none';sky.style.display=on?'':'none';if(root.SkyGL&&root.SkyGL.ok)root.SkyGL.show(on);
      if(!on){ctx.clearRect(0,0,w,h);bg.clearRect(0,0,w,h);for(const v of views)labelScene(v,'none',seasonName());return}
      const m=activeMode(),d=new Date(),hour=d.getHours()+d.getMinutes()/60,name=seasonName();
      solar=solarPosition(d.getTime(),details.lat,details.lon);
      const phase=lightPhase(hour),cover=cloudCover(m),hum=sceneHumidity();
      const sceneWind=preview&&m==='typhoon'?110:preview&&m==='wind'?45:wind;
      interactionWind*=Math.exp(-dt*.6);gust+=(Math.min(52,Math.max(0,sceneWind)/3.6)*(1+.2*Math.sin(time*.31)+.1*Math.sin(time*.93))+interactionWind-gust)*Math.min(1,dt*1.3);
      cloudBlend+=(cover-cloudBlend)*Math.min(1,Math.max(.02,dt*.45));
      lightning=Math.max(0,lightning-dt*1.6);
      if(!reduced.matches&&allowed('weather')&&['storm','typhoon'].includes(m)&&time>nextLightning){lightning=1;boltIndex=(boltIndex+1)%4;boltX=rand(.2,.8);nextLightning=time+rand(7,16);clearTimeout(thunderTimer);thunderTimer=setTimeout(()=>{if(['storm','typhoon'].includes(activeMode())&&!document.hidden&&allowed('weather')&&root.WxSfx)root.WxSfx.triggerThunder()},600+Math.random()*900)}
      tilt.x+=(tilt.tx-tilt.x)*Math.min(1,dt*3);tilt.y+=(tilt.ty-tilt.y)*Math.min(1,dt*3);
      if(time-chromeTick>.5||chromeTick<0){chromeTick=time;syncDock();syncChrome(phase,m)}
      document.body.dataset.season=name;document.body.dataset.lightPhase=phase==='dusk'?'golden':phase;document.body.dataset.atmosphere=m;document.body.dataset.fxQuality=effLevel();
      if(time-lastLightUpdate>1||lastLightUpdate<0){lastLightUpdate=time;document.documentElement.style.setProperty('--sun-x',Math.round(clamp((hour-5)/14,0,1)*70+15)+'%')}
      if(m!=='earthquake'){document.documentElement.style.setProperty('--quake-x','0px');document.documentElement.style.setProperty('--quake-y','0px')}
      // background plane
      bg.setTransform(dprSky,0,0,dprSky,0,0);bg.clearRect(0,0,w,h);
      const weather=allowed('weather');
      // v342: procedural WebGL sky (sun, twilight, stars, moon, lit clouds, ridges) when available;
      // the v341 plate + sprite path stays as the fallback for devices without WebGL.
      const glSky=root.SkyGL&&root.SkyGL.ok&&root.SkyGL.render({time,elevation:sceneElevation(phase,hour),azimuth:solar?solar.azimuth:fallbackAzimuth(hour),hourAngle:solar?solar.hourAngle:(hour-12)*Math.PI/12,moonPhase:moonPhase(Date.now()),cover:weather?cloudBlend:.15,wind:weather?gust*3.6:8,mode:weather?m:'clear',season:name,tilt,level:effLevel(),rain:weather&&rainModes.includes(m)?rainIntensity(m):0,slant:windDir()*clamp(gust/34+(m==='typhoon'?Math.sin(time*1.4)*.22:0),.03,m==='typhoon'?1.6:.9),flash:weather?lightning:0,boltX,boltSeed:boltIndex,glass:!reduced.matches&&allowed('weather'),heat:weather&&m==='heat'?1:0,fog:weather&&m!=='fog'&&known(hum)&&hum>=92&&!rainModes.includes(m)?.25:0});
      if(!glSky){drawPlates(weather?m:'none',phase,name,dt);drawGrade(weather?m:'none',phase,name)}
      if(weather){if(!glSky){drawCelestial(m,phase,hour,cloudBlend,dt);drawClouds(m,phase,cloudBlend,dt)}if(!glSky){drawFogBanks(m,phase,hum);drawLightning(m,phase)}}
      else if(!glSky)drawCelestial('clear',phase,hour,.15,dt);
      // v343: every effect is painted on the front plane (above the app) so it is actually seen;
      // the text-protection mask cuts it away wherever there is copy, panels or controls.
      ctx.setTransform(dprFx,0,0,dprFx,0,0);ctx.clearRect(0,0,w,h);
      drawBotany(m,name,phase);
      if(!reduced.matches){drawSeasonDrift(dt,m,name,phase);drawRain(dt,m,phase);drawSnow(dt,m,phase);drawHail(dt,m,phase);drawAnimals(m,name,phase,dt)}
      drawSignature(weather?m:'none',phase,name);
      if(!reduced.matches){drawBeads(dt,m);drawButterflies(m,name,phase);drawNearBokeh(dt,m,name,phase)}
      if(lightning>.35&&!glSky){const cv=sprite('bolt-'+boltIndex);if(cv){const bh=h*.55,bw=bh*cv.width/cv.height;ctx.globalAlpha=Math.min(1,(lightning-.35)*2.5)*.95;ctx.drawImage(cv,w*boltX-bw/2,h*.02,bw,bh);ctx.globalAlpha=1}}
      updateHud();
      if(layoutDirty&&(time-maskTick>.3||maskTick<0)){scanLayout();buildMask()}else if(scrollDirty&&time-maskTick>.12)buildMask();
      if(!document.body.classList.contains('scene-theatre')){ctx.globalCompositeOperation='destination-out';ctx.drawImage(mask,0,0,w,h);ctx.globalCompositeOperation='source-over'}
      if(time-viewTick>.25||viewTick<0){viewTick=time;for(const v of views){v.rect=v.cv.getBoundingClientRect();paintScene(v,m,phase)}}for(const v of views)labelScene(v,m,name);
    }
    // Sliding glass pill under the active dock item.
    function syncDock(){
      const nav=document.querySelector('.bottom-nav');if(!nav)return;
      if(!pill||!nav.contains(pill)){pill=document.createElement('span');pill.className='dock-pill';pill.setAttribute('aria-hidden','true');nav.prepend(pill);nav.classList.add('has-pill')}
      const a=nav.querySelector('.bottom-nav-item.active');if(!a){pill.style.opacity='0';return}
      const nr=nav.getBoundingClientRect(),ar=a.getBoundingClientRect(),x=ar.left-nr.left-1,wd=ar.width;
      if(pill.dataset.x!==String(Math.round(x))||pill.dataset.w!==String(Math.round(wd))){pill.dataset.x=Math.round(x);pill.dataset.w=Math.round(wd);pill.style.width=wd+'px';pill.style.transform=`translateX(${x}px)`;pill.style.opacity='1'}
    }
    // Status-bar colour follows the sky so the phone chrome is part of the scene.
    function syncChrome(phase,m){
      const meta=document.querySelector('meta[name="theme-color"]');if(!meta)return;
      const stormy=['storm','typhoon','hail','heavy'].includes(m),c=phase==='night'?'#0a1726':phase==='golden'||phase==='dusk'?'#5d5266':stormy?'#3c4a57':['rain','drizzle','fog','cloud'].includes(m)?'#7d8b96':'#6e9cc0';
      document.documentElement.dataset.sceneChrome='1';if(meta.getAttribute('content')!==c)meta.setAttribute('content',c);
    }
    function onBackground(fn){const fc=ctx,fd=dprFx;ctx=bg;dprFx=dprSky;try{fn()}finally{ctx=fc;dprFx=fd;bg.setTransform(dprSky,0,0,dprSky,0,0);bg.globalAlpha=1}}
    // Adaptive budget: a slow phone drops to a lighter density for the session instead of freezing.
    function budgetFrame(ms){
      budget.ema+=(ms-budget.ema)*.08;const order=['subtle','balanced','rich'],idx=order.indexOf(effLevel());
      if(budget.ema>24){budget.high+=1;budget.low=0;if(budget.high>45&&idx>0){sessionLevel=order[idx-1];budget.high=0;budget.ema=12;resize()}}
      else if(budget.ema<9){budget.low+=1;budget.high=0;if(budget.low>900&&sessionLevel&&idx<order.indexOf(level)){sessionLevel=order[idx+1]===level?'':order[idx+1];budget.low=0;resize()}}
      else{budget.high=Math.max(0,budget.high-1)}
    }
    function tick(now){
      raf=0;if(document.hidden||reduced.matches||!['weather','animals','seasonal'].some(allowed))return;
      // Compositor cost (backdrop blur, GPU) is invisible to script timing; watch the real frame interval too.
      if(last){const gap=now-last;if(gap<250){realFrame.ema+=(gap-realFrame.ema)*.05;if(realFrame.ema>48){if(++realFrame.slow>90){realFrame.slow=0;realFrame.ema=33;const order=['subtle','balanced','rich'],idx=order.indexOf(effLevel());if(idx>0){sessionLevel=order[idx-1];resize()}}}else realFrame.slow=Math.max(0,realFrame.slow-2)}}
      const elapsed=last?Math.min((now-last)/1000,.08):0;last=now;accum+=elapsed;
      const rate=effLevel()==='subtle'?24:30;
      if(accum>=1/rate){const t0=performance.now();time+=accum;try{draw(accum);failures=0}catch(e){if(failures++<3)console.log('nature fx frame error',e);if(failures>60){console.log('nature fx stopped after repeated errors');return}}budgetFrame(performance.now()-t0);accum=0;frameCount++}
      raf=requestAnimationFrame(tick);
    }
    function resume(){
      if(raf)cancelAnimationFrame(raf);raf=0;last=0;if(!canvas)return;
      try{draw(0)}catch(e){console.log('nature fx draw error',e)}
      if(!document.hidden&&!reduced.matches&&['weather','animals','seasonal'].some(allowed))raf=requestAnimationFrame(tick);
      if(root.WxSfx&&root.WxSfx.refresh)root.WxSfx.refresh();
    }
    function syncSound(){if(root.WxSfx){root.WxSfx.setMode(activeMode());if(root.WxSfx.setScene)root.WxSfx.setScene({season:seasonName(),animalsEligible:sceneContext(activeMode(),seasonName(),sceneIsNight(false)).animals.length>0,wind,night:sceneIsNight(new Date().getHours()<5||new Date().getHours()>=19),preview:!!preview})}}
    function update(c,t,p,v,extra={}){code=c;details=extra||{};weatherState=visualState(c,details);temp=Number(t)||0;wind=Number(v)||0;mode=extra.context?extra.context.mode:extra.typhoon&&['rain','heavy','storm'].includes(classify(c,temp,wind))?'typhoon':classify(c,temp,wind);gradeKey='';init();syncSound();resume()}
    function setQuality(value){if(!['subtle','balanced','rich'].includes(value))return;level=value;sessionLevel='';budget={ema:12,low:0,high:0};try{localStorage.setItem('nature_quality',level)}catch(e){}resize();resume()}
    function previewMode(value){returnFocus=document.activeElement;if(!previewModes.includes(value)||!allowed('weather'))return;init();preview=value;seasonPreview=null;if(value==='typhoon')gust=Math.max(gust,30);signatureMode='';gradeKey='';previewStarted=performance.now();previewUntil=performance.now()+20000;previewToken++;nextLightning=time+1.7;schedulePreviewEnd(previewToken);syncSound();resume();if(hud)hud.querySelector('button').focus({preventScroll:true})}
    function previewSeason(value){
      if(!['spring','summer','autumn','winter'].includes(value))return;
      seasonPreview=value;preview=mode==='none'?'clear':mode;signatureMode='';gradeKey='';previewStarted=performance.now();previewUntil=performance.now()+20000;previewToken++;
      schedulePreviewEnd(previewToken);syncSound();resume();
    }
    function stopPreview(){clearTimeout(previewTimer);clearInterval(hudTimer);previewToken++;preview=null;seasonPreview=null;previewUntil=0;gradeKey='';document.querySelectorAll('.nature-controls .is-preview').forEach(el=>el.classList.remove('is-preview'));document.querySelectorAll('.nature-previews button,.nature-seasons button').forEach(el=>el.setAttribute('aria-pressed','false'));document.querySelectorAll('.nature-preview-state').forEach(el=>el.textContent='');document.documentElement.style.setProperty('--quake-x','0px');document.documentElement.style.setProperty('--quake-y','0px');syncSound();resume();updateHud();if(returnFocus&&returnFocus.isConnected)returnFocus.focus({preventScroll:true});returnFocus=null}
    function schedulePreviewEnd(ticket){clearInterval(hudTimer);hudTimer=setInterval(updateHud,1000);clearTimeout(previewTimer);previewTimer=setTimeout(()=>{if(ticket===previewToken)stopPreview()},20050)}

    // Optional in-card scene canvases (hidden by current CSS); kept lightweight.
    function readViews(){
      views=[];
      // v343: the card's .scene-canvas is shown again (immersive-v341.css) and painted with a crop of the live
      // scene at its screen position a few times a second; without it the card only showed the blurred panel behind it.
      document.querySelectorAll('.scene-canvas').forEach(cv=>{
        const r=cv.getBoundingClientRect();if(r.width<1||r.height<1)return;
        const cw=Math.round(r.width),ch=Math.round(r.height);
        if(cv.width!==cw||cv.height!==ch){cv.width=cw;cv.height=ch}
        views.push({cv,c:cv.getContext('2d'),w:r.width,h:r.height,dpr:1,rect:r});
      });
    }
    function labelScene(v,m,season){
      const parent=v.cv.closest('.atmosphere-view');if(!parent)return;const zh=document.documentElement.lang!=='id',active=preview&&performance.now()<previewUntil;
      const names={spring:['春季','Musim semi'],summer:['夏季','Musim panas'],autumn:['秋季','Musim gugur'],winter:['冬季','Musim dingin']},modes={clear:['晴空','Cerah'],cloud:['雲層','Awan'],rain:['雨幕','Hujan'],heavy:['大雨','Hujan lebat'],storm:['雷雨','Badai'],wind:['風葉','Angin'],snow:['降雪','Salju'],fog:['霧景','Kabut'],cold:['冷空氣','Dingin'],heat:['盛夏暖光','Cahaya hangat'],none:['光景','Suasana']};
      for(const [selector,value] of [['[data-scene-season]',names[season][zh?0:1]+' · '+(zh?'示意預覽':'Pratinjau')],['[data-scene-weather]',seasonPreview?(zh?'四季光景':'Suasana musim'):(modes[m]||modes.none)[zh?0:1]],['[data-scene-temperature]',zh?'查看天氣':'Lihat cuaca']]){
        const el=parent.querySelector(selector);if(!el)continue;const text=active?value:el.dataset.liveText;if(el.textContent!==text)el.textContent=text;
      }
    }
    function paintScene(v,m,phase){
      if(v.rect.bottom<0||v.rect.top>h)return;const c=v.c,W=v.w,H=v.h;c.setTransform(1,0,0,1,0,0);c.clearRect(0,0,W,H);
      const g=root.SkyGL&&root.SkyGL.ok&&root.SkyGL.canvas();
      if(g){try{const r=v.rect,kx=g.width/w,ky=g.height/h;c.drawImage(g,r.left*kx,r.top*ky,r.width*kx,r.height*ky,0,0,W,H);c.drawImage(sky,r.left*dprSky,r.top*dprSky,r.width*dprSky,r.height*dprSky,0,0,W,H);return}catch(e){}}
      const im=plate(plateKey);if(im){c.drawImage(im,...coverRect(im,W,H))}else{c.fillStyle=phase==='night'?'#0f2238':'#7aa3bf';c.fillRect(0,0,W,H)}
    }

    return{update,stopPreview,previewModes:()=>previewModes.slice(),previewSeason,getSeason:seasonName,getMode:activeMode,refresh:resume,setQuality,getQuality:()=>level,preview:previewMode,_forceSilence(){if(root.WxSfx)root.WxSfx._forceSilence()},
      _debug:()=>({mode,active:activeMode(),frames:frameCount,running:!!raf,quality:level,effectiveQuality:effLevel(),frameMs:Math.round(budget.ema*10)/10,frameInterval:Math.round(realFrame.ema),tilt:{x:tilt.x,y:tilt.y},cloud:weatherState.cloud,intensity:weatherState.intensity,solar,reduced:reduced.matches,sceneLighting,plateCacheSize:plates.size,spriteCount:sprites.size,canvasCount:document.querySelectorAll('#wxfx').length,protectedRects:quiet.length,views:views.length,season:seasonName(),plate:plateKey,skyGL:root.SkyGL?root.SkyGL._debug():null,evidence:details.context||null,gust,preview:!!preview,dpr:{fx:dprFx,sky:dprSky}})};
  }};
})(window);
