#!/bin/bash
set -euo pipefail

# Run this while the conda environment that contains idtracker.ai is active.
# It installs a process_tomls command into that environment's bin directory.

if [[ -z "${CONDA_PREFIX:-}" ]]; then
    echo "ERROR: No conda environment is active." >&2
    echo "Run: conda activate idtrackerai" >&2
    echo "Then rerun this installer." >&2
    exit 1
fi

PACKAGE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GUI="$PACKAGE_DIR/firebird_idtracker_toml_folder_gui.py"
PYTHON="$CONDA_PREFIX/bin/python"
COMMAND="$CONDA_PREFIX/bin/process_tomls"

if [[ ! -f "$GUI" ]]; then
    echo "ERROR: GUI script not found: $GUI" >&2
    exit 1
fi
if [[ ! -x "$PYTHON" ]]; then
    echo "ERROR: Python not found in active conda environment: $PYTHON" >&2
    exit 1
fi

cat > "$COMMAND" <<WRAPPER
#!/bin/bash
set -euo pipefail
exec "$PYTHON" "$GUI" "\$@"
WRAPPER

chmod +x "$COMMAND"
hash -r 2>/dev/null || true

echo "Installed command: $COMMAND"
echo "You can now launch the GUI from any directory with:"
echo "  process_tomls"
