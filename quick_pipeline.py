"""Small resumable decode sessions and direct stream-copy output; no packet audits."""
import argparse,copy,csv,fcntl,json,math,os,shutil,signal,subprocess,time,struct
from pathlib import Path
from configuration import settings
from directory_selection import target_root,target_policy
from media_backend import select_backend,select_mode,validate_input
import pipeline as core
from sampling import candidate_intervals
from runtime_metrics import media_command,copy_progress
from mp4_keys import movie_duration
from merge_recovery import merge_space,require_space,failure_receipt
from state_io import read_json,quarantine,load_results
from storage_guard import ensure_publish_storage
from publication import publish as publish_file,recover as recover_publication,find_recovered,publish_tag
from cache_lifecycle import PUBLISH_ROOT,published_name,cleanup_automatic,atomic_json,delivered_file

VERSION='person-turbo-v2.0.0-dev'

def signature(path):
    p=Path(path).resolve();s=p.stat()
    return {'path':str(p),'size':s.st_size,'mtime_ns':s.st_mtime_ns}

def night_end_seconds(job,duration):
    return 21600 if job.get('night_relaxed',True) and duration>21600 else 0

def clip_offset(clip):
    return clip.get('source_time_offset',clip.get('sync_check',{}).get('video',{}).get('source_minus_output_seconds',clip['requested_start']))

def video(meta):return next(s for s in meta['streams'] if s['codec_type']=='video')

def video_start(meta):return float(video(meta).get('start_time',0))

def keys(path,folder):
    """Read only timestamps needed for stream-copy cuts; no hashes or database."""
    try:
        from mp4_keys import key_times
        return key_times(path)
    except (ValueError,KeyError,IndexError,struct.error):
        pass
    args=[core.FP,'-v','error','-select_streams','v:0','-show_packets','-show_entries','packet=pts_time,flags','-of','compact=p=0:nk=0',str(path)]
    result=[]
    with (folder/'keyframes.log').open('w') as log:
        p=subprocess.Popen(args,stdout=subprocess.PIPE,stderr=log,universal_newlines=True)
        try:
            for line in p.stdout:
                row=dict(x.split('=',1) for x in line.strip().split('|') if '=' in x)
                if 'K' in row.get('flags','') and row.get('pts_time','N/A')!='N/A':result.append(float(row['pts_time']))
            if p.wait():raise RuntimeError('读取当前分段关键帧失败')
        finally:
            if p.poll() is None:p.kill();p.wait()
            p.stdout.close()
    if not result:raise RuntimeError('当前分段没有关键帧')
    return result

def prepare(source,seconds,meta,folder,minutes,progress,night_boundary=0):
    manifest=folder/'split-manifest.json';spec={'source':signature(source),'seconds':seconds,'minutes':minutes}
    if manifest.exists():
        saved=read_json(manifest,None,recover=True,isolate=True)
        if not isinstance(saved,dict) or not isinstance(saved.get('parts'),list) or 'spec' not in saved:
            quarantine(manifest);return prepare(source,seconds,meta,folder,minutes,progress,night_boundary)
        if saved['spec']!=spec:raise ValueError('原片或分段设置变化，请新建任务')
        if not all(Path(p['path']).is_file() and Path(p['path']).stat().st_size==p['size'] for p in saved['parts']):raise ValueError('识别分段缺失，请新建任务')
        return saved['parts']
    d=folder/'parts';d.mkdir(exist_ok=True)
    for p in d.glob('part-*.mp4'):p.unlink()
    listing=folder/'split.csv'
    segment_args=['-segment_time',str(minutes*60)]
    if 0<night_boundary<seconds and night_boundary%(minutes*60):
        boundaries=sorted(set(list(range(minutes*60,math.ceil(seconds),minutes*60))+[night_boundary]))
        segment_args=['-segment_times',','.join(str(t) for t in boundaries)]
    media_command([core.FF,'-hide_banner','-nostdin','-loglevel','error','-i',str(source),'-t',str(seconds),'-map','0','-c','copy',
        '-f','segment']+segment_args+['-reset_timestamps','1','-segment_format','mp4',
        '-segment_format_options','video_track_timescale=90000','-segment_list',str(listing),'-segment_list_type','csv','-y',str(d/'part-%04d.mp4')],
        seconds,folder/'split.log',lambda **kw:progress(stage='splitting',message='无损分段，完成后逐段识别',**kw),7200)
    with listing.open() as f:rows=list(csv.reader(f))
    parts=[]
    for i,row in enumerate(rows):
        p=d/Path(row[0]).name;part_keys=keys(p,folder);start=float(row[1]) if i else video_start(meta)
        end=min(seconds,float(rows[i+1][1]) if i+1<len(rows) else seconds)
        if end<=start:continue
        parts.append({'path':str(p),'size':p.stat().st_size,'source_start':start,'source_end':end,
                      'source_offset':start-part_keys[0],'duration':movie_duration(p),'keyframes':part_keys})
    if not parts:raise RuntimeError('未生成可识别分段')
    core.atomic(manifest,{'spec':spec,'parts':parts});return parts

def run(job_path):
    job=json.loads(Path(job_path).read_text());root=Path(job['output']).resolve();root.mkdir(exist_ok=True)
    if core.RUNTIME_DATA not in root.parents:raise ValueError('临时文件必须放SSD')
    minutes=job.get('segment_minutes',30);interval=job.get('sample_interval_seconds',30);profile=job.get('detection_profile','turbo')
    if type(minutes)is not int or not 1<=minutes<=120:raise ValueError('分段分钟数无效')
    if job.get('publish_user') and (target_root(job,PUBLISH_ROOT)/published_name(job)).exists():raise ValueError('已有同名成品，拒绝覆盖')
    spec={'version':VERSION,'sources':[signature(e['source']) for e in job['plan']],'plan':job['plan'],'minutes':minutes,'interval':interval,'profile':profile,'night_relaxed':job.get('night_relaxed',True),'model':signature(settings.model)}
    fp=root/'quick-settings.json'
    previous=read_json(fp,None,recover=True,isolate=True)
    if previous is not None and not isinstance(previous,dict):quarantine(fp);previous=None
    if previous is not None and {k:v for k,v in previous.items() if k!='version'}!={k:v for k,v in spec.items() if k!='version'}:raise ValueError('原片或识别设置变化，请新建任务')
    core.atomic(fp,spec);current=['']
    def progress(**kw):
        kw.update(updated=time.time(),sample=current[0]);core.atomic(root/'progress.json',kw);print(json.dumps(kw,ensure_ascii=False),flush=True)
    results=[];detector=None
    settings.prepare_work()
    with settings.npu_lock.open('a') as guard:
        fcntl.flock(guard,fcntl.LOCK_EX|fcntl.LOCK_NB)
        try:
            for entry in job['plan']:
                current[0]=entry['label'];source=Path(entry['source']);folder=root/entry['label'];folder.mkdir(exist_ok=True)
                done=folder/'summary.json'
                complete=read_json(done,None,recover=True,isolate=True)
                if complete is None:
                    statistics=read_json(folder/'analysis-statistics.json',None,recover=True,isolate=True)
                    if statistics and statistics.get('candidates_complete') and all((root/c['path']).is_file() and (root/c['path']).stat().st_size==c['size'] for c in statistics['clips']):
                        complete=statistics;core.atomic(done,complete)
                if complete is not None:results.append(complete);core.atomic(root/'summary.json',results);continue
                meta=core.probe(source)
                validate_input(meta)
                duration=float(meta['format']['duration']);seconds=entry.get('seconds') or duration;night_end=night_end_seconds(job,duration)
                if not 0<seconds<=duration:raise ValueError('分析时长无效')
                saved_split=read_json(folder/'split-manifest.json',None,recover=True,isolate=True)
                estimated=source.stat().st_size*min(1.,seconds/duration)
                existing_parts=sum(p.stat().st_size for p in (folder/'parts').glob('part-*.mp4') if p.is_file() and not p.is_symlink())
                needed=max(0,estimated*2-existing_parts)+2*1024**3
                if saved_split and saved_split.get('spec')=={'source':signature(source),'seconds':seconds,'minutes':minutes} and all(Path(p['path']).is_file() for p in saved_split['parts']):
                    remaining=0
                    for i,part in enumerate(saved_split['parts']):
                        cached=read_json(folder/('chunk-{:04d}.json'.format(i)))
                        if not cached or not all((root/c['path']).is_file() and (root/c['path']).stat().st_size==c['size'] for c in cached.get('clips',[])):remaining+=part['size']
                    needed=remaining+2*1024**3
                if shutil.disk_usage(str(root)).free<needed:raise ValueError('SSD空间不足，先释放缓存')
                core.atomic(root/'input-metadata.json',[{'source':str(source),'duration_seconds':duration,'size_bytes':source.stat().st_size}])
                if detector is None:
                    detector=core.PersonDetector()
                    detector.presence(__import__('numpy').zeros((640,640,3),dtype='uint8'))
                detector.decode_backend=select_backend(source)
                parts=prepare(source,seconds,meta,folder,minutes,progress,night_end);clips=[];windows=[];elapsed=0.;samples=0;timings={};fallback_parts=[]
                for i,part in enumerate(parts):
                    checkpoint=folder/('chunk-{:04d}.json'.format(i));scan=folder/'scans'/('part-{:04d}'.format(i));scan.mkdir(parents=True,exist_ok=True)
                    saved=read_json(checkpoint,None,recover=True,isolate=True)
                    if saved is not None and (not isinstance(saved,dict) or not all(k in saved for k in ('clips','windows','elapsed_seconds','samples','timings')) or not isinstance(saved.get('clips'),list) or not isinstance(saved.get('windows'),list) or not isinstance(saved.get('timings'),dict)):
                        quarantine(checkpoint);saved=None
                    if saved is not None:
                        if not all((root/c['path']).exists() and (root/c['path']).stat().st_size==c['size'] for c in saved['clips']):raise ValueError('已完成分段文件缺失')
                    else:
                        span=part['source_end']-part['source_start'];local_seconds=max(1,math.floor(span))
                        use_turbo=profile=='turbo' or bool(night_end and part['source_start']<night_end)
                        def part_progress(**kw):
                            kw.update(chunk_index=i+1,chunk_total=len(parts),overall_done_seconds=min(seconds,part['source_start']+kw.get('second',0)),overall_total_seconds=seconds,overall_elapsed_seconds=elapsed+kw.get('elapsed_seconds',0),effective_profile='turbo' if use_turbo else ('fast' if profile=='turbo' else profile));progress(**kw)
                        local_keys=part.get('keyframes') or keys(part['path'],scan)
                        requested_turbo=use_turbo
                        use_turbo=use_turbo and select_mode(Path(part['path']),detector.decode_backend,'turbo',local_keys[0])
                        if requested_turbo and not use_turbo:progress(stage='scanning',message='极速抽帧不可用，使用连续解码采样',chunk_index=i+1,chunk_total=len(parts))
                        scan_error=None
                        for attempt in range(3):
                            try:
                                if detector is None and (use_turbo or not (scan/'scan-cache.json').exists()):detector=core.PersonDetector()
                                if use_turbo:
                                    from turbo_scan import scan as turbo_scan
                                    records,used=turbo_scan(detector,Path(part['path']),span,scan,part_progress,interval,local_keys,source_offset=part['source_offset'],night_seconds=night_end)
                                else:
                                    active_interval=max(60,interval) if night_end and part['source_start']<night_end else interval
                                    records,used=core.scan(detector,Path(part['path']),local_seconds,scan,part_progress,active_interval,diagnostics=False,detection_profile='fast' if profile=='turbo' else profile)
                                scan_error=None;break
                            except (RuntimeError,TimeoutError,subprocess.TimeoutExpired,ValueError,IndexError,KeyError,TypeError) as error:
                                scan_error=error
                                if attempt==2:break
                                progress(stage='retrying',message='仅重试当前分段，已完成分段保留',chunk_index=i+1,chunk_total=len(parts))
                        start_pts=local_keys[0]
                        local_end=part['source_end']-part['source_offset']
                        if scan_error is not None:
                            records=[];used=0.;keep=[[start_pts,local_end]]
                            core.atomic(scan/'scan-failed.json',{'state':'kept-whole','reason':str(scan_error),'source_start':part['source_start'],'source_end':part['source_end']})
                            progress(stage='scanning',message='此段识别异常，整段保留并继续后续分段',chunk_index=i+1,chunk_total=len(parts))
                        else:keep=candidate_intervals(records,local_end,local_keys,interval)
                        metrics=read_json(scan/'scan-cache.json',{},isolate=True) or {}
                        metrics=metrics if isinstance(metrics,dict) else {}
                        saved={'clips':[],'windows':[],'elapsed_seconds':used,'samples':len(records),'timings':metrics.get('timings',{}) if isinstance(metrics.get('timings',{}),dict) else {},'scan_failed_kept_whole':scan_error is not None,'effective_profile':'turbo' if use_turbo else ('fast' if profile=='turbo' else profile),'decode_backend':detector.decode_backend,'keyframe_sampling':use_turbo}
                        for n,(a,b) in enumerate(keep):
                            a=max(a,start_pts);b=min(b,local_end)
                            if b-a<=.01:continue
                            output=folder/('kept-{:04d}-{:04d}.mp4'.format(i,n));receipt=output.with_suffix('.json')
                            cached_clip=read_json(receipt,None,recover=True,isolate=True)
                            if output.exists() and cached_clip and output.stat().st_size==cached_clip.get('size'):c=cached_clip
                            else:
                                temp=output.with_name(output.stem+'.partial.mp4')
                                if temp.exists():temp.unlink()
                                whole=a<=start_pts+.01 and b>=local_end-.01
                                if whole:os.link(part['path'],str(temp))
                                else:
                                    seek=a+.001 if a>start_pts+.01 else 0
                                    args=[core.FF,'-v','error','-nostdin']
                                    if seek:args+=['-ss',str(seek)]
                                    args+=['-i',part['path']]
                                    if b<local_end-.01:args+=['-t',str(b-seek)]
                                    args+=['-map','0','-c','copy','-avoid_negative_ts','make_zero','-n',str(temp)]
                                    media_command(args,b-a,folder/'cut.log',lambda **kw:progress(stage='cutting',message='直接生成有人片段',chunk_index=i+1,chunk_total=len(parts),**kw),1800)
                                out_first=keys(temp,folder)[0] if not whole else start_pts
                                out_duration=movie_duration(temp);global_start=part['source_offset']+a;global_end=part['source_offset']+b
                                c={'path':str(output.relative_to(root)),'size':temp.stat().st_size,'actual_duration':out_duration,
                                   'requested_start':global_start,'requested_end':global_end,'stream_copy':True,'processing_policy':'quick','verification':'quick',
                                   'source_time_offset':global_start-out_first,'av_sync_policy':'same-cut-times-approximate'}
                                core.atomic(receipt,c);os.replace(str(temp),str(output))
                            saved['clips'].append(c);saved['windows'].append([c['requested_start'],c['requested_end']])
                        core.atomic(checkpoint,saved)
                    if saved.get('scan_failed_kept_whole'):fallback_parts.append(i+1)
                    clips+=saved['clips'];windows+=saved['windows'];elapsed+=saved['elapsed_seconds'];samples+=saved['samples']
                    for k,v in saved['timings'].items():timings[k]=timings.get(k,0)+v
                    row={'label':entry['label'],'source':str(source),'source_duration_seconds':duration,'source_size_bytes':source.stat().st_size,
                         'analyzed_seconds':min(seconds,part['source_end']),'elapsed_seconds':elapsed,'scan_timings':timings,'sample_count':samples,
                         'sample_interval_seconds':interval,'detection_profile':profile,'candidate_intervals':windows,'clips':clips,'images':[],'zoom_images':[],
                         'zoom_added_seconds':0,'candidates_complete':i==len(parts)-1,'chunk_count':len(parts),'automatic':True,'processing_policy':'quick','night_relaxed':bool(night_end),'night_until_seconds':night_end,'av_sync_policy':'same-cut-times-approximate','fallback_chunk_indices':fallback_parts}
                    core.atomic(folder/'analysis-statistics.json',row);core.atomic(root/'summary.json',results+[row])
                    progress(stage='scanning',message='分段已保存；中断后从当前进度继续',chunk_index=i+1,chunk_total=len(parts),overall_done_seconds=row['analyzed_seconds'],overall_total_seconds=seconds)
                core.atomic(done,row);results.append(row);core.atomic(root/'summary.json',results)
                # Clips are independent links/files; release the split copies before merging.
                for part in parts:Path(part['path']).unlink()
            progress(stage='done',message='识别完成，正在直接合并出片')
        finally:
            if detector is not None:detector.close()

def cleanup_delivered(job_path,receipt):
    job_path=Path(job_path).resolve();final=delivered_file(receipt)
    previous=read_json(job_path.parent/'cache-cleanup.json',{}) or {}
    if previous.get('videos_removed') and previous.get('artifacts_removed') and previous.get('state')=='done':return previous
    atomic_json(job_path.parent/'cache-cleanup.json',{'state':'cleaning','delivery':'published','videos_removed':False,'artifacts_removed':False,'published':str(final),'size':receipt['size'],'delivery_check':'copy-size','processing_policy':'quick','export_id':receipt.get('export_id'),'publish_root':receipt.get('publish_root'),'publish_storage':receipt.get('publish_storage')})
    return cleanup_automatic(job_path)

def _merge(job_path,export_id,publish):
    job_path=Path(job_path).resolve();job=read_json(job_path,None,recover=True);root=Path(job['output']).resolve();folder=job_path.parent;out=folder/'exports'/export_id;out.mkdir(parents=True,exist_ok=True)
    def progress(**kw):kw.update(updated=time.time());core.atomic(out/'status.json',kw);print(json.dumps(kw,ensure_ascii=False),flush=True)
    def finish(receipt):
        core.atomic(out/'status.json',receipt)
        if receipt.get('published'):
            try:
                receipt['automatic_cleanup']=cleanup_delivered(job_path,receipt);receipt['message']='有人视频已发布，SSD缓存已清理'
            except Exception as e:
                receipt['cleanup_error']=str(e);receipt['message']='成品已发布，缓存清理待重试'
            core.atomic(out/'status.json',receipt)
        return receipt
    if publish:
        recovered=recover_publication(out,job)
        if recovered:return finish(recovered)
        recovered,old_export=find_recovered(job_path,job)
        if recovered:return finish(dict(recovered,recovered_from_export=old_export))
        ensure_publish_storage(target_root(job,PUBLISH_ROOT),target_policy(job))
        if (target_root(job,PUBLISH_ROOT)/published_name(job)).exists():raise ValueError('已有同名成品，拒绝覆盖或仅凭大小认领')
    rows=load_results(root);clips=[c for row in rows for c in row['clips']]
    if not clips:raise ValueError('未检出有人，不生成空视频')
    require_space(merge_space(clips,root,out,target_root(job,PUBLISH_ROOT),publish))
    target=out/'merged.mp4';temp=out/'merged.partial.mp4';ready=read_json(out/'merge-ready.json',None,recover=True,isolate=True)
    if not (ready and ready.get('selected_clips')==[c['path'] for c in clips] and target.is_file() and target.stat().st_size==ready.get('size')):
        paths=[];lines=[]
        for i,c in enumerate(clips):
            p=(root/c['path']).resolve(strict=True)
            if root not in p.parents:raise ValueError('片段路径不安全')
            paths.append(p);lines.append("file '"+str(p).replace("'","'\\''")+"'\n")
            if i+1<len(clips) and abs(c['requested_end']-clips[i+1]['requested_start'])<.05:
                span=clip_offset(clips[i+1])-clip_offset(c)
                if span>0:lines.append('duration {:.9f}\n'.format(span))
        needed=sum(p.stat().st_size for p in paths)+1024**3
        if shutil.disk_usage(str(out)).free<needed:require_space(merge_space(clips,root,out,target_root(job,PUBLISH_ROOT),publish))
        listing=out/'concat.txt';listing.write_text(''.join(lines))
        if temp.exists():temp.unlink()
        media_command([core.FF,'-v','error','-nostdin','-f','concat','-safe','0','-i',str(listing),'-map','0','-c','copy','-metadata','comment='+publish_tag(job,export_id),'-avoid_negative_ts','make_zero','-n',str(temp)],
            sum(c['actual_duration'] for c in clips),out/'concat.log',lambda **kw:progress(state='merging',message='直接合并有人片段',**kw),7200)
        duration=movie_duration(temp);os.replace(str(temp),str(target))
        ready={'state':'done','job_id':job['id'],'export_id':export_id,'message':'有人视频已生成','file':str(target),'ssd_file':str(target),'published':None,'filename':target.name,'size':target.stat().st_size,'duration':duration,'av_sync_policy':'same-cut-times-approximate','selected_clips':[c['path'] for c in clips],'processing_policy':'quick','verification':'quick','updated':time.time()}
        core.atomic(out/'merge-ready.json',ready)
    receipt=dict(ready)
    if publish:
        require_space(merge_space(clips,root,out,target_root(job,PUBLISH_ROOT),publish))
        receipt=publish_file(out,job,target,receipt,lambda src,dst:copy_progress(src,dst,lambda **kw:progress(state='publishing',message='保存最终视频',**kw)))
    return finish(receipt)

def merge(job_path,export_id,publish):
    try:return _merge(job_path,export_id,publish)
    except Exception as error:
        out=Path(job_path).parent/'exports'/export_id;out.mkdir(parents=True,exist_ok=True)
        receipt=read_json(out/'status.json',{}) or {}
        if receipt.get('state')=='done':return receipt
        failed=failure_receipt(error)
        if isinstance(error,core.Cancelled):failed.update(state='paused',message='合并已暂停；已识别片段保留')
        core.atomic(out/'status.json',dict(receipt,**failed,updated=time.time()))
        raise

def main():
    p=argparse.ArgumentParser();p.add_argument('--job',required=True);a=p.parse_args()
    def stop(s,f):raise core.Cancelled('任务已暂停，已完成分段和识别记录保留')
    signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop)
    try:run(a.job)
    except BaseException as e:
        job=json.loads(Path(a.job).read_text());root=Path(job['output']);root.mkdir(exist_ok=True)
        core.atomic(root/'progress.json',{'stage':'cancelled' if isinstance(e,core.Cancelled) else 'failed','message':str(e),'updated':time.time()})
        if isinstance(e,core.Cancelled):return 130
        raise
    return 0

