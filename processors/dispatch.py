from __future__ import annotations
import argparse
import subprocess
import sys
from pathlib import Path


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
    args, extra = parser.parse_known_args()

    script = Path(__file__).with_name("run_unified_pipeline.py")
    env = dict(__import__("os").environ)
    repo_root = str(Path(__file__).resolve().parents[1])
    env["PYTHONPATH"] = repo_root + ((":" + env["PYTHONPATH"]) if env.get("PYTHONPATH") else "")
    subprocess.run(
        [
            sys.executable,
            str(script),
            "--analysis-type", args.analysis_type,
            "--session-path", args.session_path,
            "--output-root", args.output_root,
            "--run-metadata-json", args.run_metadata_json,
            *extra,
        ],
        check=True,
        env=env,
    )


if __name__ == "__main__":
    main()
