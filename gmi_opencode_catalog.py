#!/usr/bin/env python3
"""Generate a current OpenCode V2 custom GMI provider from authenticated /v1/models.

No secrets are written to files. This is a standalone, temporary workaround
until a source-backed upstream model-sync solution is accepted.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import urllib.error
import urllib.request

URL = "https://api.gmi-serving.com/v1/models"
BASE = "https://api.gmi-serving.com/v1"


def validate_models(payload: dict) -> list[dict]:
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        raise ValueError("Expected GMI /v1/models response with a data array")
    out, seen = [], set()
    for i, item in enumerate(payload["data"]):
        if not isinstance(item, dict) or not isinstance(item.get("id"), str) or not item["id"].strip():
            continue
        id_ = item["id"]
        if id_ in seen:
            raise ValueError(f"Duplicate GMI model ID {id_!r}, refusing ambiguous catalog")
        seen.add(id_)
        out.append(item)
    if not out:
        raise ValueError("GMI did not return any usable model IDs")
    return out


def fetch_models(api_key: str, *, timeout: int = 25) -> dict:
    if not api_key:
        raise ValueError("Set GMICLOUD_API_KEY or GMI_API_KEY in the environment")
    request = urllib.request.Request(URL, headers={"Authorization": "Bearer " + api_key})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"GMI /v1/models HTTP {exc.code}. Check access and credits.") from exc


def build_custom_provider(payload: dict, *, name="gmi-live") -> dict:
    models = {}
    for item in validate_models(payload):
        id_ = item["id"]
        config = {"name": str(item.get("name") or id_), "modelID": id_}
        # Endpoint states context_length; never fabricate output limits,
        # modality or reasoning support (not present in /v1/models).
        context = item.get("context_length")
        if isinstance(context, int) and not isinstance(context, bool) and context > 0:
            config["limit"] = {"context": context}
        models[id_] = config
    return {
        "$schema": "https://opencode.ai/config.json",
        "providers": {
            name: {
                "name": "GMI Cloud (live inventory)",
                "env": ["GMICLOUD_API_KEY", "GMI_API_KEY"],
                "package": "@opencode/ai/providers/openai-compatible",
                "settings": {"baseURL": BASE},
                "models": models,
            }
        },
    }


def merge_config(existing: dict, generated: dict) -> dict:
    if not isinstance(existing, dict):
        raise ValueError("Existing OpenCode configuration must be JSON object")
    result = dict(existing)
    previous = dict(result.get("providers", {}))
    if not isinstance(result.get("providers", {}), dict):
        raise ValueError("Expected providers to be an object")
    # Replace only the generated provider, never other entries or user settings.
    previous.update(generated["providers"])
    result["providers"] = previous
    result.setdefault("$schema", "https://opencode.ai/config.json")
    return result


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--inventory", type=Path, help="Read a saved /v1/models JSON response (offline testing)")
    p.add_argument("--out", type=Path, required=True, help="Write complete OpenCode V2 JSON config here")
    p.add_argument("--merge", action="store_true", help="Preserve other properties in an existing JSON config")
    p.add_argument("--provider", default="gmi-live", help="Custom provider ID, default gmi-live")
    args = p.parse_args(argv)
    if not args.provider.replace("-", "").replace("_", "").isalnum():
        p.error("Provider ID must be alphanumeric, '-' or '_'")
    try:
        if args.inventory is not None:
            payload = json.loads(args.inventory.read_text())
        else:
            payload = fetch_models(os.environ.get("GMICLOUD_API_KEY") or os.environ.get("GMI_API_KEY", ""))
        generated = build_custom_provider(payload, name=args.provider)
        existing = json.loads(args.out.read_text()) if args.merge and args.out.exists() else {}
        output = merge_config(existing, generated) if args.merge else generated
        # Reject unsafe accidental overwrites. Use --merge to explicitly update.
        if args.out.exists() and not args.merge:
            raise FileExistsError(f"{args.out} exists. Use --merge to update just the gmi-live entry.")
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(output, indent=2, ensure_ascii=False) + "\n")
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
        p.error(str(exc))
    count = len(generated["providers"][args.provider]["models"])
    print(f"Wrote {count} API-listed model IDs to {args.out} under providers.{args.provider}")
    print("No key written. In your shell set GMICLOUD_API_KEY or GMI_API_KEY before launching opencode.")
    print("Model capabilities are not inferred. Verify that a model supports tools before agent runs.")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
