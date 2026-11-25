import tempfile
from pathlib import Path
import unittest
import compare

class CompareTest(unittest.TestCase):
    def test_paired_bootstrap(self):
        data={ (task,h,trial): (1 if h=='pi' else 0) for task in ['a','b','c'] for h in ['pi','opencode'] for trial in [1,2,3] }
        report=compare.analyze(data,iterations=300)
        self.assertEqual(report['harness_results']['pi']['resolved_rate'], 1)
        self.assertEqual(report['paired_differences']['opencode minus pi']['difference'],-1)
        self.assertEqual(report['tasks'],3)

    def test_rejects_missing_trials(self):
        with self.assertRaises(ValueError):
            compare.analyze({('a','pi',1):1, ('a','oc',2):0})

    def test_reads_bool_outcomes_only(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'verdict.csv'
            p.write_text('task_id,harness,trial,resolved\na,pi,1,yes\n')
            with self.assertRaises(ValueError): compare.read_verified(p)

if __name__=='__main__':unittest.main()
