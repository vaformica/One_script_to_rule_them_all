#!/usr/bin/env python3
from __future__ import annotations
import argparse, json, os, subprocess
from pathlib import Path


def read_first(path: Path, default=''):
    try:
        return path.read_text(encoding='utf-8').strip().splitlines()[0]
    except Exception:
        return default


def parse_env(path: Path):
    out = {}
    try:
        for line in path.read_text(encoding='utf-8').splitlines():
            if '=' in line:
                k, v = line.split('=', 1); out[k] = v
    except Exception:
        pass
    return out


def maybe_recollect(run_dir: Path, project_root: Path, repo_root: Path, meta_path: Path):
    if not (run_dir/'status/postprocess.txt').exists() or (run_dir/'status/collector.txt').exists():
        return ''
    env = os.environ.copy()
    env.update({
        'PIPELINE_REPO_ROOT': str(repo_root),
        'PIPELINE_PROJECT_ROOT': str(project_root),
        'PIPELINE_RUN_DIR': str(run_dir),
        'PIPELINE_METADATA_JSON': str(meta_path),
    })
    log_dir = run_dir/'logs'; log_dir.mkdir(parents=True, exist_ok=True)
    result = subprocess.run([
        'sbatch', '--parsable',
        f'--output={log_dir}/collector_recovery_%j.out',
        f'--error={log_dir}/collector_recovery_%j.err',
        '--export=ALL', str(repo_root/'slurm/collect_one_cell.slurm')
    ], env=env, text=True, capture_output=True)
    if result.returncode == 0:
        return result.stdout.strip()
    return ''


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--project-root', required=True)
    ap.add_argument('--repo-root', required=True)
    ap.add_argument('--repair', action='store_true')
    args=ap.parse_args()
    project=Path(args.project_root); repo=Path(args.repo_root)
    records=[]
    for meta_path in sorted((project/'runs').rglob('run_metadata.json')):
        try: meta=json.loads(meta_path.read_text(encoding='utf-8'))
        except Exception: continue
        run_dir=Path(meta.get('remote_run_dir') or meta_path.parent)
        ids=parse_env(run_dir/'job_ids.env')
        recovery_job=''
        if args.repair:
            recovery_job=maybe_recollect(run_dir, project, repo, meta_path)
            if recovery_job:
                ids['COLLECTOR_JOB']=recovery_job
        stage=read_first(run_dir/'status/stage.txt') or read_first(run_dir/'status/postprocess.txt') or read_first(run_dir/'status/tracking.txt') or 'Submitted'
        records.append({
            'attempt_index': meta.get('attempt_index', meta.get('run_index', 1)),
            'run_index': meta.get('attempt_index', meta.get('run_index', 1)),
            'timestamp': meta.get('run_timestamp',''),
            'label': f"{meta.get('video_filename','')} / {meta.get('cell_label','')}",
            'idtracker_job': ids.get('IDTRACKER_JOB',''),
            'postprocess_job': ids.get('POSTPROCESS_JOB',''),
            'collector_job': ids.get('COLLECTOR_JOB',''),
            'status': 'Recovery collector submitted' if recovery_job else stage,
            'run_dir': str(run_dir),
        })
    print(json.dumps(records))

if __name__ == '__main__': main()
