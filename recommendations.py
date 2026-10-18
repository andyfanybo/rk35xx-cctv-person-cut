"""Conservative default: keep validated people candidates, preserve manual choices."""
POLICY='keep-validated-v1'

def recommendation(clip,review=None):
    review=review or {}
    if clip.get('processing_policy')=='quick' and clip.get('stream_copy') is True:
        return {'decision':'keep','reason':'自动保留识别出的有人片段，直接合并出片','policy':'quick-automatic'}
    ok=(clip.get('video_parameters_match') is True and clip.get('source_range_check',{}).get('passed') is True
        and clip.get('keyframe_check',{}).get('passed') is True and clip.get('sync_check',{}).get('passed') is True
        and clip.get('copied_video_packets_verified',0)>0 and clip.get('copied_audio_packets_verified',0)>0)
    if not ok:return {'decision':'pending','reason':'画面或音视频校验不完整，需查看','policy':POLICY}
    if any(not w.get('passed') and w.get('status')!='inconclusive-silence' for w in clip.get('waveform_check',[])):
        return {'decision':'pending','reason':'波形校验存在异常，需查看','policy':POLICY}
    if review.get('decision','pending')=='pending' and review.get('note','').strip():
        return {'decision':'pending','reason':'已有待复核备注，保留你的判断','policy':POLICY}
    return {'decision':'keep','reason':'识别到有人且画面、包来源与同步校验通过；允许多保留少量无人画面','policy':POLICY}

def apply_default(results,reviews,now):
    changed=0;kept=dict(reviews)
    for sample in results:
        for clip in sample['clips']:
            old=kept.get(clip['path'],{})
            if old.get('decision','pending')!='pending':continue
            rec=recommendation(clip,old)
            if rec['decision']!='keep':continue
            kept[clip['path']]={'decision':'keep','note':old.get('note',''),'updated':now,
                'origin':'recommendation','reason':rec['reason'],'policy':POLICY};changed+=1
    return kept,changed
