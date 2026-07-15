#!/usr/bin/env bash
#SBATCH --job-name=beetle_post
#SBATCH --partition=compute
#SBATCH --time=02:00:00
#SBATCH --cpus-per-task=4
#SBATCH --mem=16G
#SBATCH --output=%x_%j.out
#SBATCH --error=%x_%j.err

set -euo pipefail

: "${PIPELINE_REPO_ROOT:?}"
: "${PIPELINE_RUN_DIR:?}"
: "${PIPELINE_ANALYSIS_TYPE:?}"
: "${PIPELINE_METADATA_JSON:?}"

source "$HOME/miniconda3/etc/profile.d/conda.sh"
conda activate "${POSTPROCESS_CONDA_ENV:-idtrackerai}"

SESSION_PATH="${PIPELINE_SESSION:-}"
if [[ -z "$SESSION_PATH" && -f "$PIPELINE_RUN_DIR/session_path.txt" ]]; then
  SESSION_PATH="$(cat "$PIPELINE_RUN_DIR/session_path.txt")"
fi
if [[ -z "$SESSION_PATH" ]]; then
  echo "Could not determine session path." >&2
  exit 4
fi

OUTPUT_ROOT="$PIPELINE_RUN_DIR/postprocessing"
mkdir -p "$OUTPUT_ROOT"

python "$PIPELINE_REPO_ROOT/processors/dispatch.py" \
  --analysis-type "$PIPELINE_ANALYSIS_TYPE" \
  --session-path "$SESSION_PATH" \
  --output-root "$OUTPUT_ROOT" \
  --run-metadata-json "$PIPELINE_METADATA_JSON" \
  ${PIPELINE_POSTPROCESS_EXTRA_ARGS:-}
