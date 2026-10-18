"""Short filesystem/process fault tests, without NPU or real NAS modifications."""
import sys
import json,os,shutil,signal,subprocess,tempfile,threading,time,unittest,uuid
from pathlib import Path
from unittest.mock import patch,MagicMock
import state_io,publication,server,cache_lifecycle,quick_pipeline as qp,turbo_scan
from process_control import wait_worker,signal_group

class Fixtures(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        model=self.root/'model.rknn';model.write_bytes(b'test-model')
        for field,value in [('model',model),('work_dir',self.root),('npu_lock',self.root/'npu.lock')]:
            patcher=patch.object(qp.settings,field,value);patcher.start();self.addCleanup(patcher.stop)
    def job(self):
        ident=uuid.uuid4().hex;folder=self.root/'jobs'/ident;(folder/'results').mkdir(parents=True)
        job={'id':ident,'state':'done','title':'test','created':0,'output':str(folder/'results'),'plan':[{'source':str(self.root/'original.mp4'),'label':'sample-01'}],'exports':[],'verification':'quick','output_mode':'automatic'}
        state_io.atomic_json(folder/'job.json',job);return folder,job

class StateTests(Fixtures):
    def test_progress_not_fsynced_but_checkpoint_is(self):
        with patch.object(state_io.os,'fsync') as sync:
            state_io.atomic_json(self.root/'progress.json',{'stage':'scanning'});self.assertEqual(sync.call_count,0)
            state_io.atomic_json(self.root/'chunk-0000.json',{'clips':[]});self.assertEqual(sync.call_count,2)
    def test_job_previous_recovers_corruption(self):
        p=self.root/'job.json';state_io.atomic_json(p,{'value':1});state_io.atomic_json(p,{'value':2});p.write_text('{')
        self.assertEqual(state_io.read_json(p,recover=True),{'value':1});self.assertTrue(list(self.root.glob('job.json.corrupt-*')))
    def test_null_split_manifest_is_rebuilt(self):
        folder=self.root/'sample';folder.mkdir();source=self.root/'source.mp4';source.write_bytes(b'source');(folder/'split-manifest.json').write_text('null')
        def split(*args):
            d=folder/'parts';(d/'part-0000.mp4').write_bytes(b'video');(folder/'split.csv').write_text('part-0000.mp4,0,3\n')
        with patch.object(qp,'media_command',side_effect=split),patch.object(qp,'keys',return_value=[0]),patch.object(qp,'movie_duration',return_value=3):
            parts=qp.prepare(source,3,{'streams':[{'codec_type':'video','start_time':0}]},folder,1,lambda **kw:None)
        self.assertEqual(len(parts),1);self.assertTrue(list(folder.glob('split-manifest.json.corrupt-*')))
    def test_partial_summary_recovers_completed_folder(self):
        root=self.root/'results';part=root/'sample-01';part.mkdir(parents=True)
        complete={'label':'sample-01','candidates_complete':True,'clips':[]}
        state_io.atomic_json(root/'summary.json',[dict(complete,candidates_complete=False)])
        state_io.atomic_json(part/'summary.json',complete)
        self.assertEqual(state_io.load_results(root),[complete])
    def test_bad_job_isolated_others_load(self):
        folder,j=self.job();bad=self.root/'jobs'/('a'*32);bad.mkdir();(bad/'job.json').write_text('')
        loaded={}
        with patch.object(server,'DATA',self.root),patch.object(server,'jobs',loaded),patch.object(server,'clean_stale_partials'):
            server.load_jobs()
        self.assertIn(j['id'],loaded);self.assertEqual(loaded['a'*32]['state'],'failed');self.assertTrue(loaded['a'*32]['imported'])
    def test_bad_created_type_cannot_block_queue(self):
        folder,j=self.job();j['created']='broken';state_io.atomic_json(folder/'job.json',j);loaded={}
        with patch.object(server,'DATA',self.root),patch.object(server,'jobs',loaded),patch.object(server,'clean_stale_partials'):server.load_jobs()
        self.assertEqual(loaded[j['id']]['state'],'failed');self.assertIsInstance(loaded[j['id']]['created'],float)
    def test_stale_status_persisted_failed(self):
        folder,j=self.job();e={'id':'b'*32,'state':'running'};j['exports']=[e];state_io.atomic_json(folder/'job.json',j)
        out=folder/'exports'/e['id'];out.mkdir(parents=True);state_io.atomic_json(out/'status.json',{'state':'publishing','done':20})
        loaded={}
        with patch.object(server,'DATA',self.root),patch.object(server,'jobs',loaded),patch.object(server,'clean_stale_partials'):
            server.load_jobs();view=server.public(loaded[j['id']])
        self.assertEqual(view['exports'][0]['state'],'failed');self.assertEqual(json.loads((out/'status.json').read_text())['state'],'failed')
    def test_public_failed_wins_over_old_active(self):
        folder,j=self.job();j['exports']=[{'id':'b'*32,'state':'failed','message':'interrupted'}];out=folder/'exports'/('b'*32);out.mkdir(parents=True);state_io.atomic_json(out/'status.json',{'state':'merging'})
        with patch.object(server,'DATA',self.root):self.assertEqual(server.public(j)['exports'][0]['state'],'failed')

class PublishTests(Fixtures):
    def setup_publish(self):
        out=self.root/('b'*32);out.mkdir();nas=self.root/'nas';nas.mkdir();source=self.root/'merged.mp4';source.write_bytes(b'video123')
        job={'id':'a'*32,'plan':[{'source':'/original/camera.mp4'}]};receipt={'state':'done','size':8,'duration':3,'selected_clips':[],'ssd_file':str(source)}
        return out,nas,source,job,receipt
    def test_link_before_receipt_crash_recovers(self):
        out,nas,source,job,receipt=self.setup_publish();real=publication.atomic_json
        def crash(path,value,**kwargs):
            if Path(path).name=='status.json':raise SystemExit('power loss')
            return real(path,value,**kwargs)
        with patch.object(publication,'PUBLISH_ROOT',nas),patch.object(publication,'ensure_publish_storage'),patch.object(publication,'atomic_json',side_effect=crash):
            with self.assertRaises(SystemExit):publication.publish(out,job,source,receipt,shutil.copyfile)
        with patch.object(publication,'PUBLISH_ROOT',nas),patch.object(publication,'ensure_publish_storage'):
            recovered=publication.recover(out,job)
        self.assertEqual(recovered['state'],'done');self.assertTrue(recovered['publication_recovered']);self.assertFalse(list(nas.glob('.*.partial')))
    def test_unrelated_same_size_not_adopted(self):
        out,nas,source,job,receipt=self.setup_publish();(nas/'camera.mp4').write_bytes(b'notvideo')
        with patch.object(publication,'PUBLISH_ROOT',nas),patch.object(publication,'ensure_publish_storage'):
            with self.assertRaises(ValueError):publication.publish(out,job,source,receipt,shutil.copyfile)
        self.assertEqual((nas/'camera.mp4').read_bytes(),b'notvideo')
    def test_tag_recovers_when_partial_name_gone(self):
        out,nas,source,job,receipt=self.setup_publish();final=nas/'camera.mp4';final.write_bytes(b'video123');partial=nas/('.camera.mp4.'+out.name+'.partial')
        state_io.atomic_json(out/'publication-intent.json',{'job_id':job['id'],'export_id':out.name,'final':str(final),'partial':str(partial),'size':8,'tag':'owned','receipt':receipt})
        with patch.object(publication,'PUBLISH_ROOT',nas),patch.object(publication,'ensure_publish_storage'),patch.object(publication,'has_tag',return_value=False):self.assertIsNone(publication.recover(out,job))
        with patch.object(publication,'PUBLISH_ROOT',nas),patch.object(publication,'ensure_publish_storage'),patch.object(publication,'has_tag',return_value=True):self.assertEqual(publication.recover(out,job)['state'],'done')
    def test_stale_partial_only_excludes_live_and_new(self):
        out,nas,source,job,receipt=self.setup_publish();old=nas/('.camera.mp4.'+'c'*32+'.partial');old.write_bytes(b'x');os.utime(str(old),(time.time()-7200,)*2)
        live=nas/('.camera.mp4.'+'d'*32+'.partial');live.write_bytes(b'x');os.utime(str(live),(time.time()-7200,)*2)
        fresh=nas/('.camera.mp4.'+'e'*32+'.partial');fresh.write_bytes(b'x')
        with patch.object(publication,'PUBLISH_ROOT',nas),patch.object(publication,'ensure_publish_storage'):
            publication.clean_stale_partials([],['d'*32])
        self.assertFalse(old.exists());self.assertTrue(live.exists());self.assertTrue(fresh.exists())
    def test_delete_removes_owned_partial_keeps_final(self):
        from job_delete import delete_task
        folder,job=self.job();job['exports']=[{'id':'b'*32}];nas=self.root/'nas';nas.mkdir();final=nas/'original.mp4';final.write_bytes(b'final');partial=nas/('.original.mp4.'+'b'*32+'.partial');partial.write_bytes(b'partial')
        with patch.object(cache_lifecycle,'PUBLISH_ROOT',nas),patch('storage_guard.ensure_publish_storage'):
            result=delete_task(self.root,job,[])
        self.assertFalse(partial.exists());self.assertTrue(final.exists());self.assertTrue(result['nas_partials_removed'])
    def test_cleanup_failure_never_claims_removed(self):
        folder,j=self.job();root=Path(j['output']);keep=root/'keep.mp4';keep.write_bytes(b'video');(root/'summary.json').write_text(json.dumps([{'candidates_complete':True,'clips':[{'path':'keep.mp4'}]}]));state_io.atomic_json(folder/'cache-cleanup.json',{'delivery':'published','videos_removed':False})
        original_unlink=Path.unlink
        def fail(p,*a,**kw):
            if p==keep:raise PermissionError('test')
            return original_unlink(p,*a,**kw)
        with patch.object(cache_lifecycle,'DATA',self.root),patch.object(Path,'unlink',fail):
            with self.assertRaises(PermissionError):cache_lifecycle.cleanup_automatic(folder/'job.json')
        self.assertFalse(json.loads((folder/'cache-cleanup.json').read_text())['videos_removed']);self.assertTrue(keep.exists())
        with patch.object(cache_lifecycle,'DATA',self.root):self.assertTrue(cache_lifecycle.cleanup_automatic(folder/'job.json')['videos_removed'])

class ProcessTests(Fixtures):
    def child(self):
        p=subprocess.Popen([sys.executable,'-u','-c','import signal,time;signal.signal(signal.SIGTERM,signal.SIG_IGN);print("ready",flush=True);time.sleep(60)'],stdout=subprocess.PIPE,start_new_session=True)
        p._person_group=p.pid;self.assertEqual(p.stdout.readline(),b'ready\n');self.addCleanup(lambda:signal_group(p,signal.SIGKILL) if p.poll() is None else None);return p
    def test_cancel_escalates(self):
        p=self.child();code,why=wait_worker(p,{'state':'cancelling'},None,threading.Event(),self.root/'progress.json',grace=.15,stall=2)
        self.assertEqual(code,-signal.SIGKILL);self.assertIsNone(why);p.stdout.close()
    def test_stall_escalates(self):
        p=self.child();code,why=wait_worker(p,{'state':'running'},None,threading.Event(),self.root/'progress.json',grace=.1,stall=.15)
        self.assertEqual(code,-signal.SIGKILL);self.assertTrue(why);p.stdout.close()

class PipelineTests(Fixtures):
    def test_failed_scan_keeps_whole_and_version_upgrade_resumes(self):
        folder,j=self.job();source=self.root/'source.mp4';source.write_bytes(b'fake source');j.update(state='running',plan=[{'label':'sample-01','source':str(source),'seconds':90}],publish_user=False,detection_profile='turbo',segment_minutes=1,sample_interval_seconds=30);state_io.atomic_json(folder/'job.json',j)
        def prepare(source,seconds,meta,d,*a):
            parts=d/'parts';parts.mkdir();p=parts/'part-0000.mp4';p.write_bytes(b'whole segment')
            return [{'path':str(p),'size':p.stat().st_size,'source_start':0,'source_end':90,'source_offset':0,'keyframes':[0,32,64]}]
        meta={'streams':[{'codec_name':'hevc','codec_type':'video'},{'codec_name':'opus','codec_type':'audio'}],'format':{'duration':'90'}}
        with patch.object(qp.shutil,'disk_usage',return_value=MagicMock(free=10*1024**3)),patch.object(qp,'select_backend',return_value='rkmpp'),patch.object(qp,'select_mode',return_value=True),patch.object(qp.core,'RUNTIME_DATA',self.root),patch.object(qp.core,'probe',return_value=meta),patch.object(qp,'prepare',side_effect=prepare),patch.object(qp.core,'PersonDetector',return_value=MagicMock()),patch.object(turbo_scan,'scan',side_effect=RuntimeError('bad frame')),patch.object(qp,'movie_duration',return_value=90):qp.run(folder/'job.json')
        row=json.loads((folder/'results/summary.json').read_text())[0];self.assertEqual(row['fallback_chunk_indices'],[1]);self.assertEqual((folder/'results'/row['clips'][0]['path']).read_bytes(),b'whole segment')
        fp=folder/'results/quick-settings.json';settings=json.loads(fp.read_text());settings['version']='old-version';state_io.atomic_json(fp,settings)
        with patch.object(qp.core,'RUNTIME_DATA',self.root),patch.object(qp.core,'PersonDetector') as model:qp.run(folder/'job.json');model.assert_not_called()

if __name__=='__main__':unittest.main()
