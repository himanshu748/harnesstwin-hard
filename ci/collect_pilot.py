"""Combine completed workflow artifacts from one model, retaining failed attempts."""
from __future__ import annotations

import argparse
from collections import Counter
import csv
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import re
import sys
from zipfile import ZipFile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from compare import analyze
from trace_summary import opencode_exited_at_call_limit

HARNESS_ORDER = ("pi", "opencode", "mini-swe-agent")


def artifact_rows(path, run_id):
    with ZipFile(path) as archive:
        plan = json.loads(archive.read("plan.json"))
        task_ids = list(dict.fromkeys(task for task, _ in plan["attempts"]))
        for row in json.loads(archive.read("results.json")):
            path_prefix = f"task-{task_ids.index(row['task_id']) + 1:02}/{row['harness']}"
            row["source_run"] = run_id
            row["cache_read_tokens_reported"] = 0
            usage_path = f"{path_prefix}/usage.jsonl"
            if usage_path in archive.namelist():
                for line in archive.read(usage_path).splitlines():
                    usage = json.loads(line).get("usage") or {}
                    cached = (usage.get("prompt_tokens_details") or {}).get("cached_tokens")
                    if type(cached) is int:
                        row["cache_read_tokens_reported"] += cached
            if (row["harness"] == "opencode" and row["status"] == "agent_error"
                    and row.get("verifier_resolved") in (0, 1)
                    and row.get("model_calls") == plan["settings"]["max_calls"]
                    and row.get("budget_denials", 0) > 0 and row.get("model_errors") == 0):
                with archive.open(f"{path_prefix}/agent.log") as stream:
                    if opencode_exited_at_call_limit(stream):
                        row.update(status=row["verifier_status"], resolved=row["verifier_resolved"],
                                   agent_termination="benchmark_call_limit")
            if row.get("resolved") in (0, 1):
                reward = int(archive.read(f"{path_prefix}/verifier/reward.txt"))
                if reward != row["resolved"]:
                    raise ValueError(f"Verifier reward mismatch for {path_prefix}")
                patch = archive.read(f"{path_prefix}/agent/model.patch")
                if hashlib.sha256(patch).hexdigest() != row["patch_sha256"]:
                    raise ValueError(f"Candidate patch digest mismatch for {path_prefix}")
                row["patch_bytes"] = len(patch)
                verifier_log = archive.read(f"{path_prefix}/verifier/stdout.log").decode(errors="replace")
                for label, field in (("Required tests", "verifier_tests_required"), ("Passed tests", "verifier_tests_passed")):
                    counts = re.findall(rf"^{label}:[ \t]+(\d+)\s*$", verifier_log, flags=re.MULTILINE)
                    if len(counts) == 1:
                        row[field] = int(counts[0])
                if row["agent_exit"] not in (0, 124) and row.get("agent_termination") != "benchmark_call_limit":
                    row.update(verifier_status=row["status"], verifier_resolved=reward,
                               status="agent_error", resolved=None)
            yield row


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2) + "\n")


def summarize(attempts):
    model_ids = {row["model"] for row in attempts}
    if len(model_ids) != 1:
        raise ValueError("Collect one model at a time to keep benchmark strata separate")
    harnesses = {}
    for harness in HARNESS_ORDER:
        rows = [row for row in attempts if row["harness"] == harness]
        status = Counter(row["status"] for row in rows)
        first = [row["resolved"] for row in rows if row["trial"] == 1 and row.get("resolved") in (0, 1)]
        known_costs = [Decimal(row["estimated_inference_usd"]) for row in rows if row.get("estimated_inference_usd")]
        harnesses[harness] = {
            "observed_attempts": len(rows),
            "statuses": dict(status),
            "first_trial_resolved": sum(first),
            "first_trial_verified": len(first),
            "first_trial_pass_at_1": sum(first) / len(first) if first else None,
            "estimated_uncached_usd_for_attempts_with_usage": str(sum(known_costs, Decimal(0))),
            "attempts_with_usage_cost": len(known_costs),
        }
    return {"model": model_ids.pop(), "harnesses": harnesses}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("artifacts", nargs="+", help="GitHub run ID and ZIP as RUN_ID=PATH")
    parser.add_argument("--out", type=Path, required=True)
    cli = parser.parse_args()
    attempts = []
    for spec in cli.artifacts:
        run_id, path = spec.split("=", 1)
        attempts.extend(artifact_rows(path, run_id))
    if not attempts:
        raise SystemExit("Artifact files contain no live attempts")
    summary = summarize(attempts)
    rows = [row for row in attempts if row.get("resolved") in (0, 1)]
    scores = {(r["task_id"], r["harness"], r["trial"]): r["resolved"] for r in rows}
    if len(scores) != len(rows):
        raise ValueError("Duplicate scored trial in the selected archives")
    cli.out.mkdir(parents=True, exist_ok=True)
    write_json(cli.out / "attempts.json", attempts)
    write_json(cli.out / "results.json", rows)
    write_json(cli.out / "outcome_summary.json", summary)
    fields = list(dict.fromkeys(key for row in attempts for key in row))
    with (cli.out / "results.csv").open("w", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    if set(r["harness"] for r in rows) == set(HARNESS_ORDER):
        try:
            paired = analyze(scores)
        except ValueError as exc:
            paired = {"status": "unbalanced", "reason": str(exc)}
        write_json(cli.out / "paired_analysis.json", paired)


if __name__ == "__main__":
    main()
