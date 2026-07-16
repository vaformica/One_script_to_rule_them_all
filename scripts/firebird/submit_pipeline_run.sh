#!/usr/bin/env bash
set -euo pipefail
: "${PIPELINE_REPO_ROOT:?}"; : "${PIPELINE_PROJECT_ROOT:?}"; : "${PIPELINE_RUN_DIR:?}"; : "${PIPELINE_TOML:?}"; : "${PIPELINE_METADATA_JSON:?}"; : "${PIPELINE_ANALYSIS_TYPE:?}"
MODE="${PIPELINE_RUN_MODE:-full}"; LOG_DIR="$PIPELINE_RUN_DIR/logs"; mkdir -p "$LOG_DIR" "$PIPELINE_RUN_DIR/status"
VALIDATOR_PYTHON="${IDTRACKER_PYTHON:-$HOME/miniconda3/envs/${IDTRACKER_CONDA_ENV:-idtrackerai}/bin/python}"
"$VALIDATOR_PYTHON" "$PIPELINE_REPO_ROOT/scripts/firebird/validate_run_toml.py" "$PIPELINE_TOML" || exit 21
ID_JOB=""
if [[ "$MODE" == "full" ]]; then
 ID_JOB="$(sbatch --parsable --output="$LOG_DIR/idtracker_%j.out" --error="$LOG_DIR/idtracker_%j.err" --export=ALL "$PIPELINE_REPO_ROOT/slurm/idtracker_one_cell.slurm")"
 PP_DEP="--dependency=afterok:${ID_JOB}"
else
 LOCATE=(python "$PIPELINE_REPO_ROOT/pipeline/session_locator.py" --toml "$PIPELINE_TOML" --run-dir "$PIPELINE_RUN_DIR")
 if [[ -n "${PIPELINE_SESSION:-}" ]]; then LOCATE+=(--session "$PIPELINE_SESSION"); fi
 "${LOCATE[@]}" > "$PIPELINE_RUN_DIR/session_link.txt"
 PP_DEP=""
 printf 'SKIPPED\n' > "$PIPELINE_RUN_DIR/status/tracking.txt"
fi
PP_JOB="$(sbatch --parsable $PP_DEP --output="$LOG_DIR/postprocess_%j.out" --error="$LOG_DIR/postprocess_%j.err" --export=ALL "$PIPELINE_REPO_ROOT/slurm/postprocess_one_cell.slurm")"
COLLECT_JOB="$(sbatch --parsable --dependency="afterany:${PP_JOB}" --output="$LOG_DIR/collector_%j.out" --error="$LOG_DIR/collector_%j.err" --export=ALL "$PIPELINE_REPO_ROOT/slurm/collect_one_cell.slurm")"
printf 'IDTRACKER_JOB=%s\nPOSTPROCESS_JOB=%s\nCOLLECTOR_JOB=%s\n' "$ID_JOB" "$PP_JOB" "$COLLECT_JOB" | tee "$PIPELINE_RUN_DIR/job_ids.env"
