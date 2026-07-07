#!/usr/bin/env python3
"""Run fight post-processing without the GUI.

This script uses the GUI-created metadata sheets in a TOML folder and the
already-collected IDtracker session folders. It is for rerunning the Python
post-processing stage only. It does not run IDtracker.ai and does not move
session folders.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import shlex
import subprocess
import sys


def package_dir() -> Path:
    return Path(__file__).resolve().parents[1]


def build_command(args: argparse.Namespace) -> list[str]:
    toml_folder = Path(args.toml_folder).expanduser().resolve()
    pkg = package_dir()
    return [
        sys.executable, str(pkg / "postprocess" / "idtracker_unified_postprocess.py"),
        "--pipeline", "fight",
        "--search-root", str(toml_folder / "idtracker_sessions"),
        "--output-root", str(toml_folder / "postprocessing" / "fight_postprocessing"),
        "--metadata-manifest", str(toml_folder / "project_metadata" / "toml_video_manifest.csv"),
        "--toml-folder", str(toml_folder),
        "--analysis-start-frame", "-1",
        "--window-frames", str(args.window_frames),
        "--fps", str(args.fps),
        "--move-threshold-px", str(args.move_threshold_px),
        "--movement-onset-consecutive-frames", str(args.movement_onset_consecutive_frames),
        "--max-step-px", str(args.max_step_px),
        "--roi-wall-buffer-px", str(args.roi_wall_buffer_px),
        "--turtling-window-frames", str(args.turtling_window_frames),
        "--turtling-min-duration-frames", str(args.turtling_min_duration_frames),
        "--animal0", str(args.animal0),
        "--animal1", str(args.animal1),
        "--contact-px", str(args.contact_px),
        "--min-contact-s", str(args.min_contact_s),
        "--fight-px", str(args.fight_px),
        "--min-fight-frames", str(args.min_fight_frames),
        "--overwrite",
    ]


def main() -> int:
    p = argparse.ArgumentParser(description="Standalone fight post-processing rerun using a GUI-created TOML folder.")
    p.add_argument("--toml-folder", required=True, help="Folder containing TOMLs, project_metadata, idtracker_sessions, and postprocessing.")
    p.add_argument("--submit-cpu", action="store_true", help="Submit this post-processing rerun to SLURM on a CPU partition instead of running in this shell.")
    p.add_argument("--cpu-partition", default="", help="CPU partition for --submit-cpu. Leave blank to omit --partition and use the cluster default.")
    p.add_argument("--time", default="02:00:00", help="SLURM time limit for --submit-cpu.")
    p.add_argument("--mem", default="32G", help="SLURM memory for --submit-cpu.")
    p.add_argument("--cpus", default="4", help="SLURM CPUs for --submit-cpu.")
    p.add_argument("--window-frames", type=int, default=7500)
    p.add_argument("--fps", type=float, default=30.0)
    p.add_argument("--move-threshold-px", type=float, default=30.0)
    p.add_argument("--movement-onset-consecutive-frames", type=int, default=30)
    p.add_argument("--max-step-px", type=float, default=50.0)
    p.add_argument("--roi-wall-buffer-px", type=float, default=30.0)
    p.add_argument("--turtling-window-frames", type=int, default=300)
    p.add_argument("--turtling-min-duration-frames", type=int, default=300)
    p.add_argument("--animal0", type=int, default=0)
    p.add_argument("--animal1", type=int, default=1)
    p.add_argument("--contact-px", type=float, default=60.0)
    p.add_argument("--min-contact-s", type=float, default=0.2)
    p.add_argument("--fight-px", type=float, default=35.0)
    p.add_argument("--min-fight-frames", type=int, default=6)
    args = p.parse_args()

    cmd = build_command(args)
    toml_folder = Path(args.toml_folder).expanduser().resolve()
    if not args.submit_cpu:
        print("Running:", " ".join(map(str, cmd)))
        return subprocess.call(cmd, cwd=str(package_dir()))

    logs = toml_folder / "logs"
    logs.mkdir(exist_ok=True)
    sbatch = ["sbatch", "--job-name", "fight_postprocess_cpu", "--chdir", str(toml_folder), "--cpus-per-task", str(args.cpus), "--mem", str(args.mem), "--time", str(args.time), "--output", str(logs / "fight_postprocess_cpu_%j.out"), "--error", str(logs / "fight_postprocess_cpu_%j.err")]
    if args.cpu_partition.strip():
        sbatch += ["--partition", args.cpu_partition.strip()]
    sbatch += ["--wrap", shlex.join([str(x) for x in cmd])]
    print("Submitting:", " ".join(sbatch))
    return subprocess.call(sbatch)


if __name__ == "__main__":
    raise SystemExit(main())
