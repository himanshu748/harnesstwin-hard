#!/usr/bin/env python3
"""Summarize tool execution and termination events in an Actions evidence ZIP."""
import argparse
from collections import Counter
import json
from pathlib import Path
from zipfile import ZipFile


def object_lines(stream):
    for line in stream:
        try:
            event = json.loads(line)
        except (ValueError, UnicodeError):
            continue
        if isinstance(event, dict):
            yield event


def opencode_exited_at_call_limit(stream):
    error = None
    for event in object_lines(stream):
        if event.get("type") == "error":
            error = event.get("error")
    data = error.get("data", {}) if isinstance(error, dict) else {}
    if not isinstance(data, dict) or data.get("statusCode") != 429:
        return False
    try:
        payload = json.loads(data.get("responseBody", ""))["error"]
    except (KeyError, TypeError, ValueError):
        return False
    return payload == {"message": "Benchmark request or spending cap reached", "type": "harnesstwin_relay"}


def agent_trace(archive, prefix, harness):
    if harness == "mini-swe-agent":
        with archive.open(prefix + "/agent/trajectory.json") as stream:
            trajectory = json.load(stream)
        latest = trajectory["info"].get("exit_status")
        tools = []
        for message in trajectory["messages"]:
            if message.get("role") == "tool":
                outcome = message.get("extra", {})
                tools.append(("bash", outcome.get("returncode", 0) != 0 or bool(outcome.get("exception_info"))))
        return latest, tools

    latest, tools = None, []
    with archive.open(prefix + "/agent.log") as stream:
        for event in object_lines(stream):
            kind = event.get("type")
            if harness == "pi":
                if kind == "message_end" and event["message"].get("role") == "assistant":
                    latest = event["message"].get("stopReason")
                if kind == "tool_execution_end":
                    tools.append((event["toolName"], bool(event.get("isError"))))
            elif harness == "opencode":
                if kind == "step_finish":
                    latest = event["part"].get("reason")
                if kind == "error":
                    latest = "error"
                if kind == "tool_use":
                    part = event["part"]
                    state = part["state"]
                    exit_code = (state.get("metadata") or {}).get("exit")
                    tools.append((part["tool"], state.get("status") == "error" or exit_code not in (None, 0)))
    return latest, tools


def read_archive(path, run_id):
    with ZipFile(path) as archive:
        plan = json.loads(archive.read("plan.json"))
        ordered = list(dict.fromkeys(task for task, _ in plan["attempts"]))
        for row in json.loads(archive.read("results.json")):
            task, harness = row["task_id"], row["harness"]
            prefix = f"task-{ordered.index(task) + 1:02}/{harness}"
            metadata = {"source_run": run_id, "task_id": task, "harness": harness, "trial": row["trial"]}
            try:
                latest, tools = agent_trace(archive, prefix, harness)
            except KeyError:
                yield metadata | {"trace_available": False}
                continue
            failures = [index for index, (_, failed) in enumerate(tools) if failed]
            yield metadata | {
                "trace_available": True,
                "terminal_reason": latest,
                "tool_records_by_name": dict(Counter(name for name, _ in tools)),
                "tool_failures_observed": len(failures),
                "successful_tool_after_failure": any(not failed for _, failed in tools[failures[0] + 1:])
                if failures else None,
            }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("artifacts", nargs="+", help="GitHub run ID and ZIP as RUN_ID=PATH")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    rows = []
    for spec in args.artifacts:
        run_id, path = spec.split("=", 1)
        rows.extend(read_archive(path, run_id))
    args.out.write_text(json.dumps(rows, indent=2) + "\n")


if __name__ == "__main__":
    main()
