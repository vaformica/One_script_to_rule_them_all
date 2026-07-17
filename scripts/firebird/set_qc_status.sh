#!/usr/bin/env bash
set -euo pipefail
PROJECT_ROOT="$1"; RECORD_ID="$2"; DECISION="$3"; NOTES="${4-}"
REPO_ROOT="${PIPELINE_REPO_ROOT:-/data/labs/vformic1-swat-lab/idtracker_pipeline}"
PYTHON="${HOME}/miniconda3/envs/beetle_pipeline/bin/python"
export PYTHONPATH="$REPO_ROOT${PYTHONPATH:+:$PYTHONPATH}"
exec "$PYTHON" "$REPO_ROOT/collector/qc_manager.py" --project-root "$PROJECT_ROOT" --record-id "$RECORD_ID" --decision "$DECISION" --notes "$NOTES"
