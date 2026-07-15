#!/usr/bin/env bash
#SBATCH --job-name=beetle_collect
#SBATCH --partition=compute
#SBATCH --time=00:30:00
#SBATCH --cpus-per-task=1
#SBATCH --mem=4G
#SBATCH --output=%x_%j.out
#SBATCH --error=%x_%j.err

set -euo pipefail

: "${PIPELINE_REPO_ROOT:?}"
: "${PIPELINE_RUN_DIR:?}"
: "${PIPELINE_PROJECT_ROOT:?}"
: "${PIPELINE_METADATA_JSON:?}"

source "$HOME/miniconda3/etc/profile.d/conda.sh"
conda activate "${POSTPROCESS_CONDA_ENV:-idtrackerai}"

python "$PIPELINE_REPO_ROOT/collector/collect_outputs.py" \
  --run-dir "$PIPELINE_RUN_DIR" \
  --project-root "$PIPELINE_PROJECT_ROOT" \
  --run-metadata-json "$PIPELINE_METADATA_JSON"
