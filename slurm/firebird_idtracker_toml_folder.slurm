#!/bin/bash
#SBATCH --job-name=idtracker_tomls
# GPU partition/GRES are supplied by the GUI at sbatch submission time.
# IDtracker jobs usually use: --partition=gpu --gres=gpu:l40s:1
# Post-processing jobs intentionally do not request a GPU.
#SBATCH --nodes=1
#SBATCH --cpus-per-task=4
#SBATCH --mem=32G
#SBATCH --time=04:00:00
#SBATCH --output=logs/%x_%A_%a.out
#SBATCH --error=logs/%x_%A_%a.err

# Firebird simplified TOML-folder workflow.
# MODE=idtracker: one array task per row in project_metadata/toml_video_manifest.csv; tracking only.
# MODE=collect: one dependent job after the array; moves completed sessions from the original video folder.
# MODE=postprocess: one dependent job after collection succeeds.

set -euo pipefail

MODE="${MODE:-idtracker}"
TOML_ROOT="${TOML_ROOT:-$SLURM_SUBMIT_DIR}"
PROJECT_DIR="$TOML_ROOT"
SESSION_OUTPUT_ROOT="${SESSION_OUTPUT_ROOT:-$TOML_ROOT/idtracker_sessions}"
POSTPROCESS_OUTPUT_ROOT="${POSTPROCESS_OUTPUT_ROOT:-$TOML_ROOT/postprocessing}"
PACKAGE_DIR="${PACKAGE_DIR:-$SLURM_SUBMIT_DIR}"
SEARCH_ROOT_EXTRA="${SEARCH_ROOT_EXTRA:-$TOML_ROOT}"
CONDA_INIT="${CONDA_INIT:-/home/vformic1-swat/miniconda3/etc/profile.d/conda.sh}"
CONDA_ENV="${CONDA_ENV:-idtrackerai}"
METADATA_MANIFEST="${METADATA_MANIFEST:-$TOML_ROOT/project_metadata/toml_video_manifest.csv}"
MANAGER="$PACKAGE_DIR/tools/toml_folder_manager.py"
STATUS_TOOL="$PACKAGE_DIR/tools/pipeline_status.py"

# Exit-code conventions used by this package:
# 0  = stage succeeded or idtracker command finished and collector will verify outputs.
# 1  = missing file/configuration or unknown mode.
# 10 = collector manifest empty/missing.
# 20 = collector could not verify all expected sessions.
# 30 = unexpected collector/internal error.
# 40 = post-processing failed or did not process all expected cells.

mkdir -p "$TOML_ROOT/logs" "$SESSION_OUTPUT_ROOT" "$POSTPROCESS_OUTPUT_ROOT" "$TOML_ROOT/gui_slurm"

printf '\n===== Firebird IDtracker TOML-folder job =====\n'
echo "Date: $(date)"
echo "Host: $(hostname)"
echo "SLURM_JOB_ID: ${SLURM_JOB_ID:-NA}"
echo "SLURM_ARRAY_TASK_ID: ${SLURM_ARRAY_TASK_ID:-NA}"
echo "MODE: $MODE"
echo "TOML_ROOT: $TOML_ROOT"
echo "SESSION_OUTPUT_ROOT: $SESSION_OUTPUT_ROOT"
echo "POSTPROCESS_OUTPUT_ROOT: $POSTPROCESS_OUTPUT_ROOT"
echo "PACKAGE_DIR: $PACKAGE_DIR"
echo "METADATA_MANIFEST: $METADATA_MANIFEST"

if [ -f "$CONDA_INIT" ]; then
    source "$CONDA_INIT"
    conda activate "$CONDA_ENV"
else
    echo "WARNING: Conda init file not found: $CONDA_INIT"
    echo "Continuing with current shell environment."
fi

echo "Python: $(which python || true)"
echo "IDtracker.ai: $(which idtrackerai || true)"
echo "CUDA_VISIBLE_DEVICES: ${CUDA_VISIBLE_DEVICES:-NA}"
if command -v nvidia-smi >/dev/null 2>&1; then
    echo "===== nvidia-smi ====="
    nvidia-smi --query-gpu=index,name,memory.total,memory.used,utilization.gpu --format=csv || nvidia-smi || true
    echo "======================"
else
    echo "nvidia-smi not found in PATH."
fi

if [ ! -f "$MANAGER" ]; then
    echo "ERROR: Missing metadata manager: $MANAGER"
    exit 1
fi

if [ "$MODE" = "idtracker" ]; then
    TASK_ID="${SLURM_ARRAY_TASK_ID:-1}"
    if [ ! -f "$METADATA_MANIFEST" ]; then
        echo "ERROR: Manifest not found: $METADATA_MANIFEST"
        exit 1
    fi

    echo "Reading manifest row $TASK_ID"
    eval "$(python "$MANAGER" env --manifest "$METADATA_MANIFEST" --index "$TASK_ID")"

    PROJECT_ID="$PROJECT_ID_FROM_MANIFEST"
    PROJECT_NAME="$PROJECT_NAME_FROM_MANIFEST"
    METADATA_TAG="$METADATA_TAG_FROM_MANIFEST"
    PIPELINE="$PIPELINE_FROM_MANIFEST"
    ORIGINAL_VIDEO_PATH="$ORIGINAL_VIDEO_PATH_FROM_MANIFEST"
    ORIGINAL_VIDEO_NAME="$ORIGINAL_VIDEO_NAME_FROM_MANIFEST"
    ORIGINAL_VIDEO_STEM="$ORIGINAL_VIDEO_STEM_FROM_MANIFEST"
    CELL_ID="$CELL_ID_FROM_MANIFEST"
    CELL_LABEL="$CELL_LABEL_FROM_MANIFEST"
    TOML_FOLDER="$TOML_FOLDER_FROM_MANIFEST"
    TOML_PATH="$TOML_PATH_FROM_MANIFEST"
    TOML_NAME="$TOML_NAME_FROM_MANIFEST"
    TOML_STEM="$TOML_STEM_FROM_MANIFEST"
    OUTPUT_STEM="$OUTPUT_STEM_FROM_MANIFEST"

    echo "Original video: $ORIGINAL_VIDEO_PATH"
    echo "TOML: $TOML_PATH"
    echo "Pipeline: $PIPELINE"
    echo "Cell/TOML id: $CELL_ID"
    echo "Output stem: $OUTPUT_STEM"

    if [ ! -f "$TOML_PATH" ]; then
        echo "ERROR: TOML does not exist: $TOML_PATH"
        exit 1
    fi

    cd "$TOML_FOLDER"
    RUN_START_EPOCH=$(date +%s)
    echo "Working directory: $(pwd)"
    echo "Run start epoch: $RUN_START_EPOCH"

    set +e
    idtrackerai --load "$TOML_PATH" --track
    IDTRACKER_EXIT_CODE=$?
    set -e
    echo "IDtracker.ai exit code: $IDTRACKER_EXIT_CODE"
    TASK_STATUS_DIR="$TOML_ROOT/project_metadata/idtracker_task_status"
    mkdir -p "$TASK_STATUS_DIR"
    cat > "$TASK_STATUS_DIR/task_${TASK_ID}_${OUTPUT_STEM}.json" <<EOF_STATUS
{
  "stage": "idtracker",
  "status": "tracking_command_finished",
  "note": "Collector job verifies whether trajectories were created.",
  "slurm_job_id": "${SLURM_JOB_ID:-}",
  "slurm_array_task_id": "${SLURM_ARRAY_TASK_ID:-}",
  "output_stem": "$OUTPUT_STEM",
  "cell_label": "$CELL_LABEL",
  "toml_path": "$TOML_PATH",
  "original_video_path": "$ORIGINAL_VIDEO_PATH",
  "idtracker_exit_code": $IDTRACKER_EXIT_CODE,
  "host": "$(hostname)",
  "timestamp": "$(date)"
}
EOF_STATUS
    if [ "$IDTRACKER_EXIT_CODE" -ne 0 ]; then
        echo "WARNING: idtracker.ai returned non-zero exit code $IDTRACKER_EXIT_CODE for output_stem=$OUTPUT_STEM"
        echo "WARNING: idtracker.ai 6.x can sometimes return a non-zero code even after printing Success and writing trajectories."
        echo "WARNING: This array task will not fail here. The dependent collector job will verify whether a complete session with trajectories exists."
    fi

    echo "IDtracker tracking command finished for output_stem=$OUTPUT_STEM"
    echo "Session movement and trajectory verification are handled by the dependent MODE=collect job after the full array finishes."
    exit 0

elif [ "$MODE" = "collect" ]; then
    echo "Collecting all completed IDtracker sessions after array completion."
    COLLECTOR="$PACKAGE_DIR/tools/session_collector.py"
    if [ ! -f "$COLLECTOR" ]; then
        echo "ERROR: Missing session collector: $COLLECTOR"
        exit 1
    fi
    set +e
    python "$COLLECTOR" \
        --manifest "$METADATA_MANIFEST" \
        --collect-all \
        --session-output-root "$SESSION_OUTPUT_ROOT" \
        --extra-search-root "$TOML_ROOT" \
        --extra-search-root "$SEARCH_ROOT_EXTRA"
    COLLECT_EXIT_CODE=$?
    set -e
    echo "Session collector exit code: $COLLECT_EXIT_CODE"
    if [ -f "$STATUS_TOOL" ]; then
        python "$STATUS_TOOL" --toml-folder "$TOML_ROOT" || true
    fi
    if [ "$COLLECT_EXIT_CODE" -ne 0 ]; then
        echo "ERROR E210: Session collection did not verify all expected cells. See project_metadata/session_collection_report.csv"
        exit "$COLLECT_EXIT_CODE"
    fi
    echo "Session collection job finished successfully: $(date)"
    exit 0

elif [ "$MODE" = "postprocess" ]; then
    POSTPROCESS_SCRIPT="$PACKAGE_DIR/postprocess/idtracker_unified_postprocess.py"
    if [ ! -f "$POSTPROCESS_SCRIPT" ]; then
        echo "ERROR: Cannot find post-processing script: $POSTPROCESS_SCRIPT"
        exit 1
    fi

    PIPELINE=$(python - <<PYPIPE
import csv, pathlib
p=pathlib.Path("$METADATA_MANIFEST")
with p.open(newline='', encoding='utf-8') as f:
    rows=list(csv.DictReader(f))
print(rows[0].get('pipeline','') if rows else '')
PYPIPE
)
    if [ -z "$PIPELINE" ]; then
        echo "ERROR: Could not determine pipeline from manifest."
        exit 1
    fi

    OUT="$POSTPROCESS_OUTPUT_ROOT/${PIPELINE}_postprocessing"
    mkdir -p "$OUT"
    echo "Running post-processing pipeline=$PIPELINE output=$OUT"

    CMD=(python "$POSTPROCESS_SCRIPT"
        --pipeline "$PIPELINE"
        --search-root "$SESSION_OUTPUT_ROOT"
        --output-root "$OUT"
        --metadata-manifest "$METADATA_MANIFEST"
        --metadata-tag "${METADATA_TAG:-}"
        --toml-folder "$TOML_ROOT"
        --analysis-start-frame "${ANALYSIS_START_FRAME:--1}"
        --window-frames "${WINDOW_FRAMES:-7500}"
        --fps "${FPS:-30}"
        --move-threshold-px "${MOVE_THRESHOLD_PX:-30}"
        --movement-onset-consecutive-frames "${MOVEMENT_ONSET_CONSECUTIVE_FRAMES:-30}"
        --max-step-px "${MAX_STEP_PX:-50}"
        --roi-wall-buffer-px "${ROI_WALL_BUFFER_PX:-30}"
        --turtling-window-frames "${TURTLING_WINDOW_FRAMES:-300}"
        --turtling-min-duration-frames "${TURTLING_MIN_DURATION_FRAMES:-300}"
        --overwrite)

    if [ "$PIPELINE" = "ba" ]; then
        CMD+=(--animal-index "${ANIMAL_INDEX:-0}")
    else
        CMD+=(--animal0 "${ANIMAL0:-0}" --animal1 "${ANIMAL1:-1}" --contact-px "${CONTACT_PX:-60}" --min-contact-s "${MIN_CONTACT_S:-0.2}" --fight-px "${FIGHT_PX:-35}" --min-fight-frames "${MIN_FIGHT_FRAMES:-6}")
    fi
    echo "Command: ${CMD[*]}"
    set +e
    "${CMD[@]}"
    POSTPROCESS_EXIT_CODE=$?
    set -e
    echo "Post-processing exit code: $POSTPROCESS_EXIT_CODE"
    if [ -f "$STATUS_TOOL" ]; then
        python "$STATUS_TOOL" --toml-folder "$TOML_ROOT" || true
    fi
    if [ "$POSTPROCESS_EXIT_CODE" -ne 0 ]; then
        echo "ERROR E310: Post-processing failed or did not process all expected cells."
        exit 40
    fi
    echo "Post-processing finished successfully for all expected cells: $(date)"
else
    echo "ERROR: Unknown MODE=$MODE"
    exit 1
fi
