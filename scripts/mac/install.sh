#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
source "$HOME/miniconda3/etc/profile.d/conda.sh"
if conda env list | awk '{print $1}' | grep -qx beetle_pipeline_mac; then
  conda env update -n beetle_pipeline_mac -f "$ROOT/environment-mac.yml" --prune
else
  conda env create -f "$ROOT/environment-mac.yml"
fi
