#!/bin/bash
set -euo pipefail

# Launch from the conda environment that contains idtracker.ai.
# Example:
#   conda activate idtrackerai
#   bash launch_gui.sh

mkdir -p "/tmp/runtime-$USER"
chmod 700 "/tmp/runtime-$USER" || true
export XDG_RUNTIME_DIR="/tmp/runtime-$USER"
# noVNC/HPC sessions sometimes leave SESSION_MANAGER pointing at an auth method Qt cannot use.
# Unsetting it avoids the harmless but confusing "Session management error" message.
unset SESSION_MANAGER || true

cd "$(dirname "$0")"
python firebird_idtracker_toml_folder_gui.py
