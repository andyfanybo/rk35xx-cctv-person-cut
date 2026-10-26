from task_overview import build_timelines
"""Task totals with explicit analysis scope and byte estimates from real candidates."""

def union_seconds(intervals,start,end):
    windows=sorted((max(start,float(a)),min(end,float(b))) for a,b in intervals if float(b)>start and float(a)<end)
    total=0.;left=None;right=None
    for a,b in windows:
        if b<=a:continue
        if left is None:left,right=a,b
        elif a<=right:right=max(right,b)
        else:total+=right-left;left,right=a,b
    return total+(right-left if left is not None else 0.)

def summarize(plan,results,reviews,metadata,exports=None):
    inputs=list(dict.fromkeys(x['source'] for x in plan));meta={x['source']:x for x in metadata}
    groups={s:[] for s in inputs}
    for row in results:
        groups.setdefault(row['source'],[]).append(row)
        entry=meta.setdefault(row['source'],{'source':row['source']})
        if row.get('source_duration_seconds') is not None:entry['duration_seconds']=row['source_duration_seconds']
        if row.get('source_size_bytes') is not None:entry['size_bytes']=row['source_size_bytes']
    total_time=sum(meta.get(s,{}).get('duration_seconds') or 0 for s in inputs)
    total_known=all(meta.get(s,{}).get('duration_seconds') is not None for s in inputs)
    size_known=all(meta.get(s,{}).get('size_bytes') is not None for s in inputs)
    total_size=sum(meta.get(s,{}).get('size_bytes') or 0 for s in inputs)
    analyzed=people=extra=positive=0.;clips=[]
    for source,rows in groups.items():
        length=max([float(r.get('analyzed_seconds',0)) for r in rows] or [0])
        full=meta.get(source,{}).get('duration_seconds');length=min(length,float(full)) if full is not None else length
        windows=[w for r in rows for w in r.get('candidate_intervals',[])]
        keep=union_seconds(windows,0,length)
        analyzed+=length;people+=keep
        extra+=max(0,union_seconds(windows,0,float(full) if full is not None else float('inf'))-keep)
        positive+=max([r.get('combined_positive_seconds',r.get('multiview_positive_seconds',0)) for r in rows] or [0])
        clips.extend(c for r in rows for c in r.get('clips',[]))
    scanned=set(r['label'] for r in results)
    complete=all(x['label'] in scanned for x in plan) and all(r.get('candidates_complete',True) for r in results)
    estimate=sum(c['size'] for c in clips) if complete else None
    kept=[c for c in clips if reviews.get(c['path'],{}).get('decision')=='keep']
    current=[c['path'] for c in kept]
    final=next((e for e in reversed(exports or []) if e.get('state')=='done' and e.get('selected_clips')==current),None)
    return {'timelines':build_timelines(plan,results,metadata),'source_total_duration_seconds':total_time if total_known else None,
        'source_total_bytes':total_size if size_known else None,'analyzed_seconds':analyzed,
        'people_candidate_seconds':people,'no_person_detected_seconds':max(0,analyzed-people),
        'unanalyzed_seconds':max(0,total_time-analyzed) if total_known else None,
        'keyframe_extra_seconds':extra,'positive_sample_seconds':positive,
        'people_estimated_bytes':estimate,'selected_estimated_bytes':sum(c['size'] for c in kept) if complete else None,
        'merged_actual_bytes':final.get('size') if final else None,'complete':complete,
        'estimate_method':'已生成候选文件实际大小合计，最终合并封装大小可能略有变化；不按时长比例推算',
        'scope':'有人=候选保留区间，含前后缓冲与关键帧扩展；无人=已分析区间中未检出而排除的部分；未分析不算无人'}

