"""Remove a confirmed task and private caches, never NAS inputs or published media."""
import re
import shutil
import os
from pathlib import Path
from directory_selection import target_root,target_policy


def delete_task(data,job,other_jobs):
    data=Path(data).resolve();ident=job['id']
    if not re.fullmatch(r'[a-f0-9]{32}',ident):raise ValueError('无效任务标识')
    folder=data/'jobs'/ident
    if folder.is_symlink() or folder.resolve()!=folder:raise ValueError('任务目录不安全')
    monitoring=data/'monitoring'/ident
    if monitoring.parent.exists() and not os.access(str(monitoring.parent),os.W_OK|os.X_OK):
        raise ValueError('监控缓存父目录权限不足，保留任务以便重试')
    if monitoring.is_symlink():raise ValueError('监控目录不安全')
    if monitoring.exists() and (not os.access(str(monitoring),os.W_OK|os.X_OK) or not os.access(str(monitoring.parent),os.W_OK|os.X_OK)):
        raise ValueError('监控缓存权限不足，保留任务以便重试')
    root=Path(job['output']).resolve()
    if not (data/'jobs' in root.parents or data/'tests' in root.parents):raise ValueError('结果目录不安全')
    protected=[Path(x['source']).resolve() for j in [job]+list(other_jobs) for x in j['plan']]
    if any(p==folder or folder in p.parents for p in protected):raise ValueError('任务目录含原录像，拒绝删除')
    for j in other_jobs:
        other=Path(j['output']).resolve()
        if other==folder or folder in other.parents:raise ValueError('此任务私有结果被其他任务引用')
    shared=any(Path(j['output']).resolve()==root or root in Path(j['output']).resolve().parents or Path(j['output']).resolve() in root.parents for j in other_jobs)
    external_root=folder not in root.parents and root!=folder
    if external_root and not shared:
        if data/'tests' not in root.parents:raise ValueError('不能删除其他任务的私有结果')
        if any(p==root or root in p.parents for p in protected):raise ValueError('共享目录含原录像')
        if root.exists():shutil.rmtree(str(root))
    partials_removed=[];partial_cleanup_error=None
    if job.get('exports') and job.get('plan'):
        try:
            from cache_lifecycle import PUBLISH_ROOT,published_name
            from storage_guard import ensure_publish_storage
            ensure_publish_storage(target_root(job,PUBLISH_ROOT),target_policy(job))
            for export in job['exports']:
                if not re.fullmatch(r'[a-f0-9]{32}',export['id']):continue
                p=target_root(job,PUBLISH_ROOT)/('.'+published_name(job)+'.'+export['id']+'.partial')
                if p.is_symlink() or p.resolve() in protected:continue
                if p.is_file():p.unlink();partials_removed.append(str(p))
        except (OSError,RuntimeError) as error:partial_cleanup_error=str(error)
    if folder.exists():shutil.rmtree(str(folder))
    if monitoring.exists():shutil.rmtree(str(monitoring))
    return {'deleted':True,'id':ident,'shared_cache_retained':shared,'nas_partials_removed':partials_removed,'partial_cleanup_error':partial_cleanup_error,
            'message':'任务和私有缓存已删除；原录像与用户目录成品保留'}

