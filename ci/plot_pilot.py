#!/usr/bin/env python3
"""Plot measured harness wall time and metered tokens from a pilot result JSON."""
import argparse
import json
from pathlib import Path

import matplotlib.pyplot as plt
from matplotlib.ticker import FuncFormatter


HARNESSES = ("pi", "opencode", "mini-swe-agent")
LABELS = ("Pi", "OpenCode", "mini-SWE-agent")
METRICS = (
    ("agent_seconds", "Agent wall time", "seconds", "#276168"),
    ("input_tokens", "Input tokens", "millions", "#23649e"),
    ("output_tokens", "Output tokens", "thousands", "#7c4daf"),
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("results", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    rows = json.loads(args.results.read_text())
    tasks = list(dict.fromkeys(row["task_id"] for row in rows))
    if not 1 <= len(tasks) <= 2:
        parser.error("Provide verified pilot results for one or two tasks")

    plt.rcParams.update({"svg.fonttype": "none", "font.size": 10})
    fig, axes = plt.subplots(1, 3, figsize=(12, 3.7), layout="constrained")
    by_attempt = {(row["task_id"], row["harness"]): row for row in rows}
    for axis, (metric, title, units, color) in zip(axes, METRICS):
        for offset in range(len(tasks)):
            task = tasks[offset]
            for index, name in enumerate(HARNESSES):
                row = by_attempt.get((task, name), {})
                value = row.get(metric)
                position = index + (offset - (len(tasks) - 1) / 2) * 0.31
                if value is None:
                    axis.text(position, 0, "n/a", color="#555555", ha="center", va="bottom", fontsize=8)
                else:
                    axis.bar(position, value, width=0.28, color=color,
                             alpha=1 - 0.5 * offset, edgecolor=color, hatch="//" if offset else None)
        scale = {"seconds": 1, "thousands": 1_000, "millions": 1_000_000}[units]
        axis.yaxis.set_major_formatter(FuncFormatter(lambda value, _, factor=scale: f"{value / factor:g}"))
        axis.set_title(title, loc="left", fontweight="bold")
        axis.set_ylabel(units)
        axis.set_xticks(range(len(LABELS)), LABELS, rotation=18, ha="right")
        axis.set_ylim(bottom=0)
        axis.spines[["top", "right"]].set_visible(False)
        axis.grid(axis="y", alpha=0.2)
        axis.set_axisbelow(True)
    caption = "Solid bars: qutebrowser."
    if len(tasks) == 2:
        caption += " Hatched bars: Open Library."
    fig.suptitle(f"GLM-5.3 Flash pilot · {caption}", x=0.03, ha="left", fontsize=12)
    fig.savefig(args.output, metadata={"Date": None})


if __name__ == "__main__":
    main()
