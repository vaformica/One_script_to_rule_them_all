#!/usr/bin/env python3
"""Thin student-safe wrapper around idtracker_unified_postprocess.py for BA sessions."""
from idtracker_unified_postprocess import main
import sys
if __name__ == "__main__":
    raise SystemExit(main(["--pipeline", "ba"] + sys.argv[1:]))
