#!/bin/bash
set -euo pipefail

DEST="${1:-$HOME/formicalab/IDTracker_Firebird_TOML_Folder}"
mkdir -p "$(dirname "$DEST")"
rm -rf "$DEST"
cp -R "$(pwd)" "$DEST"
chmod +x "$DEST/launch_gui.sh" "$DEST/install_process_tomls_command.sh" "$DEST/firebird_idtracker_toml_folder_gui.py" "$DEST/tools/toml_folder_manager.py" "$DEST/tools/session_collector.py" "$DEST/tools/pipeline_status.py" "$DEST/slurm/firebird_idtracker_toml_folder.slurm"

echo "Installed to: $DEST"
echo "Install the one-line command inside the idtrackerai environment:"
echo "  conda activate idtrackerai"
echo "  bash $DEST/install_process_tomls_command.sh"
echo "Then launch from any directory with:"
echo "  process_tomls"
