/* v343 — procedural WebGL sky (back plane) and front plane: rain, refracting drops, lightning, fog, heat shimmer.
   Replaces the five static plates, cloud sprites, sun/moon/star sprites of the v341 background.
   The sky is computed per pixel from the real solar elevation/azimuth, cloud cover, wind and
   weather family; clouds are FBM noise lit by the sun so they drift, thicken and darken instead
   of being pasted bitmaps. Rain, snow, botany, animals, glass effects stay in nature-effects.js.
   Stage 2: rain streaks and rain haze in the sky pass; lightning is a procedural bolt that lights the
   cloud field from its position; drops on the glass (front plane) refract the rendered sky behind them
   and respect the text-protection mask from nature-effects.js.
   Fails closed: any WebGL problem sets SkyGL.ok=false and nature-effects falls back to v341 plates.
   No weather data, account or pay state is read or written here. */
(function(root){
  'use strict';
  const VERT='attribute vec2 p;void main(){gl_Position=vec4(p,0.,1.);}';
  // noise + rain + bolt helpers shared by the front (glass) shader
  const NOISE=`
float hash(vec2 p){p=fract(p*.3183099+vec2(.71,.113));p*=17.;return fract(p.x*p.y*(p.x+p.y));}
float noise(vec2 p){vec2 i=floor(p),f=fract(p);f=f*f*(3.-2.*f);
 return mix(mix(hash(i),hash(i+vec2(1,0)),f.x),mix(hash(i+vec2(0,1)),hash(i+vec2(1,1)),f.x),f.y);}
float fbm(vec2 p,float oct){float v=0.,a=.5;mat2 m=mat2(1.6,1.2,-1.2,1.6);
 for(int i=0;i<5;i++){if(float(i)>=oct)break;v+=a*noise(p);p=m*p+vec2(17.3,9.1);a*=.5;}return v;}
`;
  const FRONT_HELPERS=`
// One sheet of rain: tall cells, a streak per cell, falling with slant. Returns coverage 0..1.
float rainSheet(vec2 uv,float scale,float speed,float t,float seed){
 vec2 p=uv*vec2(scale,scale*.18);p.x+=p.y*uSlant*.6;p.y+=t*speed;
 vec2 c=floor(p),f=fract(p);float h=hash(c+seed);if(h<.7)return 0.;
 float x=mix(.2,.8,hash(c+seed+7.));float len=mix(.3,.9,hash(c+seed+3.));
 float d=abs(f.x-x)*scale*.05;float along=smoothstep(.0,.15,f.y)*smoothstep(len,len-.2,f.y);
 return smoothstep(.012,.0,d)*along*(.35+.65*(h-.7)*3.3);}
// Procedural bolt in screen space; fades inside thick cloud. Returns core, glow in x,y.
vec2 bolt(vec2 uv,float asp){
 if(uFlash<=0.)return vec2(0.);
 float y=uv.y,bottom=.42+.1*hash(vec2(uSeed,2.));
 float path=uBoltX+(fbm(vec2(y*7.+uSeed*3.,uSeed),4.)-.5)*.14*(1.-y)+(noise(vec2(y*31.+uSeed,uSeed*5.))-.5)*.03;
 float d=abs(uv.x-path)*asp;float span=smoothstep(bottom,bottom+.06,y)*smoothstep(1.,.96,y);
 float core=exp(-d*260.)*span,glow=exp(-d*14.)*span*.35;
 // one branch
 float by=.62+.15*hash(vec2(uSeed,9.));float bp=path+(y-by)*(.5+.4*hash(vec2(uSeed,4.)))*sign(hash(vec2(uSeed,6.))-.5)+(noise(vec2(y*23.,uSeed))-.5)*.03;
 float bd=abs(uv.x-bp)*asp;float bspan=smoothstep(by-.2,by-.14,y)*smoothstep(by+.01,by-.01,y);
 core+=exp(-bd*300.)*bspan*.7;glow+=exp(-bd*18.)*bspan*.2;
 return vec2(core,glow)*uFlash;}
`;
  const FRAG=`
#ifdef GL_FRAGMENT_PRECISION_HIGH
precision highp float;
#else
precision mediump float;
#endif
uniform vec2 uRes;uniform float uTime;uniform vec3 uSun;uniform float uEl;
uniform float uCover;uniform float uWind;uniform float uStorm;uniform float uWet;uniform float uFog;
uniform float uMoonPhase;uniform vec3 uMoon;uniform vec3 uTint;uniform vec2 uTilt;uniform float uOct;uniform float uHeat;uniform float uCold;
uniform float uRain;uniform float uSlant;uniform float uFlash;uniform float uBoltX;uniform float uSeed;
float hash(vec2 p){p=fract(p*.3183099+vec2(.71,.113));p*=17.;return fract(p.x*p.y*(p.x+p.y));}
float noise(vec2 p){vec2 i=floor(p),f=fract(p);f=f*f*(3.-2.*f);
 return mix(mix(hash(i),hash(i+vec2(1,0)),f.x),mix(hash(i+vec2(0,1)),hash(i+vec2(1,1)),f.x),f.y);}
float fbm(vec2 p,float oct){float v=0.,a=.5;mat2 m=mat2(1.6,1.2,-1.2,1.6);
 for(int i=0;i<5;i++){if(float(i)>=oct)break;v+=a*noise(p);p=m*p+vec2(17.3,9.1);a*=.5;}return v;}
float star(vec3 rd,float t){vec2 q=rd.xz/(rd.y+.25)*42.;vec2 c=floor(q),f=fract(q)-.5;float h=hash(c);
 if(h<.93)return 0.;vec2 o=vec2(hash(c+1.7),hash(c+3.1))-.5;float d=length(f-o*.7);
 float tw=.6+.4*sin(t*(1.+h*3.)+h*40.);return smoothstep(.07,.0,d)*tw*(.2+.8*(h-.93)*14.);}
void main(){
 vec2 uv=gl_FragCoord.xy/uRes;float asp=uRes.x/uRes.y;
 // Camera facing south; horizon at 28% from the bottom, same framing as v341 plates.
 vec3 rd=normalize(vec3((uv.x-.5)*asp*1.05+uTilt.x*.03,(uv.y-.28)*1.25-uTilt.y*.03,1.));
 float el=uEl;                       // sun elevation, degrees
 float day=smoothstep(-8.,12.,el);   // 0 night -> 1 full day
 float dusk=exp(-pow((el-1.)/7.,2.)); // twilight band around the horizon
 float night=1.-smoothstep(-12.,-3.,el);
 float sunDot=max(dot(rd,uSun),0.);
 float horizon=1.-clamp(rd.y,0.,1.);
 // sky colours
 vec3 zenithDay=vec3(.19,.43,.78),horDay=vec3(.72,.84,.93);
 vec3 zenithNight=vec3(.03,.05,.11),horNight=vec3(.09,.12,.2);
 vec3 zenith=mix(zenithNight,zenithDay,day),hor=mix(horNight,horDay,day);
 vec3 sky=mix(zenith,hor,pow(horizon,1.9));
 // twilight: warm band where the sun sits, violet opposite
 float sunSide=.5+.5*dot(normalize(vec2(rd.x,rd.z)),normalize(vec2(uSun.x,uSun.z)+1e-4));
 vec3 duskCol=mix(vec3(.32,.22,.46),vec3(1.,.55,.28),sunSide);
 sky=mix(sky,duskCol,dusk*pow(horizon,2.2)*.85);
 sky=mix(sky,vec3(1.,.74,.42),dusk*pow(sunDot,18.)*.6);
 // haze & sun glow (Mie)
 sky+=vec3(1.,.95,.85)*pow(sunDot,6.)*.18*day;
 sky+=vec3(1.,.9,.7)*pow(sunDot,120.)*1.4*day;
 sky+=vec3(.7,.78,1.)*uFlash*.03;
 // weather darkening of the whole dome
 vec3 overcast=mix(vec3(.46,.52,.58),vec3(.22,.26,.31),uStorm);
 overcast=mix(overcast*.12,overcast,day);
 sky=mix(sky,overcast,smoothstep(.55,1.,uCover)*.6);
 // stars + moon
 float starV=night*(1.-uCover)*(1.-uFog);
 if(starV>0.&&rd.y>0.)sky+=vec3(.9,.95,1.)*star(rd,uTime)*starV;
 float moonDot=dot(rd,uMoon);
 if(uMoon.y>-.05){
  float disc=smoothstep(.99945,.9997,moonDot);
  // phase: lit side faces the sun direction
  vec3 side=normalize(cross(uMoon,vec3(0.,1.,0.)));float ph=uMoonPhase;
  float u=dot(rd-uMoon*moonDot,side)/.028,k=cos(ph*6.2832);
  float lit=ph<.5?smoothstep(k-.18,k+.18,u):smoothstep(-k+.18,-k-.18,u);
  vec3 moonCol=vec3(.93,.95,.98)*(.1+.9*lit);
  sky=mix(sky,moonCol,disc*night*(1.-smoothstep(.5,.95,uCover)));
  sky+=vec3(.6,.7,.9)*pow(max(moonDot,0.),300.)*.5*night*(1.-uCover*.6);
 }
 // clouds: two slabs projected from the camera onto planes at two heights
 float cl=clamp(uCover,0.,1.);
 vec3 col=sky;
 if(rd.y>.005){
  float t=uTime*(.012+uWind*.004);
  vec2 lo=rd.xz/rd.y*1.1+vec2(t*1.6,t*.5)+uTilt*.08;
  vec2 hi=rd.xz/rd.y*2.6+vec2(t*2.3,-t*.3);
  // domain warp gives billowing edges instead of streaks
  vec2 warp=vec2(fbm(lo*.6+vec2(1.,8.),uOct-2.),fbm(lo*.6+vec2(5.,2.),uOct-2.))-.5;
  lo+=warp*.9;
  float n=fbm(lo,uOct);
  float thr=.64-cl*.52;                        // more cover => lower threshold
  float dens=smoothstep(thr,thr+.3,n);
  float thick=smoothstep(thr+.04,thr+.46,n);
  float dHi=fbm(hi+vec2(7.,3.),uOct-1.);
  float cirrus=smoothstep(.62,.9,dHi)*(.25+.4*cl)*(1.-smoothstep(.75,1.,cl));
  // lighting: density gradient toward the sun lights the sun-facing edges
  vec2 sunStep=normalize(uSun.xz+1e-4)*.07*sign(uSun.y+.05);
  float nS=fbm(lo-sunStep,uOct);
  float shade=clamp(.5+(n-nS)*7.,0.,1.);
  vec3 lit=mix(vec3(1.,.98,.95),vec3(1.,.72,.45),dusk)*mix(.3,1.,day);
  vec3 dark=mix(vec3(.58,.63,.7),vec3(.3,.34,.4),uStorm)*mix(.22,1.,day);
  dark=mix(dark,dark*.8,uWet);
  vec3 cloudCol=mix(dark,lit,shade*(1.-uStorm*.45));
  cloudCol*=mix(1.,.72,thick);                                   // thick cores shadow themselves
  cloudCol+=vec3(1.,.9,.75)*pow(sunDot,8.)*.25*day*(1.-thick);   // silver lining near the sun
  // lightning lights the cloud field from inside: strongest near the bolt and where the cloud is thick
  float lDist=length((uv-vec2(uBoltX,.78))*vec2(asp,1.4));
  cloudCol+=vec3(.78,.84,1.)*uFlash*exp(-lDist*2.4)*(.25+.75*thick)*.85;
  float fade=smoothstep(.0,.12,rd.y);                            // thin out at the horizon
  float alpha=clamp(dens*(.7+.3*cl)*fade,0.,1.);
  col=mix(col,cloudCol,alpha);
  col=mix(col,mix(lit,vec3(.75,.8,.9),.5)*mix(.3,1.,day),cirrus*fade*.6);
 } else {
  // ground: distant ridges and a haze layer, lit by the same sky
  float x=rd.x/(rd.z+.3);
  float r1=-.01+.16*(fbm(vec2(x*7.+3.,0.),3.)-.45)+uTilt.y*.01;
  float r2=-.07+.22*(fbm(vec2(x*4.2+9.,2.),3.)-.45);
  vec3 ridgeFar=mix(hor*.75,vec3(.33,.42,.47),.55)*mix(.22,1.,day);
  vec3 ridgeNear=mix(hor*.5,vec3(.19,.28,.25),.6)*mix(.18,1.,day);
  ridgeFar=mix(ridgeFar,ridgeFar*vec3(.6,.62,.66),uCover*.8);ridgeNear=mix(ridgeNear,ridgeNear*vec3(.6,.62,.66),uCover*.8);
  ridgeNear=mix(ridgeNear,ridgeNear*vec3(1.08,1.02,.9),uHeat);ridgeNear=mix(ridgeNear,ridgeNear*vec3(.9,.95,1.08),uCold);
  float g=smoothstep(.0,-.004,rd.y-r1);
  col=mix(sky,ridgeFar,g);
  col=mix(col,ridgeNear,smoothstep(.0,-.004,rd.y-r2));
  col=mix(col,hor*mix(.3,1.,day),smoothstep(-.2,.0,rd.y)*.35);
 }
 // distant rain only dims and greys the scene here; the streaks, bands and bolt are on the front plane
 if(uRain>0.)col=mix(col,mix(vec3(.08,.1,.12),vec3(.62,.67,.72),day),uRain*.22*(.3+.7*horizon));
 // fog, season tint, dither
 vec3 fogCol=mix(vec3(.08,.1,.13),vec3(.82,.85,.88),day);
 col=mix(col,fogCol,uFog*(.55+.45*horizon));
 col=mix(col,col*uTint,.22);
 col+=(hash(gl_FragCoord.xy+uTime)-.5)/255.;
 gl_FragColor=vec4(col,1.);
}`;
  const GLASS=`
#ifdef GL_FRAGMENT_PRECISION_HIGH
precision highp float;
#else
precision mediump float;
#endif
uniform vec2 uRes;uniform float uTime;uniform sampler2D uSky;uniform sampler2D uMask;uniform float uRain;uniform float uSlant;uniform float uLayers;uniform float uFlash;
uniform float uBoltX;uniform float uSeed;uniform float uDay;uniform float uFog;uniform float uHeat;
`+NOISE+FRONT_HELPERS+`
float sat(float x){return clamp(x,0.,1.);}
// One layer of drops. Tall narrow cells, at most one sliding drop per cell with a beaded trail above it.
// Returns (lens offset.xy, coverage).
vec3 drops(vec2 uv,float t,float scale,float seed){
 float colId=floor(uv.x*scale*2.6);float sp=.6+.8*hash(vec2(colId,seed));
 vec2 st=uv*vec2(scale*2.6,scale);st.y+=t*.55*sp;
 vec2 id=floor(st);float n=hash(id+seed);
 if(n<.42)return vec3(0.);
 vec2 f=fract(st)-.5;
 float x=(n-.5)*.55;
 float ti=t*1.1*sp+n*6.2832;float y=sin(ti+sin(ti+sin(ti)*.5))*.22;
 vec2 p=vec2(f.x-x,f.y-y);
 float k=5.6;                                   // physical cell height / width
 vec2 q=vec2(p.x,p.y*k*.8);float d=length(q);
 float r=.17+.08*hash(id+seed+2.);
 float main=smoothstep(r,r-.04,d);
 vec2 off=main>0.?q/r*main:vec2(0.);
 // beaded trail above the drop, narrowing with distance
 float up=sat(p.y*2.);float wdt=mix(.11,.03,up);
 float trailMask=smoothstep(wdt,wdt-.03,abs(p.x))*smoothstep(.02,.08,p.y)*step(p.y,.48);
 float by=fract(f.y*k*1.3)-.5;float bd=length(vec2(p.x*1.4,by*.5));
 float bead=smoothstep(.09,.05,bd)*trailMask*(.4+.6*hash(id+floor(f.y*k*1.3)));
 off+=vec2(p.x*1.4,by*.5)/.09*bead*.5;
 return vec3(off,sat(main+bead*.85));
}
void main(){
 vec2 uv=gl_FragCoord.xy/uRes;float asp=uRes.x/uRes.y;
 vec2 guv=vec2(uv.x+uv.y*uSlant*.18,uv.y);
 float t=uTime;
 float dens=.4+.6*uRain;
 vec3 a=uRain>0.?drops(guv,t,2.4*dens+1.2,1.):vec3(0.);
 vec3 b=uRain>0.&&uLayers>1.5?drops(guv+.31,t*1.25,4.2*dens+1.5,2.):vec3(0.);
 vec3 c=uRain>0.&&uLayers>2.5?drops(guv+.67,t*.85,6.5*dens+2.,3.):vec3(0.);
 float m=sat(a.z+b.z*.9+c.z*.75);
 vec2 off=(a.xy+b.xy*.6+c.xy*.4)*.03;
 vec3 col=vec3(0.);float alpha=0.;
 // 1. heat shimmer: a wavering copy of the scene low in the frame (hot air rising off the ground)
 if(uHeat>0.){
  float band=smoothstep(.0,.35,uv.y)*smoothstep(.62,.3,uv.y);
  vec2 wob=vec2(sin(uv.y*60.+t*5.+sin(uv.x*22.+t*3.)*.8),cos(uv.x*48.-t*4.))*.006*band;
  vec3 sh=texture2D(uSky,clamp(uv+wob,.001,.999)).rgb;
  float sa=band*.55*uHeat;col+=sh*sa;alpha+=sa;
 }
 // 2. rain streaks + typhoon bands, in front of the app
 if(uRain>0.){
  vec2 ruv=vec2(uv.x*asp,uv.y);
  float r=rainSheet(ruv,60.,2.4,t,1.)*.55+rainSheet(ruv,34.,1.7,t,2.)*.75+rainSheet(ruv,18.,1.2,t,3.);
  r=sat(r*uRain);
  float bands=abs(uSlant)>1.?smoothstep(.45,.75,fbm(vec2(uv.x*1.6+uv.y*uSlant*.5-t*.9,uv.y*.6+t*.15),3.))*.35*uRain:0.;
  vec3 rainCol=mix(vec3(.55,.6,.68),vec3(.9,.93,.96),uDay)+uFlash*.5;
  float ra=r*.7;col=col*(1.-ra)+rainCol*ra;alpha=alpha*(1.-ra)+ra;
  vec3 bandCol=mix(vec3(.3,.34,.38),vec3(.8,.84,.88),uDay);col=col*(1.-bands)+bandCol*bands;alpha=alpha*(1.-bands)+bands;
 }
 // 3. drops on the glass: refracted, slightly shrunken and inverted patch of the scene behind
 if(uRain>0.&&m>.01){
  vec3 bg=texture2D(uSky,clamp(uv-off*vec2(1.,-1.)*1.2,.001,.999)).rgb;
  // a drop is never darker than a faint grey lens: ambient fill, bright rim, upper-left highlight
  float rim=smoothstep(.45,1.,length(off)/.03);
  vec3 dc=mix(bg,vec3(.55,.62,.72),.22)*1.05+rim*.28+vec3(1.)*pow(sat(1.-length(off*vec2(1.,-1.)*28.+vec2(-.45,.35))),8.)*.7+uFlash*.25;
  float da=m*(.55+.45*uRain);col=col*(1.-da)+dc*da;alpha=alpha*(1.-da)+da;
 }
 // 4. fog: drifting mist banks between the viewer and the scene
 if(uFog>0.){
  float mist=fbm(vec2(uv.x*2.2+t*.05,uv.y*3.-t*.02),4.)*.6+fbm(vec2(uv.x*5.+t*.09,uv.y*7.),3.)*.4;
  float fa=smoothstep(.38,.72,mist)*uFog*(.4+.5*(1.-uv.y));
  vec3 fc=mix(vec3(.1,.12,.15),vec3(.86,.89,.92),uDay);col=col*(1.-fa)+fc*fa;alpha=alpha*(1.-fa)+fa;
 }
 // 5. lightning bolt in front of everything
 vec2 bl=bolt(uv,asp);
 col+=vec3(.92,.95,1.)*bl.x*1.4+vec3(.6,.7,1.)*bl.y;alpha=sat(alpha+bl.x+bl.y*.6);
 if(alpha<.003){gl_FragColor=vec4(0.);return;}
 float cover=texture2D(uMask,uv).a;
 alpha*=(1.-cover);
 gl_FragColor=vec4(col*alpha,alpha);
}`;
  let canvas=null,gl=null,prog=null,U={},w=1,h=1,scale=.66,lost=false,failed=false;
  // glass plane (front): its own context; the sky canvas is uploaded as a texture while it rains
  let gcanvas=null,ggl=null,gprog=null,GU={},gtexSky=null,gtexMask=null,gfailed=false,glassOn=false,maskSrc=null,maskDirty=true;
  const TINT={spring:[1.05,.98,1.03],summer:[.97,1.05,1.0],autumn:[1.08,.97,.86],winter:[.9,.96,1.06]};
  function compile(g,type,src){const s=g.createShader(type);g.shaderSource(s,src);g.compileShader(s);if(!g.getShaderParameter(s,g.COMPILE_STATUS)){const e=g.getShaderInfoLog(s);g.deleteShader(s);throw Error(e)}return s}
  function program(g,frag,names){const p=g.createProgram();g.attachShader(p,compile(g,g.VERTEX_SHADER,VERT));g.attachShader(p,compile(g,g.FRAGMENT_SHADER,frag));g.linkProgram(p);
    if(!g.getProgramParameter(p,g.LINK_STATUS))throw Error(g.getProgramInfoLog(p));g.useProgram(p);
    const buf=g.createBuffer();g.bindBuffer(g.ARRAY_BUFFER,buf);g.bufferData(g.ARRAY_BUFFER,new Float32Array([-1,-1,3,-1,-1,3]),g.STATIC_DRAW);
    const loc=g.getAttribLocation(p,'p');g.enableVertexAttribArray(loc);g.vertexAttribPointer(loc,2,g.FLOAT,false,0,0);
    const u={};for(const n of names)u[n]=g.getUniformLocation(p,n);return {p,u}}
  function texture(g,unit){const t=g.createTexture();g.activeTexture(g.TEXTURE0+unit);g.bindTexture(g.TEXTURE_2D,t);g.texParameteri(g.TEXTURE_2D,g.TEXTURE_MIN_FILTER,g.LINEAR);g.texParameteri(g.TEXTURE_2D,g.TEXTURE_MAG_FILTER,g.LINEAR);g.texParameteri(g.TEXTURE_2D,g.TEXTURE_WRAP_S,g.CLAMP_TO_EDGE);g.texParameteri(g.TEXTURE_2D,g.TEXTURE_WRAP_T,g.CLAMP_TO_EDGE);return t}
  function setup(){
    const opts={alpha:false,antialias:false,depth:false,stencil:false,premultipliedAlpha:false,powerPreference:'low-power',preserveDrawingBuffer:true};
    gl=canvas.getContext('webgl',{...opts,failIfMajorPerformanceCaveat:true})||canvas.getContext('webgl',opts);
    if(!gl)throw Error('no webgl');
    const r=program(gl,FRAG,['uRes','uTime','uSun','uEl','uCover','uWind','uStorm','uWet','uFog','uMoonPhase','uMoon','uTint','uTilt','uOct','uHeat','uCold','uRain','uSlant','uFlash','uBoltX','uSeed']);prog=r.p;U=r.u;
  }
  function setupGlass(){
    if(gcanvas||gfailed)return;
    try{
      gcanvas=document.createElement('canvas');gcanvas.id='nature-glass-gl';gcanvas.setAttribute('aria-hidden','true');gcanvas.style.display='none';
      const fx=document.getElementById('wxfx');if(fx)document.body.insertBefore(gcanvas,fx);else document.body.appendChild(gcanvas);
      ggl=gcanvas.getContext('webgl',{alpha:true,antialias:false,depth:false,stencil:false,premultipliedAlpha:true,powerPreference:'low-power'});
      if(!ggl)throw Error('no webgl (glass)');
      const r=program(ggl,GLASS,['uRes','uTime','uSky','uMask','uRain','uSlant','uLayers','uFlash','uBoltX','uSeed','uDay','uFog','uHeat']);gprog=r.p;GU=r.u;
      gtexSky=texture(ggl,0);gtexMask=texture(ggl,1);ggl.uniform1i(GU.uSky,0);ggl.uniform1i(GU.uMask,1);
      ggl.enable(ggl.BLEND);ggl.blendFunc(ggl.ONE,ggl.ONE_MINUS_SRC_ALPHA);ggl.pixelStorei(ggl.UNPACK_FLIP_Y_WEBGL,true);
      gcanvas.addEventListener('webglcontextlost',e=>{e.preventDefault();glassOn=false;gfailed=true;gcanvas.style.display='none'},false);
    }catch(e){gfailed=true;try{console.log('glass-gl unavailable, using 2D beads',e&&e.message)}catch(_){}if(gcanvas&&gcanvas.parentNode)gcanvas.parentNode.removeChild(gcanvas);gcanvas=null;ggl=null}
  }
  function init(){
    if(canvas||failed)return api.ok;
    try{
      canvas=document.createElement('canvas');canvas.id='nature-sky-gl';canvas.setAttribute('aria-hidden','true');
      const sky=document.getElementById('nature-sky');if(sky)document.body.insertBefore(canvas,sky);else document.body.appendChild(canvas);
      canvas.addEventListener('webglcontextlost',e=>{e.preventDefault();lost=true;api.ok=false},false);
      canvas.addEventListener('webglcontextrestored',()=>{try{setup();lost=false;api.ok=true;resize(w,h)}catch(e){fail(e)}},false);
      setup();api.ok=true;setupGlass();
    }catch(e){fail(e)}
    return api.ok;
  }
  function fail(e){failed=true;api.ok=false;try{console.log('sky-gl unavailable, using plates',e&&e.message)}catch(_){}if(canvas&&canvas.parentNode)canvas.parentNode.removeChild(canvas);canvas=null;gl=null}
  function resize(W,H,level){
    w=Math.max(1,W|0);h=Math.max(1,H|0);
    scale=level==='subtle'?.5:level==='balanced'?.62:.75;
    if(!canvas)return;
    canvas.style.width=w+'px';canvas.style.height=h+'px';
    canvas.width=Math.max(2,Math.round(w*scale));canvas.height=Math.max(2,Math.round(h*scale));
    if(gl)gl.viewport(0,0,canvas.width,canvas.height);
    if(gcanvas){gcanvas.style.width=w+'px';gcanvas.style.height=h+'px';const gs=level==='subtle'?.6:.75;gcanvas.width=Math.max(2,Math.round(w*gs));gcanvas.height=Math.max(2,Math.round(h*gs));if(ggl)ggl.viewport(0,0,gcanvas.width,gcanvas.height)}
  }
  function setMask(cv){maskSrc=cv;maskDirty=true}
  function show(on){if(canvas)canvas.style.display=on?'':'none';if(!on&&gcanvas){gcanvas.style.display='none';glassOn=false}}
  // s: {time, elevation, azimuth, hourAngle, moonPhase, cover, wind, mode, season, tilt:{x,y}, level, fog}
  function render(s){
    if(!gl||lost||!api.ok)return false;
    const rad=Math.PI/180,el=s.elevation,az=(s.azimuth==null?180:s.azimuth)*rad;
    // The view is a narrow window facing south; azimuth is compressed so the sun stays in frame like the v341 plates.
    const ce=Math.cos(el*rad);let sx=-Math.sin(az)*ce*.32,sy=Math.sin(el*rad*.85),sz=Math.max(.3,-Math.cos(az)*ce);const sl=Math.hypot(sx,sy,sz)||1;sx/=sl;sy/=sl;sz/=sl;
    const ph=s.moonPhase||0,ha=(s.hourAngle||0)+ph*Math.PI*2,mEl=Math.cos(ha)*.78,mx=-Math.sin(ha)*Math.sqrt(Math.max(0,1-mEl*mEl))*.35,mz=.6;
    const my=mEl*.5,ml=Math.hypot(mx,my,mz)||1;   // moon elevation compressed into the window like the sun
    const m=s.mode||'clear',stormy=['storm','typhoon','hail','heavy'].includes(m)?1:0,wet=['drizzle','rain','heavy','storm','typhoon','hail'].includes(m)?1:0;
    const tint=TINT[s.season]||TINT.spring,oct=s.level==='subtle'?3:s.level==='balanced'?4:5;
    gl.uniform2f(U.uRes,canvas.width,canvas.height);
    gl.uniform1f(U.uTime,s.time||0);
    gl.uniform3f(U.uSun,sx,sy,sz);gl.uniform1f(U.uEl,el);
    gl.uniform1f(U.uCover,Math.max(0,Math.min(1,s.cover||0)));
    gl.uniform1f(U.uWind,Math.max(0,Math.min(6,(s.wind||0)/20)));
    gl.uniform1f(U.uStorm,stormy);gl.uniform1f(U.uWet,wet);
    gl.uniform1f(U.uFog,m==='fog'?.75:Math.max(0,Math.min(.35,s.fog||0)));
    gl.uniform1f(U.uMoonPhase,ph);gl.uniform3f(U.uMoon,mx/ml,my/ml,mz/ml);
    gl.uniform3f(U.uTint,tint[0],tint[1],tint[2]);
    gl.uniform2f(U.uTilt,s.tilt?s.tilt.x:0,s.tilt?s.tilt.y:0);
    gl.uniform1f(U.uOct,oct);gl.uniform1f(U.uHeat,m==='heat'?1:0);gl.uniform1f(U.uCold,m==='cold'||m==='snow'?1:0);
    const rain=Math.max(0,Math.min(1,s.rain||0)),slant=Math.max(-1.6,Math.min(1.6,s.slant||0));
    gl.uniform1f(U.uRain,rain);gl.uniform1f(U.uSlant,slant);gl.uniform1f(U.uFlash,Math.max(0,Math.min(1,s.flash||0)));gl.uniform1f(U.uBoltX,s.boltX==null?.5:s.boltX);gl.uniform1f(U.uSeed,(s.boltSeed||0)+1);
    gl.drawArrays(gl.TRIANGLES,0,3);
    renderGlass(s,rain,slant);
    return true;
  }
  // Drops on the front glass, refracting the sky frame just rendered. Off (and hidden) when dry.
  function renderGlass(s,rain,slant){
    const flash=Math.max(0,Math.min(1,s.flash||0)),fog=s.mode==='fog'?.75:Math.max(0,Math.min(.35,s.fog||0)),heat=s.heat?1:0;
    const want=(rain>0||flash>0||fog>0||heat>0)&&!!ggl&&!gfailed&&s.glass!==false;
    if(want!==glassOn){glassOn=want;if(gcanvas)gcanvas.style.display=want?'':'none'}
    if(!want)return;
    ggl.activeTexture(ggl.TEXTURE0);ggl.bindTexture(ggl.TEXTURE_2D,gtexSky);ggl.texImage2D(ggl.TEXTURE_2D,0,ggl.RGB,ggl.RGB,ggl.UNSIGNED_BYTE,canvas);
    ggl.activeTexture(ggl.TEXTURE1);ggl.bindTexture(ggl.TEXTURE_2D,gtexMask);
    if(maskDirty){maskDirty=false;if(maskSrc)ggl.texImage2D(ggl.TEXTURE_2D,0,ggl.RGBA,ggl.RGBA,ggl.UNSIGNED_BYTE,maskSrc);else ggl.texImage2D(ggl.TEXTURE_2D,0,ggl.RGBA,1,1,0,ggl.RGBA,ggl.UNSIGNED_BYTE,new Uint8Array([0,0,0,0]))}
    ggl.uniform2f(GU.uRes,gcanvas.width,gcanvas.height);ggl.uniform1f(GU.uTime,s.time||0);ggl.uniform1f(GU.uRain,rain);ggl.uniform1f(GU.uSlant,slant);
    ggl.uniform1f(GU.uLayers,s.level==='subtle'?1:s.level==='balanced'?2:3);ggl.uniform1f(GU.uFlash,flash);
    ggl.uniform1f(GU.uBoltX,s.boltX==null?.5:s.boltX);ggl.uniform1f(GU.uSeed,(s.boltSeed||0)+1);ggl.uniform1f(GU.uDay,Math.max(0,Math.min(1,(s.elevation+8)/20)));ggl.uniform1f(GU.uFog,fog);ggl.uniform1f(GU.uHeat,heat);
    ggl.clearColor(0,0,0,0);ggl.clear(ggl.COLOR_BUFFER_BIT);ggl.drawArrays(ggl.TRIANGLES,0,3);
  }
  const api={ok:false,init,resize,render,show,setMask,glassActive:()=>glassOn,canvas:()=>canvas,glassCanvas:()=>gcanvas,_debug:()=>({ok:api.ok,lost,failed,scale,size:canvas?[canvas.width,canvas.height]:null,glass:{on:glassOn,failed:gfailed,size:gcanvas?[gcanvas.width,gcanvas.height]:null}})};
  root.SkyGL=api;
})(window);
