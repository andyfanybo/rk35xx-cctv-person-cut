"""Publish names and remove only private generated videos after verified delivery."""
import json
import os
import re
from pathlib import Path

from configuration import settings
PUBLISH_ROOT=settings.publish_dir
DATA=settings.work_dir


from state_io import atomic_json,read_json,load_results

def published_name(job):
    names=[Path(p['source']).name for p in job['plan']]
    if len(names)==1:return names[0]
    # Multiple inputs have no single original name; use an explicit combined name.
    return '{}-等{}段.mp4'.format(Path(names[0]).stem,len(names))


def delivered_file(receipt):
    from storage_guard import ensure_publish_storage
    root=Path(receipt.get('publish_root') or PUBLISH_ROOT).resolve()
    ensure_publish_storage(root,receipt.get('publish_storage'))
    p=Path(receipt.get('published') or '').resolve(strict=True)
    if root not in p.parents or not p.is_file() or p.suffix.lower()!='.mp4':
        raise ValueError('已发布成品路径不在专用输出目录')
    if p.stat().st_size!=receipt['size']:raise ValueError('已发布成品大小变化，保留缓存')
    return p


def cleanup_automatic(job_path,no_people=False):
    """After successful delivery (or complete empty detection), release all heavy caches."""
    job_path=Path(job_path).resolve();job=json.loads(job_path.read_text());folder=job_path.parent;root=Path(job['output']).resolve()
    if job.get('output_mode')!='automatic':return None
    if folder!=DATA.resolve()/'jobs'/job['id'] or folder not in root.parents:raise ValueError('自动缓存目录不安全')
    summary=load_results(root)
    if not summary or not all(r.get('candidates_complete') for r in summary):raise ValueError('分析尚未完成，保留缓存')
    marker=folder/'cache-cleanup.json'
    info=read_json(marker,{},recover=True,isolate=True) or {}
    if no_people:
        if any(r['clips'] for r in summary):raise ValueError('存在有人区间，不能按空任务清理')
    elif info.get('delivery')!='published' and not info.get('videos_removed'):raise ValueError('成品未确认发布，保留临时文件')
    info.update(state='cleaning',videos_removed=False,artifacts_removed=False)
    atomic_json(marker,info)
    original={Path(x['source']).resolve() for x in job['plan']}
    removed=0;freed=0
    for parent in [root,folder/'exports']:
        if not parent.exists():continue
        for p in parent.rglob('*'):
            if p.is_symlink():raise ValueError('缓存目录含链接，停止清理')
            if not p.is_file():continue
            if p.resolve() in original:raise ValueError('缓存目录中发现原片')
            if p.suffix.lower() in ('.mp4','.jpg','.png','.jsonl','.sqlite') or '.sqlite-' in p.name or p.name.endswith('.sqlite.partial'):
                stat=p.stat();freed+=stat.st_size if stat.st_nlink==1 else 0;p.unlink();removed+=1
    info.update(state='done',videos_removed=True,artifacts_removed=True,automatic=True,
                extra_files_removed=removed,extra_bytes_freed=freed,no_people=no_people)
    atomic_json(marker,info);return info
