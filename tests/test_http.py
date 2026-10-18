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


if __name__=='__main__':
    unittest.main()
