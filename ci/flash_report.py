#!/usr/bin/env python3
"""Aggregate the Flash HARD-51 sweep and account for missing task verdicts."""
import argparse
from collections import Counter
import csv
import json
from pathlib import Path
from statistics import median
from zipfile import ZipFile

from collect_pilot import HARNESS_ORDER, analyze, artifact_rows, write_json


def rollup(tasks, attempts, trial):
    enrolled = set(tasks)
    if any(row["task_id"] not in enrolled or row["harness"] not in HARNESS_ORDER for row in attempts):
        raise ValueError("Attempt is outside the official task and harness matrix")
    trials = {(row["task_id"], row["harness"]): row for row in attempts}
    if len(trials) != len(attempts) or any(row["trial"] != trial for row in attempts):
        raise ValueError("Duplicate attempts or mixed independent trials")
    scores = {(*key, trial): row["resolved"] for key, row in trials.items() if row["resolved"] in (0, 1)}
    matched = [task for task in tasks if all((task, harness, trial) in scores for harness in HARNESS_ORDER)]
    matched_scores = {key: value for key, value in scores.items() if key[0] in matched}
    results = []
    for harness in HARNESS_ORDER:
        rows = [trials[task, harness] for task in tasks if (task, harness) in trials]
        statuses = Counter(row["status"] for row in rows)
        verified = [row for row in rows if row["resolved"] in (0, 1)]
        solutions = sum(row["resolved"] for row in verified)
        row = {"harness": harness, "scheduled": len(tasks), "recorded": len(rows),
               "verified": len(verified), "resolved": solutions,
               "verified_unresolved": len(verified) - solutions,
               "no_official_verdict": len(rows) - len(verified), "missing": len(tasks) - len(rows),
               "pass_at_1_among_verified": solutions / len(verified) if verified else None,
               "official_resolutions_per_scheduled_task": solutions / len(tasks), "statuses": dict(statuses)}
        successful = [entry for entry in verified if entry["resolved"] == 1]
        for field in ("input_tokens", "output_tokens", "reasoning_tokens", "agent_seconds"):
            known = [entry[field] for entry in verified if entry.get(field) is not None]
            solved = [entry[field] for entry in successful if entry.get(field) is not None]
            row[f"median_{field}_verified"] = median(known) if known else None
            row[f"median_{field}_resolved"] = median(solved) if solved else None
        results.append(row)
    paired = analyze(matched_scores) if matched_scores else None
    return {"trial": trial, "task_order": tasks, "scheduled_attempts": len(tasks) * len(HARNESS_ORDER),
            "recorded_attempts": len(attempts), "matched_tasks": matched,
            "harnesses": results, "paired": paired}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("artifacts", nargs="+", help="GitHub run ID and ZIP as RUN_ID=PATH")
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--trial", type=int, default=1)
    args = parser.parse_args()
    tasks, attempts = None, []
    for spec in args.artifacts:
        run_id, path = spec.split("=", 1)
        with ZipFile(path) as archive:
            if "plan.json" not in archive.namelist():
                continue
            plan = json.loads(archive.read("plan.json"))
        settings = plan["settings"]
        if (plan["mode"] != "hard-flash" or settings["model"] != "zai-org/GLM-5.3-Flash"
                or settings["max_completion_tokens_per_call"] is not None):
            raise ValueError("Expected provider-native GLM-5.3 Flash artifacts")
        order = plan["task_order"]
        if len(set(order)) != 51 or (tasks is not None and order != tasks):
            raise ValueError("Inconsistent official task order")
        tasks = order
        attempts.extend(artifact_rows(path, run_id))
    if tasks is None:
        raise SystemExit("No shard reached the official benchmark plan")
    coverage = rollup(tasks, attempts, args.trial)
    args.out.mkdir(parents=True, exist_ok=True)
    write_json(args.out / "coverage.json", coverage)
    write_json(args.out / "attempts.json", attempts)
    verified = [row for row in attempts if row["resolved"] in (0, 1)]
    write_json(args.out / "results.json", verified)
    fields = list(dict.fromkeys(field for row in attempts for field in row))
    if fields:
        with (args.out / "results.csv").open("w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            writer.writerows(verified)
    note = ["# HARD-51 GLM-5.3 Flash", "", f"Trial {args.trial} recorded {len(attempts)} of {coverage['scheduled_attempts']} "
            f"scheduled attempts. {len(coverage['matched_tasks'])} tasks have a verifier verdict for all three harnesses.",
            "", "| Harness | Solved | Unresolved | No verdict | Missing | Median output tokens among verified |",
            "| --- | ---: | ---: | ---: | ---: | ---: |"]
    for row in coverage["harnesses"]:
        value = row["median_output_tokens_verified"]
        shown = "n/a" if value is None else f"{value:,.0f}"
        note.append(f"| {row['harness']} | {row['resolved']} | {row['verified_unresolved']} | "
                    f"{row['no_official_verdict']} | {row['missing']} | {shown} |")
    note += ["", "`coverage.json` has the verified, scheduled and paired denominators, with task-bootstrap intervals "
             "on the fully matched tasks. Reasoning tokens are part of output tokens."]
    (args.out / "REPORT.md").write_text("\n".join(note) + "\n")


if __name__ == "__main__":
    main()
