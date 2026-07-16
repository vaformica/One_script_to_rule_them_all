#!/usr/bin/env bash
set -euo pipefail

: "${PIPELINE_REPO_ROOT:?}"
: "${PIPELINE_PROJECT_ROOT:?}"
: "${PIPELINE_RUN_DIR:?}"
: "${PIPELINE_TOML:?}"
: "${PIPELINE_METADATA_JSON:?}"
: "${PIPELINE_ANALYSIS_TYPE:?}"

ID_SCRIPT="$PIPELINE_REPO_ROOT/slurm/idtracker_one_cell.slurm"
PP_SCRIPT="$PIPELINE_REPO_ROOT/slurm/postprocess_one_cell.slurm"
COLLECT_SCRIPT="$PIPELINE_REPO_ROOT/slurm/collect_one_cell.slurm"
LOG_DIR="$PIPELINE_RUN_DIR/logs"

mkdir -p "$LOG_DIR"


VALIDATOR="$PIPELINE_REPO_ROOT/scripts/firebird/validate_run_toml.py"
VALIDATOR_PYTHON="${IDTRACKER_PYTHON:-$HOME/miniconda3/envs/${IDTRACKER_CONDA_ENV:-idtrackerai}/bin/python}"

if [[ ! -x "$VALIDATOR_PYTHON" ]]; then
  echo "IDtracker Python not found: $VALIDATOR_PYTHON" >&2
  exit 20
fi
if [[ ! -f "$VALIDATOR" ]]; then
  echo "TOML validator not found: $VALIDATOR" >&2
  exit 20
fi

"$VALIDATOR_PYTHON" "$VALIDATOR" "$PIPELINE_TOML" || {
  echo "TOML preflight failed. No SLURM jobs were submitted." >&2
  exit 21
}

ID_JOB="$(
  sbatch --parsable \
    --output="$LOG_DIR/idtracker_%j.out" \
    --error="$LOG_DIR/idtracker_%j.err" \
    --export=ALL \
    "$ID_SCRIPT"
)"

PP_JOB="$(
  sbatch --parsable \
    --dependency="afterok:${ID_JOB}" \
    --output="$LOG_DIR/postprocess_%j.out" \
    --error="$LOG_DIR/postprocess_%j.err" \
    --export=ALL \
    "$PP_SCRIPT"
)"

COLLECT_JOB="$(
  sbatch --parsable \
    --dependency="afterok:${PP_JOB}" \
    --output="$LOG_DIR/collector_%j.out" \
    --error="$LOG_DIR/collector_%j.err" \
    --export=ALL \
    "$COLLECT_SCRIPT"
)"

cat > "$PIPELINE_RUN_DIR/job_ids.env" <<EOF
IDTRACKER_JOB=$ID_JOB
POSTPROCESS_JOB=$PP_JOB
COLLECTOR_JOB=$COLLECT_JOB
EOF

printf 'IDTRACKER_JOB=%s\nPOSTPROCESS_JOB=%s\nCOLLECTOR_JOB=%s\n' \
  "$ID_JOB" "$PP_JOB" "$COLLECT_JOB"
