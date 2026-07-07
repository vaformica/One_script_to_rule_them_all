#!/usr/bin/env python3
"""Thin student-safe wrapper around idtracker_unified_postprocess.py for fight sessions."""
from idtracker_unified_postprocess import main
import sys
if __name__ == "__main__":
    raise SystemExit(main(["--pipeline", "fight"] + sys.argv[1:]))
