(function(){
  'use strict';
  const $=id=>document.getElementById('factory-'+id);
  const params=new URLSearchParams(location.search);
  const session=params.get('session')||'';
  let state=null,station=null,translation=null,liffReady=false,lookupVersion=0,translationVersion=0;
  function notice(text,error=false){$('message').textContent=text;$('message').className='factory-notice'+(error?' factory-error':'');$('message').hidden=!text;}
  async function api(path,method='GET',body){
    const control=new AbortController(),timer=setTimeout(()=>control.abort(),45000);
    try{
      const response=await fetch('/api/factory/'+path,{method,headers:{'Content-Type':'application/json','X-Factory-Session':session},body:body?JSON.stringify(body):undefined,cache:'no-store',signal:control.signal});
      const data=await response.json();if(!response.ok||!data.ok)throw new Error(data.message||'操作失敗 / Gagal');return data;
    }catch(error){if(error.name==='AbortError')throw new Error('連線逾時，請重新確認。 / Waktu koneksi habis.');throw error;}finally{clearTimeout(timer);}
  }
  async function readyLiff(){
    if(liffReady)return true;
    if(!window.liff||!state?.liff_id)return false;
    await liff.init({liffId:state.liff_id});liffReady=true;return true;
  }
  function busy(button,operation){
    return async event=>{event?.preventDefault();if(button.disabled)return;button.disabled=true;try{await operation();}catch(error){notice(error.message||'操作失敗 / Gagal',true);}finally{button.disabled=false;}};
  }
  async function copy(text){
    try{await navigator.clipboard.writeText(text);notice('已複製全文 / Teks lengkap disalin.');}
    catch(_){const area=document.createElement('textarea');area.value=text;area.setAttribute('readonly','');document.body.append(area);area.select();const ok=document.execCommand('copy');area.remove();if(!ok)throw new Error('無法自動複製，請長按文字複製。 / Tekan lama teks untuk menyalin.');notice('已複製 / Disalin.');}
  }
  function split(text){
    const chunks=[];let current='',units=0;
    for(const char of text){if(units+char.length>4700){chunks.push({type:'text',text:current});current='';units=0;}current+=char;units+=char.length;}
    if(current)chunks.push({type:'text',text:current});return chunks;
  }
  async function share(messages){
    if(!messages.length||messages.length>5)throw new Error('內容超過 LINE 單次分享上限，請用「複製全文」。 / Teks terlalu panjang; gunakan Salin.');
    if(!await readyLiff())throw new Error('分享需要啟用 LINE 內建網頁。可先使用「複製」。 / Aktifkan LIFF atau gunakan Salin.');
    if(!liff.isLoggedIn()){liff.login({redirectUri:location.href});return;}
    if(!liff.isApiAvailable('shareTargetPicker'))throw new Error('此環境無法分享，請從手機 LINE 開啟，或使用「複製」。 / Buka melalui LINE di ponsel.');
    const result=await liff.shareTargetPicker(messages,{isMultiple:true});
    notice(result?.status==='success'?'已分享 / Berhasil dibagikan.':'已取消分享 / Berbagi dibatalkan.');
  }
  async function lookup(value){
    const version=++lookupVersion;translationVersion++;station=null;translation=null;
    $('station-output').hidden=true;$('station-share-row').hidden=true;
    const data=await api('station','POST',{value});if(version!==lookupVersion)return;station=data.station;
    $('station-detail').hidden=false;$('station-output').hidden=true;$('station-share-row').hidden=true;
    $('code').value=station.code;$('station-name').textContent=station.code+' · '+station.name_zh+' / '+station.name_id;
    $('station-context').textContent=station.context||'';
    $('sop-zh').textContent=station.sop_zh||'尚未提供作業說明，請由管理員新增。';
    $('sop-id').textContent=station.sop_id||'Petunjuk kerja belum tersedia; hubungi pengelola.';
    const link=$('form-link');link.hidden=!station.form_id;
    if(station.form_id){const query=new URLSearchParams({view:'form',id:station.form_id,session});link.href=(state.liff_id?'https://liff.line.me/'+state.liff_id:'/liff/settings')+'?'+query;}
    notice('已選定 '+station.code+' / Stasiun dipilih.');
  }
  $('lookup').addEventListener('submit',busy($('lookup').querySelector('button'),()=>lookup($('code').value)));
  $('scan').addEventListener('click',busy($('scan'),async()=>{
    if(!await readyLiff())throw new Error('掃碼需啟用 LIFF；目前可直接輸入設備代碼。 / Aktifkan LIFF atau ketik kode mesin.');
    if(!liff.scanCodeV2)throw new Error('此 LINE 版本不支援掃碼，請輸入設備代碼。 / Ketik kode mesin.');
    const result=await liff.scanCodeV2();if(result?.value)await lookup(result.value);else notice('已取消掃碼 / Pemindaian dibatalkan.');
  }));
  $('share').addEventListener('click',busy($('share'),async()=>{const data=await api('share');await share(data.messages);}));
  $('copy').addEventListener('click',busy($('copy'),async()=>{const data=await api('share');await copy(data.copy_text);}));
  $('swap').addEventListener('click',()=>{const src=$('src').value;$('src').value=$('tgt').value;$('tgt').value=src;});
  $('translate-form').addEventListener('submit',busy($('translate'),async()=>{
    if(!station)throw new Error('請先選站別 / Pilih stasiun terlebih dahulu.');
    const version=++translationVersion;
    notice('正在翻譯 / Menerjemahkan…');const result=await api('translate','POST',{station:station.code,text:$('input').value,src:$('src').value,tgt:$('tgt').value});
    if(version!==translationVersion)return;translation=result;
    $('station-output').hidden=false;$('station-output').textContent=translation.translated;$('station-share-row').hidden=false;
    $('station-share').hidden=!state.options.sharing;notice('翻譯完成 / Terjemahan selesai.');
  }));
  $('station-copy').addEventListener('click',busy($('station-copy'),()=>copy(translation.original+'\n\n'+translation.translated)));
  $('station-share').addEventListener('click',busy($('station-share'),()=>share(split(translation.original+'\n\n'+translation.translated))));
  (async()=>{
    try{
      if(!session)throw new Error('請在 LINE 傳送 /factory，或按翻譯下方「工廠工具」。 / Kirim /factory di LINE.');
      state=await api('session');$('group').textContent=state.group_name;
      $('station-card').hidden=!state.options.station_tools;
      state.stations.forEach(item=>{const opt=document.createElement('option');opt.value=item.code;opt.label=item.name_zh+' / '+item.name_id;$('codes').append(opt);});
      if(state.context){$('share-card').hidden=false;$('original').textContent=state.context.original;$('translated').textContent=state.context.translated;$('share').hidden=!state.options.sharing;$('copy').hidden=!state.options.sharing;}
      notice('');
    }catch(error){notice(error.message,true);$('station-card').hidden=true;}
  })();
})();
