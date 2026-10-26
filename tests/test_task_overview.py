import json
import tempfile
import unittest
from pathlib import Path

from task_overview import begin_processing,build_timelines,legacy_started,runtime_summary


class TimingTests(unittest.TestCase):
    def test_initial_queue_time_is_excluded(self):
        job={'created':1,'processing_started_at':100,'state':'running'}
        self.assertEqual(runtime_summary(job,now=160)['elapsed_seconds'],60)

    def test_total_includes_export_and_freezes_after_completion(self):
        job={'state':'done','output_mode':'automatic','processing_started_at':100,'processing_finished_at':310,'updated':999}
        result=runtime_summary(job,[{'state':'done','updated':305}],now=1000)
        self.assertEqual(result['elapsed_seconds'],210);self.assertEqual(result['finished_at'],310)
        self.assertEqual(result['state'],'done');self.assertFalse(result['estimated'])

    def test_scan_completion_is_not_export_completion(self):
        job={'state':'done','output_mode':'automatic','processing_started_at':100}
        result=runtime_summary(job,[{'state':'running'}],now=400)
        self.assertEqual(result['state'],'running');self.assertIsNone(result['finished_at'])
        self.assertEqual(result['elapsed_seconds'],300)

    def test_paused_clock_freezes_and_resume_preserves_first_start(self):
        job={'state':'cancelled','processing_started_at':100,'processing_stopped_at':200}
        self.assertEqual(runtime_summary(job,now=900)['elapsed_seconds'],100)
        begin_processing(job,'.',now=1000);job['state']='running'
        self.assertEqual(runtime_summary(job,now=1100)['elapsed_seconds'],1000)
        self.assertNotIn('processing_stopped_at',job)

    def test_failed_export_is_paused_not_done(self):
        job={'state':'done','processing_started_at':100,'processing_stopped_at':350}
        result=runtime_summary(job,[{'state':'failed'}],now=900)
        self.assertEqual(result['state'],'paused');self.assertEqual(result['elapsed_seconds'],250)

    def test_historical_receipt_is_used_instead_of_cleanup_update(self):
        job={'state':'done','processing_started_at':100,'updated':900}
        result=runtime_summary(job,[{'state':'done','updated':300}],now=1000)
        self.assertEqual(result['elapsed_seconds'],200);self.assertTrue(result['estimated'])

    def test_old_log_is_bounded_and_unknown_history_stays_unknown(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'worker.log'
            path.write_text('not-json\n'+json.dumps({'updated':110,'elapsed_seconds':10})+'\n'+'x'*40000)
            self.assertEqual(legacy_started(str(path)),100)
            result=runtime_summary({'state':'done','updated':200},folder=tmp,now=300)
            self.assertEqual(result['elapsed_seconds'],100);self.assertTrue(result['estimated'])
        self.assertIsNone(runtime_summary({'state':'done','updated':100},now=200)['elapsed_seconds'])

    def test_abrupt_shutdown_uses_last_progress_as_estimate(self):
        result=runtime_summary({'state':'interrupted','processing_started_at':100,'updated':101},now=900,progress={'updated':250})
        self.assertEqual(result['elapsed_seconds'],150);self.assertTrue(result['estimated'])


class TimelineTests(unittest.TestCase):
    def timeline(self,windows,analyzed=100,full=100,**extra):
        row={'source':'source.mp4','label':'a','analyzed_seconds':analyzed,'candidate_intervals':windows,'clips':[]}
        row.update(extra)
        return build_timelines([{'source':'source.mp4'}],[row],[{'source':'source.mp4','duration_seconds':full}])[0]

    def test_people_gaps_and_unanalyzed_tail(self):
        timeline=self.timeline([[10,20],[35,50]],analyzed=60,full=100)
        spans=timeline['intervals']
        self.assertEqual([s['kind'] for s in spans],['no_detection','people','no_detection','people','no_detection','unanalyzed'])
        self.assertEqual(spans[3]['gap_since_previous_people_seconds'],15)
        self.assertEqual(timeline['longest_no_detection_seconds'],15)
        self.assertEqual(timeline['totals'],{'people':25,'no_detection':35,'uncertain':0,'unanalyzed':40})

    def test_overlapping_touching_and_duplicate_windows_merge(self):
        timeline=self.timeline([[20,40],[10,30],[40,50],[20,40]])
        self.assertEqual(timeline['people_count'],1)
        self.assertEqual([(s['start'],s['end']) for s in timeline['intervals'] if s['kind']=='people'],[(10,50)])

    def test_empty_and_all_people_sources(self):
        self.assertEqual(self.timeline([])['totals']['no_detection'],100)
        self.assertEqual(self.timeline([[0,100]])['totals']['no_detection'],0)
        self.assertEqual(self.timeline([],analyzed=0)['intervals'][0]['kind'],'unanalyzed')

    def test_bad_and_out_of_range_windows_do_not_corrupt_coverage(self):
        timeline=self.timeline([[-10,10],[90,200],[float('nan'),20],['bad',4],[5,2]])
        self.assertEqual(timeline['totals']['people'],20)
        self.assertEqual(sum(s['duration_seconds'] for s in timeline['intervals']),100)

    def test_whole_segment_fallback_is_uncertain(self):
        timeline=self.timeline([[0,100]],clips=[{'path':'sample/kept-0001-0000.mp4','requested_start':40,'requested_end':60}],fallback_chunk_indices=[2])
        self.assertEqual([s['kind'] for s in timeline['intervals']],['people','uncertain','people'])
        self.assertEqual(timeline['totals']['uncertain'],20)
        self.assertEqual(timeline['totals']['no_detection'],0)

    def test_each_source_has_its_own_zero(self):
        plan=[{'source':'a.mp4'},{'source':'b.mp4'}]
        rows=[{'source':'a.mp4','analyzed_seconds':20,'candidate_intervals':[[0,10]]},{'source':'b.mp4','analyzed_seconds':30,'candidate_intervals':[[10,20]]}]
        timelines=build_timelines(plan,rows,[{'source':'a.mp4','duration_seconds':20},{'source':'b.mp4','duration_seconds':30}])
        self.assertEqual(len(timelines),2);self.assertEqual(timelines[1]['intervals'][1]['start'],10)


if __name__=='__main__':unittest.main()
