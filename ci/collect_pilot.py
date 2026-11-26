"""Combine completed workflow artifacts, retaining failed integrations as evidence."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path
import sys
from zipfile import ZipFile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from compare import analyze

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
            if row.get("resolved") in (0, 1):
                reward = int(archive.read(f"{path_prefix}/verifier/reward.txt"))
                if reward != row["resolved"]:
                    raise ValueError(f"Verifier reward mismatch for {path_prefix}")
                patch = archive.read(f"{path_prefix}/agent/model.patch")
                if hashlib.sha256(patch).hexdigest() != row["patch_sha256"]:
                    raise ValueError(f"Candidate patch digest mismatch for {path_prefix}")
                row["patch_bytes"] = len(patch)
                if row["agent_exit"] not in (0, 124):
                    row.update(verifier_status=row["status"], verifier_resolved=reward,
                               status="agent_error", resolved=None)
            yield row


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2) + "\n")


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
    cli.out.mkdir(parents=True, exist_ok=True)
    write_json(cli.out / "attempts.json", attempts)
    rows = [row for row in attempts if row.get("resolved") in (0, 1)]
    write_json(cli.out / "results.json", rows)
    fields = list(dict.fromkeys(key for row in attempts for key in row))
    with (cli.out / "results.csv").open("w", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    scores = {(r["task_id"], r["harness"], r["trial"]): r["resolved"] for r in rows}
    if len(scores) != len(rows):
        raise ValueError("Duplicate scored trial in the selected archives")
    if set(r["harness"] for r in rows) == set(HARNESS_ORDER):
        try:
            paired = analyze(scores)
        except ValueError as exc:
            paired = {"status": "unbalanced", "reason": str(exc)}
        write_json(cli.out / "paired_analysis.json", paired)


if __name__ == "__main__":
    main()
