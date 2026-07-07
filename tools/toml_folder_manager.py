#!/usr/bin/env python3
"""
TOML-folder metadata manager for the Firebird IDtracker.ai launcher.

Simplified workflow
-------------------
A student manually creates TOML files in idtracker.ai's Segmentation app.
Then this tool imports a single source video plus a folder containing all TOMLs
for that video.  Each TOML is one analysis unit, because a single video can
contain multiple arenas/cells.

The provenance backbone is:
    original video + TOML file -> IDtracker session -> post-processing outputs

Every post-processing row gets the columns in MANIFEST_COLUMNS so later
concatenated spreadsheets remain traceable to the original video and TOML.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import os
from pathlib import Path
import re
import shlex
import sys
from typing import Any, Dict, Iterable, List, Tuple

try:
    import tomllib  # Python 3.11+
except Exception:  # pragma: no cover
    import tomli as tomllib  # type: ignore

VIDEO_EXTENSIONS = {".avi", ".mp4", ".mov", ".mkv", ".m4v"}

MANIFEST_COLUMNS = [
    "project_id", "project_name", "project_folder", "metadata_tag", "pipeline",
    "video_id", "original_video_path", "original_video_name", "original_video_stem",
    "video_size_bytes", "video_mtime_iso",
    "cell_id", "cell_label", "cell_notes", "run_this_toml", "expected_animals",
    "toml_folder", "toml_path", "toml_name", "toml_stem", "output_stem",
    "toml_exists", "toml_video_path", "toml_video_matches_registered_video",
    "toml_expected_animals_found", "toml_tracking_interval_start_frame", "toml_tracking_interval_end_frame", "toml_analysis_start_frame", "toml_roi_or_polygon_found",
    "validation_status", "validation_message", "idtracker_session_name",
]


def now_iso() -> str:
    return dt.datetime.now().replace(microsecond=0).isoformat()


def safe_stem(text: str) -> str:
    text = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(text)).strip("_")
    return text or "unnamed"


def metadata_dir(toml_folder: Path) -> Path:
    return toml_folder / "project_metadata"


def manifest_path(toml_folder: Path) -> Path:
    return metadata_dir(toml_folder) / "toml_video_manifest.csv"


def all_tomls_path(toml_folder: Path) -> Path:
    return metadata_dir(toml_folder) / "toml_import_grid.csv"


def config_path(toml_folder: Path) -> Path:
    return metadata_dir(toml_folder) / "run_config.json"


def read_csv(path: Path, columns: List[str] = MANIFEST_COLUMNS) -> List[Dict[str, str]]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    out = []
    for r in rows:
        row = {c: str(r.get(c, "") or "") for c in columns}
        if not row.get("run_this_toml"):
            row["run_this_toml"] = "YES"
        out.append(row)
    return out


def write_csv(path: Path, rows: List[Dict[str, Any]], columns: List[str] = MANIFEST_COLUMNS) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({c: r.get(c, "") for c in columns})


def flatten(obj: Any, prefix: str = "") -> Iterable[Tuple[str, Any]]:
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from flatten(v, f"{prefix}.{k}" if prefix else str(k))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from flatten(v, f"{prefix}[{i}]")
    else:
        yield prefix, obj


def parse_toml_metadata(toml_path: Path) -> Dict[str, str]:
    """Extract video path, animal count, analysis start frame, and ROI clues.

    idtracker.ai TOML key names vary by version, so the search is deliberately
    permissive.  This is a validator, not an idtracker.ai parser.
    """
    out = {
        "toml_video_path": "",
        "toml_expected_animals_found": "",
        # idtracker.ai's Segmentation app stores the manually chosen tracking interval.
        # For Vince's workflow, post-processing should start at the first frame of that
        # interval.  A TOML like tracking_intervals = [[65, 8419]] yields start=65.
        "toml_tracking_interval_start_frame": "",
        "toml_tracking_interval_end_frame": "",
        "toml_analysis_start_frame": "0",
        "toml_roi_or_polygon_found": "NO",
    }
    if not toml_path.exists():
        return out
    try:
        with toml_path.open("rb") as f:
            data = tomllib.load(f)
    except Exception as exc:
        out["parse_error"] = repr(exc)
        return out

    # Prefer idtracker.ai's explicit tracking interval when present.
    # In current TOMLs this is commonly: tracking_intervals = [[start, end]].
    tracking_intervals = data.get("tracking_intervals") if isinstance(data, dict) else None
    if isinstance(tracking_intervals, list) and tracking_intervals:
        first = tracking_intervals[0]
        if isinstance(first, list) and len(first) >= 2:
            try:
                out["toml_tracking_interval_start_frame"] = str(int(first[0]))
                out["toml_tracking_interval_end_frame"] = str(int(first[1]))
                out["toml_analysis_start_frame"] = str(int(first[0]))
            except Exception:
                pass
        elif len(tracking_intervals) >= 2 and all(isinstance(x, int) for x in tracking_intervals[:2]):
            out["toml_tracking_interval_start_frame"] = str(int(tracking_intervals[0]))
            out["toml_tracking_interval_end_frame"] = str(int(tracking_intervals[1]))
            out["toml_analysis_start_frame"] = str(int(tracking_intervals[0]))

    video_candidates = []
    animal_candidates = []
    start_frame_candidates = []
    roi_found = False

    for key, value in flatten(data):
        low = key.lower()
        if isinstance(value, str):
            cleaned = value.strip().strip('"').strip("'")
            if Path(cleaned).suffix.lower() in VIDEO_EXTENSIONS:
                score = (5 if "video" in low else 0) + (3 if "file" in low else 0) + (2 if "path" in low else 0)
                video_candidates.append((score, key, cleaned))
        if isinstance(value, int) and any(tok in low for tok in ["number_of_animals", "number_animals", "n_animals", "num_animals"]):
            animal_candidates.append((10 if "number_of_animals" in low else 1, key, value))
        if isinstance(value, int) and value >= 0:
            if any(tok in low for tok in ["start_frame", "starting_frame", "first_frame", "frame_start", "analysis_start", "tracking_start"]):
                start_frame_candidates.append((20, key, value))
            elif "start" in low and "frame" in low:
                start_frame_candidates.append((10, key, value))
        if isinstance(value, list) and value and all(isinstance(x, int) for x in value[:2]):
            if any(tok in low for tok in ["interval", "range", "frame"]):
                start_frame_candidates.append((5, key, int(value[0])))
        if any(tok in low for tok in ["roi", "polygon", "polygons", "region_of_interest"]):
            if value not in [None, "", [], {}]:
                roi_found = True

    if video_candidates:
        video_candidates.sort(reverse=True)
        out["toml_video_path"] = str(video_candidates[0][2])
    if animal_candidates:
        animal_candidates.sort(reverse=True)
        out["toml_expected_animals_found"] = str(animal_candidates[0][2])
    if start_frame_candidates and not out.get("toml_tracking_interval_start_frame"):
        start_frame_candidates.sort(reverse=True)
        out["toml_analysis_start_frame"] = str(start_frame_candidates[0][2])
    out["toml_roi_or_polygon_found"] = "YES" if roi_found else "NO"
    return out


def video_info(video: Path) -> Dict[str, str]:
    v = video.expanduser().resolve()
    st = v.stat()
    return {
        "video_id": safe_stem(v.stem),
        "original_video_path": str(v),
        "original_video_name": v.name,
        "original_video_stem": safe_stem(v.stem),
        "video_size_bytes": str(st.st_size),
        "video_mtime_iso": dt.datetime.fromtimestamp(st.st_mtime).replace(microsecond=0).isoformat(),
    }


def infer_cell_label_from_toml_stem(toml_stem: str, video_stem: str = "") -> str:
    """Infer the arena/cell label from the end of a TOML filename.

    Students usually name arena-specific TOMLs with a suffix such as A1, B3,
    C12, etc.  For example:

        Camera_1_20260630_ACT1_A1.toml  -> A1
        Camera_1_20260630_ACT1__B3.toml -> B3

    If no grid-like suffix is found, fall back to the final filename token.
    The GUI lets students edit this label after import.
    """
    stem = safe_stem(toml_stem)
    base = safe_stem(video_stem) if video_stem else ""

    # Remove the source-video stem if the TOML starts with it.
    suffix = stem
    if base and suffix.startswith(base):
        suffix = suffix[len(base):]
        suffix = suffix.lstrip("_.- ")

    # Prefer a classic plate/grid label at the end: A1, B3, AA12, etc.
    m = re.search(r"([A-Za-z]{1,4}\d{1,4})$", suffix)
    if m:
        return safe_stem(m.group(1).upper())

    # Otherwise use the final token after common separators.
    tokens = [t for t in re.split(r"[_\-.\s]+", suffix) if t]
    if tokens:
        return safe_stem(tokens[-1])

    return stem


def make_output_stem(video_stem: str, cell_label: str) -> str:
    """Output stem = original video stem + edited arena/cell label.

    This avoids collisions when several TOMLs come from the same raw video and
    keeps filenames short enough to read.
    """
    video_stem = safe_stem(video_stem)
    cell_label = safe_stem(cell_label)
    return safe_stem(f"{video_stem}__{cell_label}")


def same_video(toml_video_path: str, registered_video_path: str) -> str:
    if not toml_video_path:
        return "UNKNOWN"
    try:
        if Path(toml_video_path).expanduser().resolve() == Path(registered_video_path).expanduser().resolve():
            return "YES"
    except Exception:
        pass
    # Fall back to exact filename.  Paths can differ between a local machine and
    # Firebird, but the source video filename should remain stable.
    return "YES" if Path(toml_video_path).name == Path(registered_video_path).name else "NO"


def mismatch_rows(rows: List[Dict[str, str]]) -> List[Dict[str, str]]:
    """Return rows whose TOML appears to point to a different source video.

    Missing/undetected TOML video paths are warnings, not hard mismatches.
    A hard mismatch is a detected TOML video path/name that does not match the
    registered source video selected by the student.
    """
    out = []
    for r in rows:
        if r.get("toml_video_matches_registered_video") == "NO":
            out.append(r)
    return out


def mismatch_summary(rows: List[Dict[str, str]], max_rows: int = 20) -> str:
    bad = mismatch_rows(rows)
    if not bad:
        return ""
    lines = [
        f"Detected {len(bad)} TOML/video mismatch(es).",
        "The selected video does not match the video path/name recorded inside these TOMLs.",
        "",
    ]
    for r in bad[:max_rows]:
        lines.append(f"TOML: {r.get('toml_name','')}")
        lines.append(f"  TOML video: {r.get('toml_video_path','') or '[not detected]'}")
        lines.append(f"  Selected video: {r.get('original_video_path','')}")
    if len(bad) > max_rows:
        lines.append(f"... {len(bad) - max_rows} more mismatch rows omitted.")
    lines.append("")
    lines.append("Consequence: this run should not be submitted until the correct video and TOML folder are selected.")
    return "\n".join(lines)


def import_toml_folder(toml_folder: Path, video: Path, pipeline: str, metadata_tag: str = "", recursive: bool = False) -> List[Dict[str, str]]:
    toml_folder = toml_folder.expanduser().resolve()
    video = video.expanduser().resolve()
    if not toml_folder.is_dir():
        raise SystemExit(f"TOML folder does not exist: {toml_folder}")
    if not video.exists() or video.suffix.lower() not in VIDEO_EXTENSIONS:
        raise SystemExit(f"Video does not exist or is not a supported video type: {video}")
    pipeline = pipeline.lower().strip()
    if pipeline not in {"ba", "fight"}:
        raise SystemExit("pipeline must be ba or fight")

    for sub in ["project_metadata", "idtracker_sessions", "postprocessing", "logs", "gui_slurm"]:
        (toml_folder / sub).mkdir(parents=True, exist_ok=True)

    v = video_info(video)
    metadata_tag = metadata_tag or v["original_video_stem"]
    project_name = safe_stem(toml_folder.name)
    project_id = safe_stem(f"{v['original_video_stem']}__{project_name}")
    config = {
        "workflow": "single_video_toml_folder",
        "project_id": project_id,
        "project_name": project_name,
        "project_folder": str(toml_folder),
        "metadata_tag": metadata_tag,
        "pipeline": pipeline,
        "registered_video": v,
        "recursive_toml_search": bool(recursive),
        "updated_at": now_iso(),
    }
    config_path(toml_folder).write_text(json.dumps(config, indent=2), encoding="utf-8")

    # Preserve any labels/notes the student edited in a previous import grid.
    previous_rows = read_csv(all_tomls_path(toml_folder))
    previous_by_path = {}
    for old in previous_rows:
        try:
            key = str(Path(old.get("toml_path", "")).expanduser().resolve())
        except Exception:
            key = old.get("toml_path", "")
        if key:
            previous_by_path[key] = old

    tomls = sorted(toml_folder.rglob("*.toml") if recursive else toml_folder.glob("*.toml"))
    # Do not import TOMLs inside managed output folders if recursive is on.
    tomls = [p for p in tomls if "project_metadata" not in p.parts and "idtracker_sessions" not in p.parts and "postprocessing" not in p.parts]
    rows = []
    expected = "1" if pipeline == "ba" else "2"
    for i, t in enumerate(tomls, start=1):
        parsed = parse_toml_metadata(t)
        toml_stem = safe_stem(t.stem)
        toml_key = str(t.expanduser().resolve())
        previous = previous_by_path.get(toml_key, {})
        inferred_label = infer_cell_label_from_toml_stem(toml_stem, v["original_video_stem"])
        cell_label = safe_stem(previous.get("cell_label") or inferred_label)
        cell_id = cell_label
        output_stem = make_output_stem(v["original_video_stem"], cell_label)
        row = {
            "project_id": project_id,
            "project_name": project_name,
            "project_folder": str(toml_folder),
            "metadata_tag": metadata_tag,
            "pipeline": pipeline,
            **v,
            "cell_id": cell_id,
            "cell_label": cell_label,
            "cell_notes": previous.get("cell_notes") or "Imported from existing TOML folder.",
            "run_this_toml": (previous.get("run_this_toml") or "YES").upper(),
            "expected_animals": expected,
            "toml_folder": str(t.parent.resolve()),
            "toml_path": str(t.resolve()),
            "toml_name": t.name,
            "toml_stem": toml_stem,
            "output_stem": output_stem,
            "toml_exists": "YES",
            "toml_video_path": parsed.get("toml_video_path", ""),
            "toml_video_matches_registered_video": same_video(parsed.get("toml_video_path", ""), v["original_video_path"]),
            "toml_expected_animals_found": parsed.get("toml_expected_animals_found", ""),
            "toml_tracking_interval_start_frame": parsed.get("toml_tracking_interval_start_frame", ""),
            "toml_tracking_interval_end_frame": parsed.get("toml_tracking_interval_end_frame", ""),
            "toml_analysis_start_frame": parsed.get("toml_analysis_start_frame", "0"),
            "toml_roi_or_polygon_found": parsed.get("toml_roi_or_polygon_found", "NO"),
            "validation_status": "",
            "validation_message": "",
            "idtracker_session_name": "",
        }
        rows.append(row)
    rows = validate_rows(rows)
    write_csv(all_tomls_path(toml_folder), rows)
    return rows


def validate_row(row: Dict[str, str]) -> Dict[str, str]:
    messages = []
    status = "ready"
    if str(row.get("run_this_toml", "YES")).strip().upper() in {"NO", "FALSE", "0", "EXCLUDE", "EXCLUDED"}:
        row["run_this_toml"] = "NO"
        row["validation_status"] = "excluded"
        row["validation_message"] = "Removed from this run by the user. TOML file was not deleted."
        return row
    row["run_this_toml"] = "YES"
    toml = Path(row.get("toml_path", ""))
    if not row.get("cell_label", "").strip():
        status = "error"
        messages.append("Arena/cell label is blank.")
    if not toml.exists():
        status = "error"
        messages.append("TOML file does not exist.")
        row["toml_exists"] = "NO"
    else:
        row["toml_exists"] = "YES"
    if row.get("toml_video_matches_registered_video") == "NO":
        status = "error"
        messages.append("TOML video path/name does not match the selected source video.")
    elif row.get("toml_video_matches_registered_video") == "UNKNOWN":
        status = "warning" if status == "ready" else status
        messages.append("Could not detect video path inside TOML; verify manually.")
    tv = row.get("toml_video_path", "")
    if tv and not Path(tv).expanduser().exists():
        # A missing path can happen if TOMLs were created on another machine.
        # Keep it as a warning if the filename matches the selected video.
        if row.get("toml_video_matches_registered_video") == "YES":
            status = "warning" if status == "ready" else status
            messages.append("Video path inside TOML is not accessible here, but filename matches selected video.")
        else:
            status = "error"
            messages.append("Video path inside TOML does not exist from this filesystem.")
    found_animals = row.get("toml_expected_animals_found", "")
    expected = row.get("expected_animals", "")
    if found_animals and expected and found_animals != expected:
        status = "error"
        messages.append(f"TOML animal count {found_animals} does not match {row.get('pipeline')} expected count {expected}.")
    elif not found_animals:
        status = "warning" if status == "ready" else status
        messages.append("Could not detect number_of_animals in TOML.")
    if row.get("toml_roi_or_polygon_found") != "YES":
        status = "warning" if status == "ready" else status
        messages.append("Could not detect ROI/polygon keys in TOML; verify segmentation manually.")
    row["validation_status"] = status
    row["validation_message"] = " ".join(messages) if messages else "Ready for SLURM."
    return row


def validate_rows(rows: List[Dict[str, str]]) -> List[Dict[str, str]]:
    """Validate rows individually and catch duplicate output names.

    Duplicate arena/cell labels would create duplicate output folders for the
    same source video, so they are treated as errors.
    """
    rows = [validate_row(r) for r in rows]
    counts: Dict[str, int] = {}
    for r in rows:
        if str(r.get("run_this_toml", "YES")).strip().upper() == "NO":
            continue
        key = r.get("output_stem", "")
        if key:
            counts[key] = counts.get(key, 0) + 1
    for r in rows:
        if str(r.get("run_this_toml", "YES")).strip().upper() == "NO":
            continue
        key = r.get("output_stem", "")
        if key and counts.get(key, 0) > 1:
            r["validation_status"] = "error"
            extra = f"Duplicate arena/cell label creates duplicate output_stem {key}."
            msg = r.get("validation_message", "")
            r["validation_message"] = (msg + " " + extra).strip()
    return rows


def refresh(toml_folder: Path) -> List[Dict[str, str]]:
    cfg_file = config_path(toml_folder)
    if not cfg_file.exists():
        raise SystemExit("No run_config.json exists yet. Import a video + TOML folder first.")
    cfg = json.loads(cfg_file.read_text(encoding="utf-8"))
    return import_toml_folder(
        toml_folder=toml_folder,
        video=Path(cfg["registered_video"]["original_video_path"]),
        pipeline=cfg["pipeline"],
        metadata_tag=cfg.get("metadata_tag", ""),
        recursive=bool(cfg.get("recursive_toml_search", False)),
    )


def build_manifest(toml_folder: Path, include_warnings: bool = True) -> List[Dict[str, str]]:
    rows = refresh(toml_folder)
    runnable = []
    for r in rows:
        if str(r.get("run_this_toml", "YES")).strip().upper() == "NO":
            continue
        if r.get("validation_status") == "ready":
            runnable.append(r)
        elif include_warnings and r.get("validation_status") == "warning":
            runnable.append(r)
    write_csv(manifest_path(toml_folder), runnable)
    return runnable


def set_run_status(toml_folder: Path, toml_path: Path, include: bool) -> None:
    """Mark a TOML as included/excluded without deleting the TOML file."""
    grid = all_tomls_path(toml_folder.expanduser().resolve())
    rows = read_csv(grid)
    if not rows:
        raise SystemExit("No imported TOML grid found. Import/validate the TOML folder first.")
    target = str(toml_path.expanduser().resolve())
    changed = False
    for r in rows:
        try:
            key = str(Path(r.get("toml_path", "")).expanduser().resolve())
        except Exception:
            key = r.get("toml_path", "")
        if key == target:
            r["run_this_toml"] = "YES" if include else "NO"
            changed = True
    if not changed:
        raise SystemExit(f"TOML path was not found in the import grid: {target}")
    rows = validate_rows(rows)
    write_csv(grid, rows)


def row_by_index(manifest: Path, index1: int) -> Dict[str, str]:
    rows = read_csv(manifest)
    if index1 < 1 or index1 > len(rows):
        raise SystemExit(f"Index {index1} outside manifest row range 1-{len(rows)}")
    return rows[index1 - 1]


def write_shell_env(row: Dict[str, str]) -> None:
    mapping = {
        "PROJECT_ID_FROM_MANIFEST": "project_id",
        "PROJECT_NAME_FROM_MANIFEST": "project_name",
        "PROJECT_FOLDER_FROM_MANIFEST": "project_folder",
        "METADATA_TAG_FROM_MANIFEST": "metadata_tag",
        "PIPELINE_FROM_MANIFEST": "pipeline",
        "VIDEO_ID_FROM_MANIFEST": "video_id",
        "ORIGINAL_VIDEO_PATH_FROM_MANIFEST": "original_video_path",
        "ORIGINAL_VIDEO_NAME_FROM_MANIFEST": "original_video_name",
        "ORIGINAL_VIDEO_STEM_FROM_MANIFEST": "original_video_stem",
        "VIDEO_SIZE_BYTES_FROM_MANIFEST": "video_size_bytes",
        "VIDEO_MTIME_ISO_FROM_MANIFEST": "video_mtime_iso",
        "CELL_ID_FROM_MANIFEST": "cell_id",
        "CELL_LABEL_FROM_MANIFEST": "cell_label",
        "CELL_NOTES_FROM_MANIFEST": "cell_notes",
        "EXPECTED_ANIMALS_FROM_MANIFEST": "expected_animals",
        "TOML_FOLDER_FROM_MANIFEST": "toml_folder",
        "TOML_PATH_FROM_MANIFEST": "toml_path",
        "TOML_NAME_FROM_MANIFEST": "toml_name",
        "TOML_STEM_FROM_MANIFEST": "toml_stem",
        "OUTPUT_STEM_FROM_MANIFEST": "output_stem",
    }
    for env, col in mapping.items():
        print(f"{env}={shlex.quote(str(row.get(col, '')))}")


def main() -> int:
    ap = argparse.ArgumentParser(description="Import and validate a folder of idtracker.ai TOML files for one source video.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_imp = sub.add_parser("import")
    p_imp.add_argument("--toml-folder", required=True)
    p_imp.add_argument("--video", required=True)
    p_imp.add_argument("--pipeline", choices=["ba", "fight"], required=True)
    p_imp.add_argument("--metadata-tag", default="")
    p_imp.add_argument("--recursive", action="store_true")

    p_ref = sub.add_parser("refresh")
    p_ref.add_argument("--toml-folder", required=True)

    p_manifest = sub.add_parser("build-manifest")
    p_manifest.add_argument("--toml-folder", required=True)
    p_manifest.add_argument("--strict", action="store_true", help="Exclude warning rows as well as error rows.")

    p_env = sub.add_parser("env")
    p_env.add_argument("--manifest", required=True)
    p_env.add_argument("--index", type=int, required=True)

    p_json = sub.add_parser("json")
    p_json.add_argument("--manifest", required=True)
    p_json.add_argument("--index", type=int, required=True)

    p_run = sub.add_parser("set-run-status")
    p_run.add_argument("--toml-folder", required=True)
    p_run.add_argument("--toml-path", required=True)
    p_run.add_argument("--include", choices=["YES", "NO"], required=True)

    args = ap.parse_args()
    if args.cmd == "import":
        rows = import_toml_folder(Path(args.toml_folder), Path(args.video), args.pipeline, args.metadata_tag, args.recursive)
        counts = {}
        for r in rows:
            counts[r["validation_status"]] = counts.get(r["validation_status"], 0) + 1
        print(json.dumps({"rows": len(rows), "counts": counts, "grid": str(all_tomls_path(Path(args.toml_folder)))}, indent=2))
        return 0
    if args.cmd == "refresh":
        rows = refresh(Path(args.toml_folder))
        counts = {}
        for r in rows:
            counts[r["validation_status"]] = counts.get(r["validation_status"], 0) + 1
        print(json.dumps({"rows": len(rows), "counts": counts, "grid": str(all_tomls_path(Path(args.toml_folder)))}, indent=2))
        return 0
    if args.cmd == "build-manifest":
        rows = build_manifest(Path(args.toml_folder), include_warnings=not args.strict)
        print(f"Wrote {len(rows)} runnable rows to {manifest_path(Path(args.toml_folder))}")
        return 0
    if args.cmd == "env":
        write_shell_env(row_by_index(Path(args.manifest), args.index))
        return 0
    if args.cmd == "json":
        print(json.dumps(row_by_index(Path(args.manifest), args.index), indent=2))
        return 0
    if args.cmd == "set-run-status":
        include = str(args.include).upper() == "YES"
        set_run_status(Path(args.toml_folder), Path(args.toml_path), include=include)
        print("Marked TOML as included in run." if include else "Removed TOML from run. The TOML file was not deleted.")
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
