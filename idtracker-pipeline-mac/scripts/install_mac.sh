#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [[ -f "$HOME/miniconda3/etc/profile.d/conda.sh" ]]; then
    source "$HOME/miniconda3/etc/profile.d/conda.sh"
elif [[ -f "$HOME/anaconda3/etc/profile.d/conda.sh" ]]; then
    source "$HOME/anaconda3/etc/profile.d/conda.sh"
else
    echo "Conda was not found." >&2
    exit 1
fi

if conda env list | awk '{print $1}' | grep -qx idtracker_controller_mac; then
    conda env update -n idtracker_controller_mac -f environment.yml --prune
else
    conda env create -f environment.yml
fi

echo "Installation complete."
echo "Launch with: bash scripts/launch_mac.sh"
