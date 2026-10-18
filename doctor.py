"""Bounded environment diagnostics; hardware access is explicit and serialized."""
import argparse
import ctypes.util
import fcntl
import json
import os
import platform
import signal
import subprocess
import sys
import tempfile
from pathlib import Path
from configuration import settings


def soc_name():
    try:
        text = Path('/proc/device-tree/compatible').read_bytes().replace(b'\x00', b' ').decode(errors='replace')
    except OSError:
        return 'unknown'
    for soc in ('rk3566', 'rk3568', 'rk3576', 'rk3588'):
        if soc in text:
            return soc
    return 'unknown'


def program_info(program):
    try:
        p = subprocess.run([program, '-version'], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           universal_newlines=True, timeout=10)
        return {'path': program, 'ok': p.returncode == 0, 'version': p.stdout.splitlines()[0] if p.stdout else p.stderr[-1000:]}
    except (OSError, subprocess.TimeoutExpired) as error:
        return {'path': program, 'ok': False, 'error': str(error)}


def hardware(sample):
    # This function runs in a disposable child; native crashes cannot kill the UI.
    settings.prepare_work()
    with settings.npu_lock.open('a') as guard:
        try:
            fcntl.flock(guard, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {'ok': False, 'busy': True, 'error': 'NPU lock is busy; no hardware test was started'}
        from detect import PersonDetector
        import numpy as np
        detector = PersonDetector()
        try:
            score = detector.presence(np.zeros((640, 640, 3), np.uint8))
            if not np.isfinite(score):
                raise RuntimeError('NPU returned a non-finite score')
            runtime = detector.runtime
            result = {'ok': True, 'library': runtime.library_path,
                      'runtime': runtime.sdk.api_version.decode(errors='replace'),
                      'driver': runtime.sdk.drv_version.decode(errors='replace'),
                      'native_int8': runtime.presence_native_ok, 'zero_image_score': float(score)}
            if sample:
                from media_backend import select_backend, select_mode, validate_input
                from pipeline import probe
                from mp4_keys import key_times
                path = Path(sample).resolve(strict=True)
                validate_input(probe(path))
                backend = select_backend(path)
                keys = key_times(path)
                result.update(decode_backend=backend, keyframe_sampling=select_mode(path, backend, 'turbo', keys[0]))
            return result
        finally:
            detector.close()


def storage_test():
    from storage_guard import ensure_publish_storage
    ensure_publish_storage(settings.publish_dir)
    settings.publish_dir.mkdir(parents=True, exist_ok=True)
    # All writes are confined to a fresh private subdirectory.
    with tempfile.TemporaryDirectory(prefix='.cctv-doctor-', dir=str(settings.publish_dir)) as folder:
        a, b = Path(folder)/'partial', Path(folder)/'final'
        a.write_bytes(b'publication-probe')
        os.link(str(a), str(b))
        if not os.path.samefile(str(a), str(b)):
            raise RuntimeError('Hardlink identity is not preserved')
        try:
            os.link(str(a), str(b))
        except FileExistsError:
            pass
        else:
            raise RuntimeError('Publication link did not reject an existing destination')
    return {'ok': True, 'hardlink': True, 'no_overwrite': True}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--hardware', action='store_true', help='explicitly load model and test NPU in a child process')
    ap.add_argument('--sample', help='short user-owned HEVC+Opus MP4; used only with --hardware')
    ap.add_argument('--storage-test', action='store_true', help='test links using a temporary publication subdirectory')
    ap.add_argument('--hardware-child', action='store_true', help=argparse.SUPPRESS)
    args = ap.parse_args()
    if args.hardware_child:
        try:
            result = hardware(args.sample)
        except Exception as error:
            result = {'ok': False, 'error': str(error)}
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result['ok'] else 1
    if args.sample and not args.hardware:
        ap.error('--sample requires --hardware')
    result = {'soc': soc_name(), 'machine': platform.machine(), 'kernel': platform.release(),
              'python': platform.python_version(), 'ffmpeg': program_info(settings.ffmpeg),
              'ffprobe': program_info(settings.ffprobe),
              'model_exists': settings.model.is_file(),
              'rknn_library': settings.rknn_library or ctypes.util.find_library('rknnrt'),
              'hardware_tested': False, 'storage_tested': False,
              'configured_decoder': settings.decoder, 'source_roots': list(settings.sources)}
    if args.hardware:
        cmd = [sys.executable, str(Path(__file__).resolve()), '--hardware-child']
        if args.sample:
            cmd += ['--sample', args.sample]
        try:
            p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                 universal_newlines=True, start_new_session=True)
            try:
                stdout, stderr = p.communicate(timeout=90)
            finally:
                try:os.killpg(p.pid, signal.SIGKILL)
                except ProcessLookupError:pass
                p.communicate()
            result['hardware'] = json.loads(stdout.strip().splitlines()[-1])
        except (OSError, ValueError, IndexError, subprocess.TimeoutExpired) as error:
            result['hardware'] = {'ok': False, 'error': str(error)}
        result['hardware_tested'] = True
    if args.storage_test:
        try:
            result['storage'] = storage_test()
        except Exception as error:
            result['storage'] = {'ok': False, 'error': str(error)}
        result['storage_tested'] = True
    ok = result['ffmpeg']['ok'] and result['ffprobe']['ok']
    ok = ok and result.get('hardware', {'ok': True})['ok'] and result.get('storage', {'ok': True})['ok']
    result['ok'] = bool(ok)
    result['ready_for_jobs'] = bool(ok and args.hardware and args.sample and args.storage_test)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
