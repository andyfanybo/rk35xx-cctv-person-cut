import errno,json,tempfile,unittest
from pathlib import Path
from unittest.mock import MagicMock,patch
import merge_recovery as storage
import quick_pipeline as qp
from task_control import finish_export,pause_job


class MergeSpaceTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        self.out=self.root/'exports';self.out.mkdir();self.dest=self.root/'published';self.dest.mkdir()
        self.clips=[{'path':'clip.mp4','size':100,'available':True}]

    def info(self,publish=False):return storage.merge_space(self.clips,self.root,self.out,self.dest,publish)

    def test_cleanup_then_manual_retry_has_fresh_space_check(self):
        with patch.object(storage.shutil,'disk_usage',return_value=MagicMock(free=0)):
            info=self.info();self.assertFalse(info['space_ready'])
            with self.assertRaisesRegex(ValueError,'清理后可继续合并'):storage.require_space(info)
        with patch.object(storage.shutil,'disk_usage',return_value=MagicMock(free=4*storage.RESERVE)):
            storage.require_space(self.info())

    def test_same_filesystem_reserves_merge_and_publish_space(self):
        with patch.object(storage,'same_device',return_value=True):info=self.info(publish=True)
        self.assertEqual(info['disks'][0]['required_bytes'],storage.RESERVE+200)

    def test_completed_merge_can_resume_publication_without_remerging(self):
        (self.out/'merged.mp4').write_bytes(b'merged')
        (self.out/'merge-ready.json').write_text(json.dumps({'selected_clips':['clip.mp4'],'size':6}))
        self.clips[0]['available']=False
        with patch.object(storage,'same_device',return_value=False),patch.object(storage.shutil,'disk_usage',side_effect=[MagicMock(free=10),MagicMock(free=2*storage.RESERVE)]):
            info=self.info(publish=True)
        self.assertTrue(info['merged_ready']);self.assertTrue(info['can_retry']);self.assertTrue(info['space_ready'])
        self.assertEqual(info['disks'][0]['required_bytes'],0)

    def test_missing_candidates_are_not_silently_rescanned(self):
        self.clips[0]['available']=False
        info=self.info();self.assertFalse(info['can_retry'])
        with self.assertRaisesRegex(ValueError,'片段已缺失'):storage.require_space(info)

    def test_enospc_and_ffmpeg_disk_errors_are_recoverable(self):
        for error in [OSError(errno.ENOSPC,'disk full'),RuntimeError('No space left on device'),ValueError('处理盘空间不足')]:
            receipt=storage.failure_receipt(error)
            self.assertEqual(receipt['blocked_reason'],'insufficient_space');self.assertTrue(receipt['recoverable'])

    def test_failed_merge_writes_receipt_and_preserves_candidates(self):
        job=self.root/'job.json';job.write_text('{}');clip=self.root/'clip.mp4';clip.write_bytes(b'preserved')
        with patch.object(qp,'_merge',side_effect=OSError(errno.ENOSPC,'full')):
            with self.assertRaises(OSError):qp.merge(job,'b'*32,False)
        receipt=json.loads((self.root/'exports'/('b'*32)/'status.json').read_text())
        self.assertEqual(receipt['blocked_reason'],'insufficient_space');self.assertEqual(clip.read_bytes(),b'preserved')


class PauseTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.folder=Path(self.tmp.name)
        self.save=MagicMock();self.signal=MagicMock()

    def test_queued_recognition_pauses_without_deleting_cache(self):
        cache=self.folder/'cache';cache.write_bytes(b'progress');job={'state':'queued'}
        pause_job(job,self.folder,None,self.signal,self.save)
        self.assertEqual(job['state'],'paused');self.assertEqual(cache.read_bytes(),b'progress');self.signal.assert_not_called()

    def test_running_export_pause_targets_process_and_preserves_scan_done(self):
        out=self.folder/'exports'/('a'*32);out.mkdir(parents=True)
        (out/'status.json').write_text(json.dumps({'state':'merging'}))
        proc=MagicMock();proc.poll.return_value=None
        job={'state':'done','exports':[{'id':'a'*32,'state':'running'}]}
        pause_job(job,self.folder,proc,self.signal,self.save)
        self.assertEqual(job['state'],'done');self.assertEqual(job['exports'][0]['state'],'pausing');self.signal.assert_called_once_with(proc)
        receipt=finish_export(job,job['exports'][0],{'state':'failed'},1,now=10)
        self.assertEqual(receipt['state'],'paused');self.assertEqual(job['state'],'done')

    def test_successful_publication_wins_pause_race(self):
        job={'pause_requested':True};export={'state':'pausing','pause_requested':True}
        receipt=finish_export(job,export,{'state':'done','size':8},130,now=10)
        self.assertEqual(receipt['state'],'done');self.assertEqual(job['processing_finished_at'],10)

    def test_queued_export_pauses_without_changing_recognition_state(self):
        out=self.folder/'exports'/('a'*32);out.mkdir(parents=True)
        job={'state':'done','exports':[{'id':'a'*32,'state':'queued'}]}
        pause_job(job,self.folder,None,self.signal,self.save)
        self.assertEqual(job['state'],'done');self.assertEqual(job['exports'][0]['state'],'paused');self.signal.assert_not_called()


if __name__=='__main__':unittest.main()
