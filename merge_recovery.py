"""Merge-space preflight and recoverable failure metadata; never remove inputs."""
import errno
import os
import shutil
from pathlib import Path
from state_io import read_json

RESERVE=1024**3


def existing_parent(path):
    path=Path(path)
    while not path.exists() and path!=path.parent:path=path.parent
    return path


def same_device(left,right):
    return os.stat(str(left)).st_dev==os.stat(str(right)).st_dev


def merge_space(clips,root,out,publish_root,publish=True):
    root=Path(root).resolve();out=Path(out);publish_root=Path(publish_root)
    ready=read_json(out/'merge-ready.json',{}) or {};target=out/'merged.mp4'
    merged=bool(isinstance(ready,dict) and ready.get('selected_clips')==[c['path'] for c in clips] and target.is_file() and target.stat().st_size==ready.get('size'))
    size=ready['size'] if merged else sum(max(0,c.get('size',0)) for c in clips)
    missing=any(c.get('available') is False for c in clips) and not merged
    ssd=existing_parent(out);destination=existing_parent(publish_root)
    shared=publish and same_device(ssd,destination)
    merge_bytes=0 if merged else size
    ssd_needed=(merge_bytes+(size if shared else 0)+RESERVE) if (merge_bytes or shared) else 0
    ssd_free=shutil.disk_usage(str(ssd)).free
    disks=[{'name':'处理盘','required_bytes':ssd_needed,'free_bytes':ssd_free,'shortfall_bytes':max(0,ssd_needed-ssd_free)}]
    if publish and not shared:
        free=shutil.disk_usage(str(destination)).free;needed=size+RESERVE
        disks.append({'name':'成品目录','required_bytes':needed,'free_bytes':free,'shortfall_bytes':max(0,needed-free)})
    return {'can_retry':bool(clips) and not missing,'reason':'候选片段已缺失，无法继续合并' if missing else '没有可合并的片段' if not clips else None,
            'space_ready':all(d['shortfall_bytes']==0 for d in disks),'merged_ready':merged,'disks':disks}


def require_space(info):
    if not info['can_retry']:raise ValueError(info['reason'])
    missing=next((d for d in info['disks'] if d['shortfall_bytes']>0),None)
    if missing:
        raise ValueError('{}空间不足：可用 {:.2f} GB，需 {:.2f} GB，还需释放 {:.2f} GB；片段已保留，清理后可继续合并'.format(
            missing['name'],missing['free_bytes']/1024**3,missing['required_bytes']/1024**3,missing['shortfall_bytes']/1024**3))


def failure_receipt(error):
    message=str(error)
    space=getattr(error,'errno',None)==errno.ENOSPC or any(s in message.lower() for s in ('空间不足','no space left on device','disk full'))
    return {'state':'failed','message':message,'recoverable':True,'blocked_reason':'insufficient_space' if space else None}
