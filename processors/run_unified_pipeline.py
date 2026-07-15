from __future__ import annotations
import argparse
import subprocess
import sys
from pathlib import Path

from collector.metadata_injector import enrich_tree
from pipeline.run_metadata import RunMetadata


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--analysis-type",
        choices=["auto", "ba", "fight"],
        required=True,
    )
    parser.add_argument("--session-path", required=True)
    parser.add_argument("--output-root", required=True)
    parser.add_argument("--run-metadata-json", required=True)
    parser.add_argument("--window-frames", type=int, default=7200)
    parser.add_argument("--fps", type=float, default=30.0)
    parser.add_argument("--contact-px", type=float, default=60.0)
    parser.add_argument("--min-contact-s", type=float, default=0.2)
    parser.add_argument("--fight-px", type=float, default=35.0)
    parser.add_argument("--min-fight-frames", type=int, default=6)
    parser.add_argument("--move-threshold-px", type=float, default=30.0)
    parser.add_argument(
        "--movement-onset-consecutive-frames",
        type=int,
        default=30,
    )
    parser.add_argument("--roi-wall-buffer-px", type=float, default=30.0)
    parser.add_argument("--max-step-px", type=float, default=50.0)
    args, extra = parser.parse_known_args()

    repo_root = Path(__file__).resolve().parents[1]
    command = [
        sys.executable,
        str(repo_root / "analysis/run_idtracker_unified_batch.py"),
        "--search-root", args.session_path,
        "--output-root", args.output_root,
        "--analysis-type", args.analysis_type,
        "--analysis-start-frame", "0",
        "--window-frames", str(args.window_frames),
        "--fps", str(args.fps),
        "--contact-px", str(args.contact_px),
        "--min-contact-s", str(args.min_contact_s),
        "--fight-px", str(args.fight_px),
        "--min-fight-frames", str(args.min_fight_frames),
        "--move-threshold-px", str(args.move_threshold_px),
        "--movement-onset-consecutive-frames",
        str(args.movement_onset_consecutive_frames),
        "--roi-wall-buffer-px", str(args.roi_wall_buffer_px),
        "--max-step-px", str(args.max_step_px),
        "--overwrite",
        *extra,
    ]
    subprocess.run(command, check=True)

    metadata = RunMetadata.from_json(args.run_metadata_json)
    counts = enrich_tree(Path(args.output_root), metadata)
    print(counts)


if __name__ == "__main__":
    main()
