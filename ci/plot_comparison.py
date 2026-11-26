#!/usr/bin/env python3
"""Plot measured per-task outcomes and time from one benchmark model."""
import argparse
import json
from pathlib import Path
from statistics import median

import matplotlib.pyplot as plt


HARNESSES = {"pi": "Pi", "opencode": "OpenCode", "mini-swe-agent": "mini-SWE-agent"}
OUTCOMES = {
    1: ("#166958", "Resolved", "o"),
    0: ("#a54336", "Unresolved", "x"),
    None: ("#686e73", "No verifier verdict", "s"),
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("attempts", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--trial", type=int, default=1)
    args = parser.parse_args()
    rows = [row for row in json.loads(args.attempts.read_text()) if row["trial"] == args.trial]
    models = {row["model"] for row in rows}
    if len(models) != 1:
        parser.error("Provide one model with attempts for the selected trial")

    official_tasks = json.loads(Path(__file__).with_name("tasks.json").read_text())
    tasks = [task for task in official_tasks if any(row["task_id"] == task for row in rows)]
    if not tasks:
        parser.error("Found no preregistered benchmark tasks")
    task_positions = {task: index for index, task in enumerate(tasks)}
    by_attempt = {}
    for row in rows:
        key = row["harness"], row["task_id"]
        if key not in by_attempt or row["resolved"] in (0, 1):
            by_attempt[key] = row
    by_harness = {h: [by_attempt[h, task] for task in tasks if (h, task) in by_attempt] for h in HARNESSES}

    plt.rcParams.update({"svg.fonttype": "none", "font.size": 10})
    fig, axes = plt.subplots(2, 1, figsize=(max(8, len(tasks) * 0.8), 6.3), layout="constrained")
    outcome_axis, time_axis = axes
    outcome_handles = {}
    for height, harness in enumerate(HARNESSES):
        for row in by_harness[harness]:
            reward = row["resolved"]
            color, label, glyph = OUTCOMES[reward]
            dot = outcome_axis.scatter(task_positions[row["task_id"]], height, c=color, marker=glyph, s=68)
            outcome_handles[label] = dot
        durations = [(row["agent_seconds"], row["resolved"]) for row in by_harness[harness]
                     if row.get("agent_seconds") is not None]
        for seconds, reward in durations:
            color, _, glyph = OUTCOMES[reward]
            time_axis.scatter(seconds, height, c=color, marker=glyph, s=52, alpha=0.7)
        if durations:
            middle = median(seconds for seconds, _ in durations)
            time_axis.vlines(middle, height - 0.22, height + 0.22, color="#263142", linewidth=2)

    outcome_axis.set_title("Official patch replay by task", loc="left", fontweight="bold")
    outcome_axis.set_xticks(range(len(tasks)), [f"Task {official_tasks.index(task) + 1}" for task in tasks])
    outcome_axis.set_xlim(-0.5, len(tasks) - 0.5)
    outcome_axis.legend(outcome_handles.values(), outcome_handles.keys(), loc="upper left", bbox_to_anchor=(0, -0.16),
                        frameon=False, ncols=len(outcome_handles))
    time_axis.set_title("Agent wall time", loc="left", fontweight="bold")
    time_axis.set_xlabel("Seconds · vertical mark is the harness median")
    time_axis.set_xlim(left=0)
    for axis in axes:
        axis.set_yticks(range(len(HARNESSES)), HARNESSES.values())
        axis.set_ylim(len(HARNESSES) - 0.5, -0.5)
        axis.grid(axis="x", alpha=0.2)
        axis.set_axisbelow(True)
        axis.spines[["top", "right", "left"]].set_visible(False)
    fig.suptitle(f"{models.pop()} · trial {args.trial}", x=0.025, ha="left", fontweight="bold")
    fig.savefig(args.output, metadata={"Date": None})


if __name__ == "__main__":
    main()
