#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
CONDA_SH="$HOME/miniconda3/etc/profile.d/conda.sh"

if [[ ! -f "$CONDA_SH" ]]; then
  echo "Cannot find Conda initialization file: $CONDA_SH" >&2
  exit 1
fi

source "$CONDA_SH"

if conda env list | awk '{print $1}' | grep -qx beetle_pipeline; then
  conda env update \
    --name beetle_pipeline \
    --file "$ROOT/environment-firebird.yml"
else
  conda env create --file "$ROOT/environment-firebird.yml"
fi

python -m py_compile "$ROOT/analysis/analyze_idtracker_unified.py" "$ROOT/analysis/run_idtracker_unified_batch.py"

echo
echo "Firebird installation complete."
