#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

echo "Performing code-only Firebird installation..."
echo "No Conda environment will be created, solved, or updated."

bash -n \
  "$ROOT/scripts/firebird/submit_pipeline_run.sh" \
  "$ROOT/slurm/idtracker_one_cell.slurm" \
  "$ROOT/slurm/postprocess_one_cell.slurm" \
  "$ROOT/slurm/collect_one_cell.slurm"

IDTRACKER_ENV="${IDTRACKER_CONDA_ENV:-idtrackerai}"
IDTRACKER_PYTHON="$HOME/miniconda3/envs/$IDTRACKER_ENV/bin/python"

if [[ ! -x "$IDTRACKER_PYTHON" ]]; then
  echo "IDtracker Python not found: $IDTRACKER_PYTHON" >&2
  exit 1
fi

"$IDTRACKER_PYTHON" -m py_compile \
  "$ROOT/scripts/firebird/validate_run_toml.py"

chmod +x \
  "$ROOT/scripts/firebird/"*.sh \
  "$ROOT/scripts/firebird/validate_run_toml.py" \
  "$ROOT/slurm/"*.slurm

if ! grep -Fq 'idtrackerai --track --load "$PIPELINE_TOML"' \
  "$ROOT/slurm/idtracker_one_cell.slurm"; then
  echo "Headless IDtracker command is missing from the SLURM script." >&2
  exit 2
fi

echo "Code-only installation complete."
echo "Verified: idtrackerai --track --load TOML"
