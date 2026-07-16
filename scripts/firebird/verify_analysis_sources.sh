#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

files=(
  "$ROOT/analysis/analyze_idtracker_unified.py"
  "$ROOT/analysis/run_idtracker_unified_batch.py"
  "$ROOT/processors/run_unified_pipeline.py"
  "$ROOT/processors/dispatch.py"
)

for file in "${files[@]}"; do
  [[ -s "$file" ]] || { echo "MISSING: $file" >&2; exit 2; }
  echo "OK: $file"
done

python -m py_compile "${files[@]}"
echo "Unified analysis source compiles successfully."
