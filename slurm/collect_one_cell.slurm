#!/usr/bin/env bash
#SBATCH --job-name=beetle_collect
#SBATCH --partition=compute
#SBATCH --time=00:30:00
#SBATCH --cpus-per-task=1
#SBATCH --mem=4G

set -euo pipefail

: "${PIPELINE_REPO_ROOT:?}"
: "${PIPELINE_RUN_DIR:?}"
: "${PIPELINE_PROJECT_ROOT:?}"
: "${PIPELINE_METADATA_JSON:?}"

source "$HOME/miniconda3/etc/profile.d/conda.sh"
conda activate "${POSTPROCESS_CONDA_ENV:-beetle_pipeline}"
export PYTHONPATH="$PIPELINE_REPO_ROOT${PYTHONPATH:+:$PYTHONPATH}"
cd "$PIPELINE_REPO_ROOT"

python "$PIPELINE_REPO_ROOT/collector/collect_outputs.py" \
  --run-dir "$PIPELINE_RUN_DIR" \
  --project-root "$PIPELINE_PROJECT_ROOT" \
  --run-metadata-json "$PIPELINE_METADATA_JSON"
