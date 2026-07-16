#!/usr/bin/env bash
#SBATCH --job-name=idtrack_cell
#SBATCH --partition=gpu
#SBATCH --time=04:00:00
#SBATCH --cpus-per-task=4
#SBATCH --mem=32G
#SBATCH --gres=gpu:1

set -uo pipefail

: "${PIPELINE_REPO_ROOT:?}"
: "${PIPELINE_TOML:?}"
: "${PIPELINE_RUN_DIR:?}"

mkdir -p "$PIPELINE_RUN_DIR/logs" "$PIPELINE_RUN_DIR/status"
printf 'Running IDtracker\n' > "$PIPELINE_RUN_DIR/status/stage.txt"

source "$HOME/miniconda3/etc/profile.d/conda.sh"
conda activate "${IDTRACKER_CONDA_ENV:-idtrackerai}"

# Some idtracker.ai releases can return a nonzero code after having written a
# complete, valid session. Do not decide success from the process code alone.
idtrackerai --track --load "$PIPELINE_TOML"
IDTRACKER_RC=$?
printf '%s\n' "$IDTRACKER_RC" > "$PIPELINE_RUN_DIR/status/idtracker_exit_code.txt"

printf 'Locating Session\n' > "$PIPELINE_RUN_DIR/status/stage.txt"
if SESSION_PATH="$(python "$PIPELINE_REPO_ROOT/pipeline/session_locator.py" \
    --toml "$PIPELINE_TOML" --run-dir "$PIPELINE_RUN_DIR" 2>"$PIPELINE_RUN_DIR/logs/session_locator.err")"; then
    printf '%s\n' "$SESSION_PATH" > "$PIPELINE_RUN_DIR/session_link.txt"
    printf '%s\n' "$SESSION_PATH" > "$PIPELINE_RUN_DIR/session_path.txt"
else
    printf 'FAIL\n' > "$PIPELINE_RUN_DIR/status/tracking.txt"
    printf 'Tracking Failed\n' > "$PIPELINE_RUN_DIR/status/stage.txt"
    if [[ "$IDTRACKER_RC" -ne 0 ]]; then
        exit "$IDTRACKER_RC"
    fi
    exit 1
fi

# A complete session is authoritative. Preserve the unusual return code as a
# warning for diagnostics, but allow postprocessing to run.
if [[ "$IDTRACKER_RC" -ne 0 ]]; then
    printf 'WARNING: idtrackerai exited %s, but a complete session was validated at %s\n' \
        "$IDTRACKER_RC" "$SESSION_PATH" \
        > "$PIPELINE_RUN_DIR/logs/idtracker_exit_warning.txt"
fi

# The canonical IDtracker session remains where IDtracker created it.
# Copying or moving sessions is intentionally deferred to a separate maintenance
# operation so filesystem work can never invalidate tracking or block analysis.
printf 'DEFERRED\n' > "$PIPELINE_RUN_DIR/status/archive.txt"

printf 'Tracking Complete\n' > "$PIPELINE_RUN_DIR/status/stage.txt"
printf 'PASS\n' > "$PIPELINE_RUN_DIR/status/tracking.txt"
exit 0
