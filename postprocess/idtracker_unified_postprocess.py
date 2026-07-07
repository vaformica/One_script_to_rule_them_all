#!/usr/bin/env python3
"""
Unified IDtracker.ai post-processing for Firebird
================================================

This file is intentionally self-contained and heavily annotated.  It replaces
having separate student-facing BA and fight post-processing entry points.

It supports two pipeline modes:

    ba      one beetle / behavioral assay
    fight   two beetles / dyadic combat

The same trajectory-loading, artifact-filtering, interpolation, ROI parsing,
map-writing, and batch-discovery code is shared between both modes.  The main
biological difference is the number of animals expected:

    BA:     analyze one animal, usually animal index 0
    Fight:  analyze two animals, usually animal indices 0 and 1, plus pairwise
            distance/contact/fight-like events

The script is designed for IDtracker.ai output folders that contain files such
as:

    session.json
    attributes.json
    trajectories/trajectories.npy

It also tries to handle trajectories_wo_gaps.npy, trajectories_without_gaps.npy,
trajectories.h5, and trajectories.csv.

Important unit convention
-------------------------
Most distances are pixels and most times are frames. Seconds are convenience
columns calculated from --fps.
"""

from __future__ import annotations

import argparse
import ast
import csv
import json
import math
import re
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

# Use a non-interactive matplotlib backend so this runs safely in SLURM jobs.
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.backends.backend_pdf import PdfPages
from matplotlib.path import Path as MplPath
from matplotlib.patches import Polygon as MplPolygon

try:
    import h5py  # type: ignore
except Exception:  # pragma: no cover
    h5py = None

try:
    import tomllib  # Python 3.11+
except Exception:  # pragma: no cover
    import tomli as tomllib  # type: ignore


TRAJ_NAMES = [
    "trajectories_wo_gaps.npy",
    "trajectories_without_gaps.npy",
    "trajectories.npy",
    "trajectories.h5",
    "trajectories.csv",
]


# -----------------------------------------------------------------------------
# Small generic helpers
# -----------------------------------------------------------------------------

def safe_stem(text: str) -> str:
    """Make a filesystem-safe folder/file stem."""
    text = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(text)).strip("_")
    return text or "session"


def fmt_hhmmss_comma_ms(seconds: float) -> str:
    """Format seconds as an InqScribe-friendly HH:MM:SS,mmm timestamp."""
    if not np.isfinite(seconds) or seconds < 0:
        seconds = 0.0
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    leftover = seconds % 60
    s_int = int(leftover)
    ms = int(round((leftover - s_int) * 1000))
    if ms == 1000:
        ms = 0
        s_int += 1
    if s_int == 60:
        s_int = 0
        m += 1
    if m == 60:
        m = 0
        h += 1
    return f"{h:02d}:{m:02d}:{s_int:02d},{ms:03d}"


def true_runs(mask: Sequence[bool], min_len: int = 1) -> List[Tuple[int, int]]:
    """Return inclusive start/end runs where mask is True for at least min_len."""
    arr = np.asarray(mask, dtype=bool)
    out: List[Tuple[int, int]] = []
    start: Optional[int] = None
    for i, val in enumerate(arr):
        if val and start is None:
            start = i
        elif (not val) and start is not None:
            end = i - 1
            if end - start + 1 >= min_len:
                out.append((start, end))
            start = None
    if start is not None:
        end = len(arr) - 1
        if end - start + 1 >= min_len:
            out.append((start, end))
    return out


def merge_runs(runs: List[Tuple[int, int]], max_gap: int) -> List[Tuple[int, int]]:
    """Merge nearby runs separated by <= max_gap False frames."""
    if not runs:
        return []
    runs = sorted(runs)
    merged = [runs[0]]
    for s, e in runs[1:]:
        ps, pe = merged[-1]
        if s - pe - 1 <= max_gap:
            merged[-1] = (ps, max(pe, e))
        else:
            merged.append((s, e))
    return merged


def read_json_file(path: Optional[Path]) -> Dict[str, Any]:
    """Safely read a JSON file; return {} if missing or unreadable."""
    if path is None or not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}

def metadata_columns(source_meta: Dict[str, Any]) -> Dict[str, Any]:
    """Return the provenance columns that should appear in every output row.

    These columns are deliberately repeated in all summary, event, and per-frame
    CSV files because the expected workflow is to concatenate many files later.
    A row should always be traceable back to the exact original video and TOML.
    """
    keys = [
        # Project-level provenance
        "project_id",
        "project_name",
        "project_folder",
        "metadata_tag",
        "pipeline",

        # Raw video provenance.  These should identify the original AVI/MP4.
        "video_id",
        "original_video_path",
        "original_video_name",
        "original_video_stem",
        "video_size_bytes",
        "video_mtime_iso",

        # Cell/arena provenance.  One raw video can have many cells/TOMLs.
        "cell_id",
        "cell_label",
        "cell_notes",
        "expected_animals",

        # TOML provenance.
        "toml_folder",
        "toml_path",
        "toml_name",
        "toml_stem",
        "toml_tracking_interval_start_frame",
        "toml_tracking_interval_end_frame",
        "toml_analysis_start_frame",

        # Output/session naming.
        "output_stem",
        "idtracker_session_name",
    ]
    return {k: source_meta.get(k, "") for k in keys}


def add_metadata_to_df(df: pd.DataFrame, source_meta: Dict[str, Any]) -> pd.DataFrame:
    """Insert/move provenance columns to the left side of a DataFrame."""
    out = df.copy()
    cols = metadata_columns(source_meta)
    for key, value in cols.items():
        if key not in out.columns:
            out[key] = value
        else:
            # Fill blanks but do not overwrite a non-empty value already present.
            out[key] = out[key].replace({np.nan: value, "": value})
    ordered = list(cols.keys()) + [c for c in out.columns if c not in cols]
    return out[ordered]


def read_manifest_rows(path: Optional[str]) -> List[Dict[str, str]]:
    """Read the TOML/video manifest created by the GUI."""
    if not path:
        return []
    p = Path(path).expanduser()
    if not p.exists():
        return []
    try:
        return pd.read_csv(p, dtype=str).fillna("").to_dict("records")
    except Exception:
        return []


def find_source_metadata_json(session_folder: Path) -> Dict[str, Any]:
    """Find source_metadata.json in the session folder or its parents."""
    for candidate in [session_folder] + list(session_folder.parents):
        meta_path = candidate / "source_metadata.json"
        if meta_path.exists():
            return read_json_file(meta_path)
    return {}


def resolve_source_metadata(session_folder: Path, args: argparse.Namespace) -> Dict[str, Any]:
    """Resolve original video/TOML metadata for one session.

    Preferred source is source_metadata.json written beside the moved IDtracker
    session by the SLURM script.  If that is missing, fall back to matching the
    session folder or parent folder name against the TOML/video manifest.
    """
    meta = find_source_metadata_json(session_folder)
    if not meta:
        rows = read_manifest_rows(getattr(args, "metadata_manifest", None))
        names_to_try = {session_folder.name, session_folder.parent.name}
        for row in rows:
            if (row.get("output_stem") in names_to_try or
                row.get("cell_id") in names_to_try or
                row.get("toml_stem") in names_to_try or
                row.get("original_video_stem") in names_to_try):
                meta = dict(row)
                break
    if not meta:
        # Last-resort fallback keeps outputs usable but flags that the original
        # video path was not recovered.
        meta = {
            "metadata_tag": getattr(args, "metadata_tag", ""),
            "pipeline": getattr(args, "pipeline", ""),
            "toml_folder": getattr(args, "toml_folder", ""),
            "toml_path": "",
            "toml_name": "",
            "toml_stem": "",
            "original_video_path": "",
            "original_video_name": "",
            "original_video_stem": safe_stem(session_folder.parent.name if session_folder.parent.name else session_folder.name),
            "output_stem": safe_stem(session_folder.parent.name if session_folder.parent.name else session_folder.name),
            "idtracker_session_name": session_folder.name,
        }
    # Fill defaults and preserve explicit GUI metadata tag when source file lacks one.
    meta.setdefault("metadata_tag", getattr(args, "metadata_tag", ""))
    meta.setdefault("pipeline", getattr(args, "pipeline", ""))
    meta.setdefault("toml_folder", getattr(args, "toml_folder", ""))
    meta.setdefault("idtracker_session_name", session_folder.name)
    if not meta.get("output_stem"):
        meta["output_stem"] = safe_stem(meta.get("original_video_stem") or meta.get("toml_stem") or session_folder.parent.name or session_folder.name)
    if not meta.get("original_video_stem"):
        meta["original_video_stem"] = meta.get("output_stem", "")
    return meta


def output_stem_from_metadata(source_meta: Dict[str, Any], session_folder: Path) -> str:
    """Choose the stable output filename/folder stem for a session."""
    return safe_stem(source_meta.get("output_stem") or source_meta.get("original_video_stem") or session_folder.name)


def find_nearby_file(start: Path, filename: str, max_up: int = 5) -> Optional[Path]:
    """Find filename near start by checking parents and shallow descendants."""
    cur = start if start.is_dir() else start.parent
    for _ in range(max_up + 1):
        candidate = cur / filename
        if candidate.exists():
            return candidate
        matches = list(cur.glob(f"**/{filename}"))
        if matches:
            return sorted(matches, key=lambda p: len(str(p)))[0]
        if cur.parent == cur:
            break
        cur = cur.parent
    return None


# -----------------------------------------------------------------------------
# Trajectory discovery and loading
# -----------------------------------------------------------------------------

def session_root_for_trajectory(traj_file: Path) -> Path:
    """Return the IDtracker.ai session folder that owns one trajectory file.

    IDtracker.ai often stores trajectories in:

        session_NAME/trajectories/trajectories.npy

    We want the batch unit to be session_NAME, not the trajectories subfolder.
    The strongest signal is a nearby session.json file.
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
    """Prefer gap-corrected .npy outputs, then h5/csv fallbacks."""
    try:
        return TRAJ_NAMES.index(path.name)
    except ValueError:
        return 999


def find_sessions(search_root: Path) -> List[Dict[str, Path]]:
    """Find one best trajectory file per IDtracker.ai session folder."""
    by_session: Dict[str, Dict[str, Path]] = {}
    for name in TRAJ_NAMES:
        for traj in search_root.rglob(name):
            root = session_root_for_trajectory(traj)
            key = str(root.resolve())
            current = by_session.get(key)
            if current is None or trajectory_rank(traj) < trajectory_rank(current["trajectory_file"]):
                by_session[key] = {"session_folder": root, "trajectory_file": traj}
    return sorted(by_session.values(), key=lambda d: str(d["session_folder"]))


def normalize_array_shape(arr: np.ndarray) -> np.ndarray:
    """Normalize trajectory arrays to frames x animals x xy."""
    arr = np.asarray(arr, dtype=float)
    arr = np.squeeze(arr)
    if arr.ndim == 2:
        if arr.shape[1] % 2 != 0:
            raise ValueError(f"Cannot interpret 2D trajectory array shape {arr.shape}")
        return arr.reshape((arr.shape[0], arr.shape[1] // 2, 2))
    if arr.ndim != 3:
        raise ValueError(f"Expected 2D or 3D trajectory array, got {arr.shape}")
    if arr.shape[-1] == 2:
        # Heuristic: animals x frames x xy sometimes has small first dimension and
        # large second dimension.
        if arr.shape[0] < arr.shape[1] and arr.shape[1] > 100:
            return np.transpose(arr, (1, 0, 2))
        return arr
    if arr.shape[1] == 2:
        return np.transpose(arr, (0, 2, 1))
    raise ValueError(f"Cannot interpret trajectory array shape {arr.shape}")


def csv_to_array(df: pd.DataFrame) -> np.ndarray:
    """Load common CSV trajectory layouts into frames x animals x xy."""
    ids: List[int] = []
    for c in df.columns:
        m = re.fullmatch(r"x(\d+)", str(c))
        if m and f"y{m.group(1)}" in df.columns:
            ids.append(int(m.group(1)))
    if ids:
        ids = sorted(ids)
        arr = np.full((len(df), len(ids), 2), np.nan, dtype=float)
        for j, bid in enumerate(ids):
            arr[:, j, 0] = pd.to_numeric(df[f"x{bid}"], errors="coerce").to_numpy(float)
            arr[:, j, 1] = pd.to_numeric(df[f"y{bid}"], errors="coerce").to_numpy(float)
        return arr

    # Generic fallback: keep numeric columns and assume x/y pairs.
    num = df.select_dtypes(include=[np.number]).to_numpy(float)
    if num.shape[1] % 2 != 0:
        num = num[:, 1:] if (num.shape[1] - 1) % 2 == 0 else num
    if num.shape[1] < 2 or num.shape[1] % 2 != 0:
        raise ValueError("CSV must contain xN/yN columns or paired numeric coordinate columns")
    return num.reshape((num.shape[0], num.shape[1] // 2, 2))


def load_trajectory_array(path: Path) -> np.ndarray:
    """Load a trajectory file and return frames x animals x xy."""
    suffix = path.suffix.lower()
    if suffix == ".npy":
        arr = np.load(path, allow_pickle=True)
        # Some npy files are object containers. Try the IDtracker-like key.
        if getattr(arr, "dtype", None) == object:
            try:
                arr = np.asarray(arr.item().get("trajectories"))
            except Exception:
                arr = np.asarray(arr)
        return normalize_array_shape(np.asarray(arr, dtype=float))

    if suffix == ".h5":
        if h5py is None:
            raise RuntimeError("h5py is required to load .h5 trajectory files")
        with h5py.File(path, "r") as f:
            candidate = None
            def visit(name, obj):
                nonlocal candidate
                if candidate is None and hasattr(obj, "shape") and len(obj.shape) >= 2:
                    if "traj" in name.lower() or obj.shape[-1] == 2:
                        candidate = name
            f.visititems(visit)
            if candidate is None:
                raise ValueError(f"Could not find trajectory-like dataset in {path}")
            arr = f[candidate][...]
        return normalize_array_shape(np.asarray(arr, dtype=float))

    if suffix == ".csv":
        return csv_to_array(pd.read_csv(path))

    raise ValueError(f"Unsupported trajectory file type: {path}")


# -----------------------------------------------------------------------------
# ROI parsing
# -----------------------------------------------------------------------------

@dataclass
class ROI:
    name: str
    poly: np.ndarray
    path: MplPath


def parse_roi_string(s: str) -> np.ndarray:
    """Parse a TOML/JSON ROI polygon represented as [[x,y], [x,y], ...]."""
    m = re.search(r"\[\s*\[.*\]\s*\]", s)
    raw = m.group(0) if m else s
    coords = ast.literal_eval(raw)
    return np.asarray(coords, dtype=float)


def load_rois_from_toml(toml_path: Optional[Path]) -> List[ROI]:
    """Load ROI polygons from a TOML file if present."""
    if toml_path is None or not toml_path.exists():
        return []
    try:
        data = tomllib.loads(toml_path.read_text(encoding="utf-8"))
    except Exception:
        return []
    raw_rois = data.get("roi_list") or data.get("rois") or data.get("ROIS") or []
    return parse_raw_rois(raw_rois)


def load_rois_from_session(session_data: Dict[str, Any]) -> List[ROI]:
    """Load ROI polygons from IDtracker.ai session.json."""
    raw_rois = session_data.get("roi_list") or session_data.get("rois") or []
    return parse_raw_rois(raw_rois)


def parse_raw_rois(raw_rois: Any) -> List[ROI]:
    """Parse a variety of ROI list/dict encodings."""
    rois: List[ROI] = []
    iterator = raw_rois.items() if isinstance(raw_rois, dict) else enumerate(raw_rois or [])
    for key, val in iterator:
        try:
            default_name = "primary_roi" if len(rois) == 0 else ("secondary_roi" if len(rois) == 1 else f"roi_{len(rois)}")
            if isinstance(val, dict):
                name = str(val.get("name", default_name))
                poly_val = val.get("polygon") or val.get("points") or val.get("roi") or val
                poly = np.asarray(poly_val, dtype=float)
            else:
                name = default_name if isinstance(key, int) else str(key)
                poly = parse_roi_string(str(val))
            if poly.ndim == 2 and poly.shape[1] == 2 and len(poly) >= 3:
                rois.append(ROI(name=name, poly=poly, path=MplPath(poly)))
        except Exception:
            continue
    return rois


def point_to_segment_distance(px: np.ndarray, a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Vectorized point-to-line-segment distance."""
    ap = px - a
    ab = b - a
    denom = float(np.dot(ab, ab))
    if denom <= 0:
        return np.sqrt(np.sum((px - a) ** 2, axis=1))
    t = np.clip(np.sum(ap * ab, axis=1) / denom, 0, 1)
    proj = a + t[:, None] * ab
    return np.sqrt(np.sum((px - proj) ** 2, axis=1))


def distance_to_polygon_boundary(points: np.ndarray, poly: np.ndarray) -> np.ndarray:
    """Minimum distance from each point to polygon boundary."""
    dists = []
    for i in range(len(poly)):
        dists.append(point_to_segment_distance(points, poly[i], poly[(i + 1) % len(poly)]))
    return np.min(np.vstack(dists), axis=0)


# -----------------------------------------------------------------------------
# Individual metrics shared by BA and fight pipelines
# -----------------------------------------------------------------------------

def interpolate_positions(xy_raw: np.ndarray, max_step_px: float) -> Tuple[np.ndarray, Dict[str, Any], np.ndarray]:
    """Filter large one-frame jumps and interpolate internal missing gaps.

    Leading and trailing NaNs are kept as missing.  That matters because
    IDtracker.ai may store frames before tracking starts as NaN; we do not want
    to convert those into false stationary behavior.
    """
    xy = np.asarray(xy_raw, dtype=float).copy()
    n = len(xy)
    finite_orig = np.isfinite(xy[:, 0]) & np.isfinite(xy[:, 1]) if n else np.zeros(0, dtype=bool)
    artifact = np.zeros(n, dtype=bool)

    if n >= 2 and max_step_px and max_step_px > 0:
        diffs = np.sqrt(np.sum(np.diff(xy, axis=0) ** 2, axis=1))
        bad_steps = np.isfinite(diffs) & (diffs > max_step_px)
        artifact[1:][bad_steps] = True
        xy[artifact, :] = np.nan

    finite_after_artifact = np.isfinite(xy[:, 0]) & np.isfinite(xy[:, 1]) if n else np.zeros(0, dtype=bool)
    out = xy.copy()
    interpolated = np.zeros(n, dtype=bool)
    idx = np.arange(n)

    if finite_after_artifact.any():
        first_valid = int(np.where(finite_after_artifact)[0][0])
        last_valid = int(np.where(finite_after_artifact)[0][-1])
        internal_range = (idx >= first_valid) & (idx <= last_valid)
        for dim in [0, 1]:
            vals = out[:, dim]
            good = np.isfinite(vals)
            if good.sum() < 2:
                continue
            fill = (~good) & internal_range
            if fill.any():
                vals[fill] = np.interp(idx[fill], idx[good], vals[good])
                interpolated[fill] = True
            out[:, dim] = vals

    finite_final = np.isfinite(out[:, 0]) & np.isfinite(out[:, 1]) if n else np.zeros(0, dtype=bool)
    raw_missing = ~finite_orig
    unfilled_missing = ~finite_final
    qc = {
        "valid_position_fraction_before_interpolation": float(finite_orig.mean()) if n else np.nan,
        "n_missing_position_frames_raw": int(raw_missing.sum()),
        "n_missing_position_frames_interpolated": int((raw_missing & interpolated).sum()),
        "max_step_px_for_artifact_filter": float(max_step_px),
        "n_artifact_jump_frames_interpolated": int((artifact & interpolated).sum()),
        "n_total_interpolated_position_frames": int(interpolated.sum()),
        "n_unfilled_missing_position_frames_after_interpolation": int(unfilled_missing.sum()),
        "fraction_total_interpolated_position_frames": float(interpolated.mean()) if n else np.nan,
        "fraction_unfilled_missing_position_frames_after_interpolation": float(unfilled_missing.mean()) if n else np.nan,
    }
    return out, qc, interpolated


def first_crossing(values: np.ndarray, threshold: float) -> Optional[int]:
    idx = np.where(values >= threshold)[0]
    return int(idx[0]) if idx.size else None


def sustained_onset(displacement: np.ndarray, threshold: float, consecutive: int) -> Optional[int]:
    above = displacement >= threshold
    runs = true_runs(above, max(1, consecutive))
    return runs[0][0] if runs else None


def detect_turtling(xy: np.ndarray, onset_idx: Optional[int], args: argparse.Namespace) -> Tuple[pd.DataFrame, np.ndarray]:
    """Centroid-only turtling-like event detector.

    This is not a validated posture classifier. It flags intervals with enough
    path length but low net displacement and low spatial spread, after movement
    onset. The goal is QC/event discovery for later video review.
    """
    n = len(xy)
    mask = np.zeros(n, dtype=bool)
    if getattr(args, "disable_turtling", False) or n < args.turtling_window_frames:
        return pd.DataFrame(), mask

    step_vec = np.diff(xy, axis=0)
    speed = np.sqrt(np.sum(step_vec ** 2, axis=1))
    headings = np.arctan2(step_vec[:, 1], step_vec[:, 0])
    turns = np.abs(np.arctan2(np.sin(np.diff(headings)), np.cos(np.diff(headings))))

    start_allowed = 0 if onset_idx is None else int(onset_idx + args.turtling_start_buffer_frames)
    w = int(args.turtling_window_frames)
    candidate = np.zeros(n, dtype=bool)

    for s in range(max(0, start_allowed), n - w + 1):
        e = s + w - 1
        seg = xy[s:e+1]
        if not np.isfinite(seg).all():
            continue
        path = float(np.nansum(speed[s:e]))
        net = float(np.linalg.norm(seg[-1] - seg[0]))
        centroid = np.nanmean(seg, axis=0)
        rg = float(np.sqrt(np.nanmean(np.sum((seg - centroid) ** 2, axis=1))))
        straight = net / path if path > 0 else np.nan
        t0 = max(s, 0)
        t1 = max(s, min(e - 1, len(turns)))
        mean_turn = float(np.nanmean(turns[t0:t1])) if t1 > t0 else 0.0
        is_candidate = (
            path >= args.turtling_min_path_px and
            net <= args.turtling_max_net_displacement_px and
            rg <= args.turtling_max_radius_gyration_px and
            np.isfinite(straight) and straight <= args.turtling_max_straightness and
            mean_turn >= args.turtling_min_abs_turn_rad
        )
        if is_candidate:
            candidate[s:e+1] = True

    runs = merge_runs(true_runs(candidate, args.turtling_min_duration_frames), args.turtling_merge_gap_frames)
    records: List[Dict[str, Any]] = []
    for s, e in runs:
        mask[s:e+1] = True
        seg = xy[s:e+1]
        path = float(np.nansum(speed[s:e]))
        net = float(np.linalg.norm(seg[-1] - seg[0]))
        centroid = np.nanmean(seg, axis=0)
        rg = float(np.sqrt(np.nanmean(np.sum((seg - centroid) ** 2, axis=1))))
        straight = net / path if path > 0 else np.nan
        t0 = max(s, 0)
        t1 = max(s, min(e - 1, len(turns)))
        mean_turn = float(np.nanmean(turns[t0:t1])) if t1 > t0 else np.nan
        records.append({
            "start_frame_local": int(s),
            "end_frame_local": int(e),
            "duration_frames": int(e - s + 1),
            "path_px": path,
            "net_displacement_px": net,
            "radius_gyration_px": rg,
            "straightness": straight,
            "mean_abs_turn_rad": mean_turn,
        })
    return pd.DataFrame(records), mask


def analyze_individual(
    xy_raw: np.ndarray,
    animal_index: int,
    global_start_frame: int,
    rois: List[ROI],
    session_name: str,
    n_animals_in_file: int,
    args: argparse.Namespace,
) -> Tuple[Dict[str, Any], pd.DataFrame, pd.DataFrame, np.ndarray, np.ndarray, np.ndarray, Optional[int]]:
    """Calculate BA-style metrics for one animal."""
    xy, qc, interpolated_mask = interpolate_positions(xy_raw, args.max_step_px)
    n = len(xy)
    frames_local = np.arange(n)
    frames_global = frames_local + global_start_frame
    finite = np.isfinite(xy[:, 0]) & np.isfinite(xy[:, 1]) if n else np.zeros(0, dtype=bool)

    first_valid_idx = int(np.where(finite)[0][0]) if finite.any() else None
    last_valid_idx = int(np.where(finite)[0][-1]) if finite.any() else None

    step = np.sqrt(np.sum(np.diff(xy, axis=0) ** 2, axis=1)) if n > 1 else np.array([])
    step_full = np.r_[np.nan, step] if n else np.array([])
    total_distance = float(np.nansum(step)) if len(step) else 0.0

    displacement = np.full(n, np.nan, dtype=float)
    if first_valid_idx is not None:
        start_xy = xy[first_valid_idx]
        displacement[finite] = np.sqrt(np.sum((xy[finite] - start_xy) ** 2, axis=1))
    net_displacement = float(np.linalg.norm(xy[last_valid_idx] - xy[first_valid_idx])) if first_valid_idx is not None and last_valid_idx is not None else np.nan
    path_straightness = net_displacement / total_distance if total_distance > 0 else np.nan

    first_disp_idx = first_crossing(displacement, args.move_threshold_px)
    sustained_idx = sustained_onset(displacement, args.move_threshold_px, args.movement_onset_consecutive_frames)

    cumdist = np.full(n, np.nan, dtype=float)
    if first_valid_idx is not None:
        cumdist[first_valid_idx] = 0.0
        for i in range(first_valid_idx + 1, n):
            if finite[i] and np.isfinite(cumdist[i - 1]):
                inc = step[i - 1] if i - 1 < len(step) and np.isfinite(step[i - 1]) else 0.0
                cumdist[i] = cumdist[i - 1] + inc
    cum_idx = first_crossing(cumdist, args.move_threshold_px)

    speed_threshold = args.speed_moving_threshold_px_frame
    if speed_threshold is None or not np.isfinite(speed_threshold):
        finite_step = step[np.isfinite(step)]
        speed_threshold = max(1.0, float(np.nanpercentile(finite_step, 10))) if finite_step.size else 1.0
    moving_pairs = step >= speed_threshold

    turtling_events, turtling_mask = detect_turtling(xy, sustained_idx, args)

    # ROI 0: primary arena/cell. ROI 1: optional secondary/inner ROI.
    inside_roi = np.zeros(n, dtype=bool)
    in_border = np.zeros(n, dtype=bool)
    dist_wall = np.full(n, np.nan, dtype=float)
    roi_name = ""
    if rois and n:
        roi = rois[0]
        roi_name = roi.name
        inside_roi[finite] = roi.path.contains_points(xy[finite])
        dist_wall[finite] = distance_to_polygon_boundary(xy[finite], roi.poly)
        in_border = inside_roi & np.isfinite(dist_wall) & (dist_wall <= args.roi_wall_buffer_px)
    not_border = inside_roi & (~in_border)

    inside_secondary = np.zeros(n, dtype=bool)
    secondary_name = ""
    if len(rois) >= 2 and n:
        secondary = rois[1]
        secondary_name = secondary.name
        inside_secondary[finite] = secondary.path.contains_points(xy[finite])

    active_after_disp = np.zeros(n, dtype=bool)
    if first_disp_idx is not None:
        active_after_disp[first_disp_idx:] = True
    active_after_sust = np.zeros(n, dtype=bool)
    if sustained_idx is not None:
        active_after_sust[sustained_idx:] = True

    per_frame = pd.DataFrame({
        "frame_local": frames_local,
        "frame_global": frames_global,
        "animal_index_0_based": animal_index,
        "x_px": xy[:, 0] if n else [],
        "y_px": xy[:, 1] if n else [],
        "step_px": step_full,
        "cumulative_distance_px": cumdist,
        "displacement_from_start_px": displacement,
        "position_interpolated": interpolated_mask,
        "is_valid_position": finite,
        "inside_primary_roi": inside_roi,
        "distance_to_primary_roi_wall_px": dist_wall,
        "in_primary_roi_border_buffer": in_border,
        "not_in_primary_roi_border_buffer": not_border,
        "inside_secondary_roi": inside_secondary,
        "turtling_like": turtling_mask,
    })

    def remaining_after(idx: Optional[int]) -> Tuple[float, int, float]:
        if idx is None or idx >= n:
            return (np.nan, 0, np.nan)
        remain = float(np.nansum(step[idx:])) if idx < len(step) else 0.0
        avail = int(n - idx)
        return (remain, avail, remain / avail if avail > 0 else np.nan)

    rem_disp, avail_disp, rate_disp = remaining_after(first_disp_idx)
    rem_sust, avail_sust, rate_sust = remaining_after(sustained_idx)
    active_pos_disp = active_after_disp & finite
    active_pos_sust = active_after_sust & finite

    summary: Dict[str, Any] = {
        "session_name": session_name,
        "animal_index_0_based": int(animal_index),
        "n_animals_in_file": int(n_animals_in_file),
        "analysis_start_frame": int(global_start_frame),
        "analysis_end_frame_inclusive": int(global_start_frame + n - 1) if n else np.nan,
        "requested_window_frames": int(args.window_frames) if args.window_frames is not None else "all_available",
        "actual_window_frames": int(n),
        "first_valid_position_frame_local": first_valid_idx if first_valid_idx is not None else np.nan,
        "first_valid_position_frame_global": int(global_start_frame + first_valid_idx) if first_valid_idx is not None else np.nan,
        "last_valid_position_frame_local": last_valid_idx if last_valid_idx is not None else np.nan,
        "last_valid_position_frame_global": int(global_start_frame + last_valid_idx) if last_valid_idx is not None else np.nan,
        **qc,
        "move_threshold_px": float(args.move_threshold_px),
        "movement_onset_consecutive_frames": int(args.movement_onset_consecutive_frames),
        "latency_to_displacement_threshold_frames": first_disp_idx if first_disp_idx is not None else np.nan,
        "global_frame_displacement_threshold_crossed": int(global_start_frame + first_disp_idx) if first_disp_idx is not None else np.nan,
        "latency_to_sustained_displacement_threshold_frames": sustained_idx if sustained_idx is not None else np.nan,
        "global_frame_sustained_displacement_threshold_crossed": int(global_start_frame + sustained_idx) if sustained_idx is not None else np.nan,
        "latency_to_cumulative_distance_threshold_frames": cum_idx if cum_idx is not None else np.nan,
        "global_frame_cumulative_threshold_crossed": int(global_start_frame + cum_idx) if cum_idx is not None else np.nan,
        "total_distance_px": total_distance,
        "net_displacement_px": net_displacement,
        "max_displacement_from_start_px": float(np.nanmax(displacement)) if displacement.size else np.nan,
        "path_straightness_net_over_total": path_straightness,
        "speed_threshold_for_moving_px_per_frame": float(speed_threshold),
        "moving_frame_pairs": int(np.nansum(moving_pairs)),
        "moving_fraction_frame_pairs": float(np.nanmean(moving_pairs)) if len(moving_pairs) else np.nan,
        "remaining_distance_after_displacement_threshold_px": rem_disp,
        "available_frames_after_displacement_threshold": avail_disp,
        "distance_per_available_frame_after_displacement_threshold_px": rate_disp,
        "remaining_distance_after_sustained_displacement_threshold_px": rem_sust,
        "available_frames_after_sustained_displacement_threshold": avail_sust,
        "distance_per_available_frame_after_sustained_displacement_threshold_px": rate_sust,
        "roi_wall_buffer_px": float(args.roi_wall_buffer_px),
        "primary_roi_name": roi_name,
        "frames_inside_primary_roi": int(inside_roi.sum()),
        "frames_in_primary_roi_border_buffer": int(in_border.sum()),
        "frames_not_in_primary_roi_border_buffer": int(not_border.sum()),
        "fraction_frames_not_in_primary_roi_border_buffer_total": float(not_border.sum() / n) if n else np.nan,
        "active_position_frames_after_displacement_threshold": int(active_pos_disp.sum()),
        "active_frames_in_primary_roi_border_buffer": int((active_pos_disp & in_border).sum()),
        "active_frames_not_in_primary_roi_border_buffer": int((active_pos_disp & not_border).sum()),
        "fraction_active_frames_in_primary_roi_border_buffer": float((active_pos_disp & in_border).sum() / active_pos_disp.sum()) if active_pos_disp.sum() else np.nan,
        "fraction_active_frames_not_in_primary_roi_border_buffer": float((active_pos_disp & not_border).sum() / active_pos_disp.sum()) if active_pos_disp.sum() else np.nan,
        "active_position_frames_after_sustained_displacement_threshold": int(active_pos_sust.sum()),
        "active_frames_in_primary_roi_border_buffer_after_sustained_threshold": int((active_pos_sust & in_border).sum()),
        "active_frames_not_in_primary_roi_border_buffer_after_sustained_threshold": int((active_pos_sust & not_border).sum()),
        "secondary_roi_name": secondary_name,
        "frames_inside_secondary_roi": int(inside_secondary.sum()),
        "seconds_inside_secondary_roi": float(inside_secondary.sum() / args.fps) if args.fps else np.nan,
        "fraction_frames_inside_secondary_roi_total": float(inside_secondary.sum() / n) if n else np.nan,
        "turtling_detection_enabled": not bool(args.disable_turtling),
        "turtling_event_count": int(len(turtling_events)),
        "turtling_total_frames": int(turtling_mask.sum()),
        "turtling_fraction_frames": float(turtling_mask.sum() / n) if n else np.nan,
    }
    return summary, per_frame, turtling_events, xy, turtling_mask, interpolated_mask, sustained_idx


# -----------------------------------------------------------------------------
# Pairwise fight metrics
# -----------------------------------------------------------------------------

def pairwise_metrics(xy0: np.ndarray, xy1: np.ndarray, global_start_frame: int, rois: List[ROI], args: argparse.Namespace) -> Tuple[Dict[str, Any], pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Calculate fight/contact metrics between two beetles."""
    n = min(len(xy0), len(xy1))
    xy0 = xy0[:n]
    xy1 = xy1[:n]
    frames_local = np.arange(n)
    frames_global = frames_local + global_start_frame
    dist = np.sqrt(np.sum((xy0 - xy1) ** 2, axis=1))
    both_valid = np.isfinite(dist)

    contact = both_valid & (dist <= args.contact_px)
    min_contact_frames = max(1, int(round(args.min_contact_s * args.fps)))
    contact_runs = true_runs(contact, min_contact_frames)

    fight_like = both_valid & (dist <= args.fight_px)
    fight_runs = true_runs(fight_like, max(1, args.min_fight_frames))

    finite0 = np.isfinite(xy0[:, 0]) & np.isfinite(xy0[:, 1])
    finite1 = np.isfinite(xy1[:, 0]) & np.isfinite(xy1[:, 1])
    animal0_secondary = np.zeros(n, dtype=bool)
    animal1_secondary = np.zeros(n, dtype=bool)
    secondary_name = ""
    if len(rois) >= 2:
        secondary_name = rois[1].name
        animal0_secondary[finite0] = rois[1].path.contains_points(xy0[finite0])
        animal1_secondary[finite1] = rois[1].path.contains_points(xy1[finite1])
    both_secondary = animal0_secondary & animal1_secondary
    either_secondary = animal0_secondary | animal1_secondary

    per_frame = pd.DataFrame({
        "frame_local": frames_local,
        "frame_global": frames_global,
        "time_s": frames_local / args.fps if args.fps else np.nan,
        "animal0_x_px": xy0[:, 0],
        "animal0_y_px": xy0[:, 1],
        "animal1_x_px": xy1[:, 0],
        "animal1_y_px": xy1[:, 1],
        "distance_px": dist,
        "both_valid": both_valid,
        "contact_le_threshold_px": contact,
        "possible_fight_le_threshold_px": fight_like,
        "animal0_inside_secondary_roi": animal0_secondary,
        "animal1_inside_secondary_roi": animal1_secondary,
        "both_animals_inside_secondary_roi": both_secondary,
        "either_animal_inside_secondary_roi": either_secondary,
        "contact_inside_secondary_roi": contact & both_secondary,
    })

    def event_records(runs: List[Tuple[int, int]], event_type: str) -> List[Dict[str, Any]]:
        records = []
        for s, e in runs:
            records.append({
                "event_type": event_type,
                "start_frame_local": int(s),
                "end_frame_local": int(e),
                "start_frame_global": int(global_start_frame + s),
                "end_frame_global": int(global_start_frame + e),
                "start_time_s": float(s / args.fps) if args.fps else np.nan,
                "end_time_s": float(e / args.fps) if args.fps else np.nan,
                "duration_frames": int(e - s + 1),
                "duration_s": float((e - s + 1) / args.fps) if args.fps else np.nan,
                "min_distance_px": float(np.nanmin(dist[s:e+1])),
                "mean_distance_px": float(np.nanmean(dist[s:e+1])),
            })
        return records

    contact_events = pd.DataFrame(event_records(contact_runs, "contact"))
    fight_events = pd.DataFrame(event_records(fight_runs, "possible_fight_close_contact"))

    summary = {
        "analysis_start_frame": int(global_start_frame),
        "actual_window_frames": int(n),
        "fps": float(args.fps),
        "contact_px": float(args.contact_px),
        "min_contact_s": float(args.min_contact_s),
        "min_contact_frames": int(min_contact_frames),
        "fight_px": float(args.fight_px),
        "min_fight_frames": int(args.min_fight_frames),
        "valid_pair_frames": int(both_valid.sum()),
        "fraction_valid_pair_frames": float(both_valid.mean()) if n else np.nan,
        "mean_pairwise_distance_px": float(np.nanmean(dist)) if np.isfinite(dist).any() else np.nan,
        "median_pairwise_distance_px": float(np.nanmedian(dist)) if np.isfinite(dist).any() else np.nan,
        "min_pairwise_distance_px": float(np.nanmin(dist)) if np.isfinite(dist).any() else np.nan,
        "contact_frame_count": int(contact.sum()),
        "contact_fraction_all_frames": float(contact.sum() / n) if n else np.nan,
        "contact_event_count": int(len(contact_events)),
        "contact_total_duration_frames": int(sum(e - s + 1 for s, e in contact_runs)),
        "contact_total_duration_s": float(sum(e - s + 1 for s, e in contact_runs) / args.fps) if args.fps else np.nan,
        "possible_fight_frame_count": int(fight_like.sum()),
        "possible_fight_fraction_all_frames": float(fight_like.sum() / n) if n else np.nan,
        "possible_fight_event_count": int(len(fight_events)),
        "possible_fight_total_duration_frames": int(sum(e - s + 1 for s, e in fight_runs)),
        "possible_fight_total_duration_s": float(sum(e - s + 1 for s, e in fight_runs) / args.fps) if args.fps else np.nan,
        "secondary_roi_name": secondary_name,
        "both_animals_inside_secondary_roi_frames": int(both_secondary.sum()),
        "either_animal_inside_secondary_roi_frames": int(either_secondary.sum()),
        "contact_inside_secondary_roi_frames": int((contact & both_secondary).sum()),
    }
    return summary, per_frame, contact_events, fight_events


# -----------------------------------------------------------------------------
# Plotting and data dictionary output
# -----------------------------------------------------------------------------

def draw_track_map(out_path: Path, title: str, rois: List[ROI], tracks: List[Tuple[str, np.ndarray, np.ndarray, np.ndarray, Optional[int]]], args: argparse.Namespace) -> None:
    """Write a QC map with normal track, interpolated points, and turtling points.

    The map is deliberately split into a data panel and a separate legend panel.
    Earlier versions placed the legend "outside" the axes using bbox_to_anchor,
    but matplotlib still let it overlap the trajectory area on some Firebird
    backends. A dedicated legend axis is more reliable and easier for students
    to interpret.
    """
    fig = plt.figure(figsize=(10.5, 7.2))
    grid = fig.add_gridspec(nrows=1, ncols=2, width_ratios=[4.8, 1.7], wspace=0.05)
    ax = fig.add_subplot(grid[0, 0])
    legend_ax = fig.add_subplot(grid[0, 1])
    legend_ax.axis("off")

    for roi in rois:
        patch = MplPolygon(roi.poly, fill=False, linewidth=1.0, linestyle="-" if roi == rois[0] else "--")
        ax.add_patch(patch)

    colors = ["tab:orange", "tab:blue", "tab:green", "tab:red"]
    for i, (label, xy, turtled, interpolated, onset_idx) in enumerate(tracks):
        color = colors[i % len(colors)]
        finite = np.isfinite(xy[:, 0]) & np.isfinite(xy[:, 1]) if len(xy) else np.zeros(0, dtype=bool)
        if finite.any():
            ax.plot(xy[finite, 0], xy[finite, 1], linewidth=args.track_linewidth, color=color, label=f"{label} track")
            first = int(np.where(finite)[0][0])
            last = int(np.where(finite)[0][-1])
            ax.scatter([xy[first, 0]], [xy[first, 1]], marker="o", s=35, color=color, label=f"{label} start")
            ax.scatter([xy[last, 0]], [xy[last, 1]], marker="s", s=35, color=color, label=f"{label} end")
            if onset_idx is not None and onset_idx < len(xy) and np.isfinite(xy[onset_idx]).all():
                ax.scatter([xy[onset_idx, 0]], [xy[onset_idx, 1]], marker="*", s=80, color=color, label=f"{label} sustained onset")

        idx_interp = np.where(interpolated & finite)[0]
        if len(idx_interp) > args.map_max_overlay_points:
            idx_interp = np.linspace(idx_interp[0], idx_interp[-1], args.map_max_overlay_points).astype(int)
        if len(idx_interp):
            ax.scatter(xy[idx_interp, 0], xy[idx_interp, 1], marker="x", s=14, color="0.55", alpha=0.85, label=f"{label} interpolated")

        idx_turt = np.where(turtled & finite)[0]
        if len(idx_turt) > args.map_max_overlay_points:
            idx_turt = np.linspace(idx_turt[0], idx_turt[-1], args.map_max_overlay_points).astype(int)
        if len(idx_turt):
            ax.scatter(xy[idx_turt, 0], xy[idx_turt, 1], marker="D", s=10, color="black", alpha=0.85, label=f"{label} turtling-like")

    ax.set_title(title)
    ax.set_xlabel("x position, pixels")
    ax.set_ylabel("y position, pixels")
    ax.set_aspect("equal", adjustable="box")
    ax.invert_yaxis()

    handles, labels = ax.get_legend_handles_labels()
    if handles:
        # Remove exact duplicate legend labels while preserving order.
        seen = set()
        uniq_handles = []
        uniq_labels = []
        for h, lab in zip(handles, labels):
            if lab not in seen:
                uniq_handles.append(h)
                uniq_labels.append(lab)
                seen.add(lab)
        legend_ax.legend(uniq_handles, uniq_labels, loc="center left", fontsize=7, frameon=True, title="Map legend", title_fontsize=8)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=200, bbox_inches="tight")
    plt.close(fig)


def write_data_dictionary(out_path: Path, pipeline: str) -> None:
    """Write a compact data dictionary for common outputs."""
    rows = [
        ("metadata_tag", "User-entered tag for this TOML/video set; useful after concatenating output spreadsheets"),
        ("original_video_path", "Path to the source AVI/MP4/MOV file recorded in the TOML manifest"),
        ("original_video_name", "Filename of the source video used by IDtracker.ai"),
        ("original_video_stem", "Filename stem of the source video; used for output folder and file names"),
        ("toml_path", "Path to the IDtracker.ai TOML file used for this run"),
        ("toml_name", "Name of the IDtracker.ai TOML file used for this run"),
        ("idtracker_session_name", "Original IDtracker.ai session folder name"),
        ("session_name", "IDtracker.ai session folder name"),
        ("animal_index_0_based", "Animal index in the trajectory array, starting at 0"),
        ("analysis_start_frame", "First global frame included in analysis"),
        ("requested_window_frames", "Requested number of frames after the start frame"),
        ("actual_window_frames", "Actual number of frames analyzed"),
        ("total_distance_px", "Total centroid path length in pixels"),
        ("net_displacement_px", "Straight-line distance from first to last valid position"),
        ("latency_to_sustained_displacement_threshold_frames", "First frame of sustained movement onset"),
        ("n_total_interpolated_position_frames", "Frames filled by interpolation after artifact/missing filtering"),
        ("frames_in_primary_roi_border_buffer", "Frames inside ROI and within wall buffer distance"),
        ("turtling_event_count", "Number of centroid-based turtling-like events"),
    ]
    if pipeline == "fight":
        rows.extend([
            ("contact_event_count", "Number of pairwise contact/proximity events"),
            ("possible_fight_event_count", "Number of very-close-contact candidate fight-like events"),
            ("mean_pairwise_distance_px", "Mean distance between the two beetles in pixels"),
            ("contact_px", "Pairwise distance threshold for contact/proximity"),
            ("fight_px", "Pairwise distance threshold for candidate fight-like events"),
        ])
    pd.DataFrame(rows, columns=["column", "meaning"]).to_csv(out_path, index=False)


# -----------------------------------------------------------------------------
# Session-level and batch-level runners
# -----------------------------------------------------------------------------

def crop_trajectory(arr: np.ndarray, start: int, window: Optional[int]) -> np.ndarray:
    """Apply analysis start and optional window length."""
    start = max(0, int(start))
    if window is None:
        return arr[start:]
    return arr[start:start + int(window)]


def load_rois_for_session(session_folder: Path, args: argparse.Namespace) -> List[ROI]:
    """Prefer explicit TOML, then session.json ROIs, then no ROI."""
    rois: List[ROI] = []
    if args.roi_toml:
        rois = load_rois_from_toml(Path(args.roi_toml).expanduser().resolve())
    if not rois:
        session_data = read_json_file(find_nearby_file(session_folder, "session.json"))
        rois = load_rois_from_session(session_data)
    return rois


def run_one_session(session_folder: Path, trajectory_file: Path, outdir: Path, source_meta: Dict[str, Any], args: argparse.Namespace) -> Dict[str, Any]:
    """Run BA or fight analysis on one IDtracker.ai session."""
    outdir.mkdir(parents=True, exist_ok=True)
    session_name = safe_stem(session_folder.name)
    output_stem = output_stem_from_metadata(source_meta, session_folder)
    arr = load_trajectory_array(trajectory_file)
    n_animals = arr.shape[1]
    # A negative command-line start frame means: use the per-cell start frame
    # parsed from the TOML and stored in source_metadata.json/manifest. This is
    # the normal project-manager behavior, because each arena/cell TOML can have
    # its own start frame.
    if int(args.analysis_start_frame) < 0:
        try:
            analysis_start_frame = int(source_meta.get("toml_analysis_start_frame", 0) or 0)
        except Exception:
            analysis_start_frame = 0
    else:
        analysis_start_frame = int(args.analysis_start_frame)
    arr_crop = crop_trajectory(arr, analysis_start_frame, args.window_frames)
    rois = load_rois_for_session(session_folder, args)

    manifest_row: Dict[str, Any] = {
        **metadata_columns(source_meta),
        "session_folder": str(session_folder),
        "trajectory_file": str(trajectory_file),
        "output_dir": str(outdir),
        "session_name": session_name,
        "pipeline": args.pipeline,
        "analysis_start_frame_used": int(analysis_start_frame),
        "n_animals_in_file": int(n_animals),
        "status": "ok",
        "error": "",
    }

    individual_summaries = []
    tracks_for_map = []

    if args.pipeline == "ba":
        if args.animal_index >= n_animals:
            raise ValueError(f"BA animal-index {args.animal_index} is not available; file has {n_animals} animals")
        animal_indices = [args.animal_index]
    else:
        if args.animal0 >= n_animals or args.animal1 >= n_animals:
            raise ValueError(f"Fight animal indices {args.animal0},{args.animal1} are not available; file has {n_animals} animals")
        animal_indices = [args.animal0, args.animal1]

    cleaned_by_animal: Dict[int, np.ndarray] = {}
    for animal_index in animal_indices:
        summary, per_frame, turt_events, xy, turt_mask, interp_mask, onset_idx = analyze_individual(
            arr_crop[:, animal_index, :], animal_index, analysis_start_frame, rois, session_name, n_animals, args
        )
        summary.update(metadata_columns(source_meta))
        individual_summaries.append(summary)
        cleaned_by_animal[animal_index] = xy
        add_metadata_to_df(per_frame, source_meta).to_csv(outdir / f"{output_stem}_animal{animal_index}_per_frame_kinematics.csv", index=False)
        add_metadata_to_df(turt_events, source_meta).to_csv(outdir / f"{output_stem}_turtling_events_animal{animal_index}.csv", index=False)
        tracks_for_map.append((f"animal{animal_index}", xy, turt_mask, interp_mask, onset_idx))

    ind_df = pd.DataFrame(individual_summaries)
    if args.pipeline == "ba":
        add_metadata_to_df(ind_df, source_meta).to_csv(outdir / f"{output_stem}_ba_individual_summary.csv", index=False)
    else:
        add_metadata_to_df(ind_df, source_meta).to_csv(outdir / f"{output_stem}_fight_individual_summary.csv", index=False)

    if args.pipeline == "fight":
        pair_summary, pair_frame, contact_events, fight_events = pairwise_metrics(
            cleaned_by_animal[args.animal0], cleaned_by_animal[args.animal1], analysis_start_frame, rois, args
        )
        pair_summary["session_name"] = session_name
        pair_summary.update(metadata_columns(source_meta))
        add_metadata_to_df(pd.DataFrame([pair_summary]), source_meta).to_csv(outdir / f"{output_stem}_fight_pair_summary.csv", index=False)
        add_metadata_to_df(pair_frame, source_meta).to_csv(outdir / f"{output_stem}_per_frame_pairwise.csv", index=False)
        add_metadata_to_df(contact_events, source_meta).to_csv(outdir / f"{output_stem}_contact_events.csv", index=False)
        add_metadata_to_df(fight_events, source_meta).to_csv(outdir / f"{output_stem}_possible_fight_events.csv", index=False)

        # InqScribe-compatible event list.
        inq = pd.concat([contact_events, fight_events], ignore_index=True, sort=False) if len(contact_events) or len(fight_events) else pd.DataFrame()
        if len(inq):
            inq_out = pd.DataFrame({
                "Time": [fmt_hhmmss_comma_ms(x) for x in inq["start_time_s"]],
                "Text": inq["event_type"].astype(str) + " " + inq["duration_frames"].astype(str) + " frames",
            })
        else:
            inq_out = pd.DataFrame(columns=["Time", "Text"])
        inq_out.to_csv(outdir / f"{output_stem}_InqScribe_{int(args.contact_px)}px_{int(args.fps)}fps.txt", sep="\t", index=False)

    draw_track_map(outdir / f"{output_stem}_track_map.png", f"{args.pipeline.upper()} {output_stem}", rois, tracks_for_map, args)
    write_data_dictionary(outdir / f"{output_stem}_summary_data_dictionary.csv", args.pipeline)
    return manifest_row


def expected_outputs(outdir: Path, pipeline: str) -> bool:
    if pipeline == "ba":
        return bool(list(outdir.glob("*_ba_individual_summary.csv")))
    return bool(list(outdir.glob("*_fight_pair_summary.csv"))) and bool(list(outdir.glob("*_fight_individual_summary.csv")))


def merge_csvs(paths: List[Path], output_path: Path) -> None:
    frames = []
    for p in paths:
        try:
            if p.exists() and p.stat().st_size > 0:
                frames.append(pd.read_csv(p))
        except Exception:
            pass
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if frames:
        pd.concat(frames, ignore_index=True, sort=False).to_csv(output_path, index=False)
    else:
        pd.DataFrame().to_csv(output_path, index=False)


def run_batch(args: argparse.Namespace) -> int:
    search_root = Path(args.search_root).expanduser().resolve()
    output_root = Path(args.output_root).expanduser().resolve()
    output_root.mkdir(parents=True, exist_ok=True)

    sessions = find_sessions(search_root)
    if args.limit is not None:
        sessions = sessions[:args.limit]

    if args.make_manifest_only:
        rows = []
        for x in sessions:
            outdir = output_root / safe_stem(x["session_folder"].name)
            rows.append({"session_folder": str(x["session_folder"]), "trajectory_file": str(x["trajectory_file"]), "output_dir": str(outdir)})
        pd.DataFrame(rows).to_csv(output_root / "postprocessing_manifest_preview.csv", index=False)
        print(f"Wrote manifest preview for {len(rows)} sessions")
        return 0

    manifest_rows: List[Dict[str, Any]] = []
    session_outputs_root = output_root / "session_outputs"
    session_outputs_root.mkdir(exist_ok=True)
    all_maps = output_root / "all_track_maps"
    all_maps.mkdir(exist_ok=True)

    for i, item in enumerate(sessions, start=1):
        session_folder = item["session_folder"]
        trajectory_file = item["trajectory_file"]
        source_meta = resolve_source_metadata(session_folder, args)
        output_stem = output_stem_from_metadata(source_meta, session_folder)
        outdir = session_outputs_root / output_stem
        print(f"[{i}/{len(sessions)}] {session_folder}  ->  {output_stem}")
        try:
            if expected_outputs(outdir, args.pipeline) and not args.overwrite:
                row = {**metadata_columns(source_meta), "session_folder": str(session_folder), "trajectory_file": str(trajectory_file), "output_dir": str(outdir), "pipeline": args.pipeline, "status": "skipped_existing", "error": ""}
            else:
                row = run_one_session(session_folder, trajectory_file, outdir, source_meta, args)
            for png in outdir.glob("*_track_map.png"):
                try:
                    # Keep the all_track_maps filename identical to the per-session
                    # map filename. Previous versions prepended the output stem twice,
                    # which made filenames unnecessarily long and confusing.
                    shutil.copy2(png, all_maps / png.name)
                except Exception:
                    pass
        except Exception as exc:
            row = {**metadata_columns(source_meta), "session_folder": str(session_folder), "trajectory_file": str(trajectory_file), "output_dir": str(outdir), "pipeline": args.pipeline, "status": "error", "error": repr(exc)}
            print(f"ERROR: {session_folder}: {exc}", file=sys.stderr)
        manifest_rows.append(row)

    manifest = output_root / "postprocessing_manifest.csv"
    manifest_df = pd.DataFrame(manifest_rows)
    manifest_df.to_csv(manifest, index=False)

    # Combined summary files. Generic filenames are stable for scripts; tagged
    # copies make exported/moved CSVs self-identifying in ordinary folders.
    # The metadata_tag column is also repeated inside every row.
    tag_source = ""
    for row in manifest_rows:
        tag_source = str(row.get("metadata_tag") or row.get("original_video_stem") or "")
        if tag_source:
            break
    tagged_prefix = safe_stem(tag_source) if tag_source else ""

    if args.pipeline == "ba":
        generic = output_root / "ba_individual_summary_all.csv"
        merge_csvs(list(output_root.glob("*/**/*_ba_individual_summary.csv")), generic)
        if tagged_prefix:
            shutil.copy2(generic, output_root / f"{tagged_prefix}_ba_individual_summary_all.csv")
    else:
        pair_generic = output_root / "fight_pair_summary_all.csv"
        ind_generic = output_root / "fight_individual_summary_all.csv"
        merge_csvs(list(output_root.glob("*/**/*_fight_pair_summary.csv")), pair_generic)
        merge_csvs(list(output_root.glob("*/**/*_fight_individual_summary.csv")), ind_generic)
        if tagged_prefix:
            shutil.copy2(pair_generic, output_root / f"{tagged_prefix}_fight_pair_summary_all.csv")
            shutil.copy2(ind_generic, output_root / f"{tagged_prefix}_fight_individual_summary_all.csv")

    # Strict completion accounting. The SLURM wrapper should not report success
    # unless every expected manifest row has a processed session or an explicit
    # skipped_existing status. This prevents a false COMPLETE marker when only
    # one cell was actually post-processed.
    expected_rows = read_manifest_rows(getattr(args, "metadata_manifest", None))
    expected_stems = [safe_stem(r.get("output_stem", "")) for r in expected_rows if r.get("output_stem", "")]
    seen_stems = set()
    bad_rows = []
    if not manifest_df.empty:
        for _, rr in manifest_df.iterrows():
            stem = safe_stem(str(rr.get("output_stem", "") or Path(str(rr.get("output_dir", ""))).name))
            if stem:
                seen_stems.add(stem)
            status = str(rr.get("status", ""))
            if status not in {"ok", "processed", "skipped_existing"}:
                bad_rows.append({"output_stem": stem, "status": status, "error": str(rr.get("error", ""))})
    missing = [s for s in expected_stems if s not in seen_stems]
    status_path = output_root / "postprocessing_status_by_cell.csv"
    status_records = []
    for stem in expected_stems:
        status_records.append({
            "output_stem": stem,
            "found_in_postprocessing_manifest": "YES" if stem in seen_stems else "NO",
            "status": "missing" if stem not in seen_stems else "seen",
        })
    pd.DataFrame(status_records).to_csv(status_path, index=False)

    if missing or bad_rows:
        fail = output_root / "_POSTPROCESS_INCOMPLETE_OR_FAILED.txt"
        fail.write_text(
            "Post-processing incomplete or failed.\n"
            f"Expected cells: {len(expected_stems)}\n"
            f"Seen cells: {len(seen_stems)}\n"
            f"Missing output_stems: {missing}\n"
            f"Bad rows: {bad_rows}\n"
            f"Manifest: {manifest}\n"
            f"Status table: {status_path}\n",
            encoding="utf-8",
        )
        print(f"ERROR: Post-processing incomplete. See {fail}", file=sys.stderr)
        return 2

    complete = output_root / "_POSTPROCESS_COMPLETE_ALL_CELLS.txt"
    complete.write_text(
        "Post-processing complete for all expected cells.\n"
        f"Expected cells: {len(expected_stems)}\n"
        f"Manifest: {manifest}\n",
        encoding="utf-8",
    )
    print(f"Done. Manifest: {manifest}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Unified BA/fight IDtracker.ai post-processing batch runner.")
    p.add_argument("--pipeline", choices=["ba", "fight"], required=True, help="Which post-processing pipeline to run.")
    p.add_argument("--search-root", required=True, help="Folder searched recursively for IDtracker.ai session/trajectory outputs.")
    p.add_argument("--output-root", required=True, help="Folder where post-processing outputs are written.")
    p.add_argument("--overwrite", action="store_true", help="Re-run sessions even if output summaries already exist.")
    p.add_argument("--limit", type=int, default=None, help="Process only first N discovered sessions. Useful for testing.")
    p.add_argument("--make-manifest-only", action="store_true", help="Only write a preview of discovered sessions and exit.")
    p.add_argument("--roi-toml", default=None, help="Optional shared ROI TOML file. Usually leave blank because session.json contains ROI data.")
    p.add_argument("--metadata-manifest", default=None, help="CSV linking TOML files to original video files; created by the GUI.")
    p.add_argument("--metadata-tag", default="", help="User-entered project/run tag copied to every output row.")
    p.add_argument("--toml-folder", default="", help="Folder containing original TOML files; copied to every output row.")

    # Shared analysis parameters.
    p.add_argument("--analysis-start-frame", type=int, default=-1, help="Frame to start analysis. Use -1 to read per-cell toml_analysis_start_frame from metadata manifest/source_metadata.json.")
    p.add_argument("--window-frames", type=int, default=7500)
    p.add_argument("--fps", type=float, default=30.0)
    p.add_argument("--move-threshold-px", type=float, default=30.0)
    p.add_argument("--movement-onset-consecutive-frames", type=int, default=30)
    p.add_argument("--max-step-px", type=float, default=30.0)
    p.add_argument("--speed-moving-threshold-px-frame", type=float, default=None)
    p.add_argument("--roi-wall-buffer-px", type=float, default=30.0)
    p.add_argument("--track-linewidth", type=float, default=0.4)
    p.add_argument("--map-max-overlay-points", type=int, default=400)

    # BA-specific.
    p.add_argument("--animal-index", type=int, default=0, help="BA animal index. Usually 0.")

    # Fight-specific.
    p.add_argument("--animal0", type=int, default=0)
    p.add_argument("--animal1", type=int, default=1)
    p.add_argument("--contact-px", type=float, default=60.0)
    p.add_argument("--min-contact-s", type=float, default=0.2)
    p.add_argument("--fight-px", type=float, default=35.0)
    p.add_argument("--min-fight-frames", type=int, default=6)

    # Turtling-like behavior settings. These are shared but most useful for BA.
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
