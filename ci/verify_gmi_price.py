"""Check DeepSeek V4 Flash's listed prices and record usage for two metering calls."""
from __future__ import annotations

import json
import re
import urllib.error
from datetime import datetime, timezone
from pathlib import Path

from gateway import gmi_request, inventory, inventory_failure

MODEL = "deepseek-ai/DeepSeek-V4-Flash"
OUTPUT = Path("gmi-pricing-evidence.json")
PRICE_FIELDS = ("prompt", "completion", "input_cache_read", "cache_read", "input_cache_write")


def save(report):
    OUTPUT.write_text(json.dumps(report, indent=2) + "\n")


def main():
    report = {"model": MODEL, "account_inventory_confirmed": False, "requests": []}
    save(report)
    try:
        item = inventory().get(MODEL)
        if not item:
            raise SystemExit("The exact model serving ID is absent from this account")
        report["account_inventory_confirmed"] = True
        prices = item.get("pricing")
        if not isinstance(prices, dict):
            prices = {}
        report["raw_api_pricing"] = {
            name: str(prices[name])
            for name in PRICE_FIELDS
            if name in prices and re.fullmatch(r"[0-9]{1,10}(?:\.[0-9]{1,20})?(?:[eE]-?[0-9]{1,3})?", str(prices[name]))
        }
        save(report)
        # The two prompts share a prefix so the provider can report a cached-input count.
        # Two requests, <= 32 output tokens total and a fixed 14 KB non-personal prefix.
        prefix = "An apple, a book, a chair, a desk, and a flower. " * 250
        for index in range(2):
            payload = {
                "model": MODEL,
                "messages": [{"role": "user", "content": prefix + f"\nReply only with OK. Sample {index + 1}."}],
                "max_tokens": 16,
                "temperature": 0,
                "stream": False,
            }
            started_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
            with gmi_request("/chat/completions", payload) as response:
                usage = json.load(response).get("usage", {})
            details = usage.get("prompt_tokens_details") or {}
            entry = {
                "started_utc": started_at,
                "input_tokens": usage.get("prompt_tokens"),
                "output_tokens": usage.get("completion_tokens"),
                "cached_input_tokens": details.get("cached_tokens"),
            }
            report["requests"].append({k: v for k, v in entry.items() if k == "started_utc" or type(v) is int})
            save(report)
    except urllib.error.HTTPError as exc:
        report["error"] = inventory_failure(exc)
        save(report)
        raise SystemExit(f"GMI pricing check failed: HTTP {exc.code}") from None


if __name__ == "__main__":
    main()
