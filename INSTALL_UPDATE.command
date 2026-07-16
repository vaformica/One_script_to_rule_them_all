#!/usr/bin/env bash
set -euo pipefail

# One-command update for the working Mac repository and active Firebird install.
ROOT="$(cd "$(dirname "$0")" && pwd)"
REMOTE_USER="vformic1-swat"
REMOTE_HOST="firebird.swarthmore.edu"
REMOTE_ROOT="/data/labs/vformic1-swat-lab/idtracker_pipeline"
SSH_KEY="$HOME/.ssh/id_ed25519_firebird"
MAC_ENV="beetle_pipeline_mac"
VERSION="$(tr -d '[:space:]' < "$ROOT/VERSION")"

echo
echo "Installing Beetle IDtracker Pipeline v$VERSION"
echo "Local repository: $ROOT"
echo "Firebird repository: $REMOTE_ROOT"
echo

[[ -f "$ROOT/app/mac_gui.py" ]] || { echo "ERROR: Run this from the repository root." >&2; exit 2; }
[[ -f "$SSH_KEY" ]] || { echo "ERROR: SSH key not found: $SSH_KEY" >&2; exit 3; }

# Reuse the Mac environment. Create it only if absent.
CONDA_SH="$HOME/miniconda3/etc/profile.d/conda.sh"
[[ -f "$CONDA_SH" ]] || { echo "ERROR: Conda was not found at $CONDA_SH" >&2; exit 4; }
source "$CONDA_SH"
if conda env list | awk 'NF && $1 !~ /^#/ {print $1}' | grep -Fxq "$MAC_ENV"; then
    echo "Reusing Mac Conda environment: $MAC_ENV"
else
    echo "Creating missing Mac Conda environment: $MAC_ENV"
    conda env create -f "$ROOT/environment-mac.yml"
fi

MAC_PY="$HOME/miniconda3/envs/$MAC_ENV/bin/python"
"$MAC_PY" -m py_compile \
  "$ROOT/app/mac_gui.py" \
  "$ROOT/pipeline/session_locator.py" \
  "$ROOT/processors/dispatch.py" \
  "$ROOT/processors/run_unified_pipeline.py" \
  "$ROOT/collector/collect_outputs.py" \
  "$ROOT/collector/qc_manager.py"

echo "Mac validation passed."
echo "Syncing code to Firebird..."
rsync -av \
  --exclude '.git/' \
  --exclude '__pycache__/' \
  --exclude '*.pyc' \
  --exclude 'config/user.json' \
  -e "ssh -i $SSH_KEY" \
  "$ROOT/" \
  "$REMOTE_USER@$REMOTE_HOST:$REMOTE_ROOT/"

echo "Running Firebird install and import smoke tests..."
ssh -i "$SSH_KEY" "$REMOTE_USER@$REMOTE_HOST" bash -s -- "$REMOTE_ROOT" "$VERSION" <<'REMOTE'
set -euo pipefail
ROOT="$1"
EXPECTED_VERSION="$2"
cd "$ROOT"
bash scripts/firebird/install.sh

test "$(tr -d '[:space:]' < VERSION)" = "$EXPECTED_VERSION"
PY="$HOME/miniconda3/envs/beetle_pipeline/bin/python"

# Test from /home, matching the SLURM jobs' usual working directory.
cd "$HOME"
PYTHONPATH="$ROOT" "$PY" -c "import pipeline, collector, processors; from pipeline.run_metadata import RunMetadata; from collector.metadata_injector import enrich_tree; print('Package imports OK')"
PYTHONPATH="$ROOT" "$PY" "$ROOT/processors/run_unified_pipeline.py" --help >/dev/null
PYTHONPATH="$ROOT" "$PY" "$ROOT/collector/collect_outputs.py" --help >/dev/null
PYTHONPATH="$ROOT" "$PY" "$ROOT/collector/qc_manager.py" --help >/dev/null
test -x "$ROOT/scripts/firebird/set_qc_status.sh"
grep -q 'QC_review_bundle' "$ROOT/collector/collect_outputs.py"
grep -q 'view_selected_qc_files' "$ROOT/app/mac_gui.py"
grep -q "'window_frames','Window frames',7500" "$ROOT/app/mac_gui.py"

# Normalize any QC index created by an earlier pipeline release.
PYTHONPATH="$ROOT" "$PY" "$ROOT/collector/qc_manager.py" --project-root /data/labs/vformic1-swat-lab/idtracker_pipeline_runs --migrate-index

grep -q 'export PYTHONPATH=' "$ROOT/slurm/postprocess_one_cell.slurm"
grep -q 'export PYTHONPATH=' "$ROOT/slurm/collect_one_cell.slurm"
! grep -q 'import tomlkit' "$ROOT/pipeline/session_locator.py"

echo "Firebird validation passed."
REMOTE

echo
echo "INSTALLATION COMPLETE: v$VERSION"
echo "No Conda environment was recreated unless it was missing."
echo "Launch the GUI with:"
echo "  bash \"$ROOT/scripts/mac/launch.sh\""
echo
