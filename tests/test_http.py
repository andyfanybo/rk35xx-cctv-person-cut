import json
import tempfile
import threading
import unittest
import urllib.request
from urllib.error import HTTPError
from pathlib import Path
from unittest.mock import patch
from http.server import ThreadingHTTPServer
import server


class HTTPTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.data=self.root/'work';(self.data/'jobs').mkdir(parents=True)
        self.source=self.root/'recordings';self.source.mkdir()
        (self.source/'camera.mp4').write_bytes(b'not-decoded-in-api-test')
        self.output=self.root/'published';self.output.mkdir()
        for name,value in [('DATA',self.data),('SOURCES',{'front_door':self.source}),('jobs',{})]:
            patcher=patch.object(server,name,value);patcher.start();self.addCleanup(patcher.stop)
        for name,value in [('work_dir',self.data),('browse_roots',[self.root]),('sources',{'front_door':self.source}),('publish_dir',self.output)]:
            patcher=patch.object(server.settings,name,value);patcher.start();self.addCleanup(patcher.stop)
        self.http=ThreadingHTTPServer(('127.0.0.1',0),server.Handler)
        self.thread=threading.Thread(target=self.http.serve_forever,daemon=True);self.thread.start()
        self.addCleanup(self.close)
        self.base='http://127.0.0.1:'+str(self.http.server_port)

    def close(self):
        self.http.shutdown();self.http.server_close();self.thread.join(timeout=2)

    def get(self,path):
        with urllib.request.urlopen(self.base+path,timeout=3) as response:
            return json.load(response)

    def post(self,path,body):
        request=urllib.request.Request(self.base+path,json.dumps(body).encode(),{'Content-Type':'application/json'})
        with urllib.request.urlopen(request,timeout=3) as response:
            return json.load(response)

    def test_dynamic_roots_and_empty_queue(self):
        self.assertEqual(self.get('/api/config')['source_roots'],['front_door'])
        self.assertEqual(self.get('/api/sources')['files'][0]['name'],'camera.mp4')
        self.assertEqual(self.get('/api/jobs'),[])

    def test_queue_cancel_delete_never_touches_source(self):
        with patch.object(server.shutil,'disk_usage') as usage:
            usage.return_value.free=10*1024**3
            job=self.post('/api/jobs',{'sources':[str(self.source/'camera.mp4')],'output_mode':'automatic'})
        self.assertEqual(job['state'],'queued')
        self.assertEqual(job['verification'],'quick')
        job=self.post('/api/jobs/'+job['id']+'/cancel',{})
        self.assertEqual(job['state'],'cancelled')
        result=self.post('/api/jobs/'+job['id']+'/delete',{'confirm':True})
        self.assertTrue(result['deleted'])
        self.assertTrue((self.source/'camera.mp4').is_file())

    def test_web_directory_selection_is_saved_per_job(self):
        self.assertIn(str(self.source),[d['path'] for d in self.get('/api/directories?path='+str(self.root))['directories']])
        self.assertEqual(self.get('/api/sources?directory='+str(self.source))['files'][0]['name'],'camera.mp4')
        second=self.root/'second-output';second.mkdir()
        body={'sources':[str(self.source/'camera.mp4')],'input_directory':str(self.source),'publish_directory':str(self.output)}
        with patch.object(server.shutil,'disk_usage') as usage:
            usage.return_value.free=10*1024**3
            first=self.post('/api/jobs',body)
            second_job=self.post('/api/jobs',dict(body,publish_directory=str(second)))
        self.assertEqual(first['publish_dir'],str(self.output))
        self.assertEqual(second_job['publish_dir'],str(second))
        self.assertEqual(self.get('/api/jobs/'+first['id'])['publish_dir'],str(self.output))

    def test_files_must_belong_to_the_selected_input_folder(self):
        other=self.root/'other-recordings';other.mkdir()
        body={'sources':[str(self.source/'camera.mp4')],'input_directory':str(other),'publish_directory':str(self.output)}
        with self.assertRaises(HTTPError) as error:
            self.post('/api/jobs',body)
        self.assertEqual(error.exception.code,400)
        self.assertEqual(self.get('/api/jobs'),[])


    def completed_failed_export(self):
        from state_io import atomic_json
        with patch.object(server.shutil,'disk_usage') as usage:
            usage.return_value.free=10*1024**3
            created=self.post('/api/jobs',{'sources':[str(self.source/'camera.mp4')],'output_mode':'automatic'})
        job=server.jobs[created['id']];job['state']='done'
        folder=self.data/'jobs'/job['id'];root=Path(job['output']);part=root/'sample-01';part.mkdir(parents=True)
        clip=part/'kept-0000-0000.mp4';clip.write_bytes(b'video')
        row={'label':'sample-01','source':str(self.source/'camera.mp4'),'clips':[{'path':'sample-01/kept-0000-0000.mp4','size':5,'requested_start':0,'requested_end':10,'actual_duration':10,'processing_policy':'quick'}],'candidate_intervals':[[0,10]],'analyzed_seconds':10,'source_duration_seconds':10,'candidates_complete':True}
        atomic_json(root/'summary.json',[row]);atomic_json(folder/'reviews.json',{'sample-01/kept-0000-0000.mp4':{'decision':'keep'}})
        export={'id':'b'*32,'state':'failed','publish_user':False};job['exports']=[export];job['publish_user']=False
        out=folder/'exports'/export['id'];out.mkdir(parents=True)
        atomic_json(out/'selection.json',{});atomic_json(out/'status.json',{'state':'failed','message':'处理盘空间不足'})
        server.save(job)
        return job,clip

    def test_manual_merge_rechecks_space_without_rescanning_or_duplicate_export(self):
        import merge_recovery
        job,clip=self.completed_failed_export();path='/api/jobs/'+job['id']+'/merge'
        with patch.object(merge_recovery.shutil,'disk_usage') as usage:
            usage.return_value.free=0
            with self.assertRaises(HTTPError):self.post(path,{'publish_user':False})
            self.assertEqual(job['exports'][0]['state'],'failed');self.assertTrue(clip.exists())
            usage.return_value.free=10*1024**3
            self.post(path,{'publish_user':False});self.post(path,{'publish_user':False})
        self.assertEqual(job['state'],'done');self.assertEqual(len(job['exports']),1)
        self.assertEqual(job['exports'][0]['state'],'queued');self.assertTrue(clip.exists())

    def test_pause_and_resume_export_keep_completed_recognition(self):
        job,clip=self.completed_failed_export();job['exports'][0]['state']='queued'
        result=self.post('/api/jobs/'+job['id']+'/pause',{})
        self.assertEqual(result['exports'][0]['state'],'paused');self.assertEqual(result['state'],'done')
        with patch.object(server.shutil,'disk_usage') as usage:
            usage.return_value.free=10*1024**3
            self.post('/api/jobs/'+job['id']+'/resume',{})
        self.assertEqual(job['state'],'done');self.assertEqual(job['exports'][0]['state'],'queued');self.assertTrue(clip.exists())

    def test_pause_keeps_cache_and_delete_removes_it_without_source(self):
        with patch.object(server.shutil,'disk_usage') as usage:
            usage.return_value.free=10*1024**3
            created=self.post('/api/jobs',{'sources':[str(self.source/'camera.mp4')],'output_mode':'automatic'})
        folder=self.data/'jobs'/created['id'];cache=folder/'checkpoint.json';cache.write_text('{}')
        result=self.post('/api/jobs/'+created['id']+'/pause',{})
        self.assertEqual(result['state'],'paused');self.assertTrue(cache.exists())
        result=self.post('/api/jobs/'+created['id']+'/delete',{'confirm':True})
        self.assertTrue(result['deleted']);self.assertFalse(folder.exists());self.assertTrue((self.source/'camera.mp4').exists())


if __name__=='__main__':
    unittest.main()

