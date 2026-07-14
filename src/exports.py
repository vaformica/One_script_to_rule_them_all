from __future__ import annotations

from pathlib import Path
import pandas as pd

from .database import Database


def export_all(db: Database, export_dir: Path) -> dict[str, Path]:
    export_dir.mkdir(parents=True, exist_ok=True)

    outputs = {}
    datasets = {
        "analysis_units.csv": db.list_analysis_units(),
        "runs.csv": db.list_runs(),
        "jobs.csv": db.list_jobs(),
        "accepted_results.csv": db.accepted_results(),
    }
    for filename, rows in datasets.items():
        path = export_dir / filename
        pd.DataFrame(rows).to_csv(path, index=False)
        outputs[filename] = path
    return outputs
