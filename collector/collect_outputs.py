from __future__ import annotations
import argparse
import csv
import shutil
from datetime import datetime
from pathlib import Path
from PIL import Image

from pipeline.run_metadata import RunMetadata


def safe(text: str) -> str:
    return "".join(c if c.isalnum() or c in "._-" else "_" for c in text)


def small_track_copy(source: Path, destination: Path, max_dimension: int = 1600):
    destination.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(source) as image:
        converted = image.convert("RGB")
        converted.thumbnail((max_dimension, max_dimension), Image.Resampling.LANCZOS)
        converted.save(destination, "PNG", optimize=True, compress_level=9)


def collect(run_dir: Path, project_root: Path, metadata: RunMetadata) -> dict:
    review = project_root / "review"
    tracks = review / "all_tracks"
    summaries = review / "all_summaries"
    manifests = review / "all_manifests"
    tracks.mkdir(parents=True, exist_ok=True)
    summaries.mkdir(parents=True, exist_ok=True)
    manifests.mkdir(parents=True, exist_ok=True)

    stamp = metadata.run_timestamp.replace("-", "").replace(":", "").replace(" ", "_")
    prefix = (
        f"run_{metadata.run_index:05d}_{stamp}_"
        f"{safe(Path(metadata.video_filename).stem)}_"
        f"{safe(metadata.cell_label)}_{safe(metadata.analysis_type)}"
    )

    track_sources = sorted({
        p for p in run_dir.rglob("*.png")
        if "track" in p.name.lower() or "trajectory" in p.name.lower()
    })
    summary_sources = sorted({
        p for p in run_dir.rglob("*.csv")
        if "summary" in p.name.lower() or "manifest" in p.name.lower()
    })

    for i, source in enumerate(track_sources, 1):
        suffix = "" if len(track_sources) == 1 else f"_{i:02d}"
        small_track_copy(source, tracks / f"{prefix}_track{suffix}.png")

    for i, source in enumerate(summary_sources, 1):
        suffix = "" if len(summary_sources) == 1 else f"_{i:02d}"
        shutil.copy2(source, summaries / f"{prefix}_summary{suffix}.csv")

    metadata_target = manifests / f"{prefix}_run_metadata.json"
    shutil.copy2(run_dir / "run_metadata.json", metadata_target)

    index = review / "review_index.csv"
    new = not index.exists()
    with index.open("a", newline="", encoding="utf-8") as handle:
        fields = [
            "run_index", "run_timestamp", "analysis_type", "video_filename",
            "cell_label", "run_dir", "track_count", "summary_count", "collected_at"
        ]
        writer = csv.DictWriter(handle, fieldnames=fields)
        if new:
            writer.writeheader()
        writer.writerow({
            "run_index": metadata.run_index,
            "run_timestamp": metadata.run_timestamp,
            "analysis_type": metadata.analysis_type,
            "video_filename": metadata.video_filename,
            "cell_label": metadata.cell_label,
            "run_dir": str(run_dir),
            "track_count": len(track_sources),
            "summary_count": len(summary_sources),
            "collected_at": datetime.now().isoformat(timespec="seconds"),
        })
    return {"tracks": len(track_sources), "summaries": len(summary_sources)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--project-root", required=True)
    parser.add_argument("--run-metadata-json", required=True)
    args = parser.parse_args()
    metadata = RunMetadata.from_json(args.run_metadata_json)
    print(collect(Path(args.run_dir), Path(args.project_root), metadata))


if __name__ == "__main__":
    main()
