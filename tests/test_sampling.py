import unittest
from sampling import sample_count,sample_second,validate_interval,frame_filter,candidate_intervals
class SamplingTests(unittest.TestCase):
    def test_tail_is_not_dropped(self):
        self.assertEqual(sample_count(11,5),3);self.assertEqual([sample_second(i,5) for i in range(3)],[0,5,10])
    def test_original_one_second_windows_unchanged(self):
        rows=[{'second':t,'combined':[{'score':.8}],'multiview':[{'score':.8}]} for t in [10,40]];keys=list(range(0,61,6))
        self.assertEqual(candidate_intervals(rows,60,keys,1),[[0,24],[30,54]])
    def test_sparse_index_is_source_time(self):
        rows=[{'second':sample_second(4,5),'combined':[{'score':.8}]}]
        self.assertEqual(candidate_intervals(rows,60,[0,5,10,15,20,25,30,35,40,45,50,55,60],5),[[15,35]])
    def test_long_interval_widens_margin(self):
        self.assertEqual(candidate_intervals([{'second':30,'combined':[{'score':.8}]}],80,list(range(0,81,5)),10),[[20,50]])
    def test_no_detection_still_no_candidate(self):self.assertEqual(candidate_intervals([{'second':0,'combined':[]}],10,[0,5,10],5),[])
    def test_filter_drops_before_download(self):
        f=frame_filter(5);self.assertLess(f.index('select='),f.index('hwdownload'));self.assertTrue(f.startswith('fps=1'))
    def test_invalid_interval(self):
        for value in [0,-1,4,True,'5',.5]:
            with self.assertRaises(ValueError):validate_interval(value)
if __name__=='__main__':unittest.main()
