#!/usr/bin/env python3
from pathlib import Path
import shutil
import subprocess
import sys

root = Path(__file__).resolve().parents[1]
required = [
    root / 'firebird_idtracker_toml_folder_gui.py',
    root / 'tools' / 'toml_folder_manager.py',
    root / 'slurm' / 'firebird_idtracker_toml_folder.slurm',
    root / 'postprocess' / 'idtracker_unified_postprocess.py',
]
print('Package:', root)
for p in required:
    print(('OK  ' if p.exists() else 'MISS'), p)
for exe in ['python', 'sbatch', 'squeue', 'idtrackerai']:
    print(f'{exe}:', shutil.which(exe) or 'NOT FOUND')
try:
    import tomllib  # noqa
    print('tomllib: OK')
except Exception:
    print('tomllib: missing; install tomli for Python <3.11')
try:
    import PyQt6  # noqa
    print('PyQt6: OK')
except Exception as e:
    try:
        import PySide6  # noqa
        print('PySide6: OK')
    except Exception:
        try:
            import PyQt5  # noqa
            print('PyQt5: OK')
        except Exception:
            print('Qt binding: NOT FOUND. Run: conda install -c conda-forge pyqt -y')
