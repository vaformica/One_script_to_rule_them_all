#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if command -v conda >/dev/null 2>&1; then
    if conda env list | awk '{print $1}' | grep -qx idtracker_controller; then
        echo "Conda environment idtracker_controller already exists."
    else
        conda env create -f environment.yml
    fi
else
    echo "Conda was not found."
    echo "Install Miniconda or use a Python 3.11 virtual environment."
    exit 1
fi

if [[ ! -f config/config.toml ]]; then
    cp config/config.example.toml config/config.toml
    echo "Created config/config.toml from the example."
fi

echo
echo "Installation complete."
echo "Edit config/config.toml before launching."
echo "Launch with: bash scripts/launch_app.sh"
