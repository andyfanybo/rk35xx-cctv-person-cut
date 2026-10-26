"""Bound cancellation and stalls, including FFmpeg descendants in the worker group."""
import os,signal,subprocess,time
from pathlib import Path
from state_io import read_json


def signal_group(proc,sig):
    pgid=getattr(proc,'_person_group',None)
    if type(pgid)is int and pgid>1:
        try:os.killpg(pgid,sig)
        except ProcessLookupError:pass
    elif proc.poll() is None:proc.send_signal(sig)


def wait_worker(proc,job,export,stop,status_path,grace=15,stall=900):
    last=time.monotonic();last_check=0.;signature=None;term_at=None;reason=None
    while proc.poll() is None:
        now=time.monotonic()
        cancelling=stop.is_set() or job.get('pause_requested') or (export or {}).get('pause_requested') or job.get('delete_requested') or job.get('state')=='cancelling'
        if now-last_check>=min(5,max(.05,stall/4)):
            row=read_json(status_path,{}) or {};last_check=now
            if not isinstance(row,dict):row={}
            observed=tuple(row.get(k) for k in ['stage','state','sample','second','done','samples_done','chunk_index','candidate_done','overall_done_seconds'])
            if observed!=signature:signature=observed;last=now
        if not cancelling and now-last>stall:reason='超过{}秒无实际进度，已停止并保留恢复记录'.format(stall);cancelling=True
        if cancelling and term_at is None:signal_group(proc,signal.SIGTERM);term_at=now
        if term_at is not None and now-term_at>=grace:signal_group(proc,signal.SIGKILL)
        try:proc.wait(timeout=min(.5,max(.05,grace/4)))
        except subprocess.TimeoutExpired:pass
    return proc.returncode,reason

