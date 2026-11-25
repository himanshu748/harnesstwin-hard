#!/usr/bin/env python3
"""HarnessTwin smoke benchmark: same provider/model/tasks in three agent harnesses.

Uses the local installed CLIs. Does not submit any real model calls in --dry-run.
No sandbox is provided: for untrusted repositories, run each attempt in a container.
"""
from __future__ import annotations
import argparse
import datetime as dt
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parent
BASE = "https://api.gmi-serving.com/v1"
HARNESSES = ("pi", "opencode", "mini-swe")


def safe_id(value: str) -> str:
    return ''.join(x if x.isalnum() or x in '-_.' else '_' for x in value)[:100]


def task_names() -> list[str]:
    return sorted(p.name for p in (ROOT / "tasks").iterdir() if (p / "task.json").exists())


def read_task(task_id: str) -> dict:
    task_dir = ROOT / "tasks" / task_id
    if task_id not in task_names():
        raise ValueError(f"Unknown task: {task_id}")
    manifest = json.loads((task_dir / "task.json").read_text())
    manifest['task_id'] = task_id
    return manifest


def open_code_config(model_id: str) -> dict:
    return {
        '$schema': 'https://opencode.ai/config.json',
        'model': 'gmi-bench/selected',
        'providers': {
            'gmi-bench': {
                'name': 'GMI Cloud benchmark',
                'env': ['GMI_API_KEY'],
                'package': '@opencode/ai/providers/openai-compatible',
                'settings': {'baseURL': BASE},
                'models': {'selected': {
                    'name': f'GMI {model_id}', 'modelID': model_id,
                    'capabilities': {'tools': True, 'input': ['text'], 'output': ['text']},
                    # Context and output limits must be verified for each real model.
                }},
            },
        },
    }


def pi_config(model_id: str) -> dict:
    return {'providers': {'gmi-bench': {
        'baseUrl': BASE,
        'api': 'openai-completions',
        'apiKey': 'GMI_API_KEY',
        'models': [{'id': model_id, 'name': f'GMI {model_id}'}],
    }}}


def build_command(harness: str, model_id: str, prompt: str, run_dir: Path, repo_dir: Path) -> tuple[list[str], dict]:
    env = os.environ.copy()
    env['GMI_API_KEY'] = os.environ.get('GMI_API_KEY', '')
    if harness == 'pi':
        pi_home = run_dir / '.pi'
        pi_home.mkdir(parents=True, exist_ok=True)
        (pi_home / 'models.json').write_text(json.dumps(pi_config(model_id), indent=2))
        env['PI_CODING_AGENT_DIR'] = str(pi_home)
        env['PI_TELEMETRY'] = '0'
        return ['pi', '--provider', 'gmi-bench', '--model', model_id, '--no-session', '-p', prompt], env
    if harness == 'opencode':
        (repo_dir / 'opencode.json').write_text(json.dumps(open_code_config(model_id), indent=2))
        return ['opencode', 'run', '--standalone', '--agent', 'build', '--auto', '--format', 'json', '--model', 'gmi-bench/selected', prompt], env
    if harness == 'mini-swe':
        env['OPENAI_API_KEY'] = os.environ.get('GMI_API_KEY', '')
        out = run_dir / 'mini-trajectory.json'
        return [
            'mini', '-y', '--exit-immediately',
            '-c', 'mini.yaml', '-c', f'model.model_kwargs.api_base={BASE}',
            '-c', 'model.model_kwargs.drop_params=true',
            '--model', 'openai/' + model_id,
            '--task', prompt, '--output', str(out),
        ], env
    raise ValueError(harness)


def run_one(harness: str, model_id: str, task_id: str, trial: int,
            results_root: Path, timeout: int, dry_run: bool) -> dict:
    task = read_task(task_id)
    run_dir = results_root / safe_id(model_id) / task_id / harness / f'trial-{trial}'
    repo = run_dir / 'repo'
    if run_dir.exists():
        raise FileExistsError(f'Refusing to overwrite {run_dir}')
    run_dir.mkdir(parents=True)
    shutil.copytree(ROOT / 'tasks' / task_id / 'seed', repo)
    prompt = task['prompt'].strip() + '\n\nPlease inspect the code, implement the fix, and run relevant tests if available.'
    cmd, env = build_command(harness, model_id, prompt, run_dir.resolve(), repo.resolve())
    info = {
        'timestamp_utc': dt.datetime.now(dt.timezone.utc).isoformat(),
        'model_id': model_id, 'provider': 'GMI Cloud', 'base_url': BASE,
        'task': task_id, 'trial': trial, 'harness': harness,
        'status': 'planned' if dry_run else 'started',
        'command': cmd, 'elapsed_seconds': None, 'harness_exit_code': None,
        'hidden_test_passed': None, 'input_tokens': None, 'output_tokens': None,
        'usd_cost': None, 'note': 'Token and cost usage are not inferred from CLI output in this starter.',
    }
    (run_dir / 'run.json').write_text(json.dumps(info, indent=2))
    if dry_run:
        return info
    if not os.environ.get('GMI_API_KEY'):
        raise RuntimeError('Set GMI_API_KEY in your environment (never commit the secret).')
    start = time.monotonic()
    try:
        with (run_dir / 'stdout.log').open('w') as stdout, (run_dir / 'stderr.log').open('w') as stderr:
            proc = subprocess.run(cmd, cwd=repo, env=env, stdout=stdout, stderr=stderr,
                                  timeout=timeout, stdin=subprocess.DEVNULL, check=False)
            info['harness_exit_code'] = proc.returncode
            info['status'] = 'completed'
    except subprocess.TimeoutExpired:
        info['status'] = 'timeout'
    except FileNotFoundError as exc:
        info['status'] = 'missing_cli'
        info['error'] = str(exc)
    finally:
        info['elapsed_seconds'] = round(time.monotonic() - start, 3)
    # The tests are separate from the repository seed; this is NOT a security sandbox.
    eval_file = ROOT / 'eval' / f'{task_id}.py'
    eval_env = os.environ.copy()
    eval_env['PYTHONPATH'] = str(repo.resolve())
    try:
        tested = subprocess.run([sys.executable, str(eval_file)], cwd=repo,
                               env=eval_env, capture_output=True, text=True, timeout=90)
        info['hidden_test_passed'] = tested.returncode == 0
        (run_dir / 'evaluation.log').write_text((tested.stdout + '\n' + tested.stderr)[-12000:])
    except Exception as exc:
        info['hidden_test_passed'] = False
        (run_dir / 'evaluation.log').write_text(str(exc))
    (run_dir / 'run.json').write_text(json.dumps(info, indent=2))
    return info


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--model', default='MiniMaxAI/MiniMax-M2.7', help='Exact model ID from your GMI /v1/models')
    ap.add_argument('--harnesses', nargs='+', choices=HARNESSES, default=list(HARNESSES))
    ap.add_argument('--tasks', nargs='+', default=task_names())
    ap.add_argument('--trials', type=int, default=1)
    ap.add_argument('--timeout', type=int, default=900)
    ap.add_argument('--results', type=Path, default=ROOT / 'results')
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()
    if args.trials < 1 or args.timeout < 1:
        ap.error('--trials and --timeout must be positive')
    if not args.dry_run and not os.environ.get('GMI_API_KEY'):
        ap.error('GMI_API_KEY is missing; set it or use --dry-run')
    run_tag = dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + uuid.uuid4().hex[:6]
    root = args.results / run_tag
    rows = []
    for task_id in args.tasks:
        for harness in args.harnesses:
            for trial in range(1, args.trials + 1):
                info = run_one(harness, args.model, task_id, trial, root, args.timeout, args.dry_run)
                rows.append(info)
                print(f"{task_id:12s} {harness:9s} trial={trial} status={info['status']} pass={info['hidden_test_passed']}")
    (root / 'summary.json').write_text(json.dumps(rows, indent=2))
    print(f'Results saved in {root}')
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
