import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import directory_selection as dirs
import publication,cache_lifecycle
from job_delete import delete_task


class DirectoryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.source=self.root/'recordings';self.source.mkdir()
        self.work=self.root/'work';self.work.mkdir()
        self.output=self.root/'output';self.output.mkdir()
        self.cfg=SimpleNamespace(browse_roots=[self.root],sources={'camera':self.source},
                                 work_dir=self.work,publish_dir=self.output,storage={'policy':'local'})
        patcher=patch.object(dirs,'settings',self.cfg);patcher.start();self.addCleanup(patcher.stop)

    def test_browse_cannot_escape_roots_via_parent_or_symlink(self):
        link=self.root/'escape';link.symlink_to(self.root.parent)
        listing=dirs.listing(self.root)
        self.assertNotIn(str(link),[d['path'] for d in listing['directories']])
        self.assertIsNone(listing['parent'])
        with self.assertRaises(ValueError):dirs.listing(link)

    def test_cache_and_original_output_overlap_are_rejected(self):
        for source,target in [(self.source,self.source),(self.source,self.source/'child'),(self.work,self.output),(self.root,self.output)]:
            if not target.exists():target.mkdir()
            with self.assertRaises(ValueError):dirs.select_pair(source,target)

    def test_distinct_output_keeps_its_own_storage_policy(self):
        second=self.root/'other-output';second.mkdir()
        with patch.object(dirs,'containing_mount',return_value={'mount':'/mnt/archive','fstype':'nfs','source':'nas:/archive'}):
            source,target,policy=dirs.select_pair(self.source,second)
        self.assertEqual(target,second)
        self.assertEqual(policy['mount_path'],'/mnt/archive')

    def test_publication_receipt_and_deletion_follow_job_output(self):
        source=self.source/'camera.mp4';source.write_bytes(b'original')
        jobdir=self.work/'jobs'/('a'*32);jobdir.mkdir(parents=True)
        out=jobdir/'exports'/('b'*32);out.mkdir(parents=True)
        custom=self.root/'chosen-output';custom.mkdir()
        job={'id':'a'*32,'plan':[{'source':str(source)}],'output':str(jobdir/'results'),
             'publish_dir':str(custom),'publish_storage':{'policy':'local'},'exports':[{'id':'b'*32}]}
        import shutil
        with patch.object(publication,'PUBLISH_ROOT',self.output):
            receipt=publication.publish(out,job,source,{'state':'done','size':8},shutil.copyfile)
        self.assertEqual(receipt['publish_root'],str(custom))
        self.assertEqual(cache_lifecycle.delivered_file(receipt),custom/'camera.mp4')
        self.assertFalse((self.output/'camera.mp4').exists())
        partial=custom/('.camera.mp4.'+'b'*32+'.partial');partial.write_bytes(b'partial')
        delete_task(self.work,job,[])
        self.assertTrue((custom/'camera.mp4').is_file());self.assertTrue(source.is_file());self.assertFalse(partial.exists())
