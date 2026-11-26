"""Regression tests for the relay's local accounting and output validation."""
import sys
import tempfile
import unittest
from decimal import Decimal
from email.message import Message
from io import BytesIO
from pathlib import Path
from urllib.error import HTTPError

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ci"))
from gateway import Budget, inventory_failure
from run import regular_file


class RelayBudgetTests(unittest.TestCase):
    def test_inventory_refusal_never_contains_provider_body(self):
        headers = Message()
        headers["Content-Type"] = "application/json"
        error = HTTPError("https://api.gmi-serving.com/v1/models", 403, "", headers,
                          BytesIO(b'{"error":"Invalid API key example-private-value"}'))
        self.assertEqual(inventory_failure(error), {
            "http_status": 403,
            "response_format": "json",
            "cloudflare_ray_present": False,
            "category": "invalid_credential",
        })

    def test_missing_usage_keeps_reservation_and_blocks_another_call(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as tmp:
            budget = Budget("gmi/model", 1_000_000, 8192, 0.15, 0.50, 0.20, 4, None,
                            Path(tmp) / "usage.jsonl")
            call = budget.begin()
            budget.finish(call, None, 502, set(), 1.0)
            self.assertEqual(budget.charged, Decimal("0.154096"))
            self.assertIsNone(budget.begin())
            self.assertEqual(budget.denied, 1)

    def test_usage_releases_unused_reserve_but_preserves_call_cap(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as tmp:
            budget = Budget("gmi/model", 1_000_000, 8192, 0.15, 0.50, 0.20, 2, None,
                            Path(tmp) / "usage.jsonl")
            usage = {"prompt_tokens": 4000, "completion_tokens": 1000}
            for _ in range(2):
                budget.finish(budget.begin(), usage, 200, set(), 1.0)
            self.assertEqual(budget.charged, Decimal("0.0022"))
            self.assertIsNone(budget.begin())

    def test_evidence_requires_regular_file_and_respects_size_limit(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as tmp:
            root = Path(tmp)
            patch = root / "patch"
            patch.write_bytes(b"candidate")
            link = root / "link"
            link.symlink_to(patch)
            self.assertTrue(regular_file(patch, 100))
            self.assertFalse(regular_file(patch, 1))
            self.assertFalse(regular_file(link))
