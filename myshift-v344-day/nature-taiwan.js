/* v343 — Taiwan lowland seasonal ecology, drawn procedurally (no image assets, no licences to check).
   Spring: 木棉 (kapok, big orange cups) and 油桐 (tung, white five-petal "May snow").
   Summer: 鳳凰木 (flame tree, scarlet petals, feathery leaves); fireflies stay in nature-effects.
   Autumn: 台灣欒樹 (golden rain tree, yellow sprays then pink lantern capsules) and 芒草 (silver grass plumes).
   Winter: 落羽松 (bald cypress, rust feathery sprays) and the north-east monsoon (dry leaves blown flat, wind streaks).
   Every sprite is rasterised once into a small canvas and cached. Pure drawing module: no app state. */
(function(root){
  'use strict';
  const cache=new Map(),TAU=Math.PI*2;
  function make(w,h,fn){const cv=document.createElement('canvas');cv.width=w;cv.height=h;const c=cv.getContext('2d');c.lineCap='round';c.lineJoin='round';fn(c,w,h);return cv}
  function petal(c,x,y,len,wid,rot,fill,stroke){c.save();c.translate(x,y);c.rotate(rot);c.beginPath();c.moveTo(0,0);c.bezierCurveTo(wid,-len*.35,wid,-len*.85,0,-len);c.bezierCurveTo(-wid,-len*.85,-wid,-len*.35,0,0);c.closePath();c.fillStyle=fill;c.fill();if(stroke){c.strokeStyle=stroke;c.lineWidth=1;c.stroke()}c.restore()}
  function leaf(c,x,y,len,wid,rot,fill,vein){c.save();c.translate(x,y);c.rotate(rot);c.beginPath();c.moveTo(0,0);c.quadraticCurveTo(wid,-len*.5,0,-len);c.quadraticCurveTo(-wid,-len*.5,0,0);c.fillStyle=fill;c.fill();if(vein){c.strokeStyle=vein;c.lineWidth=.8;c.beginPath();c.moveTo(0,0);c.lineTo(0,-len*.92);c.stroke()}c.restore()}
  // feathery pinnate frond (flame tree, bald cypress)
  function frond(c,x,y,len,rot,leafLen,leafW,color,dark,gap){c.save();c.translate(x,y);c.rotate(rot);c.strokeStyle=dark;c.lineWidth=1.4;c.beginPath();c.moveTo(0,0);c.lineTo(0,-len);c.stroke();for(let d=gap;d<len;d+=gap){const t=d/len,l=leafLen*(1-t*.55);leaf(c,0,-d,l,leafW,-Math.PI/2+.5,color);leaf(c,0,-d,l,leafW,Math.PI/2-.5,color)}c.restore()}
  const B={
    // ---- spring ----
    kapok:()=>make(72,72,c=>{for(let i=0;i<5;i++)petal(c,36,40,30,13,i*TAU/5,'#e85a2a','#b83a14');c.fillStyle='#f4b03a';for(let i=0;i<9;i++){const a=i*TAU/9;c.beginPath();c.arc(36+Math.cos(a)*7,40+Math.sin(a)*7,2,0,TAU);c.fill()}c.fillStyle='#8c2a10';c.beginPath();c.arc(36,40,3.5,0,TAU);c.fill()}),
    tung:()=>make(56,56,c=>{for(let i=0;i<5;i++)petal(c,28,30,22,11,i*TAU/5+.3,'#fbfbf7','#e6e2d6');c.fillStyle='#f3b6b0';c.beginPath();c.arc(28,30,5,0,TAU);c.fill();c.fillStyle='#e0a04a';for(let i=0;i<6;i++){const a=i*TAU/6;c.beginPath();c.arc(28+Math.cos(a)*3.5,30+Math.sin(a)*3.5,1.2,0,TAU);c.fill()}}),
    'tung-branch':()=>make(240,220,c=>{c.strokeStyle='#6b4a2c';c.lineWidth=5;c.beginPath();c.moveTo(6,214);c.quadraticCurveTo(70,150,150,120);c.stroke();c.lineWidth=3;c.beginPath();c.moveTo(96,146);c.quadraticCurveTo(140,110,200,96);c.moveTo(120,138);c.quadraticCurveTo(130,180,160,196);c.stroke();
      // heart-shaped tung leaves
      const hl=(x,y,s,r)=>{c.save();c.translate(x,y);c.rotate(r);c.scale(s,s);c.beginPath();c.moveTo(0,8);c.bezierCurveTo(-30,-14,-18,-36,0,-18);c.bezierCurveTo(18,-36,30,-14,0,8);c.fillStyle='#4f8b3f';c.fill();c.strokeStyle='#2f5e26';c.lineWidth=1.2/s;c.beginPath();c.moveTo(0,8);c.lineTo(0,-18);c.stroke();c.restore()};
      hl(60,172,1.4,-.5);hl(130,118,1.6,.2);hl(190,100,1.2,.8);hl(160,200,1.1,1.2);hl(100,140,1.0,-.9);
      const t=sprite('tung');for(const [x,y,s] of [[150,118,.9],[204,96,.75],[112,150,.7],[166,192,.6],[70,150,.65]])c.drawImage(t,x-28*s,y-28*s,56*s,56*s)}),
    // ---- summer ----
    'flame-petal':()=>make(40,40,c=>{petal(c,20,36,30,9,0,'#d6281e','#9c1410');c.strokeStyle='#ffd27a';c.lineWidth=1.2;c.beginPath();c.moveTo(20,34);c.lineTo(20,12);c.stroke()}),
    'flame-flower':()=>make(64,64,c=>{for(let i=0;i<4;i++)petal(c,32,34,26,10,i*TAU/5+TAU/10+.6,'#d6281e','#9c1410');petal(c,32,34,26,10,.6,'#f5e5b0','#c9a455');c.fillStyle='#8c1a12';for(let i=0;i<8;i++){const a=-1+i*.3;c.beginPath();c.moveTo(32,34);c.lineTo(32+Math.cos(a)*22,34+Math.sin(a)*22);c.strokeStyle='#8c1a12';c.lineWidth=1;c.stroke();c.beginPath();c.arc(32+Math.cos(a)*22,34+Math.sin(a)*22,1.6,0,TAU);c.fill()}}),
    'flame-branch':()=>make(260,220,c=>{c.strokeStyle='#5a3f26';c.lineWidth=5;c.beginPath();c.moveTo(4,216);c.quadraticCurveTo(80,140,170,120);c.stroke();
      for(const [x,y,r,l] of [[70,164,-1.1,80],[110,138,-.7,90],[150,124,-.3,84],[190,118,.1,70],[120,150,.9,70],[160,160,1.3,60]])frond(c,x,y,l,r,13,4,'#4c8c3c','#2d5a24',7);
      const f=sprite('flame-flower');for(const [x,y,s] of [[150,110,1],[204,112,.85],[96,148,.8],[176,158,.7]])c.drawImage(f,x-32*s,y-32*s,64*s,64*s)}),
    // ---- autumn ----
    'rain-tree-flower':()=>make(24,24,c=>{for(let i=0;i<4;i++)petal(c,12,13,9,3.5,i*TAU/4+.4,'#f2c21c','#c9960c');c.fillStyle='#c8351e';c.beginPath();c.arc(12,13,2,0,TAU);c.fill()}),
    'rain-tree-capsule':()=>make(40,48,c=>{c.fillStyle='#e8788a';c.strokeStyle='#b84e60';c.lineWidth=1.2;c.beginPath();c.moveTo(20,6);c.quadraticCurveTo(38,20,24,44);c.lineTo(16,44);c.quadraticCurveTo(2,20,20,6);c.closePath();c.fill();c.stroke();c.beginPath();c.moveTo(20,6);c.lineTo(20,44);c.stroke();c.fillStyle='#f6a7b3';c.beginPath();c.moveTo(20,8);c.quadraticCurveTo(26,20,22,40);c.lineTo(20,40);c.closePath();c.fill()}),
    'rain-tree-branch':()=>make(250,230,c=>{c.strokeStyle='#5f4631';c.lineWidth=5;c.beginPath();c.moveTo(6,224);c.quadraticCurveTo(80,150,170,130);c.stroke();
      for(const [x,y,r,l] of [[80,160,-1.0,70],[120,144,-.5,80],[160,132,-.1,76],[130,160,1.0,64]])frond(c,x,y,l,r,16,6,'#6f9b3d','#44652a',9);
      const fl=sprite('rain-tree-flower'),cp=sprite('rain-tree-capsule');
      for(let i=0;i<26;i++){const a=i*.8,x=150+Math.cos(a)*(30+i%5*9),y=110+Math.sin(a)*(18+i%3*8)-i%4*6;c.drawImage(fl,x-8,y-8,16,16)}
      for(const [x,y,s] of [[190,122,.9],[205,150,.8],[170,98,.7],[215,108,.65]])c.drawImage(cp,x-20*s,y-24*s,40*s,48*s)}),
    miscanthus:()=>make(120,260,c=>{c.strokeStyle='#b89b5a';c.lineWidth=2.2;c.beginPath();c.moveTo(60,258);c.quadraticCurveTo(56,160,62,90);c.stroke();
      // plume: many fine silvery strands
      for(let i=0;i<70;i++){const t=i/70,y=92-t*82,x=62+(t-.3)*22,a=(Math.random()-.5)*1.6,l=18+Math.random()*22;c.strokeStyle=`rgba(${236-t*20},${228-t*18},${206-t*10},${.55+Math.random()*.4})`;c.lineWidth=1.1;c.beginPath();c.moveTo(x,y);c.quadraticCurveTo(x+Math.cos(a-.9)*l*.6,y-Math.sin(a+.9)*l*.6,x+Math.cos(a-.8)*l,y-Math.abs(Math.sin(a+.6))*l*.5-l*.4);c.stroke()}
      // blade leaves
      for(const [r,l] of [[-.5,120],[.45,110],[-.2,90]]){c.strokeStyle='#8ea05a';c.lineWidth=2.4;c.beginPath();c.moveTo(60,250);c.quadraticCurveTo(60+Math.sin(r)*l*.5,250-l*.7,60+Math.sin(r)*l,250-l*.9);c.stroke()}}),
    fluff:()=>make(16,16,c=>{c.strokeStyle='rgba(248,244,232,.9)';c.lineWidth=1;for(let i=0;i<7;i++){const a=i*TAU/7;c.beginPath();c.moveTo(8,8);c.lineTo(8+Math.cos(a)*7,8+Math.sin(a)*7);c.stroke()}}),
    // ---- winter ----
    'cypress-spray':()=>make(48,96,c=>{frond(c,24,94,88,0,14,2.4,'#c4652a','#7a3d16',5)}),
    'cypress-needle':()=>make(28,12,c=>{c.strokeStyle='#c76a2e';c.lineWidth=2;c.beginPath();c.moveTo(2,6);c.lineTo(26,6);c.stroke();for(let i=4;i<26;i+=4){c.beginPath();c.moveTo(i,6);c.lineTo(i+2,2);c.moveTo(i,6);c.lineTo(i+2,10);c.stroke()}}),
    'cypress-branch':()=>make(240,240,c=>{c.strokeStyle='#5a3b22';c.lineWidth=5;c.beginPath();c.moveTo(6,234);c.quadraticCurveTo(70,150,150,110);c.stroke();
      for(const [x,y,r,l] of [[60,180,-1.2,70],[100,150,-.8,86],[140,126,-.4,90],[180,112,0,80],[120,160,.8,70],[160,150,1.2,64],[200,120,.5,60]])frond(c,x,y,l,r,15,2.6,'#c4652a','#7a3d16',5)}),
    'dry-leaf':()=>make(40,24,c=>{leaf(c,4,12,33,8,Math.PI/2,'#a8834f','#6d5230')})
  };
  function sprite(key){if(cache.has(key))return cache.get(key);const b=B[key];if(!b)return null;const cv=b();cache.set(key,cv);return cv}
  root.TaiwanFlora={sprite,keys:()=>Object.keys(B)};
})(window);
