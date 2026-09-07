/* Both the original form entry and factory SOP entry use the same form UI. */
(() => {
  'use strict';
  const root=document.getElementById('app'), params=new URLSearchParams(location.search);
  let currentForm;
  const node=(tag,text)=>{const e=document.createElement(tag);if(text!==undefined)e.textContent=text;return e;};
  function headers(){const h={'Content-Type':'application/json','Authorization':'Bearer '+liff.getAccessToken()};if(params.get('session'))h['X-Factory-Session']=params.get('session');return h;}
  async function call(path,body){const ctl=new AbortController(),timer=setTimeout(()=>ctl.abort(),30000);try{const r=await fetch('/api/liff/'+path,{method:body?'POST':'GET',headers:headers(),body:body?JSON.stringify(body):undefined,signal:ctl.signal,cache:'no-store'});const d=await r.json();if(!r.ok)throw new Error(d.error||'操作失敗 / Gagal');return d;}finally{clearTimeout(timer);}}
  function message(text,error=false){root.replaceChildren();const box=node('div',text);box.className=error?'error-box':'success-box';root.append(box);}
  async function load(id){const d=await call('form/'+encodeURIComponent(id));if(d.already_submitted){message('✅ 已填寫 / Sudah diisi');return;}render(d.form);}
  function render(form){currentForm=form;root.replaceChildren();const card=node('div');card.className='card';card.append(node('h2',form.title_zh),node('p',form.title_id));root.append(card);
    const htmlForm=node('form');htmlForm.id='verifiedForm';
    for(const field of form.fields||[]){const wrap=node('div');wrap.className='card';const label=node('label',(field.label_zh||'')+' / '+(field.label_id||'')+(field.required?' *':''));label.className='field-label';label.htmlFor='field-'+field.id;wrap.append(label);
      let input;if(field.type==='select'){input=node('select');input.append(node('option','請選擇 / Pilih'));input.firstChild.value='';for(const option of field.options||[]){const o=node('option',option.zh+' / '+option.id);o.value=option.zh;input.append(o);}}
      else if(field.type==='textarea')input=node('textarea');else{input=node('input');input.type=['number','date','checkbox'].includes(field.type)?field.type:'text';if(field.type==='number')input.step='any';}
      input.id='field-'+field.id;input.required=Boolean(field.required);if(['text','textarea'].includes(field.type))input.maxLength=5000;wrap.append(input);htmlForm.append(wrap);
    }
    const btn=node('button','提交 / Kirim');btn.className='btn btn-primary';btn.type='submit';htmlForm.append(btn);htmlForm.onsubmit=async e=>{e.preventDefault();btn.disabled=true;btn.textContent='提交中 / Mengirim…';try{const answers={};for(const f of currentForm.fields||[]){const el=document.getElementById('field-'+f.id);answers[f.id]=f.type==='checkbox'?(el.checked?'yes':'no'):el.value.trim();}await call('form/'+encodeURIComponent(form.id)+'/submit',{answers});message('✅ 提交成功 / Berhasil dikirim');}catch(error){alert(error.message);btn.disabled=false;btn.textContent='提交 / Kirim';}};root.append(htmlForm);
  }
  async function init(){try{if(!window.liff||!window.LIFF_ID)throw new Error('請設定 LIFF_ID 後從 LINE 開啟。');await liff.init({liffId:window.LIFF_ID});if(!liff.isLoggedIn()){liff.login({redirectUri:location.href});return;}const id=params.get('id');if(id){await load(id);return;}const d=await call('forms');root.replaceChildren();if(!d.forms.length){message('目前沒有可填寫表單 / Tidak ada formulir');return;}for(const form of d.forms){const btn=node('button',(form.title_zh||'')+' / '+(form.title_id||''));btn.className='btn btn-primary';btn.onclick=()=>load(form.id).catch(e=>message(e.message,true));root.append(btn);}}catch(error){message(error.message,true);}}
  init();
})();
