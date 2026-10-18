"""Read MP4 key timestamps from small sample tables, without reading media packets."""
import mmap,struct,bisect
from mp4_timescale import boxes

def key_times(path):
    with open(str(path),'rb') as f,mmap.mmap(f.fileno(),0,access=mmap.ACCESS_READ) as d:
        def u32(p):return struct.unpack_from('>I',d,p)[0]
        def child(a,b):return {n:(s,e) for n,s,e in boxes(d,a,b)}
        top=child(0,len(d))
        if b'moof' in top:raise ValueError('fragmented MP4')
        moov=top[b'moov'];mv=child(*moov)[b'mvhd'][0];movie_scale=u32(mv+(20 if d[mv]==1 else 12))
        for name,a,b in boxes(d,*moov):
            if name!=b'trak':continue
            tr=child(a,b);md=child(*tr[b'mdia']);h=md[b'hdlr'][0]
            if bytes(d[h+8:h+12])!=b'vide':continue
            mh=md[b'mdhd'][0];scale=u32(mh+(20 if d[mh]==1 else 12))
            st=child(*child(*md[b'minf'])[b'stbl']);ts=st[b'stts'][0]
            runs=[];count=0;clock=0
            for i in range(u32(ts+4)):
                n,delta=struct.unpack_from('>II',d,ts+8+i*8);runs.append((count,count+n,clock,delta));count+=n;clock+=n*delta
            if b'stss' in st:
                ss=st[b'stss'][0];indices=[u32(ss+8+i*4)-1 for i in range(u32(ss+4))]
            else:indices=list(range(count))
            shifts=[];ctcount=0
            if b'ctts' in st:
                ct=st[b'ctts'][0]
                for i in range(u32(ct+4)):
                    n=u32(ct+8+i*8);value=struct.unpack_from('>i' if d[ct]==1 else '>I',d,ct+12+i*8)[0];shifts.append((ctcount,ctcount+n,value));ctcount+=n
            offset=0.
            if b'edts' in tr:
                el=child(*tr[b'edts'])[b'elst'][0];n=u32(el+4);stride=20 if d[el]==1 else 12;delay=0.;media=None
                for i in range(n):
                    p=el+8+i*stride
                    if d[el]==1:duration,at,rate=struct.unpack_from('>QqI',d,p)
                    else:duration,at,rate=struct.unpack_from('>IiI',d,p)
                    if rate!=65536:raise ValueError('non-normal edit rate')
                    if at==-1 and media is None:delay+=duration/movie_scale
                    elif media is None:media=at
                    else:raise ValueError('multiple media edits')
                offset=delay-(media or 0)/scale
            result=[];r=0;c=0
            for index in indices:
                if not 0<=index<count:raise ValueError('invalid sync sample')
                while r+1<len(runs) and index>=runs[r][1]:r+=1
                first,last,start,delta=runs[r];pts=start+(index-first)*delta
                if shifts:
                    while c+1<len(shifts) and index>=shifts[c][1]:c+=1
                    pts+=shifts[c][2]
                result.append(pts/scale+offset)
            if not result:raise ValueError('empty keyframe table')
            return result
    raise ValueError('no video track')

def movie_duration(path):
    """Container duration from its header; no media scan."""
    with open(str(path),'rb') as f,mmap.mmap(f.fileno(),0,access=mmap.ACCESS_READ) as d:
        moov=next((s,e) for n,s,e in boxes(d,0,len(d)) if n==b'moov')
        p=next(s for n,s,e in boxes(d,*moov) if n==b'mvhd')
        at=p+(20 if d[p]==1 else 12);scale=struct.unpack_from('>I',d,at)[0]
        ticks=struct.unpack_from('>Q' if d[p]==1 else '>I',d,at+4)[0]
        if not scale:raise ValueError('missing movie timescale')
        return ticks/scale
