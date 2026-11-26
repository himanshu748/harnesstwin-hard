# HarnessTwin Hard

A paired comparison of Pi, OpenCode and mini-SWE-agent on the official SWE-bench Pro V2 HARD-51 split. Each harness gets the same task, GMI Cloud model, model-call limits and execution resources. This repository contains the runner and audit trail. The initial publication has no measured harness scores.

## Run

Use the **HARD-51 benchmark** workflow on the default GitHub branch. The GitHub-hosted standard Linux runner hosts Docker. Use `smoke` to check whether the three clients can reach the offline relay. Use `controls` to verify that an empty patch fails and that the official reference patch passes. Use `inventory` to list the exact serving IDs from the authorized GMI account. Configure the repository's Actions secret `GMI_API_KEY` before invoking any mode that uses the model. `GMI_ORGANIZATION_ID` is an optional Actions variable for multi-organization accounts.

Use `triad` to run the first task through all three harnesses, and `second` for the other task. `pilot` runs both tasks in one job. Choose either the single pilot job or the two smaller jobs to avoid duplicate charges. Download the evidence bundle from Actions before the 14-day retention period ends.

From an amd64 Linux Docker host with Python 3.11 or newer:

```sh
git init official
git -C official remote add origin https://github.com/scaleapi/SWE-bench_Pro-os.git
git -C official fetch --depth=1 origin 66f92766bba642462d4bbe5479e83f91f9211862
python3 ci/run.py plan --dataset official --out plan
python3 ci/run.py controls --dataset official --out controls
```

For live runs, inject the key from your secret manager into the host process and select a live mode. The host relay sends the authenticated requests. The agent container has no network interfaces except loopback and receives a placeholder API key. The local bridge exposes only the fixed GMI Chat Completions route. Each candidate patch is replayed in a fresh official task image with an unchanged verifier. Only official parsed verdicts count as measured outcomes. Invalid patches and missing verifier evidence are retained as separate outcomes.

## Main benchmark

The `hard` workflow mode selects the authenticated `zai-org/GLM-5.3` ID. It checks the model's live list rates before sending inference, rotates harness order by task and repeat, and stops before beginning a matched task when the remaining budget cannot cover all three attempts. Set the task range, repeat number, and remaining USD envelope in the workflow form. Allow for previous jobs when setting the remaining budget. Hard-mode and Flash-pilot observations are separate model strata.

Use `controls-second` to replay the clean and reference patches for the second pilot task. Use `resume-pilot` only after the original first task and only for the recorded Mini-SWE-agent failure followed by the second task. To reproduce the combined pilot table after downloading both workflow artifacts:

```sh
python3 ci/collect_pilot.py FIRST_RUN=first.zip RESUME_RUN=resume.zip --out pilot-results
```

## Design

- The dataset commit and HARD-51 SHA-256 are in `ci/settings.json`. The task IDs were fixed before inference. The pilot samples Qt key handling and Open Library search indexing. The ten-task extension is a convenience sample of these two repositories and cannot estimate the full HARD-51 distribution.
- Official base OCI image digests and Git HEADs are recorded for each run. Node dependencies use `ci/package-lock.json`. The Python dependency list and toolbox base image digests are pinned. The first official task image was pinned to the digest observed in the verifier controls. The official image digests for the rest of the selected tasks will be recorded as they run.
- The pilot uses a 19-minute agent budget. The official locked protocol uses 50 minutes, so scores from this pilot would describe a short-budget harness comparison.
- The relay uses the same output cap and default provider reasoning setting across clients. Differences in history serialization, system prompts and tool schemas are part of the treatment. The pilot's 40-call ceiling and $0.90 conservative inference reserve are per attempt. The six-attempt model reserve is $5.40 at the recorded upper rates. Reconcile GMI billing before launching more jobs.
- JSON and CSV preserve nulls for missing measurements. Estimated costs use uncached list pricing. The model-call ledger records provider-reported usage when returned. Agent logs and the mini-SWE-agent trajectory support tool failure inspection.
- Use `python compare.py verified.csv` only after assembling a balanced matrix containing explicit verifier 0/1 outcomes. Inspect missing or infrastructure-failed trials before interpreting any difference.

`bench.py` and `hard_mode.py` are the original local fixture and Harbor planning tools. Their dry-run output carries no HARD-51 outcome. The Docker workflow above is the maintained live runner.

[Official SWE-bench Pro V2 protocol](https://github.com/scaleapi/SWE-bench_Pro-os/tree/main/v2)

[Technical report](REPORT.md) · [Launch post draft](LAUNCH_X.md)
