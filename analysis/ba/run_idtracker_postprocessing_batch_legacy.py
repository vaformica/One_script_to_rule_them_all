#!/usr/bin/env python3
"""
Find idtracker.ai output folders under a directory, run analyze_idtracker_beetle.py
on each folder, and merge all per-session summary CSVs into one run-level CSV.

This is useful when you do not know exactly where idtracker.ai wrote each session
folder, or when you want to post-process a completed batch without SLURM.

Example:
    python run_idtracker_postprocessing_batch.py \
        --search-root /data/labs/vformic1-swat-lab/2026_Videos/2026_BA/BA_Recorded \
        --analysis-script /path/to/analyze_idtracker_beetle.py \
        --output-root /data/labs/vformic1-swat-lab/2026_Videos/2026_BA/postprocessing \
        --analysis-start-frame 1540 \
        --window-frames 7200 \
        --move-threshold-px 30
"""

from __future__ import annotations

import argparse
import csv
import os
import re
import subprocess
import sys
import shutil
from pathlib import Path
from typing import Iterable, List, Optional


TRAJECTORY_BASENAMES = ("trajectories.npy", "trajectories.h5", "trajectories.csv")
DEFAULT_SKIP_DIR_NAMES = {
    ".git", "__pycache__", ".ipynb_checkpoints",
    "postprocessing", "beetle_postprocessing", "beetle_analysis",
}


def safe_name(text: str, max_len: int = 180) -> str:
    """Convert a relative path/session name into a safe output directory name."""
    text = str(text).strip().replace(os.sep, "__")
    text = re.sub(r"[^A-Za-z0-9._=-]+", "_", text)
    text = re.sub(r"_+", "_", text).strip("_")
    return text[:max_len] if len(text) > max_len else text


def has_idtracker_output(directory: Path) -> bool:
    """Return True if a directory appears to contain idtracker.ai trajectory output."""
    if not directory.is_dir():
        return False
    has_traj = any((directory / name).is_file() for name in TRAJECTORY_BASENAMES)
    # Metadata is very useful but not required because analyze_idtracker_beetle.py can run with explicit body size.
    return has_traj


def find_nearby_file(directory: Path, filename: str, max_parent_levels: int = 3) -> Optional[Path]:
    """Find filename in directory or up to max_parent_levels parents."""
    current = Path(directory).resolve()
    for _ in range(max_parent_levels + 1):
        p = current / filename
        if p.is_file():
            return p
        if current.parent == current:
            break
        current = current.parent
    return None


def find_idtracker_dirs(search_root: Path, skip_names: set[str]) -> List[Path]:
    """Recursively find unique directories containing idtracker trajectories."""
    found: List[Path] = []
    search_root = search_root.resolve()

    for dirpath, dirnames, filenames in os.walk(search_root):
        current = Path(dirpath)

        # Prune directories we know should not be searched.
        dirnames[:] = [d for d in dirnames if d not in skip_names and not d.startswith(".")]

        if any(name in filenames for name in TRAJECTORY_BASENAMES):
            found.append(current)
            # Do not descend farther from an output directory unless there are nested sessions.
            # Usually idtracker session folders do not contain other session folders.
            # Leaving dirnames intact is safe, but pruning saves time on large runs.
            # If your runs are nested unusually, use --allow-nested-search.
            dirnames[:] = []

    return sorted(set(found))


def summary_already_done(output_dir: Path, prefix: Optional[str]) -> bool:
    if prefix:
        return (output_dir / f"{prefix}_summary.csv").exists()
    return any(output_dir.glob("*_summary.csv"))


def run_one(
    analysis_script: Path,
    session_dir: Path,
    output_dir: Path,
    prefix: str,
    args: argparse.Namespace,
) -> int:
    output_dir.mkdir(parents=True, exist_ok=True)

    cmd = [
        sys.executable,
        str(analysis_script),
        "--input-dir", str(session_dir),
        "--output-dir", str(output_dir),
        "--prefix", prefix,
        "--analysis-start-frame", str(args.analysis_start_frame),
        "--window-frames", str(args.window_frames),
        "--move-threshold-px", str(args.move_threshold_px),
        "--movement-onset-consecutive-frames", str(args.movement_onset_consecutive_frames),
        "--track-linewidth", str(args.track_linewidth),
        "--turtled-linewidth", str(args.turtled_linewidth),
        "--roi-padding-px", str(args.roi_padding_px),
        "--roi-wall-buffer-px", str(args.roi_wall_buffer_px),
        "--max-step-px", str(args.max_step_px),
        "--interpolation-warning-fraction", str(args.interpolation_warning_fraction),
        "--interpolation-warning-frames", str(args.interpolation_warning_frames),
    ]

    # Some idtracker.ai runs put trajectory files in session_X/trajectories/ while
    # session.json and attributes.json live one directory above. Pass them explicitly
    # when found so ROI and body-length metadata are not silently lost.
    session_json = find_nearby_file(session_dir, "session.json", max_parent_levels=3)
    attributes_json = find_nearby_file(session_dir, "attributes.json", max_parent_levels=3)
    if session_json is not None:
        cmd.extend(["--session-json", str(session_json)])
    if attributes_json is not None:
        cmd.extend(["--attributes-json", str(attributes_json)])

    optional_numeric = {
        "--speed-moving-threshold-px-frame": args.speed_moving_threshold_px_frame,
        "--smooth-window": args.smooth_window,
        "--smooth-polyorder": args.smooth_polyorder,
        "--turtling-window-frames": args.turtling_window_frames,
        "--turtling-min-duration-frames": args.turtling_min_duration_frames,
        "--turtling-merge-gap-frames": args.turtling_merge_gap_frames,
        "--turtling-max-net-displacement-px": args.turtling_max_net_displacement_px,
        "--turtling-max-radius-gyration-px": args.turtling_max_radius_gyration_px,
        "--turtling-max-straightness": args.turtling_max_straightness,
        "--turtling-min-path-px": args.turtling_min_path_px,
        "--turtling-min-abs-turn-rad": args.turtling_min_abs_turn_rad,
        "--turtling-start-buffer-frames": args.turtling_start_buffer_frames,
    }
    for flag, value in optional_numeric.items():
        if value is not None:
            cmd.extend([flag, str(value)])

    if args.full_frame_map:
        cmd.append("--full-frame-map")
    if args.show_map_points:
        cmd.append("--show-map-points")
    if args.disable_turtling:
        cmd.append("--disable-turtling")

    if args.dry_run:
        print("DRY RUN:", " ".join(cmd))
        return 0

    print(f"\nProcessing: {session_dir}")
    print(f"Output:     {output_dir}")
    proc = subprocess.run(cmd)
    return proc.returncode


def find_summary_files(output_root: Path) -> List[Path]:
    summaries = []
    skip_parts = {"session_output_copies", "all_track_maps"}
    for p in output_root.rglob("*_summary.csv"):
        if "data_dictionary" in p.name.lower():
            continue
        if any(part in skip_parts for part in p.parts):
            continue
        summaries.append(p)
    return sorted(summaries)


def merge_summaries(summary_files: Iterable[Path], output_csv: Path) -> int:
    """Merge CSVs with possibly non-identical columns. Adds source_summary_file."""
    summary_files = list(summary_files)
    if not summary_files:
        return 0

    rows = []
    columns = []
    seen = set()

    for path in summary_files:
        with path.open("r", newline="") as f:
            reader = csv.DictReader(f)
            if reader.fieldnames is None:
                continue
            for col in ["source_summary_file", *reader.fieldnames]:
                if col not in seen:
                    columns.append(col)
                    seen.add(col)
            for row in reader:
                row = dict(row)
                row["source_summary_file"] = str(path)
                rows.append(row)

    output_csv.parent.mkdir(parents=True, exist_ok=True)
    with output_csv.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)

    return len(rows)



def collect_outputs(output_root: Path, records: list[dict], copy_session_folders: bool = True, collect_track_maps: bool = True) -> None:
    """Create top-level convenience folders with copied session outputs and flat track maps."""
    ok_records = [r for r in records if r.get("status") in {"ok", "skipped_existing"}]

    if collect_track_maps:
        maps_dir = output_root / "all_track_maps"
        maps_dir.mkdir(parents=True, exist_ok=True)
        for r in ok_records:
            out_dir = Path(r["output_dir"])
            prefix = safe_name(r["prefix"])
            for map_file in out_dir.glob("*_track_map.png"):
                dest = maps_dir / f"{prefix}__{map_file.name}"
                shutil.copy2(map_file, dest)

    if copy_session_folders:
        copies_dir = output_root / "session_output_copies"
        copies_dir.mkdir(parents=True, exist_ok=True)
        for r in ok_records:
            out_dir = Path(r["output_dir"])
            prefix = safe_name(r["prefix"])
            dest = copies_dir / prefix
            if dest.exists():
                shutil.rmtree(dest)
            if out_dir.exists():
                shutil.copytree(out_dir, dest)

def write_manifest(output_root: Path, records: list[dict]) -> Path:
    manifest = output_root / "postprocessing_manifest.csv"
    fieldnames = ["status", "session_dir", "output_dir", "prefix", "return_code"]
    with manifest.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(records)
    return manifest


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Recursively find idtracker.ai outputs, run beetle post-processing, and merge summaries."
    )
    p.add_argument("--search-root", required=True, help="Directory to search recursively for idtracker.ai outputs.")
    p.add_argument("--analysis-script", default="analyze_idtracker_beetle.py", help="Path to analyze_idtracker_beetle.py.")
    p.add_argument("--output-root", required=True, help="Root directory where post-processing outputs will be written.")
    p.add_argument("--merged-summary", default=None, help="Output path for merged summary CSV. Default: <output-root>/beetle_behavior_summary_all.csv")
    p.add_argument("--overwrite", action="store_true", help="Re-run even if an output summary already exists.")
    p.add_argument("--dry-run", action="store_true", help="Print commands without running them.")
    p.add_argument("--limit", type=int, default=None, help="Process only the first N detected sessions. Useful for testing.")

    # Core behavior-analysis settings.
    p.add_argument("--analysis-start-frame", type=int, default=1540)
    p.add_argument("--window-frames", type=int, default=7200)
    p.add_argument("--move-threshold-px", type=float, default=30.0)
    p.add_argument("--movement-onset-consecutive-frames", type=int, default=30,
                   help="Require displacement to remain above the movement threshold for this many consecutive frames before scoring sustained onset.")
    p.add_argument("--max-step-px", type=float, default=50.0,
                   help="Maximum allowed frame-to-frame movement before filtering/interpolating likely tracking artifacts. Default: 50 px/frame.")
    p.add_argument("--interpolation-warning-fraction", type=float, default=0.05,
                   help="Flag videos where at least this fraction of position frames are interpolated. Default: 0.05.")
    p.add_argument("--interpolation-warning-frames", type=int, default=300,
                   help="Flag videos where at least this many position frames are interpolated. Default: 300.")
    p.add_argument("--speed-moving-threshold-px-frame", type=float, default=None)
    p.add_argument("--smooth-window", type=int, default=None)
    p.add_argument("--smooth-polyorder", type=int, default=None)

    # Turtling settings.
    p.add_argument("--disable-turtling", action="store_true")
    p.add_argument("--turtling-window-frames", type=int, default=None)
    p.add_argument("--turtling-min-duration-frames", type=int, default=None)
    p.add_argument("--turtling-merge-gap-frames", type=int, default=None)
    p.add_argument("--turtling-max-net-displacement-px", type=float, default=None)
    p.add_argument("--turtling-max-radius-gyration-px", type=float, default=None)
    p.add_argument("--turtling-max-straightness", type=float, default=None)
    p.add_argument("--turtling-min-path-px", type=float, default=None)
    p.add_argument("--turtling-min-abs-turn-rad", type=float, default=None)
    p.add_argument("--turtling-start-buffer-frames", type=int, default=None)

    # Map settings.
    p.add_argument("--full-frame-map", action="store_true")
    p.add_argument("--roi-padding-px", type=float, default=30.0)
    p.add_argument("--roi-wall-buffer-px", type=float, default=50.0,
                   help="Inward ROI border buffer in pixels. Frames outside this border zone are treated as not wall-following.")
    p.add_argument("--track-linewidth", type=float, default=0.25)
    p.add_argument("--turtled-linewidth", type=float, default=0.7)
    p.add_argument("--show-map-points", action="store_true")
    p.add_argument("--no-copy-session-folders", action="store_true", help="Do not create output-root/session_output_copies.")
    p.add_argument("--no-collect-track-maps", action="store_true", help="Do not create output-root/all_track_maps.")

    return p.parse_args()


def main() -> int:
    args = parse_args()
    search_root = Path(args.search_root).expanduser().resolve()
    output_root = Path(args.output_root).expanduser().resolve()
    analysis_script = Path(args.analysis_script).expanduser().resolve()

    if not search_root.exists():
        print(f"ERROR: search root does not exist: {search_root}", file=sys.stderr)
        return 2
    if not analysis_script.exists():
        print(f"ERROR: analysis script does not exist: {analysis_script}", file=sys.stderr)
        return 2

    output_root.mkdir(parents=True, exist_ok=True)
    skip_names = set(DEFAULT_SKIP_DIR_NAMES)
    # Avoid recursively finding outputs inside the output root when output-root is inside search-root.
    skip_names.add(output_root.name)

    session_dirs = find_idtracker_dirs(search_root, skip_names=skip_names)
    if args.limit is not None:
        session_dirs = session_dirs[: args.limit]

    print(f"Found {len(session_dirs)} idtracker.ai output folder(s).")
    if not session_dirs:
        print("No folders containing trajectories.npy, trajectories.h5, or trajectories.csv were found.")
        return 1

    records = []
    failures = 0
    skipped = 0

    for session_dir in session_dirs:
        try:
            rel = session_dir.relative_to(search_root)
        except ValueError:
            rel = Path(session_dir.name)
        prefix = safe_name(rel if str(rel) != "." else session_dir.name)
        output_dir = output_root / prefix

        if not args.overwrite and summary_already_done(output_dir, prefix):
            print(f"Skipping already processed folder: {session_dir}")
            records.append({
                "status": "skipped_existing",
                "session_dir": str(session_dir),
                "output_dir": str(output_dir),
                "prefix": prefix,
                "return_code": 0,
            })
            skipped += 1
            continue

        rc = run_one(analysis_script, session_dir, output_dir, prefix, args)
        status = "ok" if rc == 0 else "failed"
        failures += int(rc != 0)
        records.append({
            "status": status,
            "session_dir": str(session_dir),
            "output_dir": str(output_dir),
            "prefix": prefix,
            "return_code": rc,
        })

    manifest = write_manifest(output_root, records)

    merged_summary = Path(args.merged_summary).expanduser().resolve() if args.merged_summary else output_root / "beetle_behavior_summary_all.csv"
    n_rows = 0
    if not args.dry_run:
        n_rows = merge_summaries(find_summary_files(output_root), merged_summary)
        collect_outputs(
            output_root,
            records,
            copy_session_folders=not args.no_copy_session_folders,
            collect_track_maps=not args.no_collect_track_maps,
        )

    print("\nDone.")
    print(f"Processed: {len(session_dirs) - skipped - failures}")
    print(f"Skipped existing: {skipped}")
    print(f"Failures: {failures}")
    print(f"Manifest: {manifest}")
    if not args.dry_run:
        print(f"Merged summary rows: {n_rows}")
        print(f"Merged summary: {merged_summary}")

    return 0 if failures == 0 else 3


if __name__ == "__main__":
    raise SystemExit(main())
