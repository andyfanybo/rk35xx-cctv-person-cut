"""Small durable checkpoints; high-frequency progress stays cheap."""
import errno,json,os,stat,threading,time
from pathlib import Path


def fsync_dir(path,strict=True):
    fd=os.open(str(path),os.O_RDONLY|getattr(os,'O_DIRECTORY',0))
    try:
        try:os.fsync(fd);return True
        except OSError as e:
            if not strict and e.errno in (errno.EINVAL,errno.ENOTSUP,errno.ENOSYS,errno.EBADF):return False
            raise
    finally:os.close(fd)


def atomic_json(path,value,durable=None):
    p=Path(path);tmp=p.with_name(p.name+'.tmp-{}-{}'.format(os.getpid(),threading.get_ident()))
    if p.is_symlink():raise ValueError('状态文件不能是符号链接')
    if durable is None:
        durable=p.name not in ('progress.json','scan-state.json') and not (p.name=='status.json' and value.get('state') not in ('done','failed','cancelled','interrupted'))
    old=p.stat() if p.exists() else p.parent.stat()
    try:
        with tmp.open('w') as f:
            json.dump(value,f,ensure_ascii=False,indent=2);f.flush()
            if os.geteuid()==0:os.fchown(f.fileno(),old.st_uid,old.st_gid)
            os.fchmod(f.fileno(),stat.S_IMODE(old.st_mode) if p.exists() else 0o600)
            if durable:os.fsync(f.fileno())
        if p.name in ('job.json','status.json') and durable and p.exists():
            prev=p.with_name(p.name+'.prev');link=tmp.with_name(tmp.name+'.prev')
            try:os.link(str(p),str(link));os.replace(str(link),str(prev))
            finally:
                if link.exists():link.unlink()
        os.replace(str(tmp),str(p))
        if durable:fsync_dir(p.parent)
    finally:
        if tmp.exists():tmp.unlink()


def quarantine(path):
    p=Path(path)
    if p.exists():
        dest=p.with_name(p.name+'.corrupt-'+str(time.time_ns()));os.replace(str(p),str(dest));return str(dest)


def read_json(path,default=None,recover=False,isolate=False):
    p=Path(path)
    try:return json.loads(p.read_text())
    except FileNotFoundError:return default
    except (ValueError,UnicodeError):
        if isolate or recover:quarantine(p)
        if recover:
            try:value=json.loads(p.with_name(p.name+'.prev').read_text())
            except (OSError,ValueError,UnicodeError):return default
            atomic_json(p,value);return value
        return default

def load_results(root):
    root=Path(root);value=read_json(root/'summary.json',None,recover=True,isolate=True)
    if isinstance(value,list) and value and all(isinstance(r,dict) and r.get('candidates_complete',True) for r in value):return value
    rows={r.get('label',str(i)):r for i,r in enumerate(value or []) if isinstance(r,dict)} if isinstance(value,list) else {}
    changed=not isinstance(value,list)
    for p in sorted(root.glob('*/summary.json')):
        row=read_json(p,None,recover=True,isolate=True)
        if isinstance(row,dict) and row.get('candidates_complete') and isinstance(row.get('clips'),list):
            key=row.get('label',p.parent.name)
            if rows.get(key)!=row:rows[key]=row;changed=True
    result=list(rows.values())
    if result and changed:atomic_json(root/'summary.json',result)
    return result
