#!/usr/bin/env python3
"""Execute a preregistered HARD-51 subset in fresh Docker containers."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import shutil
import subprocess
import tempfile
import threading
import time
import tomllib
import urllib.error
import uuid
from decimal import Decimal
from pathlib import Path

from gateway import Budget, Relay, inventory

CI = Path(__file__).resolve().parent
SETTINGS = json.loads((CI / "settings.json").read_text())
TASKS = json.loads((CI / "tasks.json").read_text())
HARNESSES = ("pi", "opencode", "mini-swe-agent")
NETWORK_RULES = ("\n\nWork only in the supplied repository. Do not use external assistance. "
                 "Leave the final source changes in the working tree.\n")
RESULT_COLUMNS = (
    "task_id", "harness", "trial", "model", "status", "resolved", "agent_exit", "verifier_exit",
    "agent_seconds", "verifier_seconds", "base_commit", "image", "patch_sha256", "model_calls",
    "model_errors", "budget_denials", "tool_calls_observed", "input_tokens", "output_tokens",
    "reasoning_tokens", "estimated_inference_usd", "actual_inference_usd", "actual_compute_usd",
)


def git_show(dataset, path):
    return subprocess.check_output(["git", "-C", dataset, "show", SETTINGS["dataset_revision"] + ":" + path])


def task_data(dataset, task_id):
    root = "v2/tasks/" + task_id
    read = lambda name: git_show(dataset, root + "/" + name)
    manifest = tomllib.loads(read("task.toml").decode())
    if manifest["agent"]["network_mode"] != "no-network":
        raise ValueError("Official task expected to require network isolation")
    return manifest["environment"], read


def docker(*args, **kwargs):
    return subprocess.run(["docker", *map(str, args)], **kwargs)


def container(image, command, *, mounts, cpus, memory, log, timeout, extra=()):
    name = "ht-" + uuid.uuid4().hex[:20]
    args = ["run", "--rm", "--name", name, "--platform", "linux/amd64", "--network", "none",
            "--cpus", cpus, "--memory", f"{memory}m", "--pids-limit", 512, "--init",
            "--security-opt", "no-new-privileges", "--cap-drop", "ALL", "--entrypoint", "/bin/bash"]
    for host, dest, access in mounts:
        args += ["-v", f"{host}:{dest}:{access}"]
    with log.open("wb") as fp:
        try:
            return docker(*args, *extra, image, *command, stdout=fp, stderr=subprocess.STDOUT,
                          timeout=timeout).returncode
        except subprocess.TimeoutExpired:
            return 124
        finally:
            docker("rm", "-f", name, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                   timeout=30)


def write_json(path, data):
    path.write_text(json.dumps(data, indent=2) + "\n")


def prepare_model_config(destination):
    model = SETTINGS["model"]
    context = SETTINGS["context_ceiling"]
    output = SETTINGS["max_completion_tokens_per_call"]
    url = "http://127.0.0.1:18981/v1"
    write_json(destination / "models.json", {"providers": {"ht": {
        "apiKey": "relay-only", "baseUrl": url, "api": "openai-completions",
        "models": [{"id": model, "name": model, "reasoning": True, "input": ["text"],
                    "contextWindow": context, "maxTokens": output,
                    "compat": {"supportsDeveloperRole": False, "supportsReasoningEffort": False,
                               "maxTokensField": "max_tokens"}}],
    }}})
    write_json(destination / "opencode.json", {
        "autoupdate": False, "enabled_providers": ["ht"], "share": "disabled",
        "permission": {"*": "allow", "webfetch": "deny", "websearch": "deny", "task": "deny"},
        "provider": {"ht": {"npm": "@ai-sdk/openai-compatible", "name": "HarnessTwin",
                            "options": {"baseURL": url, "apiKey": "relay-only"},
                            "models": {model: {"name": model, "reasoning": True, "tool_call": True,
                                               "interleaved": {"field": "reasoning_content"},
                                               "limit": {"context": context, "output": output}}}}},
    })


def prepare_task(dataset, task, run_dir):
    env, read = task_data(dataset, task)
    config = run_dir / "config"
    config.mkdir(parents=True)
    for name in ("agent.sh", "mini.yaml", "loopback.mjs"):
        shutil.copyfile(CI / name, config / name)
    (config / "instruction.md").write_bytes(read("instruction.md") + NETWORK_RULES.encode())
    prepare_model_config(config)
    tests = run_dir / "tests"
    tests.mkdir()
    for name in ("test.sh", "run_script.sh", "parser.py", "config.json", "test_patch.patch"):
        (tests / name).write_bytes(read("tests/" + name))
    ref = env["docker_image"]
    if not ref.startswith("ghcr.io/scaleapi/swe-bench_pro-v2:") or ref.split(":")[-1] != task:
        raise ValueError("Unexpected official task image")
    docker("pull", "--platform", "linux/amd64", ref, timeout=900, check=True)
    image = subprocess.check_output(
        ["docker", "image", "inspect", "--format", "{{index .RepoDigests 0}}", ref], text=True
    ).strip()
    if not re.fullmatch(r"ghcr\.io/scaleapi/swe-bench_pro-v2@sha256:[0-9a-f]{64}", image):
        raise ValueError("Missing official image digest")
    return env, read, config, tests, image


def verify(image, patch, tests, run_dir, env):
    evidence = run_dir / "verifier"
    evidence.mkdir()
    evidence.chmod(0o777)
    start = time.monotonic()
    code = container(image, ["/harnesstwin-verify.sh"], mounts=[
        (CI / "verify.sh", "/harnesstwin-verify.sh", "ro"), (tests, "/tests", "ro"),
        (patch, "/candidate/patch", "ro"), (evidence, "/logs/verifier", "rw"),
    ], cpus=env["cpus"], memory=env["memory_mb"], log=evidence / "stdout.log",
        timeout=SETTINGS["verifier_seconds"])
    reward = evidence / "reward.txt"
    value = reward.read_text().strip() if reward.exists() else None
    log = (evidence / "stdout.log").read_text(errors="replace").lower()
    # An official reward=0 covers both test failures and verifier setup failures.
    # Attribute a failure only when the official verifier emitted its test verdict.
    if code == 42:
        status, resolved = "invalid_patch", None
    elif code == 0 and value == "1" and "result: passed" in log:
        status, resolved = "verified_resolved", 1
    elif value == "0" and "result: failed" in log:
        status, resolved = "verified_unresolved", 0
    else:
        status, resolved = "verifier_error", None
    base = evidence / "base.txt"
    return dict(status=status, resolved=resolved, verifier_exit=code,
                verifier_seconds=round(time.monotonic() - start, 2),
                base_commit=base.read_text().strip() if base.exists() else None)


def totals(usage_file):
    rows = [json.loads(line) for line in usage_file.read_text().splitlines()] if usage_file.exists() else []
    def total(field, details=None):
        values = [((row.get("usage") or {}).get(details) or {}).get(field) if details
                  else (row.get("usage") or {}).get(field) for row in rows]
        return sum(values) if values and all(type(n) is int for n in values) else None
    costs = [row["estimated_usd_uncached"] for row in rows]
    return {
        "tool_calls_observed": sum(len(row["tool_names"]) for row in rows),
        "input_tokens": total("prompt_tokens"), "output_tokens": total("completion_tokens"),
        "reasoning_tokens": total("reasoning_tokens", "completion_tokens_details"),
        "estimated_inference_usd": str(sum(map(Decimal, costs))) if costs and all(costs) else None,
    }


def run_agent(image, official_image, harness, config, tests, run_dir, env):
    traces = run_dir / "agent"
    traces.mkdir()
    traces.chmod(0o777)
    usage_file = run_dir / "usage.jsonl"
    budget = Budget(SETTINGS["model"], SETTINGS["context_ceiling"], SETTINGS["max_completion_tokens_per_call"],
                    SETTINGS["input_usd_per_million_upper_rate"], SETTINGS["output_usd_per_million_upper_rate"],
                    SETTINGS["attempt_cap_usd"], SETTINGS["max_calls"], SETTINGS["reasoning_effort"], usage_file)
    start = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="ht-") as socket_dir:
        Path(socket_dir).chmod(0o755)
        with Relay(Path(socket_dir) / "gmi.sock", budget) as relay:
            server = threading.Thread(target=relay.serve_forever, daemon=True)
            server.start()
            try:
                code = container(image, ["/run-config/agent.sh", harness, SETTINGS["agent_seconds"]],
                                 mounts=[(socket_dir, "/inference", "ro"), (config, "/run-config", "ro"),
                                         (traces, "/job", "rw")],
                                 cpus=env["cpus"], memory=env["memory_mb"],
                                 log=run_dir / "agent.log", timeout=SETTINGS["agent_seconds"] + 90,
                                 extra=["-e", "MODEL_ID=" + SETTINGS["model"]])
            finally:
                relay.shutdown()
                server.join()
    duration = round(time.monotonic() - start, 2)
    patch = traces / "model.patch"
    exit_file = traces / "agent-exit.txt"
    agent_exit = int(exit_file.read_text()) if exit_file.is_file() else code
    row = dict(agent_exit=agent_exit, agent_seconds=duration, model_calls=budget.calls,
               model_errors=budget.errors, budget_denials=budget.denied, **totals(usage_file))
    if code or not patch.is_file() or not budget.calls or budget.errors == budget.calls:
        return row | dict(status="agent_or_inference_error", resolved=None)
    outcome = verify(official_image, patch, tests, run_dir, env)
    agent_base = (traces / "base.txt").read_text().strip()
    if agent_base != outcome["base_commit"]:
        outcome.update(status="base_commit_mismatch", resolved=None)
    return row | outcome | {"patch_sha256": hashlib.sha256(patch.read_bytes()).hexdigest()}


def save_results(out, results):
    write_json(out / "results.json", results)
    with (out / "results.csv").open("w", newline="") as fp:
        writer = csv.DictWriter(fp, fieldnames=RESULT_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(results)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["inventory", "controls", "triad", "second", "pilot", "plan"])
    parser.add_argument("--dataset", type=Path)
    parser.add_argument("--out", type=Path, default=Path("evidence"))
    ns = parser.parse_args()
    out = ns.out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    save_results(out, [])
    if ns.mode in {"inventory", "triad", "second", "pilot"}:
        try:
            models = inventory()
        except urllib.error.HTTPError as exc:
            raise SystemExit(f"Authenticated GMI inventory request failed: HTTP {exc.code}") from None
        except ValueError as exc:
            raise SystemExit(str(exc)) from None
        write_json(out / "inventory.json", {
            "model_count": len(models),
            "matching_ids": sorted(i for i in models if re.search(r"(glm[- ]?5\.3|deepseek[-_/ ]?v4\.1|kimi[-_ ]?k3)", i, re.I)),
        })
        if ns.mode == "inventory":
            return
        model = models.get(SETTINGS["model"])
        if model is None:
            raise SystemExit("Pinned model ID is unavailable in the authenticated inventory")
        if (ctx := model.get("context_length")) and (not isinstance(ctx, int) or ctx > SETTINGS["context_ceiling"]):
            raise SystemExit("The authenticated model context exceeds the configured cost ceiling")
    first_two = TASKS[:2]
    selected = {
        "plan": TASKS, "pilot": first_two, "second": TASKS[1:2],
    }.get(ns.mode, TASKS[:1])
    attempts = [
        (task, HARNESSES[(TASKS.index(task) + shift) % len(HARNESSES)])
        for task in selected for shift in range(len(HARNESSES))
    ]
    planned_cost = Decimal(str(SETTINGS["attempt_cap_usd"])) * len(attempts) + Decimal(str(SETTINGS["compute_reserve_usd"]))
    if not ns.dataset:
        parser.error("--dataset is required")
    source = git_show(ns.dataset, "v2/hard51_ids.txt")
    if hashlib.sha256(source).hexdigest() != SETTINGS["hard51_sha256"]:
        raise SystemExit("HARD-51 checksum mismatch")
    if len(set(TASKS)) != 10 or not set(TASKS) <= set(source.decode().splitlines()):
        raise SystemExit("Invalid preregistered HARD-51 task subset")
    write_json(out / "plan.json", {"settings": SETTINGS, "mode": ns.mode, "attempts": attempts})
    if ns.mode == "plan":
        return
    if ns.mode != "controls" and planned_cost >= Decimal(str(SETTINGS["mission_cap_usd"])):
        raise SystemExit("Planned spend reaches mission ceiling")
    results = []
    for ix, task in enumerate(selected):
        task_dir = out / f"task-{ix + 1:02}"
        task_dir.mkdir()
        env, read, config, tests, official_image = prepare_task(ns.dataset, task, task_dir)
        image = "harnesstwin-agent:" + str(ix + 1)
        if ns.mode == "controls":
            controls = {}
            for name, patch_data in [("empty", b""), ("oracle", read("solution/gold_patch.diff"))]:
                control = task_dir / name
                control.mkdir()
                patch = control / "control.patch"
                patch.write_bytes(patch_data)
                controls[name] = verify(official_image, patch, tests, control, env)
                patch.unlink()
            write_json(out / "controls.json", controls)
            if [controls[name]["resolved"] for name in ("empty", "oracle")] != [0, 1]:
                raise SystemExit("Official verifier controls did not match the release gate")
        with (task_dir / "build.log").open("wb") as log:
            docker("build", "--platform", "linux/amd64", "--build-arg", "OFFICIAL_IMAGE=" + official_image,
                   "-t", image, CI, stdout=log, stderr=subprocess.STDOUT, timeout=1500, check=True)
        if ns.mode == "controls":
            break
        for _, harness in (item for item in attempts if item[0] == task):
            run_dir = task_dir / harness
            run_dir.mkdir()
            row = run_agent(image, official_image, harness, config, tests, run_dir, env)
            results.append(dict(task_id=task, harness=harness, trial=1, model=SETTINGS["model"],
                                image=official_image, **row))
            save_results(out, results)
        shutil.rmtree(tests)


if __name__ == "__main__":
    main()
