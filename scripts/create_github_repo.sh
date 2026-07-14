#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 ]]; then
    echo "Usage: $0 <github-repository-url>"
    exit 1
fi

REMOTE="$1"

git init
git add .
git commit -m "Initial IDtracker pipeline controller MVP"
git branch -M main
git remote add origin "$REMOTE"
git tag -a v0.1.0 -m "Initial MVP"
git push -u origin main
git push origin v0.1.0
