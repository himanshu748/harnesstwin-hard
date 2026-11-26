"""Regression tests for the relay's local accounting and output validation."""
import json
import sys
import tempfile
import unittest
from decimal import Decimal
from email.message import Message
from io import BytesIO
from pathlib import Path
from urllib.error import HTTPError
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ci"))
from gateway import Budget, inventory_failure
from run import CI, SETTINGS, prepare_model_config, regular_file
from trace_summary import opencode_exited_at_call_limit


class RelayBudgetTests(unittest.TestCase):
    def test_flash_profile_inherits_provider_completion_behavior(self):
        profile = json.loads((CI / "hard-flash-settings.json").read_text())
        budget = Budget(profile["model"], SETTINGS["context_ceiling"], profile["max_completion_tokens_per_call"],
                        profile["input_usd_per_million_upper_rate"], profile["output_usd_per_million_upper_rate"],
                        profile["attempt_cap_usd"], SETTINGS["max_calls"], None, Path("usage.jsonl"))
        body = {"max_tokens": 32768, "max_completion_tokens": 8192, "stream": True}
        budget.prepare_request(body)
        self.assertNotIn("max_tokens", body)
        self.assertNotIn("max_completion_tokens", body)
        self.assertEqual(body["stream_options"], {"include_usage": True})
        self.assertEqual(budget.reserve, Decimal("0.6815744"))
        self.assertEqual(budget.begin(), 1)
        self.assertIsNone(budget.begin())

    def test_only_local_open_code_limit_exit_counts_as_intentional(self):
        data = {"statusCode": 429, "responseBody": json.dumps({"error": {
            "message": "Benchmark request or spending cap reached", "type": "harnesstwin_relay",
        }})}
        local = {"type": "error", "error": {"data": data}}
        provider = {"type": "error", "error": {"data": {"statusCode": 429, "responseBody": "rate limited"}}}
        log = lambda *events: BytesIO(b"\n".join(json.dumps(x).encode() for x in events))
        self.assertTrue(opencode_exited_at_call_limit(log(local)))
        self.assertFalse(opencode_exited_at_call_limit(log(provider)))
        self.assertFalse(opencode_exited_at_call_limit(log(local, provider)))

    def test_hard_profile_sets_32k_completion_limit_for_clients_and_budget(self):
        profile = json.loads((CI / "hard-settings.json").read_text())
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as tmp, patch.dict(SETTINGS, profile):
            path = Path(tmp)
            prepare_model_config(path)
            pi = json.loads((path / "models.json").read_text())["providers"]["ht"]["models"][0]
            opencode = json.loads((path / "opencode.json").read_text())["provider"]["ht"]["models"][profile["model"]]
            self.assertEqual(pi["maxTokens"], 32768)
            self.assertEqual(opencode["limit"]["output"], 32768)
            budget = Budget(profile["model"], SETTINGS["context_ceiling"], SETTINGS["max_completion_tokens_per_call"],
                            profile["input_usd_per_million_upper_rate"], profile["output_usd_per_million_upper_rate"],
                            profile["attempt_cap_usd"], SETTINGS["max_calls"], None, path / "usage.jsonl")
            self.assertEqual(budget.reserve, Decimal("1.6121856"))

    def test_main_timeout_remains_within_agent_deadline(self):
        budget = Budget("gmi/model", 1_048_576, 32768, 1.40, 4.40, 2.50, 40, None,
                        Path("usage.jsonl"), upstream_timeout=600, agent_deadline=1140)
        with patch("gateway.time.monotonic", return_value=0):
            self.assertEqual(budget.request_timeout(), 600)
        with patch("gateway.time.monotonic", return_value=900):
            self.assertEqual(budget.request_timeout(), 240)
        with patch("gateway.time.monotonic", return_value=1140):
            self.assertIsNone(budget.begin())

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

    def test_verified_cached_input_rate_only_affects_settled_usage(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as tmp:
            budget = Budget("gmi/model", 1_000_000, 8192, 1.40, 4.40, 2.50, 2, None,
                            Path(tmp) / "usage.jsonl", cache_rate=0.28)
            call = budget.begin()
            self.assertEqual(budget.charged, Decimal("1.4360448"))
            usage = {"prompt_tokens": 4000, "completion_tokens": 1000,
                     "prompt_tokens_details": {"cached_tokens": 3000}}
            budget.finish(call, usage, 200, set(), 1.0)
            self.assertEqual(budget.charged, Decimal("0.00664"))

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
