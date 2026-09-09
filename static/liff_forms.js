// FACTORY_FORM_BUILD: 2026-09-09.ci106
// FACTORY_FORM_LIFECYCLE_API: 1
/* Shared, server-verified form entry. Preserve input across page navigation. */
(() => {
  'use strict';
  const pageDocument=document,root=pageDocument.getElementById('app'),params=new URLSearchParams(location.search);
  let currentForm, suspended=false,generation=0,loadVersion=0,submitting=false;
  const pending=new Set(),active=()=>!suspended&&window.document===pageDocument;
  const node=(tag,text)=>{const e=pageDocument.createElement(tag);if(text!==undefined)e.textContent=text;return e;};
  const inactive=()=>{const error=new Error('Page inactive');error.name='FactoryPageInactive';return error;};
  window.addEventListener('pagehide',()=>{suspended=true;generation++;loadVersion++;for(const ctl of pending)ctl.abort();});
  window.addEventListener('pageshow',()=>{if(!suspended)return;suspended=false;if(!currentForm||submitting){submitting=false;init();}});
  function headers(){const h={'Content-Type':'application/json','Authorization':'Bearer '+liff.getAccessToken()};if(params.get('session'))h['X-Factory-Session']=params.get('session');return h;}
  async function call(path,body){
    if(!active())throw inactive();
    const version=generation,ctl=new AbortController(),timer=setTimeout(()=>ctl.abort(),30000);pending.add(ctl);
    try{
      const response=await fetch('/api/liff/'+path,{method:body?'POST':'GET',headers:headers(),body:body?JSON.stringify(body):undefined,signal:ctl.signal,cache:'no-store'});
      const data=await response.json();if(!active()||version!==generation)throw inactive();
      if(!response.ok)throw new Error(data.error||'操作失敗 / Gagal');return data;
    }catch(error){if(!active()||version!==generation)throw inactive();if(error.name==='AbortError')throw new Error('連線逾時，請確認提交狀態後重試。 / Periksa status pengiriman lalu coba lagi.');throw error;}
    finally{clearTimeout(timer);pending.delete(ctl);}
  }
  function message(text,error=false){if(!active())return;root.replaceChildren();const box=node('div',text);box.className=error?'error-box':'success-box';box.setAttribute('role','status');root.append(box);}
  function report(error){if(error.name!=='FactoryPageInactive')message(error.message,true);}
  async function load(id){const version=++loadVersion,data=await call('form/'+encodeURIComponent(id));if(version!==loadVersion)return;if(data.already_submitted){message('✅ 已填寫 / Sudah diisi');return;}render(data.form);}
  function render(form){
    if(!active())return;currentForm=form;root.replaceChildren();const card=node('div');card.className='card';card.append(node('h2',form.title_zh),node('p',form.title_id));root.append(card);
    const htmlForm=node('form');htmlForm.id='verifiedForm';
    for(const field of form.fields||[]){
      const wrap=node('div');wrap.className='card';const label=node('label',(field.label_zh||'')+' / '+(field.label_id||'')+(field.required?' *':''));label.className='field-label';label.htmlFor='field-'+field.id;wrap.append(label);
      let input;
      if(field.type==='select'){input=node('select');input.append(node('option','請選擇 / Pilih'));input.firstChild.value='';for(const option of field.options||[]){const choice=node('option',option.zh+' / '+option.id);choice.value=option.zh;input.append(choice);}}
      else if(field.type==='textarea')input=node('textarea');
      else{input=node('input');input.type=['number','date','checkbox'].includes(field.type)?field.type:'text';if(field.type==='number')input.step='any';}
      if(field.type==='checkbox')wrap.classList.add('form-checkbox');
      input.id='field-'+field.id;input.required=Boolean(field.required);if(['text','textarea'].includes(field.type))input.maxLength=5000;wrap.append(input);htmlForm.append(wrap);
    }
    const button=node('button','提交 / Kirim');button.className='btn btn-primary';button.type='submit';htmlForm.append(button);
    htmlForm.onsubmit=async event=>{
      event.preventDefault();if(button.disabled||!active()||!htmlForm.reportValidity())return;
      button.disabled=true;submitting=true;button.textContent='提交中 / Mengirim…';
      try{
        const answers={};for(const field of form.fields||[]){const input=pageDocument.getElementById('field-'+field.id);answers[field.id]=field.type==='checkbox'?(input.checked?'yes':'no'):input.value.trim();}
        await call('form/'+encodeURIComponent(form.id)+'/submit',{answers});submitting=false;message('✅ 提交成功 / Berhasil dikirim');
      }catch(error){if(error.name!=='FactoryPageInactive'&&active()){submitting=false;alert(error.message);button.disabled=false;button.textContent='提交 / Kirim';}}
    };
    root.append(htmlForm);
  }
  async function init(){
    try{
      if(!window.liff||!window.LIFF_ID)throw new Error('請設定 LIFF_ID 後從 LINE 開啟。');
      const version=generation;await liff.init({liffId:window.LIFF_ID});if(!active()||version!==generation)return;
      if(!liff.isLoggedIn()){liff.login({redirectUri:location.href});return;}
      const id=params.get('id')||currentForm?.id;if(id){await load(id);return;}
      const data=await call('forms');root.replaceChildren();if(!data.forms.length){message('目前沒有可填寫表單 / Tidak ada formulir');return;}
      for(const form of data.forms){const button=node('button',(form.title_zh||'')+' / '+(form.title_id||''));button.className='btn btn-primary';button.onclick=()=>load(form.id).catch(report);root.append(button);}
    }catch(error){report(error);}
  }
  init();
})();
