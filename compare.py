#!/usr/bin/env python3
"""Paired, task-resampled bootstrap for independent verified 0/1 outcomes.

Input CSV columns: task_id,harness,trial,resolved. Only explicit 0 or 1 accepted.
Each task must have the same trial numbers for all harnesses. Network and API
failures should be tracked separately and NOT silently scored as incorrect.
"""
from __future__ import annotations
import argparse
from collections import defaultdict
import csv
import random
import statistics


def read_verified(path):
    out = {}
    with open(path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            key = (row["task_id"], row["harness"], int(row["trial"]))
            if row["resolved"] not in ("0", "1"):
                raise ValueError(f"Expected verified resolved 0 or 1 for {key}")
            if key in out:
                raise ValueError(f"Duplicate verified outcome {key}")
            out[key] = int(row["resolved"])
    if not out:
        raise ValueError("No verified outcomes")
    return out


def validate_balanced(data):
    harnesses = sorted({k[1] for k in data})
    task_ids = sorted({k[0] for k in data})
    patterns = {t: {h: {k[2] for k in data if k[0] == t and k[1] == h}
                    for h in harnesses} for t in task_ids}
    ref = set(next(iter(patterns[task_ids[0]].values())))
    if not ref or any(s != ref for task in patterns.values() for s in task.values()):
        raise ValueError("Unbalanced tasks or trials; investigate missing runs before comparison")
    return task_ids, harnesses, sorted(ref)


def analyze(data, iterations=2000, seed=1847):
    tasks, harnesses, trials = validate_balanced(data)
    per_task = {h: {t: statistics.mean(data[(t,h,i)] for i in trials) for t in tasks} for h in harnesses}
    results = {h: {"resolved_rate": round(statistics.mean(per_task[h].values()),4)} for h in harnesses}
    rng = random.Random(seed)
    pairs = {}
    for i, a in enumerate(harnesses):
        for b in harnesses[i+1:]:
            point = statistics.mean(per_task[a][t]-per_task[b][t] for t in tasks)
            draws = sorted(statistics.mean(per_task[a][t]-per_task[b][t] for t in
                                            (rng.choice(tasks) for _ in tasks)) for _ in range(iterations))
            lo = draws[int(iterations*.025)]
            hi = draws[min(iterations-1,int(iterations*.975))]
            pairs[f"{a} minus {b}"] = {"difference":round(point,4),"95pct_task_bootstrap_CI":[round(lo,4),round(hi,4)]}
    return {"tasks":len(tasks),"trials_per_task":len(trials),"harness_results":results,
            "paired_differences":pairs,"method":"paired task bootstrap; 0/1 verified outcomes only"}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("csv_file");p.add_argument("--bootstrap", type=int, default=2000)
    args=p.parse_args()
    if args.bootstrap < 40: p.error("bootstrap must be >=40")
    import json
    print(json.dumps(analyze(read_verified(args.csv_file),iterations=args.bootstrap), indent=2))

if __name__=="__main__": main()
