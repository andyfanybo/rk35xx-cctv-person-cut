"""Shared, dependency-free configuration for the server and workers."""
import json
import os
import re
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def contains(a, b):
    return a == b or a in b.parents


class Settings:
    def __init__(self, filename=None, environ=None):
        env = os.environ if environ is None else environ
        filename = filename or env.get('PERSON_CONFIG')
        self.config_file = Path(filename).expanduser().resolve() if filename else None
        raw = json.loads(self.config_file.read_text()) if self.config_file else {}
        if not isinstance(raw, dict):
            raise ValueError('Configuration must be a JSON object')
        known = {'work_dir', 'publish_dir', 'sources', 'ffmpeg', 'ffprobe', 'model',
                 'rknn_library', 'decoder', 'allow_software_decode', 'bind', 'port',
                 'storage', 'npu_lock', 'model_manifest', 'browse_roots'}
        if set(raw) - known:
            raise ValueError('Unknown configuration fields: ' + ', '.join(sorted(set(raw)-known)))
        base = self.config_file.parent if self.config_file else ROOT

        def path(value):
            p = Path(value).expanduser()
            return (p if p.is_absolute() else base / p).resolve()

        self.work_dir = path(env.get('PERSON_DATA', raw.get('work_dir', str(ROOT/'var/work'))))
        self.publish_dir = path(env.get('PERSON_PUBLISH_DIR', raw.get('publish_dir', str(ROOT/'var/published'))))
        self.model = path(env.get('PERSON_MODEL', raw.get('model', str(ROOT/'models/person.rknn'))))
        self.model_manifest = path(raw['model_manifest']) if raw.get('model_manifest') else None
        self.npu_lock = path(env.get('PERSON_NPU_LOCK', raw.get('npu_lock', str(self.work_dir/'npu.lock'))))
        self.rknn_library = env.get('PERSON_RKNN_LIBRARY', raw.get('rknn_library', ''))
        if self.rknn_library and '/' in self.rknn_library:
            self.rknn_library = str(path(self.rknn_library))
        self.ffmpeg = self._program(env.get('PERSON_FFMPEG', raw.get('ffmpeg', 'ffmpeg')), base)
        self.ffprobe = self._program(env.get('PERSON_FFPROBE', raw.get('ffprobe', 'ffprobe')), base)
        self.decoder = env.get('PERSON_DECODER', raw.get('decoder', 'auto'))
        if self.decoder not in ('auto', 'rkmpp', 'software'):
            raise ValueError('decoder must be auto, rkmpp or software')
        self.allow_software_decode = raw.get('allow_software_decode', False)
        if type(self.allow_software_decode) is not bool:
            raise ValueError('allow_software_decode must be a boolean')
        self.bind = env.get('PERSON_BIND', raw.get('bind', '127.0.0.1'))
        self.port = int(env.get('PERSON_PORT', raw.get('port', 8423)))
        if not 1 <= self.port <= 65535:
            raise ValueError('port must be between 1 and 65535')
        sources = raw.get('sources', {})
        if not isinstance(sources, dict):
            raise ValueError('sources must map names to recording directories')
        self.sources = {}
        for name, value in sources.items():
            if not re.fullmatch(r'[A-Za-z0-9_-]+', name):
                raise ValueError('Invalid source name: ' + name)
            self.sources[name] = path(value)
        browse = raw.get('browse_roots', ['/mnt', '/media', '/srv'])
        if not isinstance(browse, list) or not all(isinstance(p, str) for p in browse):
            raise ValueError('browse_roots must be a list of directory paths')
        self.browse_roots = [path(p) for p in browse]
        if Path('/') in self.browse_roots:
            raise ValueError('Use specific drive roots instead of / for directory browsing')
        roots = [self.work_dir, self.publish_dir] + list(self.sources.values())
        for i, a in enumerate(roots):
            if a == Path('/'):
                raise ValueError('A filesystem root cannot be a data directory')
            for b in roots[i+1:]:
                if contains(a, b) or contains(b, a):
                    raise ValueError('Work, publication and source directories must not overlap: {} / {}'.format(a, b))
        if contains(self.work_dir, self.model) or contains(self.publish_dir, self.model):
            raise ValueError('Keep models outside work and publication directories')
        self.storage = raw.get('storage', {'policy': 'local'})
        if not isinstance(self.storage, dict) or self.storage.get('policy') not in ('local', 'mount'):
            raise ValueError('storage.policy must be local or mount')
        if self.storage['policy'] == 'mount':
            if not self.storage.get('mount_path'):
                raise ValueError('mount storage requires mount_path')
            self.storage = dict(self.storage, mount_path=str(path(self.storage['mount_path'])))
            if not contains(Path(self.storage['mount_path']), self.publish_dir):
                raise ValueError('publish_dir must be within storage.mount_path')
        if self.storage.get('backing_mount'):
            self.storage = dict(self.storage, backing_mount=str(path(self.storage['backing_mount'])))

    @staticmethod
    def _program(value, base):
        if '/' in value:
            p = Path(value).expanduser()
            return str((p if p.is_absolute() else base / p).resolve())
        return shutil.which(value) or value

    def prepare_work(self):
        (self.work_dir/'tmp').mkdir(parents=True, exist_ok=True)
        self.npu_lock.parent.mkdir(parents=True, exist_ok=True)
        os.environ['TMPDIR'] = str(self.work_dir/'tmp')
        os.environ['SQLITE_TMPDIR'] = str(self.work_dir/'tmp')


settings = Settings()
