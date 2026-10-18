"""Install an isolated native service without replacing vendor hardware drivers."""
import argparse
import json
import os
import platform
import shlex
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path


def parser():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--prefix',type=Path,default=Path.home()/'.local/share/rk35xx-cctv-person-cut')
    ap.add_argument('--work-dir',type=Path,required=True,help='dedicated SSD work/cache directory')
    ap.add_argument('--input-dir',type=Path,help='optional initial input directory; also selectable in the Web UI')
    ap.add_argument('--output-dir',type=Path,help='initial publication directory; also selectable per job')
    ap.add_argument('--model',type=Path)
    ap.add_argument('--rknn-library')
    ap.add_argument('--ffmpeg',default='ffmpeg')
    ap.add_argument('--ffprobe',default='ffprobe')
    ap.add_argument('--bind',default='0.0.0.0')
    ap.add_argument('--port',type=int,default=8423)
    ap.add_argument('--china',action='store_true',help='use public GitHub acceleration and a temporary TUNA pip index')
    ap.add_argument('--github-proxy',help='HTTPS prefix used by the standalone bootstrap')
    ap.add_argument('--ref',default='main',help='source ref used by the standalone bootstrap')
    ap.add_argument('--pip-index-url')
    ap.add_argument('--source-dir',type=Path,default=Path(__file__).resolve().parent.parent)
    ap.add_argument('--system-site-packages',action='store_true',help='reuse existing vendor NumPy/OpenCV, including legacy Python')
    ap.add_argument('--no-deps',action='store_true',help='do not install Python packages')
    ap.add_argument('--no-start',action='store_true')
    ap.add_argument('--dry-run',action='store_true')
    return ap


def main():
    ap=parser();args=ap.parse_args()
    source=args.source_dir.expanduser().resolve();prefix=args.prefix.expanduser().resolve()
    work=args.work_dir.expanduser().resolve()
    output=(args.output_dir or prefix/'var/published').expanduser().resolve()
    if prefix==source or prefix in source.parents or source in prefix.parents:
        ap.error('Install prefix and source tree must be separate')
    if prefix.exists():
        ap.error('Install prefix already exists; choose a new prefix. Existing installs are never overwritten.')
    if work.exists() and (not work.is_dir() or any(work.iterdir())):
        ap.error('Work directory is not empty; choose a new dedicated SSD directory. Existing jobs are never imported automatically.')
    if work==Path('/') or output==Path('/'):
        ap.error('Do not use a filesystem root for task data')
    if not (source/'server.py').is_file() or not (source/'LICENSE').is_file():
        ap.error('Source tree is incomplete')
    if not 1<=args.port<=65535:
        ap.error('Invalid port')
    if args.input_dir and not args.input_dir.expanduser().is_dir():
        ap.error('Initial input directory does not exist')
    index=args.pip_index_url or ('https://mirrors.tuna.tsinghua.edu.cn/pypi/web/simple' if args.china else None)
    plan={'prefix':str(prefix),'work_dir':str(work),'output_dir':str(output),'bind':args.bind,
          'port':args.port,'pip_index':index,'system_site_packages':args.system_site_packages,
          'start':not args.no_start,'hardware_drivers_modified':False,'models_downloaded':False}
    if args.dry_run:
        print(json.dumps(plan,ensure_ascii=False,indent=2));return 0
    if platform.system()!='Linux' or platform.machine() not in ('aarch64','arm64'):
        ap.error('Native installation targets Linux ARM64')
    if sys.version_info<(3,10) and not args.system_site_packages:
        ap.error('Use Python 3.10+ for new dependencies, or --system-site-packages --no-deps for a working vendor environment')
    # Validate directory separation before writing any installation files.
    sys.path.insert(0,str(source))
    from configuration import Settings
    import tempfile
    roots=[str(p) for p in [Path('/mnt'),Path('/media'),Path('/srv'),work.parent] if p!=Path('/') and p.is_dir()]
    if args.input_dir:roots.append(str(args.input_dir.expanduser().resolve()))
    config={'work_dir':str(work),'publish_dir':str(output),'sources':{'camera':str(args.input_dir.expanduser().resolve())} if args.input_dir else {},
            'browse_roots':list(dict.fromkeys(roots)), 'model':str((args.model or prefix/'models/person.rknn').expanduser().resolve()),
            'rknn_library':args.rknn_library or '', 'ffmpeg':args.ffmpeg,'ffprobe':args.ffprobe,
            'decoder':'auto','allow_software_decode':False,'bind':args.bind,'port':args.port,'storage':{'policy':'local'}}
    with tempfile.TemporaryDirectory() as temporary:
        candidate=Path(temporary)/'config.json';candidate.write_text(json.dumps(config))
        Settings(candidate,environ={})
    excluded={'.git','.venv','var','__pycache__','.env','config.json'}
    def ignore(folder,names):
        return [name for name in names if name in excluded or name.endswith(('.local.json','.pyc','.rknn','.onnx','.pt','.log','.so')) or Path(folder,name).is_symlink()]
    shutil.copytree(str(source),str(prefix),ignore=ignore)
    work.mkdir(parents=True,exist_ok=True);output.mkdir(parents=True,exist_ok=True)
    # Pin an existing external output mount; local system-disk directories use local policy.
    from storage_guard import containing_mount
    mount=containing_mount(output)
    if mount['mount']!='/':config['storage']={'policy':'mount','mount_path':mount['mount'],'fstype':mount['fstype']}
    conf=prefix/'config/config.local.json';conf.write_text(json.dumps(config,ensure_ascii=False,indent=2)+'\n');conf.chmod(0o600)
    command=[sys.executable,'-m','venv']
    if args.system_site_packages:command.append('--system-site-packages')
    if args.no_deps:command.append('--without-pip')
    subprocess.run(command+[str(prefix/'.venv')],check=True)
    python=prefix/'.venv/bin/python'
    if not args.no_deps:
        pip=[str(python),'-m','pip','install']
        if index:pip+=['--index-url',index]
        subprocess.run(pip+['-r',str(prefix/'requirements.txt')],check=True)
    subprocess.run([str(python),'-c','import numpy,cv2'],check=True)
    env=dict(os.environ,PERSON_CONFIG=str(conf),PYTHONDONTWRITEBYTECODE='1')
    (prefix/'var').mkdir(exist_ok=True)
    start=prefix/'start.sh';start.write_text('#!/usr/bin/env bash\nset -e\nexport PERSON_CONFIG='+shlex.quote(str(conf))+'\necho \"$$\" > '+shlex.quote(str(prefix/'var/server.pid'))+'\nexec '+shlex.quote(str(python))+' -u '+shlex.quote(str(prefix/'server.py'))+'\n');start.chmod(0o755)
    stop=prefix/'stop.sh';stop.write_text('''#!/usr/bin/env bash
set -e
exec '''+shlex.quote(str(python))+''' - <<'PY'
import os,pathlib,signal
root=pathlib.Path('''+repr(str(prefix))+''')
pid=int((root/'var/server.pid').read_text())
cmd=pathlib.Path('/proc')/str(pid)/'cmdline'
if not cmd.exists():raise SystemExit('Service is not running')
if str(root/'server.py').encode() not in cmd.read_bytes().split(b'\\0'):raise SystemExit('PID belongs to another process; not stopping it')
os.kill(pid,signal.SIGTERM)
PY
''');stop.chmod(0o755)
    if not args.no_start:
        with socket.socket() as check:check.bind((args.bind,args.port))
        with (prefix/'var/server.log').open('ab') as log:
            process=subprocess.Popen([str(python),'-u',str(prefix/'server.py')],cwd=str(prefix),env=env,stdout=log,stderr=log,start_new_session=True)
        (prefix/'var/server.pid').write_text(str(process.pid))
        for _ in range(70):
            if process.poll() is not None:raise SystemExit('Service exited; inspect '+str(prefix/'var/server.log'))
            try:
                with urllib.request.urlopen('http://127.0.0.1:'+str(args.port)+'/api/config',timeout=.3) as response:
                    if json.load(response).get('name')=='rk35xx-cctv-person-cut':break
            except (OSError,ValueError):pass
            time.sleep(.2)
        else:
            process.terminate();process.wait(timeout=15)
            raise SystemExit('Service startup timed out; inspect '+str(prefix/'var/server.log'))
    print('Installed: '+str(prefix))
    print('Web panel: http://DEVICE-IP:'+str(args.port))
    print('Select input/output folders in the Web panel. Configure a matching model/runtime before recognition.')
    print('Start: '+str(start)+'\nStop: '+str(stop))
    print('No system drivers were replaced and no model weights were downloaded.')
    return 0


if __name__=='__main__':
    sys.exit(main())
