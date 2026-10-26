#!/usr/bin/env python3
"""Local, single-worker candidate-review service; never delete or rewrite sources."""
import argparse
import sys
from configuration import settings
from directory_selection import listing,selectable,select_pair,browse_roots
import fcntl
import json
import mimetypes
import os
import re
import shutil
import signal
import subprocess
import threading
import time
import uuid
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs, unquote,quote
from recommendations import recommendation,apply_default
from task_stats import summarize
from task_overview import begin_processing,runtime_summary
from sampling import validate_interval
from cache_lifecycle import PUBLISH_ROOT,delivered_file,cleanup_automatic
from job_delete import delete_task
from state_io import atomic_json,read_json,quarantine,load_results
from process_control import signal_group,wait_worker
from publication import recover as recover_publication,clean_stale_partials

HERE=Path(__file__).resolve().parent
DATA=settings.work_dir
SOURCES=settings.sources
lock=threading.RLock();wake=threading.Event();stop=threading.Event()
jobs={};processes={};cleanup_checks={};source_cache={};source_cache_lock=threading.Lock()

def atomic(path,obj):atomic_json(path,obj)

def save(job):atomic(DATA/'jobs'/job['id']/'job.json',job)

def update(job,**fields):
    job.update(fields);job['updated']=time.time()
    if fields.get('state') in ('failed','cancelled','interrupted','cancelling'):job['processing_stopped_at']=job['updated']
    save(job)

def within(path,root):return path==root or root in path.parents

def source_path(value,selected_root=None):
    p=Path(value).resolve(strict=True)
    if not any(within(p,r) for r in ([Path(selected_root)] if selected_root else SOURCES.values())) or not p.is_file() or p.suffix.lower()!='.mp4':
        raise ValueError('只能选择配置录像目录中的 MP4 文件')
    return p

def result_root(job):
    p=Path(job['output']).resolve()
    if not within(p,DATA/'jobs') and not within(p,DATA/'tests'):raise ValueError('识别结果必须在固态数据目录')
    return p

def public(job,detail=False):
    result=dict(job);folder=result_root(job)
    p=folder/'progress.json'
    if p.exists():
        result['progress']=read_json(p,{},isolate=True) or {}
    if detail:
        summary=folder/'summary.json'
        result['results']=load_results(folder)
        reviews=DATA/'jobs'/job['id']/'reviews.json'
        result['reviews']=read_json(reviews,{},recover=True,isolate=True) or {}
        result['recommendations']={c['path']:recommendation(c,result['reviews'].get(c['path'])) for r in result['results'] for c in r['clips']}
        result['zoom_review']=(folder/'zoom-review.html').exists()
        comparison=folder/'comparison.json'
        if comparison.exists():result['comparison']=json.loads(comparison.read_text())
    result['exports']=[]
    for export in job.get('exports',[]):
        item=dict(export);status=DATA/'jobs'/job['id']/'exports'/item['id']/'status.json'
        if status.exists():
            receipt=read_json(status,{},recover=True,isolate=True) or {};item.update(receipt)
            if export.get('state')=='done' and receipt.get('state')!='done':
                try:recovered=recover_publication(status.parent,job) if job.get('verification')=='quick' else None
                except (OSError,RuntimeError,ValueError):recovered=None
                if recovered:receipt=recovered;item.update(recovered)
                else:item.update(state='failed',message='导出回执不可用，可重新合并发布')
            if export.get('state') in ('failed','cancelled') and receipt.get('state')!='done':
                item['state']=export['state'];item['message']=export.get('message','导出中断，可重新合并发布')
        result['exports'].append(item)
    result['cache_cleanup']=read_json(DATA/'jobs'/job['id']/'cache-cleanup.json',{},recover=True,isolate=True) or {}
    result['cache_videos_removed']=result['cache_cleanup'].get('videos_removed',False)
    result['cache_artifacts_removed']=result['cache_cleanup'].get('artifacts_removed',False)
    if detail:
        for r in result['results']:
            for c in r['clips']:c['available']=(folder/c['path']).is_file()
    active=next((e for e in reversed(result['exports']) if e.get('state') in ('queued','running','preparing','merging','verifying','publishing','copy-verifying')),None)
    result['work_progress']=active or (result['exports'][-1] if result['exports'] else result.get('progress',{}))
    if job.get('output_mode')=='automatic' and job['state']=='done' and not result['exports']:
        result['work_progress']=dict(result['work_progress'],message=job.get('message'))
    if detail:
        labels={r['label'] for r in result['results']};statistics=list(result['results'])
        for p in folder.glob('*/analysis-statistics.json'):
            if p.parent.name not in labels:
                try:statistics.append(json.loads(p.read_text()))
                except ValueError:pass
        metadata=json.loads((folder/'input-metadata.json').read_text()) if (folder/'input-metadata.json').exists() else job.get('input_metadata',[])
        result['statistics']=summarize(job['plan'],statistics,result['reviews'],metadata,result['exports'])
        result['runtime']=runtime_summary(job,result['exports'],DATA/'jobs'/job['id'],progress=result.get('progress'))
        result['verification']=job.get('verification','full')
        result['sample_interval_seconds']=job.get('sample_interval_seconds',1)
    return result

def recommend_job(job):
    info=public(job,True);reviews,changed=apply_default(info['results'],info['reviews'],time.time())
    atomic(DATA/'jobs'/job['id']/'reviews.json',reviews)
    update(job,message='识别完成，正在自动生成有人视频' if job.get('output_mode')=='automatic' else '候选已按默认建议保留；可随时修改')
    return changed

def queue_export(job,publish):
    if job['state']!='done':raise ValueError('识别完成后才能合并')
    if job.get('cleanup_in_progress'):raise ValueError('正在释放SSD缓存，请稍后查看成品')
    if public(job).get('cache_videos_removed'):raise ValueError('SSD候选视频已释放；如需重新裁切，请新建识别任务')
    existing=next((e for e in job.get('exports',[]) if e['state'] in ('queued','running')),None)
    if existing:return existing
    info=public(job,True);clips=[c for r in info['results'] for c in r['clips']]
    if any(info['reviews'].get(c['path'],{}).get('decision','pending')=='pending' for c in clips):raise ValueError('还有待确认候选，请先按建议执行或保存判断')
    if not any(info['reviews'].get(c['path'],{}).get('decision')=='keep' for c in clips):raise ValueError('没有保留候选，不生成空视频')
    if job.get('verification')=='quick' and job.get('exports') and job['exports'][-1].get('state')=='failed':
        export=job['exports'][-1];export.update(state='queued',publish_user=publish);export.pop('message',None)
        atomic(DATA/'jobs'/job['id']/'exports'/export['id']/'status.json',{'state':'queued','message':'等待重新合并发布','updated':time.time()})
        job['publish_user']=publish;save(job);wake.set();return export
    export={'id':uuid.uuid4().hex,'state':'queued','publish_user':publish,'created':time.time()}
    folder=DATA/'jobs'/job['id']/'exports'/export['id'];folder.mkdir(parents=True)
    atomic(folder/'selection.json',info['reviews'])
    job['publish_user']=publish
    job.setdefault('exports',[]).append(export);save(job);wake.set();return export

def worker():
    while not stop.is_set():
        job=None;export=None;proc=None;cleanup_job=None
        try:
            with lock:
                for candidate in sorted(jobs.values(),key=lambda j:j['created']):
                    waiting=next((e for e in candidate.get('exports',[]) if e['state']=='queued'),None)
                    if waiting:job=candidate;export=waiting;break
                if job is None:job=next((j for j in sorted(jobs.values(),key=lambda j:j['created']) if j['state']=='queued'),None)
                if job is None:
                    for candidate in jobs.values():
                        if candidate['state']!='done' or candidate.get('cleanup_retry_after',0)>time.time():continue
                        if cleanup_checks.get(candidate['id'],0)>time.monotonic():continue
                        cleanup_checks[candidate['id']]=time.monotonic()+60
                        info=public(candidate)
                        if info['cache_cleanup'].get('state') in ('done','skipped-shared-results'):continue
                        delivered=next((e for e in reversed(info['exports']) if e.get('state')=='done' and e.get('published')),None)
                        if delivered and not any(e.get('state') in ('queued','running','preparing','merging','verifying','publishing','copy-verifying') for e in info['exports']):
                            update(candidate,cleanup_in_progress=True);cleanup_job=(candidate,delivered);break
                if job:
                    folder=DATA/'jobs'/job['id']
                    if export:
                        begin_processing(job,folder)
                        export['state']='running';save(job)
                        args=[sys.executable,'-u',str(HERE/'merge_export.py'),'--job',str(folder/'job.json'),'--export-id',export['id']]
                        if export['publish_user']:args+=['--publish']
                        logfile=folder/'exports'/export['id']/'worker.log'
                    else:
                        begin_processing(job,folder)
                        update(job,state='running',message='正在启动识别任务')
                        args=[sys.executable,'-u',str(HERE/'launch_quick_pipeline.py'),'--job',str(folder/'job.json')]
                        logfile=folder/'worker.log'
                    with logfile.open('ab') as log:proc=subprocess.Popen(args,stdout=log,stderr=log,start_new_session=True)
                    proc._person_group=proc.pid;processes[job['id']]=proc
            if job is None:
                if cleanup_job:
                    candidate,receipt=cleanup_job
                    try:
                        from quick_pipeline import cleanup_delivered
                        cleanup_delivered(DATA/'jobs'/candidate['id']/'job.json',receipt)
                    except Exception as e:
                        with lock:update(candidate,cleanup_retry_after=time.time()+3600,message='成品已发布；缓存清理待重试：'+str(e))
                    finally:
                        with lock:update(candidate,cleanup_in_progress=False)
                    continue
                wake.wait(1);wake.clear();continue
            status_path=(DATA/'jobs'/job['id']/'exports'/export['id']/'status.json') if export else result_root(job)/'progress.json'
            code,watchdog_reason=wait_worker(proc,job,export,stop,status_path,grace=15,stall=900)
            if watchdog_reason:
                atomic(status_path,{'state':'failed' if export else None,'stage':'failed','message':watchdog_reason,'updated':time.time()})
            with lock:
                if job.get('delete_requested'):
                    update(job,state='cancelled',message='正在删除任务和缓存')
                elif export:
                    receipt=read_json(status_path,{}) or {}
                    export['state']='done' if code==0 or receipt.get('state')=='done' else 'failed'
                    job['processing_finished_at' if export['state']=='done' else 'processing_stopped_at']=time.time()
                    if export['state']=='failed':
                        export['message']=watchdog_reason or receipt.get('message','合并发布中断，可重新合并');atomic(status_path,dict(receipt,state='failed',message=export['message'],updated=time.time()))
                    save(job)
                elif job['state']=='cancelling' or code==130:update(job,state='cancelled',message='任务已取消；可恢复')
                elif stop.is_set():update(job,state='interrupted',message='服务停止；可恢复')
                elif code==0:
                    if job.get('output_mode')!='automatic':job['processing_finished_at']=time.time()
                    update(job,state='done',message='识别完成，正在准备成品' if job.get('output_mode')=='automatic' else '候选已生成；可按默认建议执行')
                    if job.get('review_mode')=='recommended':
                        recommend_job(job)
                        if job.get('output_mode')=='automatic' and not any(r['clips'] for r in public(job,True)['results']):
                            cleanup_automatic(DATA/'jobs'/job['id']/'job.json',no_people=True)
                            update(job,processing_finished_at=time.time(),message='识别完成：未检出有人，不生成空视频，临时缓存已释放')
                        else:
                            try:queue_export(job,job.get('publish_user',True))
                            except ValueError as e:update(job,message=str(e))
                else:
                    progress=result_root(job)/'progress.json';message='识别或校验失败'
                    if progress.exists():message=(read_json(progress,{}) or {}).get('message',message)
                    update(job,state='failed',message=message)
        except Exception as e:
            print('任务处理错误：'+str(e),flush=True)
            if job:
                with lock:
                    if export:export['state']='failed';export['message']=str(e);job['processing_stopped_at']=time.time();save(job)
                    else:update(job,state='failed',message=str(e))
            if job is None:stop.wait(5)
        finally:
            if proc:
                signal_group(proc,signal.SIGKILL)
                if proc.poll() is None:proc.wait()
            if job:
                with lock:
                    processes.pop(job['id'],None)
                    if job.get('delete_requested'):
                        try:
                            delete_task(DATA,job,[v for k,v in jobs.items() if k!=job['id']]);jobs.pop(job['id'],None)
                        except Exception as e:update(job,state='failed',delete_requested=False,message='任务删除失败：'+str(e))

class Handler(BaseHTTPRequestHandler):
    server_version='PersonReview/1.0'
    def end_headers(self):
        self.send_header('Access-Control-Allow-Origin','*')
        super().end_headers()
    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header('Access-Control-Allow-Methods','GET, HEAD, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers','Content-Type, X-Requested-With')
        self.send_header('Access-Control-Max-Age','600')
        self.send_header('Content-Length','0')
        self.end_headers()
    def json(self,obj,status=200):
        data=json.dumps(obj,ensure_ascii=False).encode()
        self.send_response(status);self.send_header('Content-Type','application/json; charset=utf-8')
        self.send_header('Content-Length',str(len(data)));self.send_header('Cache-Control','no-store');self.end_headers();self.wfile.write(data)
    def do_GET(self):
        try:self.get()
        except (ValueError,FileNotFoundError,KeyError) as e:self.json({'error':str(e)},404)
        except (BrokenPipeError,ConnectionResetError):pass
        except PermissionError:self.json({'error':'服务暂时无法读取文件，请检查权限'},503)
    def do_HEAD(self):
        try:self.get(head=True)
        except (ValueError,FileNotFoundError,KeyError) as e:self.json({'error':str(e)},404)
        except PermissionError:self.json({'error':'服务暂时无法读取文件，请检查权限'},503)
    def get(self,head=False):
        url=urlparse(self.path);path=unquote(url.path);q=parse_qs(url.query)
        if path=='/api/config':return self.json({'name':'rk35xx-cctv-person-cut','version':'2.0.0-dev','source_roots':list(SOURCES),'license':'PolyForm-Noncommercial-1.0.0','browse_roots':[str(p) for p in browse_roots()],'default_input':str(next(iter(SOURCES.values()),'')),'default_output':str(settings.publish_dir)})
        if path=='/api/directories':return self.json(listing(q.get('path',[None])[0]))
        if path=='/api/sources':
            name=q.get('root',[next(iter(SOURCES), '')])[0];root=selectable(q['directory'][0]) if q.get('directory') else SOURCES.get(name);keyword=q.get('q',[''])[0]
            if root is None:return self.json({'files':[],'total':0,'offset':0,'roots':list(SOURCES)})
            offset=max(0,int(q.get('offset',['0'])[0]));limit=100
            with source_cache_lock:
                cached=source_cache.get(str(root))
                if cached is None or time.monotonic()-cached[0]>30:
                    cached=(time.monotonic(),sorted(root.rglob('*.mp4')) if root.exists() else []);source_cache[str(root)]=cached
            selected=[p for p in cached[1] if keyword in str(p.relative_to(root))];found=[]
            for p in selected[offset:offset+limit]:
                try:
                    if not within(p.resolve(),root):continue
                    info=p.stat();found.append({'path':str(p),'name':str(p.relative_to(root)),'size':info.st_size,'mtime':info.st_mtime})
                except OSError:continue
            return self.json({'files':found,'total':len(selected),'offset':offset,'roots':list(SOURCES)})
        if path=='/api/jobs':
            with lock:return self.json([public(j) for j in sorted(jobs.values(),key=lambda j:j['created'],reverse=True)])
        match=re.fullmatch(r'/api/jobs/([a-f0-9]{32})',path)
        if match:
            with lock:return self.json(public(jobs[match[1]],True))
        match=re.fullmatch(r'/exports/([a-f0-9]{32})/([a-f0-9]{32})',path)
        if match:
            with lock:job=dict(jobs[match[1]])
            folder=DATA/'jobs'/job['id']/'exports'/match[2]
            receipt=json.loads((folder/'status.json').read_text())
            if receipt.get('state')!='done':raise ValueError('合并视频尚未通过校验')
            target=delivered_file(receipt) if receipt.get('published') else folder/'merged.mp4'
            return self.file(target,head,receipt.get('filename') or target.name)
        match=re.fullmatch(r'/source/([a-f0-9]{32})/(\d+)',path)
        if match:
            with lock:job=dict(jobs[match[1]])
            index=int(match[2])
            if index>=len(job['plan']):raise ValueError('源录像索引无效')
            return self.file(source_path(job['plan'][index]['source'],job.get('input_directory')),head)
        match=re.fullmatch(r'/files/([a-f0-9]{32})/(.+)',path)
        if match:
            with lock:job=dict(jobs[match[1]])
            root=result_root(job);p=(root/match[2]).resolve(strict=True)
            if not within(p,root) or p.suffix.lower() not in ('.mp4','.jpg','.html','.json','.csv'):raise ValueError('文件不可访问')
            if p.suffix=='.mp4':
                summaries=[]
                for f in root.glob('*/summary.json'):summaries+=json.loads(f.read_text()).get('clips',[])
                if not any(c['path']==str(p.relative_to(root)) for c in summaries):raise ValueError('候选尚未完成校验')
            return self.file(p,head)
        if path=='/':return self.file(HERE/'web/index.html',head)
        if path in ('/app.js','/task-overview.js','/style.css'):return self.file(HERE/'web'/path[1:],head)
        raise FileNotFoundError('页面不存在')
    def file(self,p,head=False,download_name=None):
        size=p.stat().st_size;start=0;end=size-1;status=200
        value=self.headers.get('Range')
        if value:
            match=re.fullmatch(r'bytes=(\d*)-(\d*)',value.strip())
            if not match or not any(match.groups()):return self.range_error(size)
            a,b=match.groups()
            if a:start=int(a);end=min(int(b) if b else size-1,size-1)
            else:start=max(0,size-int(b))
            if start>=size or start>end:return self.range_error(size)
            status=206
        with p.open('rb') as f:
            mime=mimetypes.guess_type(str(p))[0] or 'application/octet-stream'
            if p.suffix in ('.html','.js','.css'):mime+='; charset=utf-8'
            self.send_response(status);self.send_header('Content-Type',mime);self.send_header('Content-Length',str(end-start+1))
            self.send_header('Accept-Ranges','bytes');self.send_header('X-Content-Type-Options','nosniff')
            self.send_header('Cache-Control','no-cache' if p.suffix in ('.html','.js','.css','.json') else 'private, max-age=3600')
            if download_name:self.send_header('Content-Disposition',"attachment; filename*=UTF-8''"+quote(download_name,safe=''))
            if status==206:self.send_header('Content-Range','bytes {}-{}/{}'.format(start,end,size))
            self.end_headers()
            if not head:
                f.seek(start);left=end-start+1
                while left:
                    data=f.read(min(left,256*1024))
                    if not data:break
                    self.wfile.write(data);left-=len(data)
    def range_error(self,size):
        self.send_response(416);self.send_header('Content-Range','bytes */'+str(size));self.send_header('Content-Length','0');self.end_headers()
    def do_POST(self):
        try:
            if self.headers.get('Content-Type','').split(';')[0]!='application/json':
                return self.json({'error':'请求类型无效'},403)
            size=int(self.headers.get('Content-Length','0'))
            if not 0<size<=65536:raise ValueError('请求过大或为空')
            body=json.loads(self.rfile.read(size));path=urlparse(self.path).path
            with lock:
                if path=='/api/jobs':
                    sources=body.get('sources',[]);seconds=body.get('seconds')
                    mode=body.get('review_mode','recommended');publish=body.get('publish_user',True);verification=body.get('verification','quick')
                    output_mode=body.get('output_mode','automatic' if mode=='recommended' else 'legacy')
                    interval=validate_interval(body.get('sample_interval_seconds',30 if output_mode=='automatic' else 1))
                    segment_minutes=body.get('segment_minutes',30);profile=body.get('detection_profile','turbo');night=body.get('night_relaxed',True)
                    if type(night)is not bool:raise ValueError('夜间宽松设置须为开或关')
                    if output_mode!='automatic':raise ValueError('公开版本只支持自动分段处理')
                    if type(segment_minutes)is not int or not 1<=segment_minutes<=120:raise ValueError('分段阈值须为1～120分钟')
                    if profile not in ('turbo','fast','standard'):raise ValueError('识别强度无效')
                    if verification!='quick':raise ValueError('公开版本只支持quick出片')
                    if mode!='recommended' or type(publish) is not bool:raise ValueError('公开版本自动保留有人区间')
                    if not isinstance(sources,list) or not 1<=len(sources)<=50 or len(set(sources))!=len(sources):raise ValueError('选择1～50个不同文件')
                    if output_mode=='automatic' and len(sources)!=1:raise ValueError('自动分段任务一次选择一个原视频，多天录像请分别加入队列')
                    if output_mode=='automatic':mode='recommended';verification='quick'
                    if seconds is not None and (type(seconds) is not int or seconds<1):raise ValueError('时长须为正整数')
                    if sum(j['state'] in ('queued','running','cancelling') for j in jobs.values())>=20:raise ValueError('队列已满')
                    input_dir=body.get('input_directory')
                    output_dir=body.get('publish_directory')
                    if input_dir or output_dir:
                        if not input_dir or not output_dir:raise ValueError('请同时选择输入和输出文件夹')
                        input_dir,output_dir,publish_storage=select_pair(input_dir,output_dir)
                    else:
                        input_dir=next((r for r in SOURCES.values() if all(within(Path(s).resolve(),r) for s in sources)),None)
                        output_dir=settings.publish_dir;publish_storage=dict(settings.storage)
                    if output_dir:
                        protected=list(SOURCES.values())+[Path(j['input_directory']) for j in jobs.values() if j.get('input_directory')]
                        if any(within(Path(output_dir),p) or within(p,Path(output_dir)) for p in protected):raise ValueError('输出文件夹不能与已知原录像目录重叠')
                    plan=[{'label':'sample-{:02d}'.format(n+1),'source':str(source_path(s,input_dir)),'seconds':seconds} for n,s in enumerate(sorted(sources))]
                    if shutil.disk_usage(str(DATA)).free<1024**3:raise ValueError('候选存储剩余空间不足 1 GB')
                    ident=uuid.uuid4().hex;folder=DATA/'jobs'/ident;folder.mkdir()
                    atomic(folder/'plan.json',plan)
                    job={'id':ident,'title':str(body.get('title') or Path(sources[0]).name)[:120],'state':'queued',
                         'created':time.time(),'updated':time.time(),'output':str(folder/'results'),'plan':plan,'message':'等待识别','review_mode':mode,'publish_user':publish,'verification':verification,'sample_interval_seconds':interval,
                         'output_mode':output_mode,'segment_minutes':segment_minutes,'detection_profile':profile,'night_relaxed':night,'av_sync_policy':'same-cut-times-approximate'}
                    if input_dir:job.update(input_directory=str(input_dir),publish_dir=str(output_dir),publish_storage=publish_storage)
                    jobs[ident]=job;save(job);wake.set();return self.json(public(job),201)
                match=re.fullmatch(r'/api/jobs/([a-f0-9]{32})/(cancel|resume|review|recommend|merge|delete)',path)
                if not match:raise ValueError('接口不存在')
                job=jobs[match[1]];action=match[2]
                if action=='delete':
                    if body.get('confirm') is not True:raise ValueError('请确认删除任务和缓存')
                    if job.get('cleanup_in_progress'):raise ValueError('正在清理缓存，请稍后删除')
                    if job['id'] in processes:
                        update(job,delete_requested=True,state='cancelling',message='正在停止处理并删除任务')
                        proc=processes[job['id']]
                        if proc.poll() is None:signal_group(proc,signal.SIGTERM)
                        return self.json({'deleting':True,'id':job['id']})
                    result=delete_task(DATA,job,[v for k,v in jobs.items() if k!=job['id']]);jobs.pop(job['id'])
                    return self.json(result)
                if job.get('imported') and action in ('cancel','resume'):raise ValueError('导入结果不包含可控制的后台进程')
                if action=='recommend':
                    if job['state']!='done':raise ValueError('识别完成后才能按建议执行')
                    merge=body.get('merge',False);publish=body.get('publish_user',True)
                    if type(merge) is not bool or type(publish) is not bool:raise ValueError('建议执行参数无效')
                    changed=recommend_job(job)
                    job['last_recommendation_count']=changed;save(job)
                    if merge:
                        try:queue_export(job,publish)
                        except ValueError as e:update(job,message=str(e))
                elif action=='merge':
                    publish=body.get('publish_user',True)
                    if type(publish) is not bool:raise ValueError('发布参数无效')
                    queue_export(job,publish)
                elif action=='cancel':
                    if job['state']=='queued':update(job,state='cancelled',message='排队任务已取消')
                    elif job['state']=='running':
                        update(job,state='cancelling',message='正在停止；保留扫描记录')
                        proc=processes.get(job['id'])
                        if proc and proc.poll() is None:signal_group(proc,signal.SIGTERM)
                    else:raise ValueError('当前任务不能取消')
                elif action=='resume':
                    if job['state'] not in ('failed','cancelled','interrupted'):raise ValueError('当前任务不能恢复')
                    if job['id'] in processes:raise ValueError('任务仍在停止')
                    if job.get('imported'):raise ValueError('导入结果不可恢复')
                    update(job,state='queued',message='等待恢复');wake.set()
                else:
                    clip=body.get('clip');decision=body.get('decision')
                    result=public(job,True)
                    if result.get('cache_videos_removed') or job.get('cleanup_in_progress'):raise ValueError('缓存视频已释放或正在清理；如需改变裁切结果，请新建任务')
                    if decision not in ('pending','keep','reject'):raise ValueError('审核状态无效')
                    if clip not in [c['path'] for r in result['results'] for c in r['clips']]:raise ValueError('候选不存在')
                    reviews=result['reviews'];reviews[clip]={'decision':decision,'note':str(body.get('note',''))[:2000],'updated':time.time()}
                    atomic(DATA/'jobs'/job['id']/'reviews.json',reviews)
                return self.json(public(job,True))
        except (ValueError,TypeError,KeyError,FileNotFoundError) as e:self.json({'error':str(e)},400)
        except PermissionError:self.json({'error':'任务操作未完成，请检查任务与缓存目录权限'},503)
        except (BrokenPipeError,ConnectionResetError):pass
    def log_message(self,fmt,*args):
        if not self.path.startswith('/api/jobs'):super().log_message(fmt,*args)

def load_jobs():
    for p in (DATA/'jobs').glob('*/job.json'):
        if p.parent.is_symlink() or p.parent.parent.resolve()!=(DATA/'jobs').resolve():continue
        ident=p.parent.name
        if not re.fullmatch(r'[a-f0-9]{32}',ident):continue
        try:
            j=read_json(p,None,recover=True,isolate=True)
            if not isinstance(j,dict) or not j:raise ValueError('任务JSON损坏且没有有效备份')
            j.setdefault('created',p.parent.stat().st_mtime)
            if type(j.get('created')) not in (int,float) or not isinstance(j.get('state'),str) or not isinstance(j.get('plan'),list) or not isinstance(j.get('exports',[]),list):raise ValueError('任务记录结构损坏')
            if j['id']!=ident:raise ValueError('任务ID与目录不符')
            result_root(j);j['cleanup_in_progress']=False
            if j['state'] in ('running','cancelling'):j.update(state='interrupted',message='服务重启；可继续已有进度')
            for export in j.get('exports',[]):
                status=DATA/'jobs'/ident/'exports'/export['id']/'status.json'
                receipt=read_json(status,{},recover=True,isolate=True) or {}
                if export.get('state')=='running' or export.get('state')=='done' and receipt.get('state')!='done' or receipt.get('state') in ('running','preparing','merging','verifying','publishing','copy-verifying'):
                    try:recovered=recover_publication(status.parent,j) if j.get('verification')=='quick' else None
                    except (OSError,RuntimeError,ValueError) as error:
                        recovered=None;receipt['recovery_error']=str(error)
                    if recovered:receipt=recovered
                    if receipt.get('state')=='done':export['state']='done'
                    else:
                        export.update(state='failed',message='合并发布中断，可重新合并发布')
                        atomic(status,dict(receipt,state='failed',message=export['message'],updated=time.time()))
            jobs[ident]=j;save(j)
        except (OSError,ValueError,KeyError,TypeError,IndexError) as e:
            try:
                quarantine(p)
                j={'id':ident,'title':'损坏任务（缓存保留）','state':'failed','created':p.parent.stat().st_mtime,'updated':time.time(),'output':str(p.parent/'results'),'plan':[],'exports':[],'imported':True,'message':'任务记录已隔离：'+str(e)}
                jobs[ident]=j;save(j)
            except OSError:print('无法读取或隔离任务 {}：{}'.format(ident,e),flush=True)
    for ident,j in list(jobs.items()):
        if j.get('delete_requested'):
            try:delete_task(DATA,j,[v for k,v in jobs.items() if k!=ident]);jobs.pop(ident)
            except Exception as e:update(j,state='failed',delete_requested=False,message='任务删除失败：'+str(e))
    try:
        live={e['id'] for j in jobs.values() for e in j.get('exports',[]) if e.get('state')=='queued'}
        clean_stale_partials(list(jobs.values()),live)
    except (OSError,RuntimeError) as e:print('成品临时文件清理延期：'+str(e),flush=True)

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bind',default=settings.bind);ap.add_argument('--port',type=int,default=settings.port)
    args=ap.parse_args();settings.prepare_work();(DATA/'jobs').mkdir(parents=True,exist_ok=True)
    guard=(DATA/'server.lock').open('a');fcntl.flock(guard,fcntl.LOCK_EX|fcntl.LOCK_NB)
    load_jobs()
    thread=threading.Thread(target=worker,daemon=True);thread.start()
    http=ThreadingHTTPServer((args.bind,args.port),Handler)
    def shutdown(signum,frame):
        stop.set();wake.set()
        with lock:
            for proc in processes.values():
                if proc.poll() is None:signal_group(proc,signal.SIGTERM)
        threading.Thread(target=http.shutdown,daemon=True).start()
    signal.signal(signal.SIGTERM,shutdown);signal.signal(signal.SIGINT,shutdown)
    print('Person review http://{}:{}/'.format(args.bind,args.port),flush=True)
    try:http.serve_forever()
    finally:http.server_close();thread.join(timeout=30)

if __name__=='__main__':main()

