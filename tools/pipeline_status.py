#!/usr/bin/env python3
"""Summarize Firebird IDtracker TOML-folder pipeline status.

This script is intentionally dependency-light so it can be called from the GUI,
from SLURM, or by a student in a terminal. It reads the manifest, collection
report, post-processing report, and completion/failure markers, then writes a
human-readable summary and a machine-readable JSON file.
"""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Dict, List, Any, Optional


def read_csv(path: Path) -> List[Dict[str, str]]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as f:
        return [{k: str(v or "") for k, v in row.items()} for row in csv.DictReader(f)]


def count_by(rows: List[Dict[str, str]], col: str) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for r in rows:
        key = r.get(col, "") or "blank"
        out[key] = out.get(key, 0) + 1
    return out


def first_existing(paths: List[Path]) -> Optional[Path]:
    for p in paths:
        if p.exists():
            return p
    return None


def summarize(toml_folder: Path) -> Dict[str, Any]:
    pm = toml_folder / "project_metadata"
    manifest_path = pm / "toml_video_manifest.csv"
    import_path = pm / "toml_import_grid.csv"
    collect_path = pm / "session_collection_report.csv"
    summary_json = pm / "pipeline_status_summary.json"
    summary_txt = pm / "pipeline_status_summary.txt"

    manifest = read_csv(manifest_path)
    imported = read_csv(import_path)
    collection = read_csv(collect_path)

    pipeline = manifest[0].get("pipeline", "") if manifest else ""
    post_root = toml_folder / "postprocessing" / (f"{pipeline}_postprocessing" if pipeline else "")
    post_manifest_path = post_root / "postprocessing_manifest.csv" if pipeline else Path("__missing__")
    post_status_path = post_root / "postprocessing_status_by_cell.csv" if pipeline else Path("__missing__")
    post_manifest = read_csv(post_manifest_path)
    post_status = read_csv(post_status_path)

    complete_marker = first_existing(list((toml_folder / "postprocessing").glob("*/_POSTPROCESS_COMPLETE_ALL_CELLS.txt")))
    incomplete_marker = first_existing(list((toml_folder / "postprocessing").glob("*/_POSTPROCESS_INCOMPLETE_OR_FAILED.txt")))
    collect_complete = pm / "_SESSION_COLLECTION_COMPLETE_ALL_CELLS.txt"
    collect_failed = pm / "_SESSION_COLLECTION_INCOMPLETE_OR_FAILED.txt"

    expected = len(manifest)
    ready = sum(1 for r in manifest if r.get("validation_status") == "ready")
    collected = sum(1 for r in collection if r.get("trajectory_found") == "YES")
    processed_ok = sum(1 for r in post_manifest if r.get("status") in {"ok", "processed", "skipped_existing"})

    # If the explicit status-by-cell table exists, use it for processed count.
    if post_status:
        processed_ok = sum(1 for r in post_status if r.get("found_in_postprocessing_manifest") == "YES" and r.get("status") in {"seen", "ok", "processed", "skipped_existing"})

    errors: List[str] = []
    warnings: List[str] = []
    if not manifest_path.exists():
        errors.append("E100: missing project_metadata/toml_video_manifest.csv. Import/validate the TOML folder before running.")
    elif expected == 0:
        errors.append("E101: manifest exists but has zero rows.")
    elif ready != expected:
        warnings.append(f"W110: only {ready}/{expected} manifest rows are marked ready.")

    collection_started = collect_path.exists() or collect_complete.exists() or collect_failed.exists()
    post_started = post_manifest_path.exists() or post_status_path.exists() or bool(complete_marker) or bool(incomplete_marker)

    if expected and not collection_started:
        warnings.append("W200: session collection has not run yet or has not written its report. This is normal while IDtracker jobs are still running or while the collector is pending.")
    elif expected and collected != expected:
        if collect_failed.exists():
            errors.append(f"E210: session collection incomplete: {collected}/{expected} cells have trajectories.")
        else:
            warnings.append(f"W210: session collection report exists but only {collected}/{expected} cells have trajectories so far.")

    if collect_failed.exists():
        errors.append(f"E211: session collection failure marker exists: {collect_failed}")
    if collect_complete.exists() and collected == expected:
        pass

    if expected and collected == expected and not post_started:
        warnings.append("W300: all sessions collected, but post-processing has not written status files yet. This is normal while the post-processing job is pending or running.")
    elif expected and post_started and processed_ok != expected:
        if incomplete_marker:
            errors.append(f"E310: post-processing incomplete: {processed_ok}/{expected} cells processed successfully.")
        else:
            warnings.append(f"W310: post-processing has started but only {processed_ok}/{expected} cells are marked processed so far.")

    if incomplete_marker:
        errors.append(f"E311: post-processing failure marker exists: {incomplete_marker}")
    if complete_marker and processed_ok == expected:
        pass

    if errors:
        overall = "FAILED_OR_INCOMPLETE"
    elif expected and collected == expected and processed_ok == expected and complete_marker:
        overall = "COMPLETE"
    elif expected and collected == expected:
        overall = "TRACKING_AND_COLLECTION_COMPLETE_POSTPROCESS_PENDING"
    else:
        overall = "RUNNING_OR_PENDING"

    data: Dict[str, Any] = {
        "toml_folder": str(toml_folder),
        "pipeline": pipeline,
        "overall_status": overall,
        "expected_cells": expected,
        "ready_cells": ready,
        "collected_cells_with_trajectories": collected,
        "postprocessed_cells_ok": processed_ok,
        "manifest_path": str(manifest_path),
        "collection_report_path": str(collect_path),
        "postprocessing_manifest_path": str(post_manifest_path),
        "postprocessing_status_path": str(post_status_path),
        "complete_marker": str(complete_marker) if complete_marker else "",
        "incomplete_marker": str(incomplete_marker) if incomplete_marker else "",
        "collection_status_counts": count_by(collection, "status"),
        "postprocessing_status_counts": count_by(post_manifest, "status"),
        "errors": errors,
        "warnings": warnings,
    }

    lines = [
        "Firebird IDtracker pipeline status summary",
        "==========================================",
        f"TOML folder: {toml_folder}",
        f"Pipeline: {pipeline or 'unknown'}",
        f"Overall status: {overall}",
        "",
        f"Expected cells/TOMLs: {expected}",
        f"Ready manifest rows: {ready}/{expected}",
        f"Collected sessions with trajectories: {collected}/{expected}",
        f"Post-processed cells OK: {processed_ok}/{expected}",
        "",
        "Important files:",
        f"  Manifest: {manifest_path} {'[FOUND]' if manifest_path.exists() else '[MISSING]'}",
        f"  Collection report: {collect_path} {'[FOUND]' if collect_path.exists() else '[MISSING]'}",
        f"  Post-processing manifest: {post_manifest_path} {'[FOUND]' if post_manifest_path.exists() else '[MISSING]'}",
        f"  Post-processing status: {post_status_path} {'[FOUND]' if post_status_path.exists() else '[MISSING]'}",
        f"  Complete marker: {complete_marker or '[none]'}",
        f"  Incomplete marker: {incomplete_marker or '[none]'}",
        "",
        "Warnings:",
    ]
    lines += [f"  {w}" for w in warnings] or ["  none"]
    lines.append("")
    lines.append("Errors:")
    lines += [f"  {e}" for e in errors] or ["  none"]

    summary_json.write_text(json.dumps(data, indent=2), encoding="utf-8")
    summary_txt.write_text("\n".join(lines) + "\n", encoding="utf-8")
    data["summary_text"] = "\n".join(lines)
    return data


def main() -> int:
    ap = argparse.ArgumentParser(description="Summarize the status of an IDtracker TOML-folder pipeline run.")
    ap.add_argument("--toml-folder", required=True)
    ap.add_argument("--json", action="store_true", help="Print JSON instead of human-readable text.")
    args = ap.parse_args()
    data = summarize(Path(args.toml_folder).expanduser().resolve())
    if args.json:
        print(json.dumps({k: v for k, v in data.items() if k != "summary_text"}, indent=2))
    else:
        print(data["summary_text"])
    return 0 if data["overall_status"] == "COMPLETE" else 1


if __name__ == "__main__":
    raise SystemExit(main())
