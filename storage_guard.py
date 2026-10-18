"""Validate actual backing mounts, not ismount(output-subdirectory)."""
import os,re
from pathlib import Path
from configuration import settings


def containing_mount(path):
    path=Path(path).resolve();found=None
    with open('/proc/self/mountinfo') as f:
        for line in f:
            left,right=line.rstrip().split(' - ',1);fields=left.split();extra=right.split()
            mount=Path(re.sub(r'\\([0-7]{3})',lambda m:chr(int(m.group(1),8)),fields[4]))
            if path==mount or mount in path.parents:
                if found is None or len(str(mount))>=len(found['mount']):found={'mount':str(mount),'fstype':extra[0],'source':extra[1]}
    if found is None:raise RuntimeError('无法确认成品存储挂载')
    return found


def ensure_publish_storage(root,policy=None):
    root=Path(root).resolve();probe=root
    while not probe.exists() and probe!=probe.parent:probe=probe.parent
    mount=containing_mount(probe);policy=policy or settings.storage
    if policy['policy']=='mount':
        expected=Path(policy['mount_path'])
        if not os.path.ismount(str(expected)) or mount['mount']!=str(expected):
            raise RuntimeError('Publication mount unavailable or changed; SSD cache retained')
        if policy.get('fstype') and mount['fstype']!=policy['fstype']:
            raise RuntimeError('Publication filesystem type does not match configuration')
    if policy.get('backing_mount'):
        backing=Path(policy['backing_mount'])
        if not os.path.ismount(str(backing)):raise RuntimeError('Publication backing disk is not mounted')
        a=os.statvfs(str(probe));b=os.statvfs(str(backing));total_a=a.f_blocks*a.f_frsize;total_b=b.f_blocks*b.f_frsize
        if not total_b or abs(total_a-total_b)>max(1024**3,total_b*.01):
            raise RuntimeError('Publication filesystem and backing disk do not match')
    return mount
