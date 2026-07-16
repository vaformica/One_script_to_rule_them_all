#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
source "$HOME/miniconda3/etc/profile.d/conda.sh"
conda activate beetle_pipeline_mac
cd "$ROOT"
exec python -m app.mac_gui
