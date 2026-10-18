"""Bounded clock validation and observable FFmpeg/file operations."""
import os
import selectors
import subprocess
import time
import queue
import threading
from pathlib import Path


class FrameReader:
    """Read at most two frames ahead so decoding can overlap serial inference."""
    def __init__(self,stream,frame_bytes,count):
        self.stream=stream;self.frame_bytes=frame_bytes;self.count=count
        self.queue=queue.Queue(maxsize=2);self.stop=threading.Event();self.read_seconds=0.
        self.thread=threading.Thread(target=self._run,daemon=True);self.thread.start()

    def _put(self,value):
        while not self.stop.is_set():
            try:self.queue.put(value,timeout=.2);return
            except queue.Full:pass

    def _run(self):
        try:
            for _ in range(self.count):
                if self.stop.is_set():break
                started=time.monotonic();data=self.stream.read(self.frame_bytes)
                self.read_seconds+=time.monotonic()-started
                self._put((data,None))
                if len(data)!=self.frame_bytes:break
        except Exception as e:self._put((None,e))

    def read(self,timeout=90):
        deadline=time.monotonic()+timeout
        while True:
            remaining=deadline-time.monotonic()
            if remaining<=0:raise TimeoutError('解码超过90秒未输出新帧；记录已保留，可恢复')
            try:
                data,error=self.queue.get(timeout=min(.5,remaining))
                if error:raise error
                return data
            except queue.Empty:
                if not self.thread.is_alive():raise RuntimeError('解码帧读取线程提前退出')

    def close(self):
        self.stop.set();self.thread.join(timeout=3)


def rate_fields(done,total,elapsed):
    total=max(0,total);done=min(max(0,done),total)
    rate=done/elapsed if elapsed>=3 and done>=max(1,total*.001) else None
    return {'done':min(done,total),'total_units':total,'elapsed_seconds':elapsed,
            'rate_per_second':rate,'remaining_seconds':0 if total and done>=total else max(0,total-done)/rate if rate else None}


def copy_progress(source,target,callback):
    source=Path(source);target=Path(target);total=source.stat().st_size;done=0
    started=time.monotonic();last=started
    callback(**rate_fields(0,total,0))
    with source.open('rb') as src,target.open('xb') as dst:
        for chunk in iter(lambda:src.read(1024*1024),b''):
            dst.write(chunk);done+=len(chunk);now=time.monotonic()
            if now-last>=1:
                callback(**rate_fields(done,total,now-started));last=now
        dst.flush();os.fsync(dst.fileno())
    callback(**rate_fields(done,total,time.monotonic()-started))


def media_command(args,duration,log,callback,timeout=7200):
    """Read FFmpeg progress without blocking indefinitely on a stalled pipe."""
    started=time.monotonic();selector=selectors.DefaultSelector();buffer=b''
    last=0.;fields={}
    with Path(log).open('ab') as errors:
        proc=subprocess.Popen([args[0],'-progress','pipe:1','-nostats']+args[1:],
                              stdout=subprocess.PIPE,stderr=errors)
        selector.register(proc.stdout,selectors.EVENT_READ)
        try:
            while selector.get_map():
                if time.monotonic()-started>timeout:
                    raise TimeoutError('FFmpeg阶段超时；保留原片和恢复记录')
                for key,_ in selector.select(.5):
                    chunk=os.read(key.fd,65536)
                    if not chunk:
                        selector.unregister(key.fileobj);continue
                    buffer+=chunk
                    while b'\n' in buffer:
                        line,buffer=buffer.split(b'\n',1)
                        text=line.decode(errors='replace').strip()
                        if '=' not in text:continue
                        name,value=text.split('=',1);fields[name]=value
                        if name=='progress':
                            try:second=float(fields.get('out_time_us',fields.get('out_time_ms','0')))/10**6
                            except ValueError:second=0
                            now=time.monotonic()
                            if now-last>=1 or value=='end':
                                callback(**rate_fields(second,duration,now-started));last=now
            code=proc.wait(timeout=30)
            if code:
                with Path(log).open('rb') as f:
                    f.seek(max(0,Path(log).stat().st_size-3000))
                    raise RuntimeError(f.read().decode(errors='replace'))
        finally:
            selector.close();proc.stdout.close()
            if proc.poll() is None:proc.kill();proc.wait()
    callback(**rate_fields(duration,duration,time.monotonic()-started))
