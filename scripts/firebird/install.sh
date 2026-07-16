#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
CONDA_SH="$HOME/miniconda3/etc/profile.d/conda.sh"
PIPELINE_ENV="${PIPELINE_CONDA_ENV:-beetle_pipeline}"
UPDATE_ENV=false

usage() {
  cat <<'EOF'
Usage:
  bash scripts/firebird/install.sh
      Quick install. Reuses existing Conda environments and validates scripts.

  bash scripts/firebird/install.sh --update-env
      Explicitly solve and update the beetle_pipeline Conda environment.

  bash scripts/firebird/install.sh --help
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --update-env)
      UPDATE_ENV=true
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if [[ ! -f "$CONDA_SH" ]]; then
  echo "Cannot find Conda initialization file: $CONDA_SH" >&2
  exit 1
fi

source "$CONDA_SH"

env_exists() {
  conda env list | awk 'NF && $1 !~ /^#/ {print $1}' | grep -Fxq "$1"
}

if env_exists "$PIPELINE_ENV"; then
  if [[ "$UPDATE_ENV" == true ]]; then
    echo "Updating Conda environment '$PIPELINE_ENV'..."
    conda env update \
      --name "$PIPELINE_ENV" \
      --file "$ROOT/environment-firebird.yml"
  else
    echo "Conda environment '$PIPELINE_ENV' already exists."
    echo "Skipping Conda dependency solving."
  fi
else
  echo "Conda environment '$PIPELINE_ENV' does not exist."
  echo "Creating it from environment-firebird.yml..."
  conda env create \
    --name "$PIPELINE_ENV" \
    --file "$ROOT/environment-firebird.yml"
fi

# The tracking job uses the established idtrackerai environment. Verify it
# exists, but do not alter it during a routine pipeline-code update.
IDTRACKER_ENV="${IDTRACKER_CONDA_ENV:-idtrackerai}"
if ! env_exists "$IDTRACKER_ENV"; then
  echo "Required IDtracker environment '$IDTRACKER_ENV' was not found." >&2
  echo "Install IDtracker.ai separately before submitting tracking jobs." >&2
  exit 3
fi

echo "Validating shell scripts..."
bash -n \
  "$ROOT/scripts/firebird/install.sh" \
  "$ROOT/scripts/firebird/submit_pipeline_run.sh" \
  "$ROOT/scripts/firebird/diagnose_pipeline_run.sh" \
  "$ROOT/slurm/idtracker_one_cell.slurm" \
  "$ROOT/slurm/postprocess_one_cell.slurm" \
  "$ROOT/slurm/collect_one_cell.slurm"

echo "Validating Python scripts..."
PIPELINE_PYTHON="$HOME/miniconda3/envs/$PIPELINE_ENV/bin/python"
IDTRACKER_PYTHON="$HOME/miniconda3/envs/$IDTRACKER_ENV/bin/python"

"$PIPELINE_PYTHON" -m py_compile \
  "$ROOT/analysis/analyze_idtracker_unified.py" \
  "$ROOT/analysis/run_idtracker_unified_batch.py" \
  "$ROOT/collector/collect_outputs.py" \
  "$ROOT/collector/metadata_injector.py"

"$IDTRACKER_PYTHON" -m py_compile \
  "$ROOT/scripts/firebird/validate_run_toml.py"

echo "Testing repository package imports from an external working directory..."
(
  cd "$HOME"
  PYTHONPATH="$ROOT" "$PIPELINE_PYTHON" -c \
    "import pipeline, collector, processors; from pipeline.run_metadata import RunMetadata; from collector.metadata_injector import enrich_tree; print('Repository imports OK')"
  PYTHONPATH="$ROOT" "$PIPELINE_PYTHON" "$ROOT/processors/run_unified_pipeline.py" --help >/dev/null
  PYTHONPATH="$ROOT" "$PIPELINE_PYTHON" "$ROOT/collector/collect_outputs.py" --help >/dev/null
)

chmod +x \
  "$ROOT/scripts/firebird/"*.sh \
  "$ROOT/scripts/firebird/validate_run_toml.py" \
  "$ROOT/slurm/"*.slurm

echo
echo "Quick Firebird installation complete."
echo "Conda environments were reused."
echo
echo "IDtracker command installed:"
grep -F 'idtrackerai --track --load' "$ROOT/slurm/idtracker_one_cell.slurm"
echo
echo "Only use the slower dependency update when environment-firebird.yml changes:"
echo "  bash scripts/firebird/install.sh --update-env"
