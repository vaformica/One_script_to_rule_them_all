#!/usr/bin/env bash
#SBATCH --job-name=idtrack_cell
#SBATCH --partition=gpu
#SBATCH --time=04:00:00
#SBATCH --cpus-per-task=4
#SBATCH --mem=32G
#SBATCH --gres=gpu:1
#SBATCH --output=%x_%j.out
#SBATCH --error=%x_%j.err

set -euo pipefail

: "${PIPELINE_TOML:?PIPELINE_TOML is required}"
: "${PIPELINE_RUN_DIR:?PIPELINE_RUN_DIR is required}"
: "${PIPELINE_SESSION_OUTPUT_DIR:?PIPELINE_SESSION_OUTPUT_DIR is required}"

source "$HOME/miniconda3/etc/profile.d/conda.sh"
conda activate "${IDTRACKER_CONDA_ENV:-idtrackerai}"

mkdir -p "$PIPELINE_RUN_DIR/logs" "$PIPELINE_SESSION_OUTPUT_DIR"
cd "$PIPELINE_SESSION_OUTPUT_DIR"

# IDtracker.ai 6.x invocation. This deliberately omits the obsolete
# 'terminal_mode' positional argument.
idtrackerai --load "$PIPELINE_TOML"

# Find the newest session directory produced by this job.
SESSION_PATH="$(
  find "$PIPELINE_SESSION_OUTPUT_DIR" -maxdepth 2 -type d -name 'session_*' \
    -printf '%T@\t%p\n' | sort -nr | head -1 | cut -f2-
)"
if [[ -z "$SESSION_PATH" ]]; then
  echo "No session_* directory was created." >&2
  exit 3
fi

printf '%s\n' "$SESSION_PATH" > "$PIPELINE_RUN_DIR/session_path.txt"
