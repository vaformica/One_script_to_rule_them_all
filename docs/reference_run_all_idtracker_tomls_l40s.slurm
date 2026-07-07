#!/bin/bash
#SBATCH --job-name=idtracker_all_tomls
#SBATCH --partition=gpu
#SBATCH --gres=gpu:1
#SBATCH --nodes=1
#SBATCH --cpus-per-task=4
#SBATCH --mem=32G
#SBATCH --time=04:00:00
#SBATCH --array=1-200%20
#SBATCH --output=logs/idtracker_%A_%a.out
#SBATCH --error=logs/idtracker_%A_%a.err

set -euo pipefail

module purge

source /home/vformic1-swat/miniconda3/etc/profile.d/conda.sh
conda activate idtrackerai

# This makes the script reusable.
# It uses the directory where you run "sbatch" as the TOML/project directory.
PROJECT_DIR="$SLURM_SUBMIT_DIR"

# SLURM needs the logs folder to exist before the job starts.
# Still create it here as a backup.
mkdir -p "$PROJECT_DIR/logs"

# All completed IDTracker.ai session folders will be moved here.
FINAL_SESSION_ROOT="$PROJECT_DIR/idtracker_sessions"
mkdir -p "$FINAL_SESSION_ROOT"

echo "Host: $(hostname)"
echo "SLURM job ID: $SLURM_JOB_ID"
echo "SLURM array task ID: $SLURM_ARRAY_TASK_ID"
echo "Project directory:"
echo "$PROJECT_DIR"

echo "Using Python:"
which python

echo "Using IDTracker.ai:"
which idtrackerai

cd "$PROJECT_DIR"

mapfile -t TOMLS < <(find "$PROJECT_DIR" -maxdepth 1 -type f -name "*.toml" | sort)

NUM_TOMLS=${#TOMLS[@]}

echo "Found $NUM_TOMLS TOML files in:"
echo "$PROJECT_DIR"

if [ "$NUM_TOMLS" -eq 0 ]; then
    echo "ERROR: No TOML files found."
    exit 1
fi

if [ "$SLURM_ARRAY_TASK_ID" -gt "$NUM_TOMLS" ]; then
    echo "Array task $SLURM_ARRAY_TASK_ID is greater than number of TOML files found: $NUM_TOMLS"
    echo "Exiting cleanly."
    exit 0
fi

INDEX=$((SLURM_ARRAY_TASK_ID - 1))
TOML_PATH="${TOMLS[$INDEX]}"
TOML_NAME=$(basename "$TOML_PATH")
RUN_ID="${TOML_NAME%.toml}"

echo "Selected TOML:"
echo "$TOML_PATH"

echo "Run ID:"
echo "$RUN_ID"

echo "Checking TOML exists:"
ls -lh "$TOML_PATH"

echo "Starting IDTracker.ai for $RUN_ID"

set +e
idtrackerai --load "$TOML_PATH" --track
IDTRACKER_EXIT_CODE=$?
set -e

echo "IDTracker.ai exit code: $IDTRACKER_EXIT_CODE"

echo "Searching for trajectory output..."

# Search for this run's trajectory output.
# IDTracker.ai usually writes session folders near the video path, not necessarily in PROJECT_DIR.
TRAJECTORY_FILE=$(find \
    "$PROJECT_DIR" \
    /data/labs/vformic1-swat-lab/2026_Videos \
    -path "*${RUN_ID}*/trajectories/trajectories.npy" \
    2>/dev/null | head -n 1)

if [ -n "$TRAJECTORY_FILE" ]; then
    echo "Trajectory output found:"
    echo "$TRAJECTORY_FILE"

    # trajectories.npy lives inside:
    # session_folder/trajectories/trajectories.npy
    # So dirname(dirname(TRAJECTORY_FILE)) gives the session folder.
    SESSION_DIR=$(dirname "$(dirname "$TRAJECTORY_FILE")")
    SESSION_NAME=$(basename "$SESSION_DIR")
    FINAL_SESSION_DIR="$FINAL_SESSION_ROOT/$SESSION_NAME"

    echo "Detected IDTracker.ai session folder:"
    echo "$SESSION_DIR"

    echo "Final session root:"
    echo "$FINAL_SESSION_ROOT"

    if [ "$SESSION_DIR" = "$FINAL_SESSION_DIR" ]; then
        echo "Session folder is already in the final location."
    elif [ -e "$FINAL_SESSION_DIR" ]; then
        echo "WARNING: Final session folder already exists:"
        echo "$FINAL_SESSION_DIR"

        TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
        FINAL_SESSION_DIR="${FINAL_SESSION_DIR}_rerun_${TIMESTAMP}"

        echo "Moving this run to timestamped folder:"
        echo "$FINAL_SESSION_DIR"

        mv "$SESSION_DIR" "$FINAL_SESSION_DIR"
    else
        echo "Moving session folder to:"
        echo "$FINAL_SESSION_DIR"

        mv "$SESSION_DIR" "$FINAL_SESSION_DIR"
    fi

    echo "Final session folder contents:"
    ls -lh "$FINAL_SESSION_DIR"

    echo "Finished $RUN_ID successfully."
    exit 0
else
    echo "ERROR: No trajectories.npy file found for $RUN_ID"
    echo "IDTracker.ai exit code was: $IDTRACKER_EXIT_CODE"
    exit 1
fi
