"""Run DOM integration checks against a local fake LINE/AI fixture, no real sends."""
import os, subprocess, sys, tempfile, time, urllib.request
from pathlib import Path
root=Path(__file__).resolve().parent
with tempfile.TemporaryFile() as log:
 server=subprocess.Popen([sys.executable,str(root/'factory_ui_server.py')],stdout=log,stderr=log)
 try:
  url=os.environ.get('FACTORY_UI_URL','http://127.0.0.1:8765')
  for _ in range(100):
   try:urllib.request.urlopen(url+'/preview-admin',timeout=.5).close();break
   except OSError:time.sleep(.03)
  else:
   log.seek(0);print(log.read().decode());raise RuntimeError('UI fixture did not start')
  result=subprocess.run(['node',str(root/'factory_ui_smoke.cjs')],timeout=45)
  raise SystemExit(result.returncode)
 finally:
  server.terminate();server.wait(timeout=5)
