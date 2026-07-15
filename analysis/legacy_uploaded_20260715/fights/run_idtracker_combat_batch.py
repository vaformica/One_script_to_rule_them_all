#!/usr/bin/env python3
"""
Batch runner for analyze_idtracker_combat.py.

Recursively finds IDtracker.ai trajectory outputs, runs the combat single-session
analysis on each one, and merges pair-level and individual-level summaries.
"""
from __future__ import annotations

import argparse
import csv
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional, Sequence

import pandas as pd

TRAJ_NAMES = [
    # Prefer gap-corrected IDtracker outputs when present, then npy, h5, csv.
    "trajectories_wo_gaps.npy",
    "trajectories_without_gaps.npy",
    "trajectories.npy",
    "trajectories.h5",
    "trajectories.csv",
]


def safe_stem(text: str) -> str:
    import re
    text = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(text)).strip("_")
    return text or "session"


def session_root_for_trajectory(traj_file: Path) -> Path:
    """Return the IDtracker.ai session root for one trajectory file.

    IDtracker.ai often writes several equivalent trajectory formats for the same
    cell/session, for example::

        session_X/trajectories/trajectories.npy
        session_X/trajectories/trajectories_csv/trajectories.csv

    The batch unit should be ``session_X``, not each file-format folder.  We
    therefore climb upward until we find ``session.json``. If that fails, we
    use the parent of a folder named ``trajectories`` as a reasonable fallback.
    """
    cur = traj_file.parent.resolve()
    for parent in [cur] + list(cur.parents):
        if (parent / "session.json").exists():
            return parent
    for parent in [cur] + list(cur.parents):
        if parent.name == "trajectories":
            return parent.parent
    return traj_file.parent


def trajectory_rank(path: Path) -> int:
    """Lower rank is preferred when several trajectory formats exist."""
    name = path.name
    try:
        return TRAJ_NAMES.index(name)
    except ValueError:
        return 999


def find_sessions(search_root: Path) -> List[Dict[str, Path]]:
    """Return one chosen trajectory per IDtracker.ai session/cell.

    This deliberately collapses ``trajectories.npy``, ``trajectories.h5``, and
    ``trajectories_csv/trajectories.csv`` from the same cell/session into one
    batch job. Without this, each cell can be processed multiple times and the
    merged summary will contain repeated rows.
    """
    by_session: Dict[str, Dict[str, Path]] = {}
    for name in TRAJ_NAMES:
        for traj in search_root.rglob(name):
            root = session_root_for_trajectory(traj)
            key = str(root.resolve())
            current = by_session.get(key)
            if current is None or trajectory_rank(traj) < trajectory_rank(current["trajectory_file"]):
                by_session[key] = {"session_folder": root, "trajectory_file": traj}
    return sorted(by_session.values(), key=lambda d: str(d["session_folder"]))


def make_output_subdir(output_root: Path, session_folder: Path) -> Path:
    return output_root / safe_stem(session_folder.name)


def expected_outputs(outdir: Path) -> bool:
    return bool(list(outdir.glob("*_combat_pair_summary.csv"))) and bool(list(outdir.glob("*_combat_individual_summary.csv")))


def merge_csvs(paths: List[Path], output_path: Path) -> None:
    frames = []
    for p in paths:
        try:
            if p.exists() and p.stat().st_size > 0:
                frames.append(pd.read_csv(p))
        except Exception:
            pass
    if frames:
        pd.concat(frames, ignore_index=True, sort=False).to_csv(output_path, index=False)
    else:
        pd.DataFrame().to_csv(output_path, index=False)


def build_command(args: argparse.Namespace, session_folder: Path, trajectory_file: Path, outdir: Path) -> List[str]:
    cmd = [
        sys.executable,
        str(Path(args.analysis_script).expanduser().resolve()),
        "--input-dir", str(session_folder),
        "--trajectories", str(trajectory_file),
        "--output-dir", str(outdir),
        "--analysis-start-frame", str(args.analysis_start_frame),
        "--fps", str(args.fps),
        "--animal0", str(args.animal0),
        "--animal1", str(args.animal1),
        "--contact-px", str(args.contact_px),
        "--min-contact-s", str(args.min_contact_s),
        "--move-threshold-px", str(args.move_threshold_px),
        "--movement-onset-consecutive-frames", str(args.movement_onset_consecutive_frames),
        "--max-step-px", str(args.max_step_px),
        "--interpolation-warning-fraction", str(args.interpolation_warning_fraction),
        "--interpolation-warning-frames", str(args.interpolation_warning_frames),
        "--roi-wall-buffer-px", str(args.roi_wall_buffer_px),
        "--roi-padding-px", str(args.roi_padding_px),
        "--track-linewidth", str(args.track_linewidth),
        "--turtled-linewidth", str(args.turtled_linewidth),
        "--turtling-window-frames", str(args.turtling_window_frames),
        "--turtling-min-duration-frames", str(args.turtling_min_duration_frames),
        "--turtling-merge-gap-frames", str(args.turtling_merge_gap_frames),
        "--turtling-max-net-displacement-px", str(args.turtling_max_net_displacement_px),
        "--turtling-max-radius-gyration-px", str(args.turtling_max_radius_gyration_px),
        "--turtling-max-straightness", str(args.turtling_max_straightness),
        "--turtling-min-path-px", str(args.turtling_min_path_px),
        "--turtling-min-abs-turn-rad", str(args.turtling_min_abs_turn_rad),
        "--turtling-start-buffer-frames", str(args.turtling_start_buffer_frames),
        "--animal0-color", str(args.animal0_color),
        "--animal1-color", str(args.animal1_color),
        "--turtled-color", str(args.turtled_color),
        "--interpolated-color", str(args.interpolated_color),
        "--map-max-overlay-points", str(args.map_max_overlay_points),
    ]
    if args.window_frames is not None:
        cmd += ["--window-frames", str(args.window_frames)]
    if args.min_contact_frames is not None:
        cmd += ["--min-contact-frames", str(args.min_contact_frames)]
    if args.fight_px is not None:
        cmd += ["--fight-px", str(args.fight_px)]
    else:
        cmd += ["--fight-px", "0"]
    cmd += ["--min-fight-frames", str(args.min_fight_frames)]
    if args.metadata_csv:
        cmd += ["--metadata-csv", str(Path(args.metadata_csv).expanduser().resolve())]
    if args.roi_toml:
        cmd += ["--roi-toml", str(Path(args.roi_toml).expanduser().resolve())]
    if args.speed_moving_threshold_px_frame is not None:
        cmd += ["--speed-moving-threshold-px-frame", str(args.speed_moving_threshold_px_frame)]
    if args.disable_turtling:
        cmd += ["--disable-turtling"]
    if args.show_map_points:
        cmd += ["--show-map-points"]
    if args.full_frame_map:
        cmd += ["--full-frame-map"]
    return cmd


def run_batch(args: argparse.Namespace) -> int:
    search_root = Path(args.search_root).expanduser().resolve()
    output_root = Path(args.output_root).expanduser().resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    sessions = find_sessions(search_root)
    if args.limit is not None:
        sessions = sessions[:args.limit]

    if args.make_manifest_only:
        rows = [{"session_folder": str(x["session_folder"]), "trajectory_file": str(x["trajectory_file"]), "output_dir": str(make_output_subdir(output_root, x["session_folder"]))} for x in sessions]
        pd.DataFrame(rows).to_csv(output_root / "postprocessing_manifest_preview.csv", index=False)
        print(f"Wrote manifest preview for {len(rows)} sessions: {output_root / 'postprocessing_manifest_preview.csv'}")
        return 0

    manifest_rows: List[Dict[str, object]] = []
    pair_summaries: List[Path] = []
    individual_summaries: List[Path] = []
    all_maps = output_root / "all_track_maps"
    if not args.no_collect_track_maps:
        all_maps.mkdir(exist_ok=True)

    for i, item in enumerate(sessions, start=1):
        session_folder = item["session_folder"]
        trajectory_file = item["trajectory_file"]
        outdir = make_output_subdir(output_root, session_folder)
        outdir.mkdir(parents=True, exist_ok=True)
        if expected_outputs(outdir) and not args.overwrite:
            status = "skipped_existing"
            rc = 0
            stdout = ""
            stderr = ""
        else:
            cmd = build_command(args, session_folder, trajectory_file, outdir)
            print(f"[{i}/{len(sessions)}] {session_folder}  [{trajectory_file.name}]")
            proc = subprocess.run(cmd, text=True, capture_output=True)
            status = "ok" if proc.returncode == 0 else "error"
            rc = proc.returncode
            stdout = proc.stdout[-4000:]
            stderr = proc.stderr[-4000:]
            (outdir / "run_command.txt").write_text(" ".join(cmd) + "\n", encoding="utf-8")
            (outdir / "stdout.txt").write_text(proc.stdout, encoding="utf-8")
            (outdir / "stderr.txt").write_text(proc.stderr, encoding="utf-8")
        ps = list(outdir.glob("*_combat_pair_summary.csv"))
        inds = list(outdir.glob("*_combat_individual_summary.csv"))
        pair_summaries.extend(ps)
        individual_summaries.extend(inds)
        if not args.no_collect_track_maps:
            for png in outdir.glob("*_track_map.png"):
                dest = all_maps / f"{outdir.name}__{png.name}"
                try:
                    shutil.copy2(png, dest)
                except Exception:
                    pass
        manifest_rows.append({
            "session_folder": str(session_folder),
            "trajectory_file": str(trajectory_file),
            "output_dir": str(outdir),
            "status": status,
            "return_code": rc,
            "pair_summary_count": len(ps),
            "individual_summary_count": len(inds),
            "stdout_tail": stdout,
            "stderr_tail": stderr,
        })

    manifest = output_root / "postprocessing_manifest.csv"
    pd.DataFrame(manifest_rows).to_csv(manifest, index=False)
    merge_csvs(pair_summaries, Path(args.merged_pair_summary) if args.merged_pair_summary else output_root / "combat_pair_summary_all.csv")
    merge_csvs(individual_summaries, Path(args.merged_individual_summary) if args.merged_individual_summary else output_root / "combat_individual_summary_all.csv")
    print(f"Done. Manifest: {manifest}")
    print(f"Pair summary: {Path(args.merged_pair_summary) if args.merged_pair_summary else output_root / 'combat_pair_summary_all.csv'}")
    print(f"Individual summary: {Path(args.merged_individual_summary) if args.merged_individual_summary else output_root / 'combat_individual_summary_all.csv'}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Batch-run two-beetle IDtracker.ai combat post-processing.")
    p.add_argument("--search-root", required=True, help="Top-level folder searched recursively for IDtracker.ai trajectory outputs.")
    p.add_argument("--analysis-script", default=str(Path(__file__).with_name("analyze_idtracker_combat.py")), help="Path to analyze_idtracker_combat.py.")
    p.add_argument("--output-root", required=True, help="Folder where all post-processing outputs are written.")
    p.add_argument("--merged-pair-summary", default=None, help="Optional path for merged one-row-per-fight summary.")
    p.add_argument("--merged-individual-summary", default=None, help="Optional path for merged two-rows-per-fight summary.")
    p.add_argument("--overwrite", action="store_true", help="Re-run sessions even when outputs already exist.")
    p.add_argument("--limit", type=int, default=None, help="Process only first N discovered sessions.")
    p.add_argument("--make-manifest-only", action="store_true", help="Only write a preview of discovered sessions and exit.")

    p.add_argument("--metadata-csv", default=None, help="Optional CSV with session/beetle metadata.")
    p.add_argument("--roi-toml", default=None, help="Optional shared ROI TOML file for all sessions.")
    p.add_argument("--animal0", type=int, default=0)
    p.add_argument("--animal1", type=int, default=1)
    p.add_argument("--analysis-start-frame", type=int, default=0, help="First global frame to analyze for every session. Default is 0; IDtracker.ai pre-start NaN frames remain missing/invalid in the analysis.")
    p.add_argument("--window-frames", type=int, default=None)
    p.add_argument("--fps", type=float, default=30.0)
    p.add_argument("--contact-px", type=float, default=60.0)
    p.add_argument("--min-contact-s", type=float, default=0.2)
    p.add_argument("--min-contact-frames", type=int, default=None)
    p.add_argument("--fight-px", type=float, default=35.0)
    p.add_argument("--min-fight-frames", type=int, default=6)
    p.add_argument("--move-threshold-px", type=float, default=30.0)
    p.add_argument("--movement-onset-consecutive-frames", type=int, default=30)
    p.add_argument("--max-step-px", type=float, default=50.0)
    p.add_argument("--interpolation-warning-fraction", type=float, default=0.05)
    p.add_argument("--interpolation-warning-frames", type=int, default=300)
    p.add_argument("--speed-moving-threshold-px-frame", type=float, default=None)
    p.add_argument("--roi-wall-buffer-px", type=float, default=50.0)
    p.add_argument("--roi-padding-px", type=float, default=30.0)
    p.add_argument("--track-linewidth", type=float, default=0.4)
    p.add_argument("--turtled-linewidth", type=float, default=1.2)
    p.add_argument("--show-map-points", action="store_true")
    p.add_argument("--full-frame-map", action="store_true")
    p.add_argument("--animal0-color", default="tab:orange")
    p.add_argument("--animal1-color", default="tab:blue")
    p.add_argument("--turtled-color", default="black")
    p.add_argument("--interpolated-color", default="0.55")
    p.add_argument("--map-max-overlay-points", type=int, default=400)
    p.add_argument("--no-collect-track-maps", action="store_true")
    p.add_argument("--disable-turtling", action="store_true")
    p.add_argument("--turtling-window-frames", type=int, default=300)
    p.add_argument("--turtling-min-duration-frames", type=int, default=300)
    p.add_argument("--turtling-merge-gap-frames", type=int, default=60)
    p.add_argument("--turtling-max-net-displacement-px", type=float, default=80.0)
    p.add_argument("--turtling-max-radius-gyration-px", type=float, default=50.0)
    p.add_argument("--turtling-max-straightness", type=float, default=0.25)
    p.add_argument("--turtling-min-path-px", type=float, default=80.0)
    p.add_argument("--turtling-min-abs-turn-rad", type=float, default=0.30)
    p.add_argument("--turtling-start-buffer-frames", type=int, default=300)
    return p


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    return run_batch(args)


if __name__ == "__main__":
    raise SystemExit(main())
