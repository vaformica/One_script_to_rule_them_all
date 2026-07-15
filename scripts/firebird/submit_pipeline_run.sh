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

ID_JOB="$(
  sbatch --parsable \
    --export=ALL \
    "$ID_SCRIPT"
)"
PP_JOB="$(
  sbatch --parsable \
    --dependency="afterok:${ID_JOB}" \
    --export=ALL \
    "$PP_SCRIPT"
)"
COLLECT_JOB="$(
  sbatch --parsable \
    --dependency="afterok:${PP_JOB}" \
    --export=ALL \
    "$COLLECT_SCRIPT"
)"

printf 'IDTRACKER_JOB=%s\nPOSTPROCESS_JOB=%s\nCOLLECTOR_JOB=%s\n' \
  "$ID_JOB" "$PP_JOB" "$COLLECT_JOB"
