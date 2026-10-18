"""Discard non-key packets before VPU decoding; NPU person-presence only."""
import json,subprocess,time,math
import numpy as np
import pipeline as core
from runtime_metrics import FrameReader,rate_fields
from state_io import read_json,quarantine
from media_backend import frame_args


def select_samples(keys,seconds,interval,source_offset=0,night_seconds=0):
    result=[];last=None;last_night=None
    for index,t in enumerate(keys):
        if t>=seconds:break
        night=bool(night_seconds and source_offset+t<night_seconds)
        step=max(60,interval) if night else interval
        if last is None or night!=last_night or t-last>=step-.0001:
            result.append((index,t));last=t;last_night=night
    return result


def scan(detector,source,seconds,folder,progress,interval,keys,source_offset=0,night_seconds=0):
    selected=select_samples(keys,seconds,interval,source_offset,night_seconds)
    if not selected:raise RuntimeError('当前分段没有可采样关键帧')
    journal=folder/'detections.jsonl';records=[];valid=0
    if journal.exists():
        with journal.open('rb') as f:
            for line in f:
                try:r=json.loads(line)
                except ValueError:break
                if not isinstance(r,dict) or len(records)>=len(selected):break
                try:
                    if abs(r['second']-selected[len(records)][1])>.0001:break
                except (KeyError,TypeError):break
                records.append(r);valid=f.tell()
        with journal.open('r+b') as f:f.truncate(valid)
    cache=folder/'scan-cache.json'
    cached=read_json(cache,None,recover=True,isolate=True)
    if cached is not None and (not isinstance(cached,dict) or 'elapsed_seconds' not in cached):quarantine(cache);cached=None
    if cached and len(records)==len(selected):return records,cached['elapsed_seconds']
    saved=read_json(folder/'scan-state.json',{},isolate=True) or {}
    if not isinstance(saved,dict):saved={}
    timings=saved.get('timings',{}) if isinstance(saved.get('timings',{}),dict) else {};elapsed=saved.get('elapsed_seconds',0.);started=time.monotonic();done=len(records)
    for k in ['decode_wait_seconds','detection_seconds','preprocess_seconds','inference_seconds','postprocess_seconds','image_write_seconds','journal_seconds','replay_decode_seconds']:timings.setdefault(k,0.)
    rotation=records[-1].get('rotation',0) if records else 0
    last_probe=max([r['second'] for r in records if r.get('direction_probe')] or [-1000000])
    remaining=selected[done:]
    if not remaining:
        core.atomic(cache,{'elapsed_seconds':elapsed,'timings':timings,'frames':len(records),'sampling':'keyframes'});return records,elapsed
    # Match real PTS rather than decoded frame count: a dropped key must not shift
    # every subsequent sample. A missing sample triggers whole-segment fallback.
    expression='+'.join('lt(abs(t-{:.9f}),0.02)'.format(t) for i,t in remaining)
    selection="setpts='if(isnan(PTS),{:.9f}/TB,PTS)',select='({})*(isnan(prev_selected_t)+gt(t-prev_selected_t,0.04))'".format(keys[0],expression)
    args=frame_args(source,selection,(640,360),len(remaining),getattr(detector,'decode_backend','rkmpp'),True)
    progress(stage='scanning',message='关键帧极速识别',samples_done=done,samples_total=len(selected),second=remaining[0][1],total=seconds)
    with (folder/'decode.log').open('a') as log,journal.open('a') as output:
        p=subprocess.Popen(args,stdout=subprocess.PIPE,stderr=log);reader=FrameReader(p.stdout,640*360*3,len(remaining))
        try:
            for n,(index,second) in enumerate(remaining,done):
                night=bool(night_seconds and source_offset+second<night_seconds)
                active_interval=max(60,interval) if night else interval
                threshold=.15 if night else .20
                start=time.monotonic();data=reader.read();timings['decode_wait_seconds']+=time.monotonic()-start
                if len(data)!=640*360*3:raise RuntimeError('关键帧解码未完成，当前分段进度已保留')
                rgb=np.frombuffer(data,np.uint8).reshape(360,640,3);before=dict(detector.profile);start=time.monotonic()
                score=detector.presence(np.rot90(rgb,rotation).copy() if rotation else rgb)
                probed=False
                if score<threshold and second-last_probe>=600:
                    probed=True;last_probe=second;best=score;chosen=rotation
                    for turn in range(4):
                        if turn==rotation:continue
                        candidate=detector.presence(np.rot90(rgb,turn).copy() if turn else rgb)
                        if candidate>best:best=candidate;chosen=turn
                    if best>=threshold:rotation=chosen;score=best
                timings['detection_seconds']+=time.monotonic()-start
                for k,v in detector.profile.items():timings[k]+=v-before[k]
                positive=score>=threshold
                row={'second':second,'sample_index':n,'keyframe_index':index,'sample_interval_seconds':active_interval,'person_present':positive,'night_relaxed':night,
                     'combined':[{'score':score,'presence_only':True}] if positive else [],'baseline':[],'multiview':[],'zoom':[],
                     'zoom_applied':False,'processing_policy':'turbo','confidence':score,'rotation':rotation,'direction_probe':probed}
                start=time.monotonic();output.write(json.dumps(row,ensure_ascii=False)+'\n');output.flush();records.append(row)
                timings['journal_seconds']+=time.monotonic()-start
                used=elapsed+time.monotonic()-started
                core.atomic(folder/'scan-state.json',{'elapsed_seconds':used,'timings':timings,'samples_done':len(records)})
                progress(stage='scanning',message='夜间宽松粗筛 · 约60秒一次' if night else '日间关键帧极速识别',second=second,total=seconds,samples_done=len(records),samples_total=len(selected),
                         night_relaxed=night,sample_interval_seconds=active_interval,
                         sampling='keyframes',timings=timings,unit='frames',**rate_fields(len(records),len(selected),used))
            reader.close();p.stdout.close()
            if p.wait(timeout=30):raise RuntimeError('当前分段关键帧解码失败')
        finally:
            if p.poll() is None:p.kill();p.wait()
            reader.close();p.stdout.close()
            core.atomic(folder/'scan-state.json',{'elapsed_seconds':elapsed+time.monotonic()-started,'timings':timings,'samples_done':len(records)})
    elapsed+=time.monotonic()-started;core.atomic(cache,{'elapsed_seconds':elapsed,'timings':timings,'frames':len(records),'sampling':'keyframes'})
    return records,elapsed
