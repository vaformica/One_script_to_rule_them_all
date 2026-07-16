#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
CONDA_SH="$HOME/miniconda3/etc/profile.d/conda.sh"
ENV_NAME="${MAC_PIPELINE_CONDA_ENV:-beetle_pipeline_mac}"
source "$CONDA_SH"
if conda env list | awk 'NF && $1 !~ /^#/ {print $1}' | grep -Fxq "$ENV_NAME"; then
  echo "Conda environment '$ENV_NAME' already exists; reusing it unchanged."
else
  echo "Creating missing Conda environment '$ENV_NAME'..."
  conda env create -f "$ROOT/environment-mac.yml"
fi
"$HOME/miniconda3/envs/$ENV_NAME/bin/python" -m py_compile \
  "$ROOT/app/mac_gui.py" "$ROOT/pipeline/session_locator.py"
echo "Mac installation validated."
