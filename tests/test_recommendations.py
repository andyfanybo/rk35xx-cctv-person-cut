import unittest
from recommendations import recommendation,apply_default
class RecommendationTests(unittest.TestCase):
    def setUp(self):
        self.clip={'path':'a/c.mp4','video_parameters_match':True,'source_range_check':{'passed':True},'keyframe_check':{'passed':True},'sync_check':{'passed':True},'copied_video_packets_verified':10,'copied_audio_packets_verified':10,'waveform_check':[]}
        self.results=[{'clips':[self.clip]}]
    def test_valid_candidate(self):self.assertEqual(recommendation(self.clip)['decision'],'keep')
    def test_invalid_sync(self):
        self.clip['sync_check']['passed']=False;self.assertEqual(recommendation(self.clip)['decision'],'pending')
    def test_manual_reject_preserved(self):
        old={'a/c.mp4':{'decision':'reject','note':'没人'}};new,n=apply_default(self.results,old,123);self.assertEqual(new,old);self.assertEqual(n,0)
    def test_pending_note_preserved(self):
        old={'a/c.mp4':{'decision':'pending','note':'检查门口'}};new,n=apply_default(self.results,old,123);self.assertEqual(new,old);self.assertEqual(n,0)
    def test_idempotent(self):
        new,n=apply_default(self.results,{},123);second,m=apply_default(self.results,new,124);self.assertEqual(n,1);self.assertEqual(m,0);self.assertEqual(new,second)
    def test_silence_packet_sync_sufficient(self):
        self.clip['waveform_check']=[{'passed':False,'status':'inconclusive-silence'}];self.assertEqual(recommendation(self.clip)['decision'],'keep')
if __name__=='__main__':unittest.main()
