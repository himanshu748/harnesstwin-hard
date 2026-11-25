#!/usr/bin/env python3
"""Plan and optionally run a reproducible 3-harness benchmark on SWE-bench Pro V2 HARD-51.

Requires the public scaleapi/SWE-bench_Pro-os repository, Harbor, a container
runtime, and an explicitly supplied GMI_API_KEY for live runs. No model calls
are made unless --execute is used. It DOES NOT grade or invent test scores.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
from typing import Iterable

HARNESSES = ("pi", "opencode", "mini-swe-agent")
GMI_URL = "https://api.gmi-serving.com/v1"
GMI_HOST = "api.gmi-serving.com"


def load_hard51(dataset: Path) -> list[str]:
    """Read the authoritative pinned subset list; fail on missing/unsafe tasks."""
    source = dataset / "v2" / "hard51_ids.txt"
    if not source.is_file():
        raise FileNotFoundError(f"SWE-bench Pro V2 subset not found: {source}. Clone scaleapi/SWE-bench_Pro-os.")
    raw = source.read_text(encoding="utf-8")
    task_ids = [item.strip() for item in raw.splitlines() if item.strip()]
    if len(task_ids) != 51 or len(set(task_ids)) != 51:
        raise ValueError(f"Expected exactly 51 unique HARD-51 tasks, received {len(task_ids)}")
    for task_id in task_ids:
        if not re.fullmatch(r"instance_[A-Za-z0-9_.-]{12,240}", task_id):
            raise ValueError(f"Unsafe task ID: {task_id!r}")
        task = dataset / "v2" / "tasks" / task_id
        if not (task / "task.toml").is_file() or not (task / "tests" / "test.sh").is_file():
            raise FileNotFoundError(f"Missing task manifest or verifier: {task_id}")
    return task_ids


def git_revision(dataset: Path) -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=dataset, capture_output=True,
            text=True, check=True, timeout=5,
        )
        sha = result.stdout.strip()
        return sha if re.fullmatch(r"[0-9a-f]{40}", sha) else None
    except (FileNotFoundError, subprocess.SubprocessError):
        return None


def sanitized_job_name(model: str, harness: str, trial: int, task_number: int, tag: str) -> str:
    token = re.sub(r"[^a-z0-9-]", "-", model.lower()).strip("-")[:18] or "model"
    return f"ht-{tag}-{token}-{harness.replace('mini-swe-agent', 'mini')}-{task_number:02d}-t{trial}"


def harbor_command(*, task_id: str, harness: str, model: str, environment: str,
                   job_name: str) -> list[str]:
    if harness not in HARNESSES:
        raise ValueError(f"Unsupported harness: {harness}")
    if not model or any(ch.isspace() for ch in model):
        raise ValueError("Pass an exact GMI model ID without whitespace")
    if environment not in ("docker", "modal"):
        raise ValueError("Environment must be docker or modal")
    # Harbor's standard openai provider prefix is a routing hint and not part
    # of the model ID sent to the endpoint. Nested model IDs stay unchanged.
    return [
        "harbor", "run", "-p", "v2/tasks", "-i", task_id,
        "-e", environment, "-n", "1", "-a", harness,
        "-m", "openai/" + model,
        "--api-format", "openai_chat_completions",
        "--base-url", GMI_URL,
        "--api-key-name", "GMI_API_KEY",
        "--allow-agent-host", GMI_HOST,
        "--job-name", job_name,
    ]


def make_plan(*, dataset: Path, model: str, environment: str, harnesses: Iterable[str],
              task_limit: int, trials: int, run_tag: str) -> dict:
    all_ids = load_hard51(dataset)
    if trials < 1:
        raise ValueError("trials must be >= 1")
    if not 1 <= task_limit <= 51:
        raise ValueError("task_limit must be between 1 and 51")
    subset = all_ids[:task_limit]
    harnesses = list(harnesses)
    if not harnesses or len(set(harnesses)) != len(harnesses):
        raise ValueError("Harness list must be nonempty without duplicates")
    if any(h not in HARNESSES for h in harnesses):
        raise ValueError("Unknown harness")
    jobs = []
    # Interleaved order avoids confounding daily provider changes with harness.
    for trial in range(1, trials+1):
        for ix, tid in enumerate(subset, 1):
            for shift in range(len(harnesses)):
                harness = harnesses[(shift + trial + ix) % len(harnesses)]
                name = sanitized_job_name(model, harness, trial, ix, run_tag)
                jobs.append({
                    "task_id": tid, "harness": harness, "trial": trial,
                    "job_name": name,
                    "command": harbor_command(task_id=tid, harness=harness, model=model,
                                              environment=environment, job_name=name),
                    "status": "planned", "exit_code": None,
                })
    input_file = (dataset / "v2" / "hard51_ids.txt").read_bytes()
    return {
        "schema": "harnesstwin-hard-plan-v1",
        "created_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "dataset": str(dataset.resolve()),
        "dataset_git_commit": git_revision(dataset),
        "hard51_sha256": hashlib.sha256(input_file).hexdigest(),
        "model_id_gmi_exact": model,
        "provider_url": GMI_URL,
        "environment": environment,
        "harnesses": harnesses,
        "task_count": len(subset),
        "trials": trials,
        "total_attempts": len(jobs),
        "grading": "Harbor default verifier. For publishable HARD-51 scores, replay captured patches in pristine images using the dataset's locked protocol.",
        "jobs": jobs,
    }


def run_plan(plan: dict, destination: Path, dataset: Path, *, execute: bool) -> None:
    if execute:
        if not os.environ.get("GMI_API_KEY"):
            raise RuntimeError("GMI_API_KEY required for --execute. Keep it out of shell history and git.")
        if shutil.which("harbor") is None:
            raise RuntimeError("Harbor not installed; see https://github.com/harbor-framework/harbor")
    destination.mkdir(parents=True, exist_ok=True)
    plan_path = destination / "plan.json"
    plan_path.write_text(json.dumps(plan, indent=2) + "\n")
    status_file = destination / "jobs.jsonl"
    with status_file.open("w") as result:
        for j in plan["jobs"]:
            rendered = shlex.join(j["command"])
            print(("EXEC " if execute else "PLAN ") + rendered, flush=True)
            if execute:
                with (destination / f"{j['job_name']}.stdout").open("w") as stdout, (
                    destination / f"{j['job_name']}.stderr"
                ).open("w") as stderr:
                    proc = subprocess.run(j["command"], cwd=dataset, env=os.environ.copy(),
                                          stdout=stdout, stderr=stderr, stdin=subprocess.DEVNULL,
                                          check=False)
                    j["exit_code"] = proc.returncode
                    j["status"] = "completed" if proc.returncode == 0 else "harness_or_infrastructure_error"
            result.write(json.dumps({"task_id": j["task_id"], "harness": j["harness"],
                                     "trial": j["trial"], "job_name": j["job_name"],
                                     "status": j["status"], "exit_code": j["exit_code"]}) + "\n")
            result.flush()
    if execute:
        # Preserve status in plan and never substitute an exit code for an evaluation result.
        plan_path.write_text(json.dumps(plan, indent=2) + "\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", required=True, type=Path,
                        help="Path to scaleapi/SWE-bench_Pro-os checkout")
    parser.add_argument("--model", required=True, help="Exact GMI /v1/models ID")
    parser.add_argument("--environment", choices=["modal", "docker"], default="modal")
    parser.add_argument("--tasks", type=int, default=2, help="First N HARD-51 tasks, 51 for all")
    parser.add_argument("--trials", type=int, default=1)
    parser.add_argument("--harnesses", nargs="+", choices=HARNESSES, default=list(HARNESSES))
    parser.add_argument("--output", type=Path, default=Path("hard-results"))
    parser.add_argument("--tag", type=str, default="trial")
    parser.add_argument("--execute", action="store_true", help="Actually run paid API calls; off by default")
    ns = parser.parse_args(argv)
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,20}", ns.tag):
        parser.error("--tag requires 1-20 alphanumeric, _ or - characters")
    try:
        plan = make_plan(dataset=ns.dataset, model=ns.model, environment=ns.environment,
                         harnesses=ns.harnesses, task_limit=ns.tasks, trials=ns.trials,
                         run_tag=ns.tag)
        run_plan(plan, ns.output, ns.dataset, execute=ns.execute)
    except (ValueError, FileNotFoundError, RuntimeError) as exc:
        parser.error(str(exc))
    print(f"{plan['total_attempts']} jobs. Manifest: {ns.output / 'plan.json'}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
