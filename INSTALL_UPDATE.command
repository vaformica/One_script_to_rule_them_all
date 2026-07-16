#!/usr/bin/env bash
set -euo pipefail

# One-step installer for Vince's Mac + Firebird installation.
# Run this from:
# /Users/New/Library/CloudStorage/Dropbox/Projects/One_script_to_rule_them_all

ROOT="$(cd "$(dirname "$0")" && pwd)"
EXPECTED_ROOT="/Users/New/Library/CloudStorage/Dropbox/Projects/One_script_to_rule_them_all"
REMOTE_USER="vformic1-swat"
REMOTE_HOST="firebird.swarthmore.edu"
REMOTE_ROOT="/data/labs/vformic1-swat-lab/idtracker_pipeline"
SSH_KEY="$HOME/.ssh/id_ed25519_firebird"
MAC_ENV="beetle_pipeline_mac"

printf '\nBeetle IDtracker Pipeline installer\n'
printf 'Local source: %s\n' "$ROOT"
printf 'Firebird destination: %s@%s:%s\n\n' "$REMOTE_USER" "$REMOTE_HOST" "$REMOTE_ROOT"

if [[ "$ROOT" != "$EXPECTED_ROOT" ]]; then
    echo "WARNING: This installer is normally run from:" >&2
    echo "  $EXPECTED_ROOT" >&2
    echo "Current folder is:" >&2
    echo "  $ROOT" >&2
    read -r -p "Continue from this folder? [y/N] " reply
    [[ "$reply" =~ ^[Yy]$ ]] || exit 1
fi

if [[ ! -f "$ROOT/VERSION" ]]; then
    echo "ERROR: VERSION file not found. Copy the ZIP contents into the working repository first." >&2
    exit 2
fi

echo "Installing pipeline version $(cat "$ROOT/VERSION")"

# Reuse the Mac Conda environment. Create it only when absent.
CONDA_SH="$HOME/miniconda3/etc/profile.d/conda.sh"
if [[ ! -f "$CONDA_SH" ]]; then
    echo "ERROR: Cannot find $CONDA_SH" >&2
    exit 3
fi
source "$CONDA_SH"
if conda env list | awk 'NF && $1 !~ /^#/ {print $1}' | grep -Fxq "$MAC_ENV"; then
    echo "Mac Conda environment '$MAC_ENV' already exists; leaving it unchanged."
else
    echo "Creating missing Mac Conda environment '$MAC_ENV'..."
    conda env create -f "$ROOT/environment-mac.yml"
fi

MAC_PY="$HOME/miniconda3/envs/$MAC_ENV/bin/python"
"$MAC_PY" -m py_compile "$ROOT/app/mac_gui.py" "$ROOT/pipeline/session_locator.py"

echo "Mac code validation passed."

if [[ ! -f "$SSH_KEY" ]]; then
    echo "ERROR: SSH key not found: $SSH_KEY" >&2
    exit 4
fi

echo
 echo "Syncing current repository to Firebird..."
rsync -av \
    --exclude '.git/' \
    --exclude '__pycache__/' \
    --exclude '*.pyc' \
    --exclude 'config/user.json' \
    -e "ssh -i $SSH_KEY" \
    "$ROOT/" \
    "$REMOTE_USER@$REMOTE_HOST:$REMOTE_ROOT/"

echo
 echo "Running Firebird validation installer..."
ssh -i "$SSH_KEY" "$REMOTE_USER@$REMOTE_HOST" \
    "cd '$REMOTE_ROOT' && bash scripts/firebird/install.sh"

echo
 echo "Verifying the active Firebird session locator..."
ssh -i "$SSH_KEY" "$REMOTE_USER@$REMOTE_HOST" \
    "cd '$REMOTE_ROOT' && \
     test \"\$(cat VERSION)\" = '0.8.3' && \
     ! grep -q 'import tomlkit' pipeline/session_locator.py && \
     \$HOME/miniconda3/envs/idtrackerai/bin/python -m py_compile pipeline/session_locator.py && \
     echo 'Firebird installation verified: version 0.8.3; no tomlkit dependency.'"

echo
 echo "INSTALLATION COMPLETE"
echo "Launch the GUI with:"
echo "  bash '$ROOT/scripts/mac/launch.sh'"
