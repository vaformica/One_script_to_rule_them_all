from __future__ import annotations

import os
import shlex
from pathlib import Path

from .config import AppConfig
from .database import Database
from .ssh import SSHClient


def retrieve_quick_results(
    config: AppConfig,
    db: Database,
    ssh: SSHClient,
    run_id: str,
) -> dict[str, int]:
    runs = {r["run_id"]: r for r in db.list_runs()}
    run = runs[run_id]
    remote_run = run["remote_run_dir"]
    local_root = config.local_project_root / "retrieved_results" / run_id
    tracks_dir = local_root / "tracks"
    summaries_dir = local_root / "individual_summaries"
    tracks_dir.mkdir(parents=True, exist_ok=True)
    summaries_dir.mkdir(parents=True, exist_ok=True)

    script = f"""
set -e
REMOTE_RUN={shlex.quote(remote_run)}
find "$REMOTE_RUN" -type f \\( -iname '*track*.png' -o -iname '*trajectory*.png' \\) -print0 |
while IFS= read -r -d '' f; do
  rel="${{f#$REMOTE_RUN/}}"
  safe=$(printf '%s' "$rel" | tr '/' '__')
  printf '%s\\t%s\\n' "$f" "$safe"
done
"""
    result = ssh.run_bash(script, timeout=300).check()
    track_count = 0
    for line in result.stdout.splitlines():
        if not line.strip():
            continue
        remote_path, safe_name = line.split("\t", 1)
        local_path = tracks_dir / safe_name
        ssh.copy_from_remote(remote_path, local_path).check()
        track_count += 1

    script2 = f"""
set -e
REMOTE_RUN={shlex.quote(remote_run)}
find "$REMOTE_RUN" -type f \\( -iname '*individual_summary*.csv' -o -iname '*summary*.csv' \\) -print0 |
while IFS= read -r -d '' f; do
  rel="${{f#$REMOTE_RUN/}}"
  safe=$(printf '%s' "$rel" | tr '/' '__')
  printf '%s\\t%s\\n' "$f" "$safe"
done
"""
    result2 = ssh.run_bash(script2, timeout=300).check()
    summary_count = 0
    for line in result2.stdout.splitlines():
        if not line.strip():
            continue
        remote_path, safe_name = line.split("\t", 1)
        local_path = summaries_dir / safe_name
        ssh.copy_from_remote(remote_path, local_path).check()
        summary_count += 1

    return {"tracks": track_count, "summaries": summary_count}
