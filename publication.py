"""Crash-recoverable publication without reading/hashing whole videos."""
import json,os,re,subprocess,time
from pathlib import Path
from configuration import settings
from directory_selection import target_root,target_policy,target_receipt
from state_io import atomic_json,read_json,fsync_dir,load_results
from storage_guard import ensure_publish_storage
from cache_lifecycle import PUBLISH_ROOT,published_name,delivered_file


def publish_tag(job,export_id):return 'person-recognition:{}:{}'.format(job['id'],export_id)


def paths(out,job):
    name=published_name(job);root=target_root(job,PUBLISH_ROOT);return root/name,root/('.'+name+'.'+out.name+'.partial')


def has_tag(path,tag):
    try:
        p=subprocess.run([settings.ffprobe,'-v','error','-show_entries','format_tags=comment','-of','json',str(path)],stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,timeout=20)
        return p.returncode==0 and json.loads(p.stdout.decode())['format'].get('tags',{}).get('comment')==tag
    except (OSError,ValueError,KeyError,subprocess.TimeoutExpired):return False


def recover(out,job):
    if not job.get('plan'):return None
    out=Path(out);intent=read_json(out/'publication-intent.json',None,recover=True)
    final,partial=paths(out,job)
    if not intent:
        marker=read_json(out.parent.parent/'cache-cleanup.json',{}) or {}
        last=job.get('exports',[])[-1] if job.get('exports') else {}
        recorded=marker.get('published')==str(final) and marker.get('size') and (marker.get('export_id')==out.name or not marker.get('export_id') and last.get('id')==out.name and last.get('publish_user'))
        if recorded:
            from mp4_keys import movie_duration
            receipt={'state':'done','published':str(final),'file':str(final),'ssd_file':str(out/'merged.mp4'),'filename':final.name,'size':marker['size'],'duration':movie_duration(delivered_file(dict({'published':str(final),'size':marker['size']},**target_receipt(job,PUBLISH_ROOT)))),'selected_clips':[c['path'] for row in load_results(Path(job['output'])) for c in row['clips']],'processing_policy':'quick','verification':'quick','message':'已从已发布清理凭证恢复回执','export_id':out.name,'job_id':job['id']}
            receipt.update(target_receipt(job,PUBLISH_ROOT))
            atomic_json(out/'status.json',receipt,durable=True);return receipt
        merged=out/'merged.mp4'
        if final.is_file() and partial.is_file() and merged.is_file() and not final.is_symlink() and not partial.is_symlink() and os.path.samefile(str(final),str(partial)) and final.stat().st_size==merged.stat().st_size:
            from mp4_keys import movie_duration
            rows=load_results(Path(job['output']));receipt={'state':'done','published':str(final),'file':str(final),'ssd_file':str(merged),'filename':final.name,'size':merged.stat().st_size,'duration':movie_duration(merged),'selected_clips':[c['path'] for row in rows for c in row['clips']],'processing_policy':'quick','verification':'quick','message':'已恢复同一发布硬链接的回执'}
            intent={'schema':1,'job_id':job['id'],'export_id':out.name,'final':str(final),'partial':str(partial),'size':receipt['size'],'tag':publish_tag(job,out.name),'receipt':receipt}
            atomic_json(out/'publication-intent.json',intent)
        else:return None
    if intent.get('job_id')!=job['id'] or intent.get('export_id')!=out.name or intent.get('final')!=str(final) or intent.get('partial')!=str(partial):raise ValueError('发布意图与任务不符')
    ensure_publish_storage(target_root(job,PUBLISH_ROOT),target_policy(job))
    if not final.is_file():return None
    if final.is_symlink() or final.stat().st_size!=intent['size']:return None
    linked=partial.is_file() and not partial.is_symlink() and os.path.samefile(str(final),str(partial))
    if not linked and not has_tag(final,intent['tag']):return None
    receipt=dict(intent['receipt'],state='done',published=str(final),file=str(final),filename=final.name,updated=time.time(),publication_recovered=True)
    receipt.update(target_receipt(job,PUBLISH_ROOT))
    atomic_json(out/'status.json',receipt,durable=True)
    if partial.exists() and not partial.is_symlink():partial.unlink()
    return receipt


def find_recovered(job_path,job):
    folder=Path(job_path).parent
    for item in reversed(job.get('exports',[])):
        out=folder/'exports'/item['id']
        if (out/'publication-intent.json').exists():
            value=recover(out,job)
            if value:return value,item['id']
    return None,None


def publish(out,job,source,receipt,copy):
    receipt=dict(receipt,**target_receipt(job,PUBLISH_ROOT))
    out=Path(out);old=recover(out,job)
    if old:return old
    ensure_publish_storage(target_root(job,PUBLISH_ROOT),target_policy(job));target_root(job,PUBLISH_ROOT).mkdir(exist_ok=True)
    final,partial=paths(out,job)
    if final.exists() or final.is_symlink():raise ValueError('已有同名成品，拒绝覆盖或仅凭大小认领')
    if partial.is_symlink():raise ValueError('发布临时路径不能是符号链接')
    if partial.exists():
        intent=read_json(out/'publication-intent.json')
        if not intent or intent.get('partial')!=str(partial) or partial.is_symlink():raise ValueError('发布临时文件冲突')
        partial.unlink()
    tag=publish_tag(job,out.name)
    intent={'schema':1,'job_id':job['id'],'export_id':out.name,'final':str(final),'partial':str(partial),'size':receipt['size'],'tag':tag,'receipt':receipt}
    atomic_json(out/'publication-intent.json',intent,durable=True)
    copy(source,partial)
    if partial.stat().st_size!=receipt['size']:raise RuntimeError('成品复制未完成')
    ensure_publish_storage(target_root(job,PUBLISH_ROOT),target_policy(job));os.chmod(str(partial),0o644);os.link(str(partial),str(final));fsync_dir(target_root(job,PUBLISH_ROOT),strict=False)
    receipt=dict(receipt,published=str(final),file=str(final),filename=final.name,updated=time.time())
    atomic_json(out/'status.json',receipt,durable=True)
    partial.unlink();return receipt


def clean_stale_partials(jobs,live_export_ids=(),min_age=3600):
    sources={Path(p['source']).resolve() for j in jobs for p in j.get('plan',[])};removed=[];now=time.time()
    roots={str(PUBLISH_ROOT):settings.storage}
    for job in jobs:roots[str(target_root(job,PUBLISH_ROOT))]=target_policy(job)
    for value,policy in roots.items():
        root=Path(value)
        try:ensure_publish_storage(root,policy)
        except (OSError,RuntimeError):continue
        for p in root.glob('.*.partial'):
            match=re.fullmatch(r'\..+\.([0-9a-f]{32})\.partial',p.name)
            if not match or match[1] in live_export_ids or p.is_symlink() or not p.is_file() or p.resolve() in sources:continue
            if now-p.stat().st_mtime<min_age:continue
            p.unlink();removed.append(str(p))
    return removed
