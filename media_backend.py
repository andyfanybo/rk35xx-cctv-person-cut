"""Probe a decode chain rather than guessing from an FFmpeg version."""
import functools
import subprocess
from configuration import settings


@functools.lru_cache(maxsize=8)
def _decoder_list(ffmpeg):
    p = subprocess.run([ffmpeg, '-hide_banner', '-decoders'], stdout=subprocess.PIPE,
                       stderr=subprocess.PIPE, timeout=10, universal_newlines=True)
    if p.returncode:
        raise RuntimeError('Cannot query FFmpeg decoders: ' + p.stderr[-1500:])
    return p.stdout


def frame_args(source, selection, size, count, backend, keyframes=False):
    args = [settings.ffmpeg, '-hide_banner', '-nostdin', '-loglevel', 'error']
    if keyframes:
        args += ['-discard', 'nokey']
    if backend == 'rkmpp':
        args += ['-c:v', 'hevc_rkmpp']
    download = ',hwdownload,format=nv12' if backend == 'rkmpp' else ''
    vf = selection + download + ',scale={}:{}'.format(*size)
    return args + ['-i', str(source), '-an', '-vf', vf, '-frames:v', str(count),
                   '-vsync', '0', '-pix_fmt', 'rgb24', '-f', 'rawvideo', 'pipe:1']


def probe_frame(source, backend, keyframes=False, selection='null'):
    args = frame_args(source, selection, (64, 64), 1, backend, keyframes)
    p = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=25)
    if p.returncode or len(p.stdout) != 64*64*3:
        raise RuntimeError('{} decode failed: {}'.format(backend, p.stderr.decode(errors='replace')[-2000:]))
    return p.stdout


def select_backend(source):
    available = 'hevc_rkmpp' in _decoder_list(settings.ffmpeg)
    candidates = []
    if settings.decoder != 'software' and available:
        candidates.append('rkmpp')
    if settings.decoder == 'software' or settings.decoder == 'auto' and settings.allow_software_decode:
        candidates.append('software')
    errors = []
    for backend in candidates:
        try:
            probe_frame(source, backend)
            return backend
        except (OSError, RuntimeError, subprocess.TimeoutExpired) as error:
            errors.append(str(error))
    raise RuntimeError('No working decode backend: {}. Configure software decoding explicitly, or install a compatible MPP stack.'.format('; '.join(errors) or 'hevc_rkmpp unavailable'))


def select_mode(source, backend, profile, first_key):
    if profile == 'turbo':
        selection = "setpts='if(isnan(PTS),{:.9f}/TB,PTS)',select='lt(abs(t-{:.9f}),0.02)'".format(first_key, first_key)
        try:
            probe_frame(source, backend, True, selection)
            return True
        except (OSError, RuntimeError, subprocess.TimeoutExpired):
            return False
    return False


def validate_input(meta):
    streams = meta.get('streams', [])
    video = [s for s in streams if s.get('codec_type') == 'video']
    audio = [s for s in streams if s.get('codec_type') == 'audio']
    if len(streams) != 2 or len(video) != 1 or len(audio) != 1 or video[0].get('codec_name') != 'hevc' or audio[0].get('codec_name') != 'opus':
        raise ValueError('Initial portable release supports one HEVC video + one Opus audio stream in MP4')
    if video[0].get('pix_fmt', 'yuv420p') != 'yuv420p':
        raise ValueError('Initial portable release requires 8-bit yuv420p input')
