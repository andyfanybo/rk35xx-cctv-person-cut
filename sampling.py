"""Sparse sampling on the proven 1 FPS time grid, without treating indices as seconds."""
INTERVALS=(1,2,3,5,10,15,30,60)

def validate_interval(value):
    if type(value) is not int or value not in INTERVALS:raise ValueError('采样间隔必须为1、2、3、5、10、15、30或60秒')
    return value

def sample_count(seconds,interval):return (seconds+validate_interval(interval)-1)//interval

def frame_filter(interval):
    validate_interval(interval)
    select='' if interval==1 else ",select='not(mod(n,{}))'".format(interval)
    return 'fps=1'+select+',hwdownload,format=nv12,scale=960:540'

def sample_second(index,interval):return index*validate_interval(interval)

def sample_windows(records,seconds,interval):
    validate_interval(interval)
    # A wider margin preserves uncertain gaps; it cannot recover an entirely missed event.
    result=[]
    for r in records:
        if not (r.get('person_present') or any(p['score']>=.25 for p in r['combined'])):continue
        step=r.get('sample_interval_seconds',interval)
        result.append([max(0,r['second']-max(5,step)),min(seconds,r['second']+step+10)])
    return result

def candidate_intervals(records,seconds,keys,interval):
    windows=[]
    for a,b in sample_windows(records,seconds,interval):
        if windows and a<=windows[-1][1]+5:windows[-1][1]=max(windows[-1][1],b)
        else:windows.append([a,b])
    result=[]
    for a,b in windows:
        left=[k for k in keys if k<=a];right=[k for k in keys if k>=b]
        a=max(left) if left else 0;b=min(right) if right else b
        if result and a<=result[-1][1]:result[-1][1]=max(result[-1][1],b)
        else:result.append([a,b])
    return result
