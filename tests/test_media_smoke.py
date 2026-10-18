"""Short CPU-only generated-media checks; no recordings, VPU or NPU used."""
import contextlib
import io
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import configuration
import media_backend
import quick_pipeline as qp
import publication
import cache_lifecycle
from mp4_keys import key_times, movie_duration


@unittest.skipUnless(shutil.which(configuration.settings.ffmpeg), 'FFmpeg not installed')
class GeneratedMediaTests(unittest.TestCase):
    def test_software_decode_stream_copy_publish_and_cleanup(self):
        # MPEG4 is used only as a CPU-generated fixture for the generic media helpers.
        # This is not an HEVC/NPU integration acceptance test.
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);data=root/'work';data.mkdir();nas=root/'published';nas.mkdir()
            original=root/'camera.mp4'
            ff=configuration.settings.ffmpeg
            subprocess.run([ff,'-v','error','-nostdin','-f','lavfi','-i','testsrc2=size=64x64:rate=2',
                            '-t','3','-an','-c:v','mpeg4','-threads','1','-g','2','-y',str(original)],
                           stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=20,check=True)
            args=media_backend.frame_args(original,'fps=1',(64,64),3,'software')
            decoded=subprocess.run(args,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=20,check=True)
            self.assertEqual(len(decoded.stdout),3*64*64*3)
            self.assertGreater(len(key_times(original)),1)
            self.assertAlmostEqual(movie_duration(original),3,delta=.1)
            ident='a'*32;export='b'*32;jobdir=data/'jobs'/ident;results=jobdir/'results'
            results.mkdir(parents=True);clip=results/'keep.mp4';shutil.copyfile(str(original),str(clip))
            row={'label':'sample-01','candidates_complete':True,'clips':[{'path':'keep.mp4','size':clip.stat().st_size,
                 'requested_start':0,'requested_end':3,'actual_duration':3}]}
            (results/'summary.json').write_text(json.dumps([row]))
            job={'id':ident,'state':'done','output_mode':'automatic','verification':'quick','output':str(results),
                 'plan':[{'source':str(original),'label':'sample-01'}],'exports':[{'id':export,'state':'running'}]}
            path=jobdir/'job.json';path.write_text(json.dumps(job))
            # Local storage protection remains active; only path constants are isolated.
            with patch.object(qp,'PUBLISH_ROOT',nas),patch.object(publication,'PUBLISH_ROOT',nas),patch.object(cache_lifecycle,'PUBLISH_ROOT',nas),patch.object(cache_lifecycle,'DATA',data),patch.object(qp.shutil,'disk_usage',return_value=SimpleNamespace(free=10*1024**3)),contextlib.redirect_stdout(io.StringIO()):
                receipt=qp.merge(path,export,True)
            final=nas/'camera.mp4'
            self.assertEqual(receipt['state'],'done')
            self.assertTrue(final.is_file())
            self.assertTrue(original.is_file())
            self.assertFalse(clip.exists())
            self.assertTrue(receipt['automatic_cleanup']['videos_removed'])
            self.assertTrue(publication.has_tag(final,publication.publish_tag(job,export)))
            self.assertAlmostEqual(movie_duration(final),3,delta=.2)
            before=final.read_bytes()
            # A different job cannot claim or overwrite an existing same-name video.
            other=root/('c'*32);other.mkdir()
            with patch.object(publication,'PUBLISH_ROOT',nas):
                with self.assertRaises(ValueError):
                    publication.publish(other,dict(job,id='d'*32),original,dict(receipt,size=original.stat().st_size),shutil.copyfile)
            self.assertEqual(final.read_bytes(),before)


if __name__=='__main__':
    unittest.main()
