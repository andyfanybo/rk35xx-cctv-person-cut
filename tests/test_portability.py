import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch, MagicMock
import configuration
import media_backend as media
import storage_guard
import quick_pipeline as qp
import doctor
import fcntl


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def config(self, raw, env=None):
        path = self.root/'config.json'
        path.write_text(json.dumps(raw))
        return configuration.Settings(path, environ={} if env is None else env)

    def test_paths_are_relative_to_config_and_env_wins(self):
        cfg = self.config({'work_dir':'ssd/work', 'publish_dir':'nas/done',
                           'sources':{'front_door':'recordings'}, 'model':'weights/person.rknn'},
                          {'PERSON_DATA':str(self.root/'other/work')})
        self.assertEqual(cfg.work_dir, self.root/'other/work')
        self.assertEqual(cfg.sources['front_door'], self.root/'recordings')
        self.assertEqual(cfg.model, self.root/'weights/person.rknn')

    def test_overlapping_source_cache_or_publication_rejected(self):
        for changes in [{'work_dir':'data','sources':{'camera':'data/source'}},
                        {'work_dir':'data/work','sources':{'camera':'data'}},
                        {'work_dir':'data','publish_dir':'data/output'},
                        {'publish_dir':'data','sources':{'camera':'data/source'}}]:
            with self.assertRaises(ValueError):
                self.config(changes)

    def test_unknown_option_and_missing_mount_rejected(self):
        for raw in [{'workdir':'typo'}, {'storage':{'policy':'mount'}},
                    {'allow_software_decode':'false'}, {'decoder':'cuda'}]:
            with self.assertRaises(ValueError):
                self.config(raw)

    def test_import_configuration_does_not_create_directories(self):
        cfg = self.config({'work_dir':'new/work','publish_dir':'new/published'})
        self.assertFalse(cfg.work_dir.exists())


class DecoderTests(unittest.TestCase):
    def setUp(self):
        self.cfg = SimpleNamespace(ffmpeg='ffmpeg', decoder='auto', allow_software_decode=False)
        patcher = patch.object(media, 'settings', self.cfg)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_software_filter_has_no_hardware_download(self):
        args = media.frame_args('sample.mp4','fps=1,select=1',(640,360),2,'software')
        self.assertNotIn('hevc_rkmpp', args)
        self.assertNotIn('hwdownload', args[args.index('-vf')+1])

    def test_packet_discard_kept_before_input(self):
        args = media.frame_args('sample.mp4','select=1',(640,360),2,'rkmpp',True)
        self.assertLess(args.index('-discard'),args.index('-i'))
        self.assertIn('hwdownload',args[args.index('-vf')+1])
        self.assertNotIn('-skip_frame',args)

    def test_listed_decoder_must_actually_decode(self):
        with patch.object(media,'_decoder_list',return_value='hevc_rkmpp'), patch.object(media,'probe_frame',side_effect=RuntimeError('driver mismatch')):
            with self.assertRaisesRegex(RuntimeError,'driver mismatch'):
                media.select_backend('sample.mp4')

    def test_software_fallback_requires_explicit_permission(self):
        self.cfg.allow_software_decode=True
        with patch.object(media,'_decoder_list',return_value='hevc_rkmpp'), patch.object(media,'probe_frame',side_effect=[RuntimeError('bad hw'),b'frame']) as probe:
            self.assertEqual(media.select_backend('sample.mp4'),'software')
            self.assertEqual([c[0][1] for c in probe.call_args_list],['rkmpp','software'])

    def test_unsupported_turbo_becomes_continuous_sampling(self):
        with patch.object(media,'probe_frame',side_effect=subprocess.TimeoutExpired('ffmpeg',25)):
            self.assertFalse(media.select_mode('sample.mp4','rkmpp','turbo',.01))

    def test_input_stream_order_is_irrelevant_and_10bit_rejected(self):
        meta={'streams':[{'codec_type':'audio','codec_name':'opus'},
                         {'codec_type':'video','codec_name':'hevc','pix_fmt':'yuv420p'}]}
        media.validate_input(meta)
        meta['streams'][1]['pix_fmt']='yuv420p10le'
        with self.assertRaises(ValueError):
            media.validate_input(meta)


class StorageTests(unittest.TestCase):
    def test_missing_mount_does_not_fall_back_to_local_directory(self):
        cfg=SimpleNamespace(storage={'policy':'mount','mount_path':'/mnt/archive'})
        with patch.object(storage_guard,'settings',cfg), patch.object(storage_guard,'containing_mount',return_value={'mount':'/','fstype':'ext4'}), patch.object(storage_guard.os.path,'ismount',return_value=False):
            with self.assertRaises(RuntimeError):
                storage_guard.ensure_publish_storage('/mnt/archive/published')

    def test_filesystem_type_is_checked_when_configured(self):
        cfg=SimpleNamespace(storage={'policy':'mount','mount_path':'/mnt/archive','fstype':'nfs'})
        with patch.object(storage_guard,'settings',cfg), patch.object(storage_guard,'containing_mount',return_value={'mount':'/mnt/archive','fstype':'ext4'}), patch.object(storage_guard.os.path,'ismount',return_value=True):
            with self.assertRaises(RuntimeError):
                storage_guard.ensure_publish_storage('/mnt/archive/published')


class PreflightTests(unittest.TestCase):
    def test_busy_hardware_lock_skips_runtime_loading(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'shared-npu.lock'
            with path.open('a') as guard:
                fcntl.flock(guard,fcntl.LOCK_EX|fcntl.LOCK_NB)
                cfg=SimpleNamespace(npu_lock=path,prepare_work=lambda:None)
                with patch.object(doctor,'settings',cfg),patch('detect.PersonDetector') as detector:
                    result=doctor.hardware(None)
                    self.assertTrue(result['busy'])
                    detector.assert_not_called()

    def test_model_failure_stops_before_split_and_whole_segment_fallback(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);source=root/'source.mp4';source.write_bytes(b'video')
            model=root/'model.rknn';model.write_bytes(b'model')
            job={'id':'a'*32,'output':str(root/'results'),'plan':[{'source':str(source),'label':'sample-01','seconds':30}],
                 'publish_user':False,'detection_profile':'turbo','segment_minutes':1}
            (root/'job.json').write_text(json.dumps(job))
            cfg=SimpleNamespace(model=model,npu_lock=root/'npu.lock',prepare_work=lambda:None)
            meta={'streams':[{'codec_type':'video','codec_name':'hevc'},{'codec_type':'audio','codec_name':'opus'}],'format':{'duration':'30'}}
            with patch.object(qp,'settings',cfg),patch.object(qp.core,'RUNTIME_DATA',root),patch.object(qp.core,'probe',return_value=meta),patch.object(qp.shutil,'disk_usage',return_value=SimpleNamespace(free=10*1024**3)),patch.object(qp.core,'PersonDetector',side_effect=RuntimeError('incompatible model')),patch.object(qp,'prepare') as split:
                with self.assertRaisesRegex(RuntimeError,'incompatible model'):
                    qp.run(root/'job.json')
                split.assert_not_called()
                self.assertFalse(list(root.rglob('scan-failed.json')))


if __name__=='__main__':
    unittest.main()
