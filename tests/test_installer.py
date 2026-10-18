import json
import subprocess
import sys
import tempfile
import unittest
import os
import io
import tarfile
from pathlib import Path

ROOT=Path(__file__).resolve().parent.parent


class InstallerTests(unittest.TestCase):
    def test_download_mirror_error_page_falls_back_to_direct_archive(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);bootstrap=root/'install.sh';bootstrap.write_text((ROOT/'install.sh').read_text())
            archive=root/'fixture.tar.gz'
            with tarfile.open(str(archive),'w:gz') as tar:
                for name,content in [('package/server.py',b''),('package/LICENSE',b'fixture'),('package/tools/install.py',b'import json,sys;print(json.dumps(sys.argv[1:]))')]:
                    entry=tarfile.TarInfo(name);entry.size=len(content);tar.addfile(entry,io.BytesIO(content))
            fakebin=root/'bin';fakebin.mkdir();curl=fakebin/'curl'
            curl.write_text('#!'+sys.executable+'\nimport os,sys,pathlib,shutil\na=sys.argv[1:];url=next(x for x in a if x.startswith("https://"));out=a[a.index("-o")+1]\nwith open(os.environ["TEST_DOWNLOAD_LOG"],"a") as f:f.write(url+"\\n")\nif "ghfast.top" in url:pathlib.Path(out).write_text("<html>temporary error</html>")\nelse:shutil.copyfile(os.environ["TEST_DOWNLOAD_ARCHIVE"],out)\n');curl.chmod(0o755)
            env=dict(os.environ,PATH=str(fakebin)+os.pathsep+os.environ['PATH'],TEST_DOWNLOAD_ARCHIVE=str(archive),TEST_DOWNLOAD_LOG=str(root/'urls'))
            result=subprocess.run(['bash',str(bootstrap),'--china','--work-dir','/example/ssd'],env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,universal_newlines=True,timeout=10)
            self.assertEqual(result.returncode,0,result.stderr)
            urls=(root/'urls').read_text().splitlines()
            self.assertTrue(urls[0].startswith('https://ghfast.top/https://github.com/'))
            self.assertTrue(urls[1].startswith('https://github.com/'))
            self.assertIn('--china',json.loads(result.stdout))

    def test_china_dry_run_changes_only_download_plan(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);prefix=root/'installation';work=root/'ssd/work'
            p=subprocess.run(['bash',str(ROOT/'install.sh'),'--prefix',str(prefix),'--work-dir',str(work),'--china','--dry-run'],stdout=subprocess.PIPE,stderr=subprocess.PIPE,universal_newlines=True,timeout=10)
            self.assertEqual(p.returncode,0,p.stderr)
            plan=json.loads(p.stdout)
            self.assertIn('tuna',plan['pip_index']);self.assertFalse(plan['hardware_drivers_modified'])
            self.assertFalse(prefix.exists());self.assertFalse(work.exists())

    def test_existing_install_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);marker=root/'keep';marker.write_text('existing')
            p=subprocess.run([sys.executable,str(ROOT/'tools/install.py'),'--prefix',str(root),'--work-dir',str(root/'cache'),'--dry-run'],stdout=subprocess.PIPE,stderr=subprocess.PIPE,universal_newlines=True,timeout=10)
            self.assertNotEqual(p.returncode,0);self.assertEqual(marker.read_text(),'existing')

    def test_existing_work_jobs_are_not_imported(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);work=root/'work';work.mkdir();marker=work/'old-job.json';marker.write_text('keep')
            p=subprocess.run([sys.executable,str(ROOT/'tools/install.py'),'--prefix',str(root/'new-app'),'--work-dir',str(work),'--dry-run'],stdout=subprocess.PIPE,stderr=subprocess.PIPE,universal_newlines=True,timeout=10)
            self.assertNotEqual(p.returncode,0);self.assertEqual(marker.read_text(),'keep')
            self.assertFalse((root/'new-app').exists())
