#!/usr/bin/env python3
"""
Collect completed IDtracker.ai session folders into the controlled TOML-folder tree.

This helper is deliberately conservative.  idtracker.ai commonly writes completed
session folders next to the original video, not next to the TOML files.  Vince's
workflow should keep the raw-video storage folder clean, so this script moves the
ENTIRE completed IDtracker session folder into:

    <TOML_FOLDER>/idtracker_sessions/<original_video_stem>/<output_stem>/

The preferred SLURM flow is now:
    1. MODE=idtracker array: run idtracker.ai only.
    2. MODE=collect: after the array finishes, collect/move all sessions.
    3. MODE=postprocess: run only if the collector verified every expected cell.

The collector writes:
    project_metadata/session_collection_report.csv

and exits nonzero if any expected manifest row is missing a moved trajectory file.
"""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
import re
import shutil
import sys
import time
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

try:
    import tomllib
except Exception:  # pragma: no cover
    import tomli as tomllib  # type: ignore

TRAJECTORY_NAMES = {
    "trajectories.npy",
    "trajectories_wo_gaps.npy",
    "trajectories_without_gaps.npy",
    "trajectories.h5",
    "trajectories.csv",
}
TEXT_NAMES = {
    "idtrackerai.log",
    "session.json",
    "source_metadata.json",
    "attributes.json",
    "local_settings.py",
    "idtrackerai-app.log",
}

# Collector exit codes used by the SLURM wrapper and GUI.
# 0  = all expected sessions collected and verified.
# 10 = manifest missing/empty.
# 20 = at least one expected cell/session could not be collected.
# 30 = unexpected internal collector error.



def safe_stem(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", str(text)).strip("_") or "unnamed"


def read_manifest_rows(path: Path) -> List[Dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as f:
        return [{k: str(v or "") for k, v in row.items()} for row in csv.DictReader(f)]


def toml_name_value(toml_path: Path) -> str:
    try:
        with toml_path.open("rb") as f:
            data = tomllib.load(f)
        value = data.get("name", "") if isinstance(data, dict) else ""
        return safe_stem(str(value)) if value else ""
    except Exception:
        return ""


def iter_trajectory_files(root: Path) -> Iterable[Path]:
    if not root.exists() or not root.is_dir():
        return
    for p in root.rglob("*"):
        if p.is_file() and p.name in TRAJECTORY_NAMES:
            yield p


def session_root_for_trajectory(traj: Path) -> Path:
    # Common current layout: session_folder/trajectories/trajectories.npy
    if traj.parent.name == "trajectories":
        return traj.parent.parent
    # Alternate: session_folder/trajectories/trajectories_csv/trajectories.csv
    if traj.parent.name == "trajectories_csv" and traj.parent.parent.name == "trajectories":
        return traj.parent.parent.parent
    for parent in [traj.parent] + list(traj.parents):
        if (parent / "session.json").exists() or (parent / "idtrackerai.log").exists():
            return parent
    return traj.parent


def newest_mtime(path: Path) -> float:
    latest = path.stat().st_mtime if path.exists() else 0.0
    if path.is_dir():
        for p in path.rglob("*"):
            try:
                latest = max(latest, p.stat().st_mtime)
            except Exception:
                pass
    return latest


def has_trajectory(session: Path) -> bool:
    return any(iter_trajectory_files(session))


def read_small_texts(session: Path, max_bytes: int = 2_000_000) -> str:
    parts: List[str] = []
    try:
        files = list(session.rglob("*"))
    except Exception:
        return ""
    for p in files:
        if not p.is_file() or p.name not in TEXT_NAMES:
            continue
        try:
            if p.stat().st_size > max_bytes:
                continue
            parts.append(p.read_text(encoding="utf-8", errors="ignore"))
        except Exception:
            continue
    return "\n".join(parts).lower()


def score_candidate(session: Path, row: Dict[str, str], toml_name: str, text_cache: Dict[Path, str]) -> Tuple[int, List[str]]:
    path_text = str(session).lower()
    session_name = session.name.lower()
    score = 0
    reasons: List[str] = []

    output_stem = safe_stem(row.get("output_stem", ""))
    toml_stem = safe_stem(row.get("toml_stem", ""))
    toml_file_stem = safe_stem(row.get("toml_name", "")).replace(".toml", "")
    cell = safe_stem(row.get("cell_label", ""))

    strong_signals = [output_stem, toml_stem, toml_file_stem, toml_name]
    for sig in strong_signals:
        if sig and sig.lower() in path_text:
            score += 140
            reasons.append(f"path contains {sig}")

    # Short cell labels are useful but not sufficient by themselves.
    if cell and re.search(rf"(^|[_\-.]){re.escape(cell.lower())}($|[_\-.])", session_name):
        score += 45
        reasons.append(f"session folder name contains cell label {cell}")

    if session not in text_cache:
        text_cache[session] = read_small_texts(session)
    txt = text_cache[session]
    for sig in [row.get("toml_name", ""), row.get("toml_stem", ""), toml_name, output_stem]:
        if sig and sig.lower() in txt:
            score += 100
            reasons.append(f"metadata/log contains {sig}")
            break

    # If idtracker used the TOML name as the top-level session name, this catches it.
    if toml_name and toml_name.lower() == session.name.lower():
        score += 200
        reasons.append("session folder name exactly matches TOML name")

    return score, reasons


def destination_for(row: Dict[str, str], session_output_root: Path) -> Path:
    output_stem = safe_stem(row.get("output_stem", ""))
    original_video_stem = safe_stem(row.get("original_video_stem", ""))
    return session_output_root / original_video_stem / output_stem


def build_search_roots(rows: Sequence[Dict[str, str]], session_output_root: Path, extra_roots: Sequence[str]) -> List[Path]:
    roots: List[Path] = []
    for row in rows:
        candidates = [
            Path(row.get("original_video_path", "")).expanduser().parent,
            Path(row.get("toml_folder", "")).expanduser(),
            Path(row.get("project_folder", "")).expanduser(),
            session_output_root,
        ]
        for p in candidates:
            try:
                rp = p.resolve()
            except Exception:
                continue
            if rp.exists() and rp.is_dir() and rp not in roots:
                roots.append(rp)
    for ptxt in extra_roots:
        try:
            rp = Path(ptxt).expanduser().resolve()
        except Exception:
            continue
        if rp.exists() and rp.is_dir() and rp not in roots:
            roots.append(rp)
    return roots


def discover_sessions(search_roots: Sequence[Path]) -> Dict[Path, List[Path]]:
    """Return session_root -> trajectory files."""
    out: Dict[Path, List[Path]] = {}
    forbidden = {r.resolve() for r in search_roots if r.exists()}
    for root in search_roots:
        for traj in iter_trajectory_files(root):
            session = session_root_for_trajectory(traj).resolve()
            if session in forbidden:
                continue
            out.setdefault(session, []).append(traj)
    return out


def merge_or_move_session(src: Path, dest: Path) -> Path:
    src = src.resolve()
    dest = dest.resolve()
    if src == dest:
        return dest
    if dest.exists() and has_trajectory(dest):
        # Already collected.  If src is a duplicate elsewhere, leave it in place;
        # we do not want to overwrite a valid collected session.
        return dest
    if not dest.exists():
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dest))
        return dest
    # Existing dest is a wrapper with input_toml/source_metadata but no trajectories.
    for child in src.iterdir():
        target = dest / child.name
        if target.exists():
            target = dest / f"{child.name}_from_session_{time.strftime('%Y%m%d_%H%M%S')}"
        shutil.move(str(child), str(target))
    try:
        src.rmdir()
    except Exception:
        pass
    return dest


def write_source_metadata(moved: Path, row: Dict[str, str], original_session: Path) -> None:
    toml_path = Path(row.get("toml_path", "")).expanduser()
    input_dir = moved / "input_toml"
    input_dir.mkdir(parents=True, exist_ok=True)
    if toml_path.exists():
        shutil.copy2(toml_path, input_dir / row.get("toml_name", toml_path.name))
    meta = dict(row)
    meta["idtracker_session_name"] = original_session.name
    meta["moved_session_folder"] = str(moved)
    meta["original_session_folder"] = str(original_session)
    (moved / "source_metadata.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")



def write_collection_stage_status(project_folder: Path, status: str, expected: int, collected: int, report: Path, message: str, exit_code: int) -> None:
    pm = project_folder / "project_metadata"
    pm.mkdir(parents=True, exist_ok=True)
    payload = {
        "stage": "session_collection",
        "status": status,
        "expected_cells": expected,
        "collected_cells_with_trajectories": collected,
        "report": str(report),
        "message": message,
        "exit_code": exit_code,
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    (pm / "session_collection_status.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    # Remove stale opposite markers, then write the current one.
    complete = pm / "_SESSION_COLLECTION_COMPLETE_ALL_CELLS.txt"
    failed = pm / "_SESSION_COLLECTION_INCOMPLETE_OR_FAILED.txt"
    for marker in (complete, failed):
        try:
            marker.unlink()
        except FileNotFoundError:
            pass
        except Exception:
            pass
    if status == "complete":
        complete.write_text(
            "Session collection complete for all expected cells.\n"
            f"Expected cells: {expected}\n"
            f"Collected cells: {collected}\n"
            f"Report: {report}\n",
            encoding="utf-8",
        )
    else:
        failed.write_text(
            "Session collection incomplete or failed.\n"
            f"Expected cells: {expected}\n"
            f"Collected cells: {collected}\n"
            f"Exit code: {exit_code}\n"
            f"Message: {message}\n"
            f"Report: {report}\n",
            encoding="utf-8",
        )


def collect_all(manifest: Path, session_output_root: Path, extra_roots: Sequence[str], report_path: Optional[Path] = None) -> int:
    rows = read_manifest_rows(manifest)
    if not rows:
        project_folder = manifest.parent.parent
        report = report_path or project_folder / "project_metadata" / "session_collection_report.csv"
        report.parent.mkdir(parents=True, exist_ok=True)
        print(f"ERROR E200: manifest has no rows: {manifest}", file=sys.stderr)
        write_collection_stage_status(project_folder, "failed", 0, 0, report, "E200: manifest has no rows", 10)
        return 10
    project_folder = Path(rows[0].get("project_folder") or rows[0].get("toml_folder") or manifest.parent.parent).expanduser()
    report = report_path or project_folder / "project_metadata" / "session_collection_report.csv"
    report.parent.mkdir(parents=True, exist_ok=True)

    search_roots = build_search_roots(rows, session_output_root, extra_roots)
    print("Session collector: collect-all mode")
    print(f"Manifest: {manifest}")
    print(f"Session output root: {session_output_root}")
    print("Search roots:")
    for r in search_roots:
        print(f"  {r}")

    discovered = discover_sessions(search_roots)
    print(f"Discovered {len(discovered)} session-like folders with trajectory files.")
    for s, trajs in sorted(discovered.items(), key=lambda kv: str(kv[0])):
        print(f"  session={s} trajectories={len(trajs)} newest={time.ctime(newest_mtime(s))}")

    used_sessions: set[Path] = set()
    text_cache: Dict[Path, str] = {}
    records: List[Dict[str, Any]] = []
    n_ok = 0

    # Process in manifest order so task/cell ordering remains understandable.
    for row in rows:
        output_stem = safe_stem(row.get("output_stem", ""))
        dest = destination_for(row, session_output_root)
        toml_name = toml_name_value(Path(row.get("toml_path", "")).expanduser())
        base_record: Dict[str, Any] = {
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            "cell_label": row.get("cell_label", ""),
            "toml_name": row.get("toml_name", ""),
            "output_stem": output_stem,
            "original_video_path": row.get("original_video_path", ""),
            "expected_destination": str(dest),
        }

        if dest.exists() and has_trajectory(dest):
            write_source_metadata(dest, row, dest)
            records.append({**base_record, "status": "already_collected", "trajectory_found": "YES", "original_session": str(dest), "moved_session": str(dest), "score": "", "message": "Destination already contained trajectories."})
            n_ok += 1
            continue

        candidates: List[Tuple[int, Path, List[str]]] = []
        for session in discovered:
            session = session.resolve()
            if session in used_sessions:
                continue
            # Do not move parent folders already inside a different collected destination.
            try:
                session.relative_to(session_output_root.resolve())
                # If it is already inside session_output_root but not this row's dest, only use if strongly matching.
            except Exception:
                pass
            score, reasons = score_candidate(session, row, toml_name, text_cache)
            if score > 0:
                candidates.append((score, session, reasons))
        candidates.sort(key=lambda x: (x[0], newest_mtime(x[1])), reverse=True)

        print(f"\nRow {row.get('cell_label')} {row.get('toml_name')} -> {output_stem}")
        for score, session, reasons in candidates[:10]:
            print(f"  candidate score={score} session={session} reasons={'; '.join(reasons)}")

        if not candidates:
            records.append({**base_record, "status": "error", "trajectory_found": "NO", "original_session": "", "moved_session": "", "score": "", "message": "No candidate session with trajectories matched this TOML/cell."})
            continue
        best_score, best_session, best_reasons = candidates[0]
        if best_score < 80:
            records.append({**base_record, "status": "error", "trajectory_found": "NO", "original_session": str(best_session), "moved_session": "", "score": best_score, "message": f"Best candidate score too weak; reasons: {'; '.join(best_reasons)}"})
            continue

        try:
            moved = merge_or_move_session(best_session, dest)
            write_source_metadata(moved, row, best_session)
            ok = has_trajectory(moved)
            records.append({**base_record, "status": "moved" if ok else "error", "trajectory_found": "YES" if ok else "NO", "original_session": str(best_session), "moved_session": str(moved), "score": best_score, "message": "; ".join(best_reasons) if ok else "Moved session but no trajectory found after move."})
            if ok:
                used_sessions.add(best_session.resolve())
                n_ok += 1
        except Exception as exc:
            records.append({**base_record, "status": "error", "trajectory_found": "NO", "original_session": str(best_session), "moved_session": "", "score": best_score, "message": f"Move failed: {exc}"})

    # Write report atomically enough for debugging.
    fieldnames = [
        "timestamp", "cell_label", "toml_name", "output_stem", "original_video_path", "expected_destination",
        "status", "trajectory_found", "original_session", "moved_session", "score", "message",
    ]
    with report.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for rec in records:
            writer.writerow({k: rec.get(k, "") for k in fieldnames})
    print(f"\nWrote session collection report: {report}")
    print(f"Collected/verified {n_ok} of {len(rows)} expected sessions.")
    if n_ok != len(rows):
        msg = f"E210: Not all expected sessions were collected ({n_ok}/{len(rows)}). See {report}"
        print("ERROR " + msg, file=sys.stderr)
        write_collection_stage_status(project_folder, "failed", len(rows), n_ok, report, msg, 20)
        return 20
    write_collection_stage_status(project_folder, "complete", len(rows), n_ok, report, "All expected sessions collected and verified.", 0)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Move completed IDtracker.ai session folders into the TOML-folder output tree.")
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--collect-all", action="store_true", help="Collect sessions for every manifest row. This is the preferred mode.")
    ap.add_argument("--index", type=int, default=None, help="Deprecated one-row collection mode retained for compatibility.")
    ap.add_argument("--run-start-epoch", type=int, default=0, help="Deprecated one-row mode hint.")
    ap.add_argument("--session-output-root", required=True)
    ap.add_argument("--extra-search-root", action="append", default=[])
    args = ap.parse_args()

    manifest = Path(args.manifest).expanduser().resolve()
    session_output_root = Path(args.session_output_root).expanduser().resolve()
    if args.collect_all or args.index is None:
        return collect_all(manifest, session_output_root, args.extra_search_root)

    # Compatibility: collect all even when called with an index.  This prevents
    # concurrent array tasks from stealing or ambiguously matching sessions.
    print("WARNING: one-row collection mode is deprecated; collecting all sessions instead.")
    return collect_all(manifest, session_output_root, args.extra_search_root)


if __name__ == "__main__":
    raise SystemExit(main())
