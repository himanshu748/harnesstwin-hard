from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

import hard_mode


class HardModeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        src = self.root / "v2"
        src.mkdir()
        ids = [f"instance_fixture__repository-{i:04d}" for i in range(51)]
        (src / "hard51_ids.txt").write_text("\n".join(ids) + "\n")
        for tid in ids:
            task = src / "tasks" / tid
            (task / "tests").mkdir(parents=True)
            (task / "task.toml").write_text("version = '1.0'\n")
            (task / "tests" / "test.sh").write_text("#!/bin/sh\nexit 0\n")

    def tearDown(self):
        self.tmp.cleanup()

    def test_load_hard51_integrity(self):
        ids = hard_mode.load_hard51(self.root)
        self.assertEqual(len(ids), 51)
        (self.root / "v2" / "hard51_ids.txt").write_text("instance_../../etc/passwd\n"*51)
        with self.assertRaises(ValueError):
            hard_mode.load_hard51(self.root)

    def test_plan_matrix_and_model_routes(self):
        p = hard_mode.make_plan(dataset=self.root, model="zai-org/GLM-5.3", environment="modal",
                                harnesses=hard_mode.HARNESSES, task_limit=2, trials=3, run_tag="tests")
        self.assertEqual(p["total_attempts"], 18)
        self.assertEqual(len(set(j["job_name"] for j in p["jobs"])), 18)
        self.assertEqual(len({(j["task_id"], j["harness"], j["trial"]) for j in p["jobs"]}), 18)
        for j in p["jobs"]:
            c = j["command"]
            self.assertIn("--allow-agent-host", c)
            self.assertIn("api.gmi-serving.com", c)
            self.assertIn("--api-key-name", c)
            self.assertIn("GMI_API_KEY", c)
            self.assertIn("openai/zai-org/GLM-5.3", c)
            self.assertNotIn("example-key", c)

    def test_fails_on_missing_or_invalid_task(self):
        with self.assertRaises(ValueError):
            hard_mode.make_plan(dataset=self.root, model="m", environment="docker",
                                harnesses=["fake"], task_limit=1, trials=1, run_tag="tests")
        with self.assertRaises(ValueError):
            hard_mode.make_plan(dataset=self.root, model="m", environment="docker",
                                harnesses=hard_mode.HARNESSES, task_limit=52, trials=1, run_tag="tests")
        missing = self.root / "v2" / "tasks" / "instance_fixture__repository-0000" / "task.toml"
        missing.unlink()
        with self.assertRaises(FileNotFoundError):
            hard_mode.load_hard51(self.root)

    def test_plan_only_never_calls_provider(self):
        p = hard_mode.make_plan(dataset=self.root, model="example/Live-Model", environment="docker",
                                harnesses=hard_mode.HARNESSES, task_limit=1, trials=1, run_tag="tests")
        target = self.root / "output"
        hard_mode.run_plan(p, target, self.root, execute=False)
        saved = json.loads((target / "plan.json").read_text())
        self.assertEqual(saved["total_attempts"], 3)
        self.assertTrue(all(j["status"] == "planned" for j in saved["jobs"]))
        self.assertTrue((target / "jobs.jsonl").is_file())

if __name__ == "__main__":
    unittest.main()
