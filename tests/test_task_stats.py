import unittest
from task_stats import summarize
class TaskStatsTests(unittest.TestCase):
    def setUp(self):
        self.plan=[{'label':'one','source':'source.mp4'}];self.meta=[{'source':'source.mp4','duration_seconds':100.,'size_bytes':1000}]
        self.row={'label':'one','source':'source.mp4','analyzed_seconds':60,'candidate_intervals':[[10,30],[25,40]],'clips':[{'path':'one/a.mp4','size':200},{'path':'one/b.mp4','size':300}]}
    def stats(self,**kw):return summarize(self.plan,[self.row],kw.get('reviews',{}),kw.get('meta',self.meta),kw.get('exports',[]))
    def test_partial_not_empty(self):
        s=self.stats();self.assertEqual(s['people_candidate_seconds'],30);self.assertEqual(s['no_person_detected_seconds'],30);self.assertEqual(s['unanalyzed_seconds'],40)
    def test_size_from_real_files(self):self.assertEqual(self.stats()['people_estimated_bytes'],500)
    def test_keyframe_scope(self):
        self.row.update(analyzed_seconds=50,candidate_intervals=[[45,55]]);s=self.stats();self.assertEqual(s['people_candidate_seconds'],5);self.assertEqual(s['keyframe_extra_seconds'],5)
    def test_unknown_total(self):
        s=self.stats(meta=[]);self.assertIsNone(s['source_total_duration_seconds']);self.assertIsNone(s['source_total_bytes'])
    def test_preliminary_size(self):
        self.row['candidates_complete']=False;self.assertIsNone(self.stats()['people_estimated_bytes'])
    def test_no_people(self):
        self.row.update(clips=[],candidate_intervals=[]);s=self.stats();self.assertEqual(s['people_estimated_bytes'],0);self.assertEqual(s['no_person_detected_seconds'],60)
    def test_changed_review_does_not_reuse_actual_export_size(self):
        r={'one/a.mp4':{'decision':'keep'}};e=[{'state':'done','selected_clips':['one/a.mp4','one/b.mp4'],'size':499}]
        self.assertIsNone(self.stats(reviews=r,exports=e)['merged_actual_bytes']);e.append({'state':'done','selected_clips':['one/a.mp4'],'size':199})
        s=self.stats(reviews=r,exports=e);self.assertEqual(s['selected_estimated_bytes'],200);self.assertEqual(s['merged_actual_bytes'],199)
    def test_clamps_to_source(self):
        self.row.update(analyzed_seconds=200,candidate_intervals=[[0,200]]);s=self.stats();self.assertEqual(s['analyzed_seconds'],100);self.assertEqual(s['people_candidate_seconds'],100)
if __name__=='__main__':unittest.main()
