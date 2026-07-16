#!/usr/bin/env bash
set -euo pipefail

ROOT="${1:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
cd "$ROOT"

echo "Applying Beetle IDtracker Pipeline v0.8.1 hotfix in: $ROOT"

chmod +x \
  "$ROOT/slurm/idtracker_one_cell.slurm" \
  "$ROOT/slurm/postprocess_one_cell.slurm" \
  "$ROOT/slurm/collect_one_cell.slurm" \
  "$ROOT/scripts/firebird/submit_pipeline_run.sh"

python -m py_compile \
  "$ROOT/pipeline/session_locator.py" \
  "$ROOT/processors/dispatch.py" \
  "$ROOT/collector/collect_outputs.py"

bash -n "$ROOT/slurm/idtracker_one_cell.slurm"
bash -n "$ROOT/slurm/postprocess_one_cell.slurm"
bash -n "$ROOT/slurm/collect_one_cell.slurm"
bash -n "$ROOT/scripts/firebird/submit_pipeline_run.sh"

grep -F 'POSTPROCESS_CONDA_ENV:-beetle_pipeline' "$ROOT/slurm/postprocess_one_cell.slurm" >/dev/null
grep -F 'POSTPROCESS_CONDA_ENV:-beetle_pipeline' "$ROOT/slurm/collect_one_cell.slurm" >/dev/null
grep -F 'afterany:${PP_JOB}' "$ROOT/scripts/firebird/submit_pipeline_run.sh" >/dev/null

printf '\nHotfix installed successfully.\n'
printf 'Version: '; cat "$ROOT/VERSION"
printf '\nCompleted IDtracker sessions can now be resubmitted with Postprocess Existing Session.\n'
