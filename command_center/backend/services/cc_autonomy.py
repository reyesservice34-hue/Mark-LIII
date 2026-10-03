from pathlib import Path
import os,json,ast,subprocess,socket,http.client,threading
ROOT=Path(os.environ.get('JARVIS_CC_SOURCE_DIR','/repo' if Path('/repo').is_dir() else str(Path(__file__).resolve().parents[3])))
def enabled():
 try:return json.loads((ROOT/'config/cc_autonomy.json').read_text()).get('enabled') is True
 except Exception:return False
ALLOWED_DIRS=('backend/modules','backend/adapters','backend/services','backend/ai','web/src')
ALLOWED_EXT={'.py','.ts','.tsx','.css','.md'}
DENY_WORDS=('auth','approval','cc_autonomy','selfext','secret','credential','api_key','token','password','.env','__init__','source','security','guard','evolution','healing','install','gateway','server','desktop','mcp','policy','permission','role','executor')
def allows_path(path):
 if not enabled():return False
 try:
  base=(ROOT/'command_center').resolve(); rel=(ROOT/str(path)).resolve().relative_to(base)
 except (ValueError,OSError,RuntimeError):return False
 if rel.suffix not in ALLOWED_EXT:return False
 if not any(rel.is_relative_to(d) for d in ALLOWED_DIRS):return False
 if any(x.startswith('.') or x in {'node_modules','data','__pycache__'} for x in rel.parts):return False
 low=str(rel).lower()
 if any(x in low for x in DENY_WORDS):return False
 return (base/rel).is_file()
def check_backend():
 for p in (ROOT/'command_center/backend').rglob('*.py'):
  if '__pycache__' not in p.parts:ast.parse(p.read_text(),filename=str(p))
def rebuild():
 check_backend()
 p=subprocess.run(['npm','run','typecheck'],cwd=ROOT/'command_center/web',capture_output=True,text=True,timeout=900)
 if p.returncode:raise RuntimeError(p.stdout[-2000:]+p.stderr[-2000:])
 return {'built':False,'deployed':False,'checks':'backend syntax, TypeScript (web)','note':'live build of command_center/web and restart of mia-command-center-web must run on the host','output':p.stdout[-1200:]}
def restart():
 check_backend()
 def run():
  s=socket.socket(socket.AF_UNIX);s.connect('/var/run/docker.sock');c=http.client.HTTPConnection('localhost',timeout=30);c.sock=s;c.request('POST','/containers/jarvis-command-center/restart?t=10');r=c.getresponse();r.read()
 threading.Timer(2,run).start()
 return {'restarting':True,'container':'jarvis-command-center','checks':'backend syntax'}
