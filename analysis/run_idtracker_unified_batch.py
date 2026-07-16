#!/usr/bin/env python3
"""Unified recursive batch runner for BA and fight IDtracker.ai sessions."""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Dict, List

import pandas as pd


TRAJ_NAMES = [
    "trajectories_wo_gaps.npy",
    "trajectories_without_gaps.npy",
    "trajectories.npy",
    "trajectories.h5",
    "trajectories.csv",
]


def safe_stem(text: str) -> str:
    import re
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", str(text)).strip("_") or "session"


def session_root_for_trajectory(path: Path) -> Path:
    current = path.parent.resolve()
    for parent in [current] + list(current.parents):
        if (parent / "session.json").exists():
            return parent
    for parent in [current] + list(current.parents):
        if parent.name == "trajectories":
            return parent.parent
    return path.parent


def trajectory_rank(path: Path) -> int:
    try:
        return TRAJ_NAMES.index(path.name)
    except ValueError:
        return 999


def find_sessions(search_root: Path) -> List[Dict[str, Path]]:
    by_session: Dict[str, Dict[str, Path]] = {}
    for name in TRAJ_NAMES:
        for trajectory in search_root.rglob(name):
            session = session_root_for_trajectory(trajectory)
            key = str(session.resolve())
            current = by_session.get(key)
            if (
                current is None
                or trajectory_rank(trajectory)
                < trajectory_rank(current["trajectory_file"])
            ):
                by_session[key] = {
                    "session_folder": session,
                    "trajectory_file": trajectory,
                }
    return sorted(
        by_session.values(),
        key=lambda item: str(item["session_folder"]),
    )


def merge_csvs(paths: List[Path], destination: Path) -> None:
    frames = []
    for path in paths:
        try:
            if path.exists() and path.stat().st_size:
                frames.append(pd.read_csv(path))
        except Exception:
            pass
    if frames:
        pd.concat(frames, ignore_index=True, sort=False).to_csv(
            destination, index=False
        )
    else:
        pd.DataFrame().to_csv(destination, index=False)


def build_command(args, session_folder, trajectory_file, output_dir):
    command = [
        sys.executable,
        str(Path(args.analysis_script).expanduser().resolve()),
        "--analysis-type", args.analysis_type,
        "--input-dir", str(session_folder),
        "--trajectories", str(trajectory_file),
        "--output-dir", str(output_dir),
        "--analysis-start-frame", str(args.analysis_start_frame),
        "--fps", str(args.fps),
        "--animal0", str(args.animal0),
        "--animal1", str(args.animal1),
        "--contact-px", str(args.contact_px),
        "--min-contact-s", str(args.min_contact_s),
        "--fight-px", str(args.fight_px),
        "--min-fight-frames", str(args.min_fight_frames),
        "--move-threshold-px", str(args.move_threshold_px),
        "--movement-onset-consecutive-frames",
        str(args.movement_onset_consecutive_frames),
        "--max-step-px", str(args.max_step_px),
        "--roi-wall-buffer-px", str(args.roi_wall_buffer_px),
        "--roi-padding-px", str(args.roi_padding_px),
        "--track-linewidth", str(args.track_linewidth),
        "--turtling-window-frames", str(args.turtling_window_frames),
        "--turtling-min-duration-frames",
        str(args.turtling_min_duration_frames),
        "--turtling-merge-gap-frames",
        str(args.turtling_merge_gap_frames),
        "--turtling-max-net-displacement-px",
        str(args.turtling_max_net_displacement_px),
        "--turtling-max-radius-gyration-px",
        str(args.turtling_max_radius_gyration_px),
        "--turtling-max-straightness",
        str(args.turtling_max_straightness),
        "--turtling-min-path-px", str(args.turtling_min_path_px),
        "--turtling-min-abs-turn-rad",
        str(args.turtling_min_abs_turn_rad),
        "--turtling-start-buffer-frames",
        str(args.turtling_start_buffer_frames),
        "--animal0-color", args.animal0_color,
        "--animal1-color", args.animal1_color,
        "--turtled-color", args.turtled_color,
        "--interpolated-color", args.interpolated_color,
        "--map-max-overlay-points", str(args.map_max_overlay_points),
    ]
    if args.window_frames is not None:
        command += ["--window-frames", str(args.window_frames)]
    if args.metadata_csv:
        command += ["--metadata-csv", str(Path(args.metadata_csv).resolve())]
    if args.roi_toml:
        command += ["--roi-toml", str(Path(args.roi_toml).resolve())]
    if args.disable_turtling:
        command += ["--disable-turtling"]
    if args.show_map_points:
        command += ["--show-map-points"]
    if args.full_frame_map:
        command += ["--full-frame-map"]
    return command


def run(args) -> int:
    search_root = Path(args.search_root).expanduser().resolve()
    output_root = Path(args.output_root).expanduser().resolve()
    output_root.mkdir(parents=True, exist_ok=True)

    sessions = find_sessions(search_root)
    if args.limit is not None:
        sessions = sessions[: args.limit]

    manifest = []
    ba_summaries = []
    fight_pair_summaries = []
    fight_individual_summaries = []
    all_maps = output_root / "all_track_maps"
    all_maps.mkdir(exist_ok=True)

    for number, item in enumerate(sessions, start=1):
        session = item["session_folder"]
        trajectory = item["trajectory_file"]
        output_dir = output_root / safe_stem(session.name)

        if output_dir.exists() and args.overwrite:
            shutil.rmtree(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        command = build_command(
            args, session, trajectory, output_dir
        )
        result = subprocess.run(
            command,
            text=True,
            capture_output=True,
        )
        row = {
            "batch_number": number,
            "session_folder": str(session),
            "trajectory_file": str(trajectory),
            "output_dir": str(output_dir),
            "return_code": result.returncode,
            "status": "success" if result.returncode == 0 else "failed",
            "stdout": result.stdout,
            "stderr": result.stderr,
        }
        manifest.append(row)

        if result.returncode != 0:
            print(f"FAILED: {session}\n{result.stderr}", file=sys.stderr)
            if args.stop_on_error:
                break
            continue

        ba_summaries.extend(output_dir.glob("*_ba_individual_summary.csv"))
        fight_pair_summaries.extend(
            output_dir.glob("*_combat_pair_summary.csv")
        )
        fight_individual_summaries.extend(
            output_dir.glob("*_combat_individual_summary.csv")
        )

        for map_path in output_dir.glob("*track_map*.png"):
            destination = (
                all_maps
                / f"{safe_stem(session.name)}__{map_path.name}"
            )
            shutil.copy2(map_path, destination)

    pd.DataFrame(manifest).to_csv(
        output_root / "postprocessing_manifest.csv",
        index=False,
    )
    merge_csvs(
        ba_summaries,
        output_root / "ba_individual_summary_all.csv",
    )
    merge_csvs(
        fight_pair_summaries,
        output_root / "combat_pair_summary_all.csv",
    )
    merge_csvs(
        fight_individual_summaries,
        output_root / "combat_individual_summary_all.csv",
    )

    summary = {
        "sessions_found": len(sessions),
        "sessions_successful": sum(
            row["status"] == "success" for row in manifest
        ),
        "sessions_failed": sum(
            row["status"] == "failed" for row in manifest
        ),
        "ba_summary_files": len(ba_summaries),
        "fight_pair_summary_files": len(fight_pair_summaries),
        "fight_individual_summary_files": len(
            fight_individual_summaries
        ),
        "track_maps_collected": len(list(all_maps.glob("*.png"))),
    }
    (output_root / "batch_summary.json").write_text(
        json.dumps(summary, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(summary, indent=2))
    return 1 if summary["sessions_failed"] else 0


def parser():
    p = argparse.ArgumentParser(
        description=(
            "Recursively process BA and fight IDtracker.ai sessions "
            "through one shared analysis and plotting stream."
        )
    )
    p.add_argument("--search-root", required=True)
    p.add_argument("--output-root", required=True)
    p.add_argument(
        "--analysis-script",
        default=str(
            Path(__file__).with_name("analyze_idtracker_unified.py")
        ),
    )
    p.add_argument(
        "--analysis-type",
        choices=["auto", "ba", "fight"],
        default="auto",
    )
    p.add_argument("--analysis-start-frame", type=int, default=0)
    p.add_argument("--window-frames", type=int, default=7500)
    p.add_argument("--fps", type=float, default=30.0)
    p.add_argument("--animal0", type=int, default=0)
    p.add_argument("--animal1", type=int, default=1)
    p.add_argument("--contact-px", type=float, default=60.0)
    p.add_argument("--min-contact-s", type=float, default=0.2)
    p.add_argument("--fight-px", type=float, default=35.0)
    p.add_argument("--min-fight-frames", type=int, default=6)
    p.add_argument("--move-threshold-px", type=float, default=30.0)
    p.add_argument(
        "--movement-onset-consecutive-frames",
        type=int,
        default=30,
    )
    p.add_argument("--max-step-px", type=float, default=50.0)
    p.add_argument("--roi-wall-buffer-px", type=float, default=30.0)
    p.add_argument("--roi-padding-px", type=float, default=30.0)
    p.add_argument("--track-linewidth", type=float, default=0.4)
    p.add_argument("--animal0-color", default="tab:orange")
    p.add_argument("--animal1-color", default="tab:blue")
    p.add_argument("--turtled-color", default="black")
    p.add_argument("--interpolated-color", default="0.55")
    p.add_argument("--map-max-overlay-points", type=int, default=400)
    p.add_argument("--turtling-window-frames", type=int, default=300)
    p.add_argument(
        "--turtling-min-duration-frames",
        type=int,
        default=300,
    )
    p.add_argument("--turtling-merge-gap-frames", type=int, default=60)
    p.add_argument(
        "--turtling-max-net-displacement-px",
        type=float,
        default=80.0,
    )
    p.add_argument(
        "--turtling-max-radius-gyration-px",
        type=float,
        default=50.0,
    )
    p.add_argument(
        "--turtling-max-straightness",
        type=float,
        default=0.25,
    )
    p.add_argument("--turtling-min-path-px", type=float, default=80.0)
    p.add_argument(
        "--turtling-min-abs-turn-rad",
        type=float,
        default=0.30,
    )
    p.add_argument(
        "--turtling-start-buffer-frames",
        type=int,
        default=300,
    )
    p.add_argument("--metadata-csv")
    p.add_argument("--roi-toml")
    p.add_argument("--disable-turtling", action="store_true")
    p.add_argument("--show-map-points", action="store_true")
    p.add_argument("--full-frame-map", action="store_true")
    p.add_argument("--overwrite", action="store_true")
    p.add_argument("--stop-on-error", action="store_true")
    p.add_argument("--limit", type=int)
    return p


def main():
    raise SystemExit(run(parser().parse_args()))


if __name__ == "__main__":
    main()
