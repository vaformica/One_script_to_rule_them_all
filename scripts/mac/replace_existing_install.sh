#!/usr/bin/env bash
set -euo pipefail

SOURCE_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
TARGET="${1:-$HOME/Library/CloudStorage/Dropbox/Projects/One_script_to_rule_them_all}"

if [[ ! -d "$TARGET" ]]; then
    echo "Target project folder does not exist: $TARGET" >&2
    exit 1
fi

STAMP="$(date +%Y%m%d_%H%M%S)"
BACKUP="${TARGET}_backup_${STAMP}"

echo "Backing up existing installation:"
echo "$BACKUP"
cp -a "$TARGET" "$BACKUP"

echo "Replacing project files while preserving config/user.json..."
rsync -a --delete \
    --exclude '.git/' \
    --exclude 'config/user.json' \
    "$SOURCE_ROOT/" "$TARGET/"

echo
echo "Replacement complete."
echo "Next commands:"
echo "cd \"$TARGET\""
echo "bash scripts/mac/install.sh"
echo "bash scripts/mac/launch.sh"
