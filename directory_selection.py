"""Browse server directories and freeze each job's publication location."""
import os
from pathlib import Path
from configuration import settings, contains
from storage_guard import containing_mount


def browse_roots():
    values = settings.browse_roots + list(settings.sources.values()) + [settings.publish_dir]
    roots = []
    for path in values:
        path = Path(path).resolve()
        if path.is_dir() and path not in roots and not contains(settings.work_dir, path):
            roots.append(path)
    return roots


def selectable(value):
    path = Path(value).expanduser().resolve(strict=True)
    if not path.is_dir() or not any(contains(root, path) for root in browse_roots()):
        raise ValueError('请选择设备可浏览范围内的文件夹')
    if contains(settings.work_dir, path) or contains(path, settings.work_dir):
        raise ValueError('原片和成品目录不能包含工作缓存，也不能放在工作缓存内')
    return path


def listing(value=None):
    roots = browse_roots()
    if not value:
        return {'roots': [str(p) for p in roots], 'path': None, 'parent': None, 'directories': []}
    path = Path(value).expanduser().resolve(strict=True)
    # A drive root may contain the work directory: it may be browsed, not selected.
    if not path.is_dir() or not any(contains(root, path) for root in roots):
        raise ValueError('文件夹不在可浏览范围内')
    directories = []
    for child in sorted(path.iterdir(), key=lambda p: p.name.lower()):
        if child.name.startswith('.'):
            continue
        try:
            resolved = child.resolve(strict=True)
            if child.is_dir() and any(contains(root, resolved) for root in roots) and not contains(settings.work_dir, resolved):
                directories.append({'name': child.name, 'path': str(resolved)})
        except (OSError, RuntimeError):
            continue
        if len(directories) >= 500:
            break
    parent = path.parent
    return {'roots': [str(p) for p in roots], 'path': str(path),
            'parent': str(parent) if parent != path and any(contains(root, parent) for root in roots) else None,
            'directories': directories, 'selectable': not (contains(path, settings.work_dir) or contains(settings.work_dir, path))}


def select_pair(input_dir, output_dir):
    source = selectable(input_dir)
    target = selectable(output_dir)
    if contains(source, target) or contains(target, source):
        raise ValueError('输入和输出文件夹不能相同或互相包含')
    if not os.access(str(source), os.R_OK | os.X_OK):
        raise ValueError('服务无法读取输入文件夹')
    if not os.access(str(target), os.W_OK | os.X_OK):
        raise ValueError('服务没有输出文件夹的写入权限')
    if target == settings.publish_dir:
        policy = dict(settings.storage)
    else:
        mount = containing_mount(target)
        policy = {'policy': 'local'} if mount['mount'] == '/' else {
            'policy': 'mount', 'mount_path': mount['mount'], 'fstype': mount['fstype']}
    return source, target, policy


def target_root(job, default):
    return Path(job.get('publish_dir') or default).resolve()


def target_policy(job):
    return job.get('publish_storage') or settings.storage


def target_receipt(job, default):
    return {'publish_root': str(target_root(job, default)), 'publish_storage': target_policy(job)}
