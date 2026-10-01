from pathlib import Path
import os,json,ast,subprocess,socket,http.client,threading
ROOT=Path(os.environ.get('JARVIS_CC_SOURCE_DIR','/repo' if Path('/repo').is_dir() else str(Path(__file__).resolve().parents[3])))
def enabled():
 try:return json.loads((ROOT/'config/cc_autonomy.json').read_text()).get('enabled') is True
 except Exception:return False
def allows_path(path):
 if not enabled():return False
 try:
  p=(ROOT/str(path)).resolve(); rel=p.relative_to((ROOT/'command_center').resolve())
 except (ValueError,OSError,RuntimeError):return False
 if any(x.startswith('.') or x in {'node_modules','data','__pycache__'} for x in rel.parts):return False
 low=str(rel).lower()
 return not any(x in low for x in ('auth','approval','cc_autonomy','secret','credential','api_key'))
def check_backend():
 for p in (ROOT/'command_center/backend').rglob('*.py'):
  if '__pycache__' not in p.parts:ast.parse(p.read_text(),filename=str(p))
def rebuild():
 check_backend()
 p=subprocess.run(['npm','run','build'],cwd=ROOT/'command_center/frontend',capture_output=True,text=True,timeout=900)
 if p.returncode:raise RuntimeError(p.stdout[-2000:]+p.stderr[-2000:])
 return {'built':True,'checks':'backend syntax, TypeScript, Vite','output':p.stdout[-1200:]}
def restart():
 check_backend()
 def run():
  s=socket.socket(socket.AF_UNIX);s.connect('/var/run/docker.sock');c=http.client.HTTPConnection('localhost',timeout=30);c.sock=s;c.request('POST','/containers/jarvis-command-center/restart?t=10');r=c.getresponse();r.read()
 threading.Timer(2,run).start()
 return {'restarting':True,'container':'jarvis-command-center','checks':'backend syntax'}
def approved_command(command):
 return enabled() and command in {'npm --prefix command_center/frontend run typecheck','npm --prefix command_center/frontend run build','docker restart jarvis-command-center'}
def run_command(command):
 if command=='docker restart jarvis-command-center':return str(restart())
 if command.endswith('run build'):return str(rebuild())
 p=subprocess.run(['npm','run','typecheck'],cwd=ROOT/'command_center/frontend',capture_output=True,text=True,timeout=300)
 return str({'exit_code':p.returncode,'output':p.stdout[-2000:]+p.stderr[-2000:]})
