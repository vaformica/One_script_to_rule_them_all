#!/usr/bin/env bash
#SBATCH --job-name=beetle_post
#SBATCH --partition=compute
#SBATCH --time=02:00:00
#SBATCH --cpus-per-task=4
#SBATCH --mem=16G
set -euo pipefail
: "${PIPELINE_REPO_ROOT:?}"; : "${PIPELINE_RUN_DIR:?}"; : "${PIPELINE_ANALYSIS_TYPE:?}"; : "${PIPELINE_METADATA_JSON:?}"; : "${PIPELINE_TOML:?}"
mkdir -p "$PIPELINE_RUN_DIR/status"; printf 'Running Postprocess\n' > "$PIPELINE_RUN_DIR/status/stage.txt"
source "$HOME/miniconda3/etc/profile.d/conda.sh"; conda activate "${POSTPROCESS_CONDA_ENV:-beetle_pipeline}"
SESSION_PATH="${PIPELINE_SESSION:-}"
if [[ -z "$SESSION_PATH" ]]; then SESSION_PATH="$(python "$PIPELINE_REPO_ROOT/pipeline/session_locator.py" --toml "$PIPELINE_TOML" --run-dir "$PIPELINE_RUN_DIR")"; fi
printf '%s\n' "$SESSION_PATH" > "$PIPELINE_RUN_DIR/session_link.txt"
OUTPUT_ROOT="$PIPELINE_RUN_DIR/outputs"; mkdir -p "$OUTPUT_ROOT"
read -r -a EXTRA <<< "${PIPELINE_POSTPROCESS_EXTRA_ARGS:-}"
python "$PIPELINE_REPO_ROOT/processors/dispatch.py" --analysis-type "$PIPELINE_ANALYSIS_TYPE" --session-path "$SESSION_PATH" --output-root "$OUTPUT_ROOT" --run-metadata-json "$PIPELINE_METADATA_JSON" "${EXTRA[@]}"
printf 'PASS\n' > "$PIPELINE_RUN_DIR/status/postprocess.txt"; printf 'Postprocessing Complete\n' > "$PIPELINE_RUN_DIR/status/stage.txt"
