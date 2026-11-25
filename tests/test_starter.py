import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from bench import open_code_config, pi_config, task_names

class TestStarter(unittest.TestCase):
    def test_tasks(self):
        self.assertEqual(task_names(), ['export', 'slugify'])
    def test_opencode_config(self):
        cfg = open_code_config('vendor/model')
        self.assertEqual(cfg['providers']['gmi-bench']['models']['selected']['modelID'], 'vendor/model')
        self.assertNotIn('sk-', json.dumps(cfg))
    def test_pi_config(self):
        self.assertEqual(pi_config('vendor/model')['providers']['gmi-bench']['apiKey'], 'GMI_API_KEY')
    def test_dry_run(self):
        with tempfile.TemporaryDirectory() as t:
            r = subprocess.run([sys.executable, str(ROOT / 'bench.py'), '--model', 'vendor/model',
                                '--results', t, '--dry-run'], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(len(list(Path(t).glob('**/run.json'))), 6)
            summaries = list(Path(t).glob('*/summary.json'))
            self.assertEqual(len(summaries), 1)
            self.assertEqual(len(json.loads(summaries[0].read_text())), 6)
    def test_fixture_baselines_fail(self):
        for task in task_names():
            seed = ROOT / 'tasks' / task / 'seed'
            env = os.environ.copy()
            env['PYTHONPATH'] = str(seed)
            r = subprocess.run([sys.executable, str(ROOT / 'eval' / (task+'.py'))], env=env,
                               cwd=seed, capture_output=True, text=True)
            self.assertNotEqual(r.returncode, 0, f'{task} should start with a failing test')

if __name__ == '__main__':
    unittest.main()
