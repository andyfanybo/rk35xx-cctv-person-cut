"""Pause jobs without deleting checkpoints; retain the legacy cancel endpoint."""
import time
from state_io import read_json,atomic_json

ACTIVE_EXPORTS=('queued','running','preparing','merging','verifying','publishing','copy-verifying','pausing')
RECOVERABLE_EXPORTS=('failed','paused','cancelled','interrupted')


def pause_job(job,folder,proc,signal_process,save,legacy=False):
    if job.get('imported'):raise ValueError('导入结果没有可暂停的处理进程')
    export=next((e for e in reversed(job.get('exports',[])) if e.get('state') in ACTIVE_EXPORTS),None)
    now=time.time()
    if export:
        path=folder/'exports'/export['id']/'status.json';receipt=read_json(path,{}) or {}
        if receipt.get('state')=='done':raise ValueError('成品已完成，无需暂停')
        waiting=export['state']=='queued'
        export.update(state='paused' if waiting else 'pausing',pause_requested=not waiting,message='合并已暂停；已识别片段保留' if waiting else '正在暂停合并；保留识别结果')
        job['pause_requested']=not waiting
        if waiting:job['processing_stopped_at']=now
        atomic_json(path,dict(receipt,state=export['state'],message=export['message'],updated=now))
        save(job)
        if not waiting and proc is not None and proc.poll() is None:signal_process(proc)
        return
    if job['state']=='queued':
        job.update(state='cancelled' if legacy else 'paused',message='任务已暂停；已有进度保留',updated=now,processing_stopped_at=now)
    elif job['state']=='running':
        job.update(state='cancelling',pause_requested=not legacy,message='正在暂停；保留已识别进度',updated=now)
    else:raise ValueError('当前任务没有正在运行或排队的处理')
    save(job)
    if job['state']=='cancelling' and proc is not None and proc.poll() is None:signal_process(proc)


def finish_export(job,export,receipt,code,reason=None,now=None):
    now=time.time() if now is None else now
    if receipt.get('state')=='done' or code==0:
        export['state']='done';export.pop('message',None);job['processing_finished_at']=now
    elif export.get('pause_requested') or job.get('pause_requested'):
        export.update(state='paused',message='合并已暂停；已识别片段保留，可继续合并');job['processing_stopped_at']=now
    else:
        export.update(state='failed',message=reason or receipt.get('message','合并中断，可继续合并'));job['processing_stopped_at']=now
    export.pop('pause_requested',None);job.pop('pause_requested',None)
    return dict(receipt,state=export['state'],message=export.get('message',receipt.get('message')),updated=now)
