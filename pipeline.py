"""Resumable scan core extracted from the v1.9.2 production pipeline."""
import json,os,subprocess,time
from pathlib import Path
import cv2
import numpy as np
from detect import PersonDetector
from sampling import validate_interval,sample_count,frame_filter,sample_second
from runtime_metrics import rate_fields,FrameReader
from state_io import read_json
from configuration import settings
from media_backend import frame_args
FF=settings.ffmpeg
FP=settings.ffprobe
RUNTIME_DATA=settings.work_dir
SETTINGS={'diagnostic_interval_seconds':60}

def decode_plan(source,folder,done,frames,interval):
    # Replay decoding only; saved samples are never inferred twice.
    return Path(source),frame_filter(interval),0,frames,False

class Cancelled(Exception): pass

def atomic(path,obj):
    from state_io import atomic_json
    atomic_json(path,obj)

def command(args, timeout=600):
    p = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        out, err = p.communicate(timeout=timeout)
        if p.returncode: raise RuntimeError(err.decode(errors='replace')[-3000:])
        return out
    finally:
        if p.poll() is None: p.kill(); p.wait()

def probe(path):
    return json.loads(command([FP,'-v','error','-show_format','-show_streams',
        '-of','json',str(path)],60))

def load_records(folder,interval=1):
    p=folder/'detections.jsonl';records=[]
    if p.exists():
        # Keep complete lines only, recovering a write interrupted by cancellation.
        valid=0
        with p.open('rb') as f:
            for line in f:
                try:r=json.loads(line)
                except ValueError:break
                if not isinstance(r,dict) or r.get('second')!=sample_second(len(records),interval):break
                records.append(r);valid=f.tell()
        with p.open('r+b') as f:f.truncate(valid)
    return records

def scan(detector,source,seconds,folder,progress,interval=1,diagnostics=True,detection_profile='standard'):
    records=load_records(folder,interval);done=len(records);frames=sample_count(seconds,interval)
    cache=folder/'scan-cache.json'
    cached=read_json(cache,None,recover=True,isolate=True)
    if isinstance(cached,dict) and done==frames:return records,cached.get('elapsed_seconds',0)
    state=read_json(folder/'scan-state.json',{'elapsed_seconds':0},isolate=True) or {'elapsed_seconds':0}
    if not isinstance(state,dict):state={'elapsed_seconds':0}
    if done==frames:
        cached={'elapsed_seconds':state.get('elapsed_seconds',0),'frames':done,'timings':state.get('timings',{})};atomic(cache,cached)
        return records,cached['elapsed_seconds']
    elapsed=state['elapsed_seconds'];started=time.monotonic();last_count=done
    timings=state.get('timings',{}) if isinstance(state.get('timings',{}),dict) else {}
    for key in ['decode_wait_seconds','detection_seconds','preprocess_seconds','inference_seconds','postprocess_seconds','image_write_seconds','journal_seconds','replay_decode_seconds']:
        timings.setdefault(key,0.)
    zoom_buckets={int(p.stem.split('-')[-1])//60 for p in folder.glob('zoom-*.jpg')}
    last_fallback_bucket=((done-1)*interval)//15 if done else -1
    decode_source,decode_filter,start_index,decode_frames,recovered=decode_plan(source,folder,done,frames,interval)
    backend=getattr(detector,'decode_backend','rkmpp')
    selection=decode_filter.split(',hwdownload',1)[0]
    args=frame_args(decode_source,selection,(960,540),decode_frames,backend)
    initial_stage='recovering' if recovered else 'replaying' if done else 'scanning'
    progress(stage=initial_stage,second=min(seconds,done*interval),total=seconds,samples_done=done,samples_total=frames,sample_interval_seconds=interval,message='正在恢复解码，已有识别结果保留' if done else 'NPU 识别与分区补检')
    with (folder/'decode.log').open('a') as log, (folder/'detections.jsonl').open('a') as journal:
        proc=subprocess.Popen(args,stdout=subprocess.PIPE,stderr=log)
        reader=FrameReader(proc.stdout,960*540*3,decode_frames)
        try:
            for index in range(start_index,frames):
                second=sample_second(index,interval)
                wait_started=time.monotonic()
                data=reader.read()
                wait_elapsed=time.monotonic()-wait_started
                timings['decode_wait_seconds']+=wait_elapsed
                if len(data)!=960*540*3:raise RuntimeError('VPU 解码不完整：'+str(second))
                if index<done:
                    timings['replay_decode_seconds']+=wait_elapsed
                    if index%max(1,60//interval)==0:
                        progress(stage='replaying',message='正在恢复解码；已识别部分无需重复识别',samples_done=done,samples_total=frames,
                                 second=done*interval,total=seconds,unit='frames',**rate_fields(index+1,done,time.monotonic()-started))
                    continue
                rgb=np.frombuffer(data,np.uint8).reshape(540,960,3)
                detection_started=time.monotonic();before=dict(detector.profile)
                extended=detection_profile!='fast'
                baseline,multi,npu=detector.multiview(rgb,(1,2,3) if extended else (1,3))
                zoom=[];zsec=0
                fallback=not any(p['score']>=.25 for p in multi)
                if fallback and extended:zoom,zsec=detector.zoom_fallback(rgb,.35)
                if extended:last_fallback_bucket=second//15
                timings['detection_seconds']+=time.monotonic()-detection_started
                for key,value in detector.profile.items():timings[key]+=value-before[key]
                people=multi+zoom
                row={'second':second,'sample_index':index,'sample_interval_seconds':interval,'baseline':baseline,'multiview':multi,'zoom':zoom,'combined':people,
                     'zoom_applied':fallback and extended,'npu_seconds':npu+zsec,'zoom_npu_seconds':zsec}
                image_started=time.monotonic()
                if diagnostics and second%SETTINGS['diagnostic_interval_seconds']==0:
                    # Keep clean diagnostic images, annotations belong only in HTML.
                    cv2.imwrite(str(folder/('frame-{:05d}.jpg'.format(second))),cv2.cvtColor(rgb,cv2.COLOR_RGB2BGR))
                    row['image_path']='frame-{:05d}.jpg'.format(second)
                if diagnostics and zoom and second//60 not in zoom_buckets:
                    cv2.imwrite(str(folder/('zoom-{:05d}.jpg'.format(second))),cv2.cvtColor(rgb,cv2.COLOR_RGB2BGR))
                    row['zoom_image_path']='zoom-{:05d}.jpg'.format(second);zoom_buckets.add(second//60)
                timings['image_write_seconds']+=time.monotonic()-image_started
                journal_started=time.monotonic()
                journal.write(json.dumps(row,ensure_ascii=False)+'\n');journal.flush();records.append(row)
                timings['journal_seconds']+=time.monotonic()-journal_started
                last_count=index+1
                if index%max(1,10//interval)==0:
                    active=elapsed+time.monotonic()-started
                    atomic(folder/'scan-state.json',{'elapsed_seconds':active,'second':last_count,'timings':timings})
                    progress(stage='scanning',second=min(seconds,last_count*interval),total=seconds,samples_done=last_count,samples_total=frames,sample_interval_seconds=interval,message='NPU 识别与分区补检',timings=timings,unit='frames',**rate_fields(last_count,frames,active))
            reader.close();proc.stdout.close()
            if proc.wait(timeout=30):raise RuntimeError('VPU 解码失败')
        finally:
            if proc.poll() is None:proc.kill();proc.wait()
            reader.close();proc.stdout.close()
            atomic(folder/'scan-state.json',{'elapsed_seconds':elapsed+time.monotonic()-started,'second':last_count,'timings':timings})
    elapsed+=time.monotonic()-started
    atomic(cache,{'elapsed_seconds':elapsed,'frames':len(records),'timings':timings})
    return records,elapsed
