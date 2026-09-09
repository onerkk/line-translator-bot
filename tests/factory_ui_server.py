import sys, tempfile, os, ast, time
from pathlib import Path
repo=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(repo))
import pytest
from flask import render_template, redirect, request, jsonify
import test_line_factory_features as cases
import line_ack_reminders
if getattr(line_ack_reminders,'BUILD_ID',None)!=cases.factory.BUILD_ID:
 raise RuntimeError('Reminder modules do not match. Update line_ack_reminders.py and line_factory_features.py together in the repository root.')
from line_factory_store import FeatureStore
path=Path(tempfile.mkdtemp(prefix='factory-ui-'))
hub=cases.hub.__wrapped__(FeatureStore(path=path/'state.db'),pytest.MonkeyPatch(),path)
app=hub.app
app.template_folder=str(repo/'templates')
app.static_folder=str(repo/'static')
hub.menu.register(app)
original, payload, messages=cases.notice(hub)
feedback=[]
hub.h['_send_reply_with_push_fallback']=lambda **kwargs: feedback.append(kwargs['fallback_text'])

@app.route('/preview-receipt-reply',methods=['POST'])
def receipt_reply():
 data=request.get_json(silent=True) or {}
 hub.postback(cases.event(uid=cases.COLLEAGUE,stamp=int(data.get('timestamp',500))),
              {'action':data.get('action','factory_ack'),'token':original})
 return jsonify(ok=True,feedback=feedback[-1])

@app.route('/preview-ack-reminder',methods=['POST'])
def reminder_due():
 data=request.get_json(silent=True) or {}
 def unavailable(group):raise RuntimeError('LINE member IDs unavailable')
 hub.h['_factory_member_ids']=unavailable
 # This scenario explicitly contains one additional unidentifiable member;
 # earlier UI interactions must not change whether the fixture is incomplete.
 hub.h['_factory_member_count']=lambda group:len(hub.known_members(group))+1
 sent=[]
 hub.reminders.sender=lambda group,messages,key:sent.append(messages)
 event=cases.event('/ack PMI 提醒測試',mid='ui-reminder-'+str(time.monotonic_ns()))
 with hub.message_scope(event,'text'):
  metadata=hub.payload_metadata()
  hub.command(event)
 row=next(row for row in hub.store.recent('notice:'+cases.GROUP) if row.get('factory_event')==metadata)
 token=row['token']
 hub.postback(cases.event(uid=cases.COLLEAGUE,stamp=700),{'action':'factory_ack','token':token})
 key='notice:'+cases.GROUP+':'+token
 now=time.time()
 hub.store.update(key,lambda row:dict(row,reminder_repeat=bool(data.get('repeat')),
     reminder_minutes=1,reminder_due_at=now-1,wake_at=now-1))
 hub.reminders.run_due()
 row=hub.store.get(key)
 return jsonify(token=token,state=row['reminder_state'],unknown=row.get('roster_count'),messages=sent[-1] if sent else [])

@app.route('/preview-fixture-health')
def fixture_health():
 return jsonify(build=cases.factory.BUILD_ID,reminder_build=line_ack_reminders.BUILD_ID)

hub.insight=lambda menu, start, end, mode='summary': {
 'privacy_limited':False,'cached':False,'note':'測試資料 UTC+9',
 'data':{'impression':{'metrics':[{'date':'20260906','count':42,'uniqueUsers':21}] if mode=='daily' else {'count':42,'uniqueUsers':21}},'clicks':[]}}
form={'id':'f1','status':'active','title_zh':'PMI 作業確認','title_id':'Konfirmasi PMI',
 'fields':[{'id':'pmi','type':'checkbox','required':True,'label_zh':'已檢驗鋼種','label_id':'Jenis baja diperiksa'},
           {'id':'quantity','type':'number','required':True,'label_zh':'數量','label_id':'Jumlah'}]}
submitted=False
@app.route('/api/liff/forms')
def forms_list():return jsonify(forms=[form])
@app.route('/api/liff/form/f1')
def form_detail():return jsonify(form=form,already_submitted=submitted)
@app.route('/api/liff/form/f1/submit',methods=['POST'])
def form_submit():
 global submitted
 import line_liff_forms
 if submitted:return jsonify(error='已填寫'),409
 try:line_liff_forms.answers(form,request.get_json())
 except ValueError as exc:return jsonify(error=str(exc)),400
 submitted=True
 return jsonify(ok=True)

@app.route('/preview-admin')
def admin():
 return '<html><head><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="stylesheet" href="/static/line_factory.css"></head><body class="factory-page"><main id="factory-admin-root" class="factory-shell"></main><script>function adminHeaders(){return {"Content-Type":"application/json"}}</script><script src="/static/admin_factory.js"></script><script>loadFactoryTools()</script></body></html>'
@app.route('/preview-menu')
def menu():
 return '<html><head><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="stylesheet" href="/static/admin_quick_reply.css"></head><body style="background:#101525"><main id="quickreply-admin-root"></main><script>function adminHeaders(){return {"Content-Type":"application/json"}}</script><script src="/static/admin_quick_reply.js"></script><script>qrLoad()</script></body></html>'
@app.route('/preview-factory')
def factory():
 return redirect('/liff/settings?'+hub.session_url(cases.GROUP,cases.USER,original).split('?',1)[1])
@app.route('/liff/settings')
def page():
 if request.args.get('view')=='form':
  tree=ast.parse((repo/'app.py').read_text())
  definition=next(n for n in tree.body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='LIFF_FORM_HTML' for t in n.targets))
  html=ast.literal_eval(definition.value).replace('__LIFF_ID_JSON__','"1234567890-test"')
 else:
  html=render_template('line_factory.html',liff_id=hub.h['LIFF_ID'])
 return html.replace('https://static.line-scdn.net/liff/edge/2/sdk.js','/fake-liff.js')
@app.route('/fake-liff.js')
def sdk():
 return app.response_class('window.liff={init:async()=>{},isLoggedIn:()=>true,getAccessToken:()=>"mock-browser-token",isApiAvailable:()=>true,scanCodeV2:async()=>({value:"line-factory:PMI"}),shareTargetPicker:async()=>({status:"success"})};',mimetype='text/javascript')
@app.route('/mobile')
def mobile():
 return '<html><body style="margin:0;background:#ddd"><iframe title="mobile" src="'+request.args.get('view','/preview-admin')+'" style="border:0;width:390px;height:844px"></iframe></body></html>'
app.run(host='0.0.0.0',port=int(os.environ.get('FACTORY_UI_PORT','8765')),threaded=True)
