"""Cheap task wall-clock timing and source-relative intervals from saved summaries."""
import json
import math
import re
import time
from functools import lru_cache
from pathlib import Path


def number(value,nonnegative=True):
    if isinstance(value,bool):return None
    try:value=float(value)
    except (TypeError,ValueError):return None
    return value if math.isfinite(value) and (not nonnegative or value>=0) else None


@lru_cache(maxsize=512)
def legacy_started(log_path):
    """Read only the start of an old log, once; never scan media or full logs."""
    try:
        with Path(log_path).open('rb') as stream:lines=stream.read(32768).splitlines()
    except OSError:return None
    for line in lines:
        try:row=json.loads(line)
        except (ValueError,UnicodeError):continue
        if not isinstance(row,dict):continue
        stamp=number(row.get('updated'))
        if stamp is not None:
            elapsed=number(row.get('elapsed_seconds')) or 0
            return max(0,stamp-elapsed)
    return None


def begin_processing(job,folder,now=None):
    now=time.time() if now is None else now
    if number(job.get('processing_started_at')) is None:
        old=legacy_started(str(Path(folder)/'worker.log'))
        job['processing_started_at']=old if old is not None else now
        job['processing_timing_estimated']=old is not None
    job.pop('processing_finished_at',None)
    job.pop('processing_stopped_at',None)


def runtime_summary(job,exports=None,folder=None,now=None,progress=None):
    now=time.time() if now is None else now
    start=number(job.get('processing_started_at'));estimated=bool(job.get('processing_timing_estimated'))
    if start is None and folder is not None:
        start=legacy_started(str(Path(folder)/'worker.log'));estimated=start is not None
    exports=list(exports or []);last=exports[-1] if exports else {}
    end=number(job.get('processing_finished_at'))
    active=any(e.get('state') in ('queued','running','preparing','merging','verifying','publishing','copy-verifying') for e in exports)
    paused=job.get('state') in ('failed','cancelled','interrupted','cancelling') or last.get('state') in ('failed','cancelled','interrupted')
    complete=not active and not paused and (end is not None or (job.get('state')=='done' and (not exports or last.get('state')=='done')))
    state='done' if complete else 'paused' if paused else 'running' if start is not None else 'queued'
    if complete and end is None:
        end=number(last.get('updated')) if last.get('state')=='done' else None
        if end is None:end=number(job.get('updated'))
        estimated=True
    stopped=number(job.get('processing_stopped_at'))
    if paused and stopped is None:
        stopped=max([number(v) or 0 for v in (last.get('updated'),job.get('updated'),(progress or {}).get('updated'))]) or None
        estimated=True
    reference=end if complete else stopped if paused else now
    elapsed=max(0,min(now,reference)-start) if start is not None and reference is not None else None
    return {'started_at':start,'finished_at':end if complete else None,'elapsed_seconds':elapsed,
            'state':state,'estimated':estimated,'includes_pauses':True}


def windows(values,limit):
    clean=[]
    for item in values:
        if not isinstance(item,(list,tuple)) or len(item)!=2:continue
        a,b=number(item[0],False),number(item[1],False)
        if a is None or b is None:continue
        a=max(0,a);b=min(limit,b)
        if b>a:clean.append((a,b))
    merged=[]
    for a,b in sorted(clean):
        if merged and a<=merged[-1][1]+1e-7:merged[-1]=(merged[-1][0],max(b,merged[-1][1]))
        else:merged.append((a,b))
    return merged


def build_timelines(plan,results,metadata):
    meta={x.get('source'):x for x in metadata};groups={}
    for item in plan:groups.setdefault(item['source'],[])
    for row in results:groups.setdefault(row.get('source'),[]).append(row)
    timelines=[]
    for source,rows in groups.items():
        full=number(meta.get(source,{}).get('duration_seconds'))
        if full is None:full=max([number(r.get('source_duration_seconds')) or 0 for r in rows] or [0]) or None
        analyzed=max([number(r.get('analyzed_seconds')) or 0 for r in rows] or [0])
        if full is not None:analyzed=min(analyzed,full)
        total=full if full is not None else analyzed
        people=[];uncertain=[]
        for row in rows:
            people.extend(row.get('candidate_intervals',[]))
            if not row.get('candidate_intervals'):
                people.extend([c.get('requested_start'),c.get('requested_end')] for c in row.get('clips',[]))
            uncertain.extend(row.get('uncertain_intervals',[]))
            fallback=set(row.get('fallback_chunk_indices',[]))
            for clip in row.get('clips',[]):
                match=re.search(r'(?:^|/)kept-(\d+)-\d+\.mp4$',clip.get('path',''))
                if match and int(match.group(1))+1 in fallback:
                    uncertain.append([clip.get('requested_start'),clip.get('requested_end')])
        events={0:{},analyzed:{},total:{}}
        for kind,spans in [('people',windows(people,analyzed)),('uncertain',windows(uncertain,analyzed))]:
            for a,b in spans:
                events.setdefault(a,{})[kind]=events.setdefault(a,{}).get(kind,0)+1
                events.setdefault(b,{})[kind]=events.setdefault(b,{}).get(kind,0)-1
        points=sorted(events);counts={'people':0,'uncertain':0};segments=[]
        for a,b in zip(points,points[1:]):
            for kind,delta in events[a].items():counts[kind]+=delta
            if b-a<=1e-7:continue
            kind='unanalyzed' if a>=analyzed else 'uncertain' if counts['uncertain'] else 'people' if counts['people'] else 'no_detection'
            if segments and segments[-1]['kind']==kind:segments[-1]['end']=b;segments[-1]['duration_seconds']=b-segments[-1]['start']
            else:segments.append({'kind':kind,'start':a,'end':b,'duration_seconds':b-a})
        previous=None
        for segment in segments:
            segment['gap_since_previous_people_seconds']=max(0,segment['start']-previous) if segment['kind']=='people' and previous is not None else None
            if segment['kind']=='people':previous=segment['end']
        totals={k:sum(s['duration_seconds'] for s in segments if s['kind']==k) for k in ('people','no_detection','uncertain','unanalyzed')}
        timelines.append({'source':source,'duration_seconds':full,'analyzed_seconds':analyzed,'intervals':segments,
                          'people_count':sum(s['kind']=='people' for s in segments),'no_detection_count':sum(s['kind']=='no_detection' for s in segments),
                          'longest_no_detection_seconds':max([s['duration_seconds'] for s in segments if s['kind']=='no_detection'] or [0]),'totals':totals})
    return timelines
