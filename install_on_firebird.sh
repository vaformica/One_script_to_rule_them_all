#!/bin/bash
set -euo pipefail

DEST="${1:-$HOME/formicalab/IDTracker_Firebird_TOML_Folder}"
mkdir -p "$(dirname "$DEST")"
rm -rf "$DEST"
cp -R "$(pwd)" "$DEST"
chmod +x "$DEST/launch_gui.sh" "$DEST/firebird_idtracker_toml_folder_gui.py" "$DEST/tools/toml_folder_manager.py" "$DEST/tools/session_collector.py" "$DEST/tools/pipeline_status.py" "$DEST/slurm/firebird_idtracker_toml_folder.slurm"

echo "Installed to: $DEST"
echo "Launch with:"
echo "  conda activate idtrackerai"
echo "  cd $DEST"
echo "  bash launch_gui.sh"
