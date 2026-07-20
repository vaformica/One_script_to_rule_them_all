#!/usr/bin/env python3
"""
IDtracker.ai two-beetle combat post-processing.

This script is designed as a combat-specific companion to the one-beetle BA
post-processing workflow. It keeps the original combat outputs (pairwise
proximity/contact, NaN/missing events, InqScribe annotations, pairwise distance
CSV, ROI summaries, track maps) and adds BA-style per-individual summaries for
both beetles in the bin.

All distance and time quantities are reported in pixels and frames unless the
column name explicitly says otherwise. Seconds are only used for InqScribe/event
convenience and are computed from --fps.
"""
from __future__ import annotations

import argparse
import ast
import csv
import json
import math
import os
import re
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.backends.backend_pdf import PdfPages
from matplotlib.patches import Polygon as MplPolygon
from matplotlib.path import Path as MplPath

try:
    import h5py  # type: ignore
except Exception:  # pragma: no cover
    h5py = None

try:
    import tomllib  # py3.11+
except Exception:  # pragma: no cover
    import tomli as tomllib  # type: ignore


TRAJ_NAMES = [
    "trajectories.npy",
    "trajectories_wo_gaps.npy",
    "trajectories_without_gaps.npy",
    "trajectories.h5",
    "trajectories.csv",
]


# -----------------------------------------------------------------------------
# Generic helpers
# -----------------------------------------------------------------------------

def safe_stem(text: str) -> str:
    text = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(text)).strip("_")
    return text or "session"


def fmt_hhmmss_comma_ms(seconds: float) -> str:
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


def find_nearby_file(start: Path, filename: str, max_up: int = 4) -> Optional[Path]:
    paths = [start]
    if start.is_file():
        paths = [start.parent]
    cur = paths[0]
    for _ in range(max_up + 1):
        candidate = cur / filename
        if candidate.exists():
            return candidate
        matches = list(cur.glob(f"**/{filename}"))
        if matches:
            return matches[0]
        if cur.parent == cur:
            break
        cur = cur.parent
    return None


def read_json_file(path: Optional[Path]) -> Dict[str, Any]:
    if path is None or not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def flatten_json(prefix: str, obj: Any, out: Dict[str, Any], max_items: int = 80) -> None:
    if len(out) >= max_items:
        return
    if isinstance(obj, dict):
        for k, v in obj.items():
            flatten_json(f"{prefix}{k}_", v, out, max_items=max_items)
    elif isinstance(obj, (list, tuple)):
        if len(obj) <= 8 and all(not isinstance(x, (dict, list, tuple)) for x in obj):
            out[prefix[:-1]] = json.dumps(obj)
        else:
            out[prefix[:-1] + "_len"] = len(obj)
    else:
        out[prefix[:-1]] = obj


# -----------------------------------------------------------------------------
# Trajectory loading
# -----------------------------------------------------------------------------

def discover_trajectory_file(input_dir: Path, explicit: Optional[str] = None) -> Path:
    if explicit:
        p = Path(explicit).expanduser().resolve()
        if not p.exists():
            raise FileNotFoundError(f"Trajectory file does not exist: {p}")
        return p
    root = input_dir.expanduser().resolve()
    if root.is_file():
        return root
    for name in TRAJ_NAMES:
        direct = root / name
        if direct.exists():
            return direct
    for name in TRAJ_NAMES:
        matches = list(root.glob(f"**/{name}"))
        if matches:
            # Prefer files in folders literally called trajectories.
            matches = sorted(matches, key=lambda p: (p.parent.name != "trajectories", len(str(p))))
            return matches[0]
    raise FileNotFoundError(f"Could not find any known trajectory file under {root}")


def load_trajectory_array(path: Path) -> np.ndarray:
    suffix = path.suffix.lower()
    if suffix == ".npy":
        arr = np.load(path, allow_pickle=True)
        if isinstance(arr, np.lib.npyio.NpzFile):
            keys = list(arr.keys())
            arr = arr[keys[0]]
        if arr.dtype == object:
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
        df = pd.read_csv(path)
        return csv_to_array(df)
    raise ValueError(f"Unsupported trajectory file type: {path}")


def csv_to_array(df: pd.DataFrame) -> np.ndarray:
    # Old combat format: x1,y1,x2,y2 or x0,y0,x1,y1.
    ids = []
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
    # Wide numeric fallback: columns grouped x/y pairs.
    num = df.select_dtypes(include=[np.number]).to_numpy(float)
    if num.shape[1] % 2 != 0:
        num = num[:, 1:] if (num.shape[1] - 1) % 2 == 0 else num
    if num.shape[1] < 4 or num.shape[1] % 2 != 0:
        raise ValueError("CSV must contain xN/yN columns or an even number of coordinate columns")
    return num.reshape((num.shape[0], num.shape[1] // 2, 2))


def normalize_array_shape(arr: np.ndarray) -> np.ndarray:
    arr = np.asarray(arr, dtype=float)
    arr = np.squeeze(arr)
    if arr.ndim == 2:
        # frame x coordinates; assume x1,y1,x2,y2...
        if arr.shape[1] % 2 != 0:
            raise ValueError(f"Cannot interpret 2D trajectory array shape {arr.shape}")
        return arr.reshape((arr.shape[0], arr.shape[1] // 2, 2))
    if arr.ndim != 3:
        raise ValueError(f"Expected trajectory array with 2 or 3 dimensions, got {arr.shape}")
    # Expected IDtracker shape is frames x animals x xy. Some variants store animals x frames x xy.
    if arr.shape[-1] == 2:
        if arr.shape[0] < arr.shape[1] and arr.shape[1] > 100:
            return np.transpose(arr, (1, 0, 2))
        return arr
    if arr.shape[1] == 2:
        # frames x xy x animals
        return np.transpose(arr, (0, 2, 1))
    raise ValueError(f"Cannot interpret trajectory array shape {arr.shape}")


# -----------------------------------------------------------------------------
# ROI logic
# -----------------------------------------------------------------------------

@dataclass
class ROI:
    name: str
    poly: np.ndarray
    path: MplPath


def parse_roi_string(s: str) -> np.ndarray:
    # IDtracker TOML often stores a string containing [[x,y], [x,y], ...].
    m = re.search(r"\[\s*\[.*\]\s*\]", s)
    raw = m.group(0) if m else s
    coords = ast.literal_eval(raw)
    return np.asarray(coords, dtype=float)


def load_rois(toml_path: Optional[Path]) -> List[ROI]:
    if toml_path is None or not toml_path.exists():
        return []
    with toml_path.open("rb") as f:
        data = tomllib.load(f)
    raw_rois = data.get("roi_list") or data.get("rois") or data.get("ROIS") or []
    rois: List[ROI] = []
    if isinstance(raw_rois, dict):
        iterator = raw_rois.items()
    else:
        iterator = enumerate(raw_rois)
    for key, val in iterator:
        try:
            if isinstance(val, dict):
                name = str(val.get("name", key))
                poly_val = val.get("polygon") or val.get("points") or val.get("roi") or val
                poly = np.asarray(poly_val, dtype=float)
            else:
                name = "arena" if len(rois) == 0 else f"roi_{key}"
                poly = parse_roi_string(str(val))
            if poly.ndim == 2 and poly.shape[1] == 2 and len(poly) >= 3:
                rois.append(ROI(name=name, poly=poly, path=MplPath(poly)))
        except Exception:
            continue
    return rois




def load_rois_from_session(session_data: Dict[str, Any]) -> List[ROI]:
    """Parse ROI polygons stored in IDtracker.ai session.json.

    ROI 0 is treated as the primary arena/cell ROI. ROI 1, when present,
    is treated as the secondary/inner ROI for combat occupancy summaries.
    """
    raw_rois = session_data.get("roi_list") or []
    rois: List[ROI] = []
    if isinstance(raw_rois, dict):
        iterator = raw_rois.items()
    else:
        iterator = enumerate(raw_rois)
    for key, val in iterator:
        try:
            name = "primary_roi" if len(rois) == 0 else ("secondary_roi" if len(rois) == 1 else f"roi_{len(rois)}")
            if isinstance(val, dict):
                name = str(val.get("name", name))
                poly_val = val.get("polygon") or val.get("points") or val.get("roi") or val
                poly = np.asarray(poly_val, dtype=float)
            else:
                poly = parse_roi_string(str(val))
            if poly.ndim == 2 and poly.shape[1] == 2 and len(poly) >= 3:
                rois.append(ROI(name=name, poly=poly, path=MplPath(poly)))
        except Exception:
            continue
    return rois


def point_to_segment_distance(px: np.ndarray, a: np.ndarray, b: np.ndarray) -> np.ndarray:
    ap = px - a
    ab = b - a
    denom = float(np.dot(ab, ab))
    if denom <= 0:
        return np.sqrt(np.sum((px - a) ** 2, axis=1))
    t = np.clip(np.sum(ap * ab, axis=1) / denom, 0, 1)
    proj = a + t[:, None] * ab
    return np.sqrt(np.sum((px - proj) ** 2, axis=1))


def distance_to_polygon_boundary(points: np.ndarray, poly: np.ndarray) -> np.ndarray:
    dists = []
    for i in range(len(poly)):
        a = poly[i]
        b = poly[(i + 1) % len(poly)]
        dists.append(point_to_segment_distance(points, a, b))
    return np.min(np.vstack(dists), axis=0)


# -----------------------------------------------------------------------------
# Per-individual BA-style calculations
# -----------------------------------------------------------------------------

def interpolate_positions(xy_raw: np.ndarray, max_step_px: float) -> Tuple[np.ndarray, Dict[str, Any], np.ndarray]:
    """Filter large jumps and interpolate internal gaps only.

    IDtracker.ai can store frames before tracking starts as NaN. Those leading
    NaNs are real missing-data frames, not stationary positions. Therefore this
    function only fills gaps bounded by valid positions on both sides. Leading
    and trailing NaN regions remain NaN and are carried into the analysis as
    invalid/missing frames.
    """
    xy = np.asarray(xy_raw, dtype=float).copy()
    n = len(xy)
    finite_orig = np.isfinite(xy[:, 0]) & np.isfinite(xy[:, 1])
    artifact = np.zeros(n, dtype=bool)
    if n >= 2 and max_step_px and max_step_px > 0:
        diffs = np.sqrt(np.sum(np.diff(xy, axis=0) ** 2, axis=1))
        bad_steps = np.isfinite(diffs) & (diffs > max_step_px)
        # Mark the destination point of a huge jump. This is conservative and mirrors the BA idea.
        artifact[1:][bad_steps] = True
        xy[artifact, :] = np.nan

    finite_after_artifact = np.isfinite(xy[:, 0]) & np.isfinite(xy[:, 1])
    out = xy.copy()
    idx = np.arange(n)
    interpolated = np.zeros(n, dtype=bool)

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

    finite_final = np.isfinite(out[:, 0]) & np.isfinite(out[:, 1])
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


def sustained_onset(displacement: np.ndarray, threshold: float, consecutive: int) -> Optional[int]:
    above = displacement >= threshold
    runs = true_runs(above, max(1, consecutive))
    return runs[0][0] if runs else None


def first_crossing(values: np.ndarray, threshold: float) -> Optional[int]:
    idx = np.where(values >= threshold)[0]
    return int(idx[0]) if idx.size else None


def detect_turtling(xy: np.ndarray, onset_idx: Optional[int], args: argparse.Namespace) -> Tuple[pd.DataFrame, np.ndarray]:
    n = len(xy)
    mask = np.zeros(n, dtype=bool)
    if args.disable_turtling or n < args.turtling_window_frames:
        return pd.DataFrame(columns=["start_frame_local","end_frame_local","duration_frames","path_px","net_displacement_px","radius_gyration_px","straightness","mean_abs_turn_rad"]), mask
    step_vec = np.diff(xy, axis=0)
    speed = np.sqrt(np.sum(step_vec ** 2, axis=1))
    headings = np.arctan2(step_vec[:, 1], step_vec[:, 0])
    turns = np.abs(np.arctan2(np.sin(np.diff(headings)), np.cos(np.diff(headings))))
    start_allowed = 0 if onset_idx is None else int(onset_idx + args.turtling_start_buffer_frames)
    w = int(args.turtling_window_frames)
    candidate = np.zeros(n, dtype=bool)
    records = []
    for s in range(max(0, start_allowed), n - w + 1):
        e = s + w - 1
        seg = xy[s:e+1]
        path = float(np.nansum(speed[s:e]))
        net = float(np.linalg.norm(seg[-1] - seg[0]))
        centroid = np.nanmean(seg, axis=0)
        rg = float(np.sqrt(np.nanmean(np.sum((seg - centroid) ** 2, axis=1))))
        straight = net / path if path > 0 else np.nan
        turn_slice_start = max(s, 0)
        turn_slice_end = max(s, min(e - 1, len(turns)))
        mean_turn = float(np.nanmean(turns[turn_slice_start:turn_slice_end])) if turn_slice_end > turn_slice_start else 0.0
        is_candidate = (
            path >= args.turtling_min_path_px
            and net <= args.turtling_max_net_displacement_px
            and rg <= args.turtling_max_radius_gyration_px
            and (np.isfinite(straight) and straight <= args.turtling_max_straightness)
            and mean_turn >= args.turtling_min_abs_turn_rad
        )
        if is_candidate:
            candidate[s:e+1] = True
    runs = merge_runs(true_runs(candidate, args.turtling_min_duration_frames), args.turtling_merge_gap_frames)
    final_records = []
    for s, e in runs:
        mask[s:e+1] = True
        seg = xy[s:e+1]
        path = float(np.nansum(speed[s:e]))
        net = float(np.linalg.norm(seg[-1] - seg[0]))
        centroid = np.nanmean(seg, axis=0)
        rg = float(np.sqrt(np.nanmean(np.sum((seg - centroid) ** 2, axis=1))))
        straight = net / path if path > 0 else np.nan
        t0 = max(s, 0); t1 = max(s, min(e - 1, len(turns)))
        mean_turn = float(np.nanmean(turns[t0:t1])) if t1 > t0 else np.nan
        final_records.append({
            "start_frame_local": int(s),
            "end_frame_local": int(e),
            "duration_frames": int(e - s + 1),
            "path_px": path,
            "net_displacement_px": net,
            "radius_gyration_px": rg,
            "straightness": straight,
            "mean_abs_turn_rad": mean_turn,
        })
    return pd.DataFrame(final_records), mask


def analyze_individual(
    xy_raw: np.ndarray,
    animal_index: int,
    global_start_frame: int,
    n_animals: int,
    rois: List[ROI],
    session_meta: Dict[str, Any],
    args: argparse.Namespace,
) -> Tuple[Dict[str, Any], pd.DataFrame, pd.DataFrame, np.ndarray, np.ndarray, np.ndarray, Optional[int]]:
    xy, qc, interpolated_mask = interpolate_positions(xy_raw, args.max_step_px)
    n = len(xy)
    frames_local = np.arange(n)
    frames_global = frames_local + global_start_frame
    finite = np.isfinite(xy[:, 0]) & np.isfinite(xy[:, 1]) if n else np.zeros(0, dtype=bool)
    first_valid_idx = int(np.where(finite)[0][0]) if finite.any() else None
    last_valid_idx = int(np.where(finite)[0][-1]) if finite.any() else None

    step = np.sqrt(np.sum(np.diff(xy, axis=0) ** 2, axis=1)) if n > 1 else np.array([])
    step_full = np.r_[np.nan, step]
    total_distance = float(np.nansum(step))

    displacement = np.full(n, np.nan, dtype=float)
    if first_valid_idx is not None:
        start_xy = xy[first_valid_idx]
        displacement[finite] = np.sqrt(np.sum((xy[finite] - start_xy) ** 2, axis=1))
    net_displacement = (
        float(np.linalg.norm(xy[last_valid_idx] - xy[first_valid_idx]))
        if first_valid_idx is not None and last_valid_idx is not None
        else np.nan
    )
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
    cum_idx = first_crossing(cumdist, args.move_threshold_px) if n else None
    speed_threshold = args.speed_moving_threshold_px_frame
    if speed_threshold is None or not np.isfinite(speed_threshold):
        # Small but nonzero automatic threshold: median + MAD-like jitter guard for tiny centroid movement.
        finite_step = step[np.isfinite(step)]
        speed_threshold = max(1.0, float(np.nanpercentile(finite_step, 10))) if finite_step.size else 1.0
    moving_pairs = step >= speed_threshold

    turtling_events, turtling_mask = detect_turtling(xy, sustained_idx, args)

    # ROI metrics: ROI 0 is the primary arena/cell ROI used for wall-buffer variables.
    # ROI 1, when present, is the secondary/inner ROI used for combat occupancy summaries.
    roi_buffer_available = False
    inside_roi = np.zeros(n, dtype=bool)
    in_border = np.zeros(n, dtype=bool)
    dist_wall = np.full(n, np.nan, dtype=float)
    roi_name = ""
    secondary_roi_available = False
    inside_secondary_roi = np.zeros(n, dtype=bool)
    secondary_roi_name = ""
    if rois and n:
        roi = rois[0]
        roi_name = roi.name
        inside_roi[finite] = roi.path.contains_points(xy[finite])
        dist_wall[finite] = distance_to_polygon_boundary(xy[finite], roi.poly)
        in_border = inside_roi & np.isfinite(dist_wall) & (dist_wall <= args.roi_wall_buffer_px)
        roi_buffer_available = True
    not_border = inside_roi & (~in_border)
    if len(rois) >= 2 and n:
        secondary = rois[1]
        secondary_roi_name = secondary.name
        inside_secondary_roi[finite] = secondary.path.contains_points(xy[finite])
        secondary_roi_available = True

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
        "x_px": xy[:, 0],
        "y_px": xy[:, 1],
        "step_px": step_full,
        "cumulative_distance_px": cumdist,
        "displacement_from_start_px": displacement,
        "speed_moving_threshold_px_per_frame": speed_threshold,
        "is_moving_frame_pair_endpoint": np.r_[False, moving_pairs] if n > 1 else np.zeros(n, dtype=bool),
        "position_interpolated": interpolated_mask,
        "inside_primary_roi": inside_roi,
        "distance_to_primary_roi_wall_px": dist_wall,
        "in_primary_roi_border_buffer": in_border,
        "not_in_primary_roi_border_buffer": not_border,
        "inside_secondary_roi": inside_secondary_roi,
        "turtling_like": turtling_mask,
    })

    def remaining_after(idx: Optional[int]) -> Tuple[float, int, float]:
        if idx is None or idx >= n:
            return (np.nan, 0, np.nan)
        # Path after this frame: sum step intervals from idx onward means steps[idx:] because step[k] is k->k+1.
        remain = float(np.nansum(step[idx:])) if idx < len(step) else 0.0
        avail = int(n - idx)
        return (remain, avail, remain / avail if avail > 0 else np.nan)

    rem_disp, avail_disp, rate_disp = remaining_after(first_disp_idx)
    rem_sust, avail_sust, rate_sust = remaining_after(sustained_idx)

    active_pos_disp = active_after_disp & finite
    active_pos_sust = active_after_sust & finite

    summary: Dict[str, Any] = {
        "session_name": session_meta.get("session_name", ""),
        "animal_index_0_based": int(animal_index),
        "n_animals_in_file": int(n_animals),
        "analysis_start_frame": int(global_start_frame),
        "analysis_end_frame_inclusive": int(global_start_frame + n - 1) if n else np.nan,
        "requested_window_frames": int(args.window_frames) if args.window_frames is not None else "all_available",
        "actual_window_frames": int(n),
        "first_valid_position_frame_local": first_valid_idx if first_valid_idx is not None else np.nan,
        "first_valid_position_frame_global": int(global_start_frame + first_valid_idx) if first_valid_idx is not None else np.nan,
        "last_valid_position_frame_local": last_valid_idx if last_valid_idx is not None else np.nan,
        "last_valid_position_frame_global": int(global_start_frame + last_valid_idx) if last_valid_idx is not None else np.nan,
        **qc,
        "interpolation_warning_fraction_threshold": float(args.interpolation_warning_fraction),
        "interpolation_warning_frame_threshold": int(args.interpolation_warning_frames),
        "interpolation_warning": bool(qc["fraction_total_interpolated_position_frames"] >= args.interpolation_warning_fraction or qc["n_total_interpolated_position_frames"] >= args.interpolation_warning_frames),
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
        "roi_buffer_available": bool(roi_buffer_available),
        "primary_roi_name": roi_name,
        "frames_inside_roi": int(inside_roi.sum()),
        "frames_in_roi_border_buffer": int(in_border.sum()),
        "frames_not_in_roi_border_buffer": int(not_border.sum()),
        "fraction_frames_not_in_roi_border_buffer_total": float(not_border.sum() / n) if n else np.nan,
        "active_position_frames_after_displacement_threshold": int(active_pos_disp.sum()),
        "active_frames_in_roi_border_buffer": int((active_pos_disp & in_border).sum()),
        "active_frames_not_in_roi_border_buffer": int((active_pos_disp & not_border).sum()),
        "fraction_active_frames_in_roi_border_buffer": float((active_pos_disp & in_border).sum() / active_pos_disp.sum()) if active_pos_disp.sum() else np.nan,
        "fraction_active_frames_not_in_roi_border_buffer": float((active_pos_disp & not_border).sum() / active_pos_disp.sum()) if active_pos_disp.sum() else np.nan,
        "active_position_frames_after_sustained_displacement_threshold": int(active_pos_sust.sum()),
        "active_frames_in_roi_border_buffer_after_sustained_threshold": int((active_pos_sust & in_border).sum()),
        "active_frames_not_in_roi_border_buffer_after_sustained_threshold": int((active_pos_sust & not_border).sum()),
        "fraction_active_frames_in_roi_border_buffer_after_sustained_threshold": float((active_pos_sust & in_border).sum() / active_pos_sust.sum()) if active_pos_sust.sum() else np.nan,
        "fraction_active_frames_not_in_roi_border_buffer_after_sustained_threshold": float((active_pos_sust & not_border).sum() / active_pos_sust.sum()) if active_pos_sust.sum() else np.nan,
        "secondary_roi_available": bool(secondary_roi_available),
        "secondary_roi_name": secondary_roi_name,
        "frames_inside_secondary_roi": int(inside_secondary_roi.sum()),
        "seconds_inside_secondary_roi": float(inside_secondary_roi.sum() / args.fps) if args.fps else np.nan,
        "fraction_frames_inside_secondary_roi_total": float(inside_secondary_roi.sum() / n) if n else np.nan,
        "active_frames_inside_secondary_roi_after_displacement_threshold": int((active_pos_disp & inside_secondary_roi).sum()),
        "active_seconds_inside_secondary_roi_after_displacement_threshold": float((active_pos_disp & inside_secondary_roi).sum() / args.fps) if args.fps else np.nan,
        "fraction_active_frames_inside_secondary_roi_after_displacement_threshold": float((active_pos_disp & inside_secondary_roi).sum() / active_pos_disp.sum()) if active_pos_disp.sum() else np.nan,
        "active_frames_inside_secondary_roi_after_sustained_threshold": int((active_pos_sust & inside_secondary_roi).sum()),
        "active_seconds_inside_secondary_roi_after_sustained_threshold": float((active_pos_sust & inside_secondary_roi).sum() / args.fps) if args.fps else np.nan,
        "fraction_active_frames_inside_secondary_roi_after_sustained_threshold": float((active_pos_sust & inside_secondary_roi).sum() / active_pos_sust.sum()) if active_pos_sust.sum() else np.nan,
        "turtling_detection_enabled": not bool(args.disable_turtling),
        "turtling_window_frames": int(args.turtling_window_frames),
        "turtling_min_duration_frames": int(args.turtling_min_duration_frames),
        "turtling_merge_gap_frames": int(args.turtling_merge_gap_frames),
        "turtling_event_count": int(len(turtling_events)),
        "turtling_total_frames": int(turtling_mask.sum()),
        "turtling_fraction_frames": float(turtling_mask.sum() / n) if n else np.nan,
    }
    for k, v in session_meta.items():
        if k not in summary:
            summary[k] = v
    return summary, per_frame, turtling_events, xy, turtling_mask, interpolated_mask, sustained_idx


# -----------------------------------------------------------------------------
# Pairwise combat calculations
# -----------------------------------------------------------------------------

def pairwise_metrics(xy0: np.ndarray, xy1: np.ndarray, global_start_frame: int, rois: List[ROI], args: argparse.Namespace) -> Tuple[Dict[str, Any], pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    n = min(len(xy0), len(xy1))
    xy0 = xy0[:n]
    xy1 = xy1[:n]
    frames_local = np.arange(n)
    frames_global = frames_local + global_start_frame
    dist = np.sqrt(np.sum((xy0 - xy1) ** 2, axis=1))
    both_valid = np.isfinite(dist)
    contact = both_valid & (dist <= args.contact_px)
    finite0 = np.isfinite(xy0[:, 0]) & np.isfinite(xy0[:, 1])
    finite1 = np.isfinite(xy1[:, 0]) & np.isfinite(xy1[:, 1])
    animal0_inside_secondary_roi = np.zeros(n, dtype=bool)
    animal1_inside_secondary_roi = np.zeros(n, dtype=bool)
    secondary_roi_name = ""
    secondary_roi_available = False
    if len(rois) >= 2 and n:
        secondary = rois[1]
        secondary_roi_name = secondary.name
        animal0_inside_secondary_roi[finite0] = secondary.path.contains_points(xy0[finite0])
        animal1_inside_secondary_roi[finite1] = secondary.path.contains_points(xy1[finite1])
        secondary_roi_available = True
    both_inside_secondary_roi = animal0_inside_secondary_roi & animal1_inside_secondary_roi
    either_inside_secondary_roi = animal0_inside_secondary_roi | animal1_inside_secondary_roi
    contact_inside_secondary_roi = contact & both_inside_secondary_roi
    min_contact_frames = max(1, int(round(args.min_contact_frames if args.min_contact_frames is not None else args.min_contact_s * args.fps)))
    contact_runs = true_runs(contact, min_contact_frames)
    # Near/contact proximity table every frame.
    per_frame = pd.DataFrame({
        "frame_local": frames_local,
        "frame_global": frames_global,
        "time_s": frames_local / args.fps,
        "animal_0_x_px": xy0[:, 0],
        "animal_0_y_px": xy0[:, 1],
        "animal_1_x_px": xy1[:, 0],
        "animal_1_y_px": xy1[:, 1],
        "distance_px": dist,
        "both_valid": both_valid,
        "contact_le_threshold_px": contact,
        "animal0_inside_secondary_roi": animal0_inside_secondary_roi,
        "animal1_inside_secondary_roi": animal1_inside_secondary_roi,
        "both_animals_inside_secondary_roi": both_inside_secondary_roi,
        "either_animal_inside_secondary_roi": either_inside_secondary_roi,
        "contact_inside_secondary_roi": contact_inside_secondary_roi,
    })
    events = []
    for s, e in contact_runs:
        events.append({
            "event_type": "contact",
            "start_frame_local": int(s),
            "end_frame_local": int(e),
            "start_frame_global": int(global_start_frame + s),
            "end_frame_global": int(global_start_frame + e),
            "start_time_s": float(s / args.fps),
            "end_time_s": float(e / args.fps),
            "duration_frames": int(e - s + 1),
            "duration_s": float((e - s + 1) / args.fps),
            "min_distance_px": float(np.nanmin(dist[s:e+1])),
            "mean_distance_px": float(np.nanmean(dist[s:e+1])),
        })
    # Missing runs, before interpolation is not represented here. These are after individual interpolation,
    # so this mostly reports unrecoverable all-NaN cases.
    missing_pair = ~both_valid
    for s, e in true_runs(missing_pair, min_contact_frames):
        events.append({
            "event_type": "pair_missing_or_invalid",
            "start_frame_local": int(s),
            "end_frame_local": int(e),
            "start_frame_global": int(global_start_frame + s),
            "end_frame_global": int(global_start_frame + e),
            "start_time_s": float(s / args.fps),
            "end_time_s": float(e / args.fps),
            "duration_frames": int(e - s + 1),
            "duration_s": float((e - s + 1) / args.fps),
            "min_distance_px": np.nan,
            "mean_distance_px": np.nan,
        })
    events_df = pd.DataFrame(events)
    if not events_df.empty:
        events_df = events_df.sort_values(["start_frame_local", "event_type"]).reset_index(drop=True)
    fight_like = contact & (dist <= args.fight_px) if args.fight_px is not None else np.zeros(n, dtype=bool)
    fight_runs = true_runs(fight_like, max(1, int(round(args.min_fight_frames))))
    fight_events = []
    for s, e in fight_runs:
        fight_events.append({
            "event_type": "possible_fight_close_contact",
            "start_frame_local": int(s),
            "end_frame_local": int(e),
            "start_frame_global": int(global_start_frame + s),
            "end_frame_global": int(global_start_frame + e),
            "duration_frames": int(e - s + 1),
            "min_distance_px": float(np.nanmin(dist[s:e+1])),
            "mean_distance_px": float(np.nanmean(dist[s:e+1])),
        })
    fight_df = pd.DataFrame(fight_events)
    summary = {
        "analysis_start_frame": int(global_start_frame),
        "analysis_end_frame_inclusive": int(global_start_frame + n - 1) if n else np.nan,
        "actual_window_frames": int(n),
        "fps_for_event_times_only": float(args.fps),
        "contact_threshold_px": float(args.contact_px),
        "min_contact_frames": int(min_contact_frames),
        "min_contact_s": float(min_contact_frames / args.fps),
        "mean_pair_distance_px": float(np.nanmean(dist)) if n else np.nan,
        "median_pair_distance_px": float(np.nanmedian(dist)) if n else np.nan,
        "min_pair_distance_px": float(np.nanmin(dist)) if n else np.nan,
        "max_pair_distance_px": float(np.nanmax(dist)) if n else np.nan,
        "frames_in_contact": int(contact.sum()),
        "fraction_frames_in_contact": float(contact.mean()) if n else np.nan,
        "contact_event_count": int(len(contact_runs)),
        "total_contact_duration_frames": int(sum(e - s + 1 for s, e in contact_runs)),
        "longest_contact_event_frames": int(max([e - s + 1 for s, e in contact_runs], default=0)),
        "fight_threshold_px": float(args.fight_px) if args.fight_px is not None else np.nan,
        "possible_fight_event_count": int(len(fight_runs)),
        "possible_fight_total_frames": int(sum(e - s + 1 for s, e in fight_runs)),
        "possible_fight_fraction_frames": float(fight_like.mean()) if n else np.nan,
        "secondary_roi_available": bool(secondary_roi_available),
        "secondary_roi_name": secondary_roi_name,
        "animal0_frames_inside_secondary_roi": int(animal0_inside_secondary_roi.sum()),
        "animal0_seconds_inside_secondary_roi": float(animal0_inside_secondary_roi.sum() / args.fps) if args.fps else np.nan,
        "animal0_fraction_frames_inside_secondary_roi": float(animal0_inside_secondary_roi.sum() / n) if n else np.nan,
        "animal1_frames_inside_secondary_roi": int(animal1_inside_secondary_roi.sum()),
        "animal1_seconds_inside_secondary_roi": float(animal1_inside_secondary_roi.sum() / args.fps) if args.fps else np.nan,
        "animal1_fraction_frames_inside_secondary_roi": float(animal1_inside_secondary_roi.sum() / n) if n else np.nan,
        "both_animals_frames_inside_secondary_roi": int(both_inside_secondary_roi.sum()),
        "both_animals_seconds_inside_secondary_roi": float(both_inside_secondary_roi.sum() / args.fps) if args.fps else np.nan,
        "both_animals_fraction_frames_inside_secondary_roi": float(both_inside_secondary_roi.sum() / n) if n else np.nan,
        "either_animal_frames_inside_secondary_roi": int(either_inside_secondary_roi.sum()),
        "either_animal_seconds_inside_secondary_roi": float(either_inside_secondary_roi.sum() / args.fps) if args.fps else np.nan,
        "either_animal_fraction_frames_inside_secondary_roi": float(either_inside_secondary_roi.sum() / n) if n else np.nan,
        "contact_frames_inside_secondary_roi": int(contact_inside_secondary_roi.sum()),
        "contact_seconds_inside_secondary_roi": float(contact_inside_secondary_roi.sum() / args.fps) if args.fps else np.nan,
        "contact_fraction_inside_secondary_roi_among_all_frames": float(contact_inside_secondary_roi.sum() / n) if n else np.nan,
        "contact_fraction_inside_secondary_roi_among_contact_frames": float(contact_inside_secondary_roi.sum() / contact.sum()) if contact.sum() else np.nan,
    }
    return summary, per_frame, events_df, fight_df


def write_inqscribe(events_df: pd.DataFrame, path: Path) -> None:
    cols = ["Start Time", "End Time", "Title", "Comment"]
    with path.open("w", encoding="utf-8-sig", newline="\n") as f:
        w = csv.writer(f, delimiter="\t", lineterminator="\n")
        w.writerow(cols)
        for _, r in events_df.iterrows():
            title = f"{r.get('event_type', 'event')} [F{int(r.start_frame_global)}-{int(r.end_frame_global)}]"
            comment = f"duration={int(r.duration_frames)} frames; min_distance_px={r.get('min_distance_px', '')}"
            w.writerow([fmt_hhmmss_comma_ms(float(r.start_time_s)), fmt_hhmmss_comma_ms(float(r.end_time_s)), title, comment])


def plot_tracks(
    xys: List[np.ndarray],
    turt_masks: List[np.ndarray],
    interp_masks: List[np.ndarray],
    onset_indices: List[Optional[int]],
    rois: List[ROI],
    path_png: Path,
    path_pdf: Path,
    args: argparse.Namespace,
    interaction_mask: Optional[np.ndarray] = None,
) -> None:
    """Make one standardized BA-style QC map stream for BA and fight tracks.

    This intentionally follows the BA map design closely:
    local ROI-cropped coordinates, very thin normal movement lines, start/end markers,
    sustained movement-onset stars, interpolated positions as x symbols, and
    turtling-like frames as symbols rather than connected lines.
    """
    finite_arrays = [xy[np.isfinite(xy[:, 0]) & np.isfinite(xy[:, 1])] for xy in xys if len(xy)]
    all_pts = np.vstack(finite_arrays) if finite_arrays else np.empty((0, 2))

    primary_roi = rois[0].poly if rois else None
    local_roi_map = (primary_roi is not None) and (not args.full_frame_map)
    if local_roi_map:
        xmin0, ymin0 = np.nanmin(primary_roi, axis=0)
        xmax0, ymax0 = np.nanmax(primary_roi, axis=0)
        pad = float(args.roi_padding_px)
        x_offset = xmin0 - pad
        y_offset = ymin0 - pad
        xmin, xmax = 0.0, (xmax0 - xmin0) + 2 * pad
        ymin, ymax = 0.0, (ymax0 - ymin0) + 2 * pad
        xlabel = "local x position within ROI crop (pixels)"
        ylabel = "local y position within ROI crop (pixels)"
        title_suffix = " [ROI-local map]"
    elif len(all_pts):
        pad = float(args.roi_padding_px)
        x_offset = 0.0
        y_offset = 0.0
        xmin = float(np.nanmin(all_pts[:, 0]) - pad)
        xmax = float(np.nanmax(all_pts[:, 0]) + pad)
        ymin = float(np.nanmin(all_pts[:, 1]) - pad)
        ymax = float(np.nanmax(all_pts[:, 1]) + pad)
        xlabel = "x position in full video frame (pixels)"
        ylabel = "y position in full video frame (pixels)"
        title_suffix = ""
    else:
        x_offset = 0.0
        y_offset = 0.0
        xmin, xmax, ymin, ymax = 0.0, 1.0, 0.0, 1.0
        xlabel = "x position (pixels)"
        ylabel = "y position (pixels)"
        title_suffix = ""

    def localize(xy: np.ndarray) -> np.ndarray:
        return xy - np.array([x_offset, y_offset]) if local_roi_map else xy

    def plot_segmented_line(ax, xy_local: np.ndarray, **kwargs):
        if len(xy_local) < 2:
            return None
        finite = np.isfinite(xy_local[:, 0]) & np.isfinite(xy_local[:, 1])
        d = np.sqrt(np.sum(np.diff(xy_local, axis=0) ** 2, axis=1)) if len(xy_local) > 1 else np.array([])
        break_before = np.zeros(len(xy_local), dtype=bool)
        break_before[0] = True
        break_before[~finite] = True
        if len(d) and args.max_step_px and args.max_step_px > 0:
            break_before[np.r_[False, np.isfinite(d) & (d > float(args.max_step_px))]] = True
        label = kwargs.pop("label", None)
        first_line = None
        start = None
        for j in range(len(xy_local) + 1):
            end_now = (j == len(xy_local)) or break_before[j]
            if end_now and start is not None:
                if j - start >= 2:
                    k = dict(kwargs)
                    if first_line is None and label is not None:
                        k["label"] = label
                    line = ax.plot(xy_local[start:j, 0], xy_local[start:j, 1], **k)[0]
                    if first_line is None:
                        first_line = line
                start = None
            if j < len(xy_local) and finite[j] and start is None:
                start = j
        return first_line

    def draw_one(ax, animal_indices: List[int], title: str):
        for r_i, roi in enumerate(rois):
            rp = localize(roi.poly)
            closed = np.vstack([rp, rp[0]])
            if r_i == 0:
                ax.plot(closed[:, 0], closed[:, 1], linewidth=1.5, color="C2", label="primary ROI")
            elif r_i == 1:
                ax.plot(closed[:, 0], closed[:, 1], linewidth=1.2, linestyle="--", color="C4", label="secondary ROI")
            else:
                ax.plot(closed[:, 0], closed[:, 1], linewidth=0.8, linestyle=":", color="0.4", label=f"ROI {r_i}")

        for i in animal_indices:
            xy_local = localize(xys[i])
            animal_colors = [args.animal0_color, args.animal1_color, "tab:green", "tab:purple"]
            color = animal_colors[i % len(animal_colors)]
            label_prefix = f"animal_{i}"
            plot_segmented_line(ax, xy_local, linewidth=float(args.track_linewidth), alpha=0.70, color=color, label=f"{label_prefix} track")
            finite = np.isfinite(xy_local[:, 0]) & np.isfinite(xy_local[:, 1])
            if finite.any():
                first = int(np.where(finite)[0][0])
                last = int(np.where(finite)[0][-1])
                ax.scatter(xy_local[first, 0], xy_local[first, 1], s=55, marker="o", color=color, edgecolor="black", linewidth=0.35, label=f"{label_prefix} start", zorder=6)
                ax.scatter(xy_local[last, 0], xy_local[last, 1], s=55, marker="s", color=color, edgecolor="black", linewidth=0.35, label=f"{label_prefix} end", zorder=6)

            if i < len(onset_indices) and onset_indices[i] is not None:
                oi = int(onset_indices[i])
                if 0 <= oi < len(xy_local) and np.isfinite(xy_local[oi]).all():
                    ax.scatter(xy_local[oi, 0], xy_local[oi, 1], s=90, marker="*", color=color, edgecolor="black", linewidth=0.35, label=f"{label_prefix} sustained onset", zorder=7)

            if i < len(interp_masks):
                im = np.asarray(interp_masks[i], dtype=bool) & finite
                if im.any():
                    idx = np.where(im)[0]
                    max_pts = int(args.map_max_overlay_points)
                    if max_pts > 0 and len(idx) > max_pts:
                        idx = idx[np.linspace(0, len(idx) - 1, max_pts).astype(int)]
                    ax.scatter(xy_local[idx, 0], xy_local[idx, 1], s=7, marker="x", color=args.interpolated_color, alpha=0.45, label=f"{label_prefix} interpolated", zorder=5)

            if i < len(turt_masks):
                tm = np.asarray(turt_masks[i], dtype=bool) & finite
                if tm.any():
                    idx = np.where(tm)[0]
                    max_pts = int(args.map_max_overlay_points)
                    if max_pts > 0 and len(idx) > max_pts:
                        idx = idx[np.linspace(0, len(idx) - 1, max_pts).astype(int)]
                    ax.scatter(xy_local[idx, 0], xy_local[idx, 1], s=float(args.turtled_point_size), marker="D", color=args.turtled_color, alpha=float(args.turtled_point_alpha), label=f"{label_prefix} turtled-like", zorder=4)

            if args.show_map_points:
                frame_rel = np.arange(len(xy_local))
                ax.scatter(xy_local[finite, 0], xy_local[finite, 1], c=frame_rel[finite], s=1, alpha=0.25)

        ax.set_xlabel(xlabel)
        ax.set_ylabel(ylabel)
        ax.set_title(title + title_suffix)
        ax.set_aspect("equal", adjustable="box")
        ax.invert_yaxis()
        ax.set_xlim(xmin, xmax)
        ax.set_ylim(ymax, ymin)
        handles, labels = ax.get_legend_handles_labels()
        if handles:
            seen = set(); h2 = []; l2 = []
            for h, lab in zip(handles, labels):
                if lab not in seen:
                    h2.append(h); l2.append(lab); seen.add(lab)
            ax.legend(h2, l2, frameon=False, fontsize=8, loc="upper center", bbox_to_anchor=(0.5, -0.12), ncol=2)

    def draw_interaction_map(ax):
        draw_one(ax, list(range(len(xys))), "Fight interactions: contact locations")
        if interaction_mask is None or len(xys) < 2:
            return
        n = min(len(xys[0]), len(xys[1]), len(interaction_mask))
        mask = np.asarray(interaction_mask[:n], dtype=bool)
        xy0 = localize(xys[0][:n])
        xy1 = localize(xys[1][:n])
        valid = mask & np.isfinite(xy0).all(axis=1) & np.isfinite(xy1).all(axis=1)
        idx = np.where(valid)[0]
        max_pts = int(getattr(args, "map_max_interaction_points", 600))
        if max_pts > 0 and len(idx) > max_pts:
            idx = idx[np.linspace(0, len(idx) - 1, max_pts).astype(int)]
        if len(idx):
            mid = (xy0[idx] + xy1[idx]) / 2.0
            ax.scatter(mid[:, 0], mid[:, 1], s=18, marker="*", color="crimson",
                       edgecolor="black", linewidth=0.20, alpha=0.80,
                       label="contact location", zorder=10)
            handles, labels = ax.get_legend_handles_labels()
            seen = set(); h2=[]; l2=[]
            for h, lab in zip(handles, labels):
                if lab not in seen:
                    h2.append(h); l2.append(lab); seen.add(lab)
            ax.legend(h2, l2, frameon=False, fontsize=8, loc="upper center",
                      bbox_to_anchor=(0.5, -0.12), ncol=2)

    def draw_track_3d(fig):
        """Draw an idTracker-style corner view of x-y movement through time.

        The animal identity colors are retained throughout the entire trajectory.
        The arena footprint is drawn on the z=0 floor, while time rises vertically.
        This makes the plot read like the classic idTracker space-time view rather
        than a flat x-y chart with a third axis added afterward.
        """
        ax = fig.add_subplot(111, projection="3d")
        colors = [args.animal0_color, args.animal1_color, "tab:green", "tab:purple"]
        fps = float(args.fps) if args.fps else 1.0

        # Draw the arena/ROI footprint on the floor of the 3-D plot.
        for r_i, roi in enumerate(rois):
            rp = localize(roi.poly)
            closed = np.vstack([rp, rp[0]])
            floor_z = np.zeros(len(closed), dtype=float)
            if r_i == 0:
                ax.plot(closed[:, 0], closed[:, 1], floor_z,
                        linewidth=1.4, color="C2", alpha=0.95,
                        label="primary ROI (floor)")
            elif r_i == 1:
                ax.plot(closed[:, 0], closed[:, 1], floor_z,
                        linewidth=1.0, linestyle="--", color="C4", alpha=0.85,
                        label="secondary ROI (floor)")

        # Plot each animal as segmented colored paths so NaNs and large jumps do
        # not create misleading straight lines through the 3-D volume.
        for i in range(len(xys)):
            xy = localize(xys[i])
            t = np.arange(len(xy), dtype=float) / fps
            finite = np.isfinite(xy).all(axis=1)
            d = np.sqrt(np.sum(np.diff(xy, axis=0) ** 2, axis=1)) if len(xy) > 1 else np.array([])
            breaks = np.zeros(len(xy), dtype=bool)
            if len(xy):
                breaks[0] = True
                breaks[~finite] = True
                if len(d) and args.max_step_px and args.max_step_px > 0:
                    breaks[np.r_[False, np.isfinite(d) & (d > float(args.max_step_px))]] = True

            first_segment = True
            start = None
            for j in range(len(xy) + 1):
                end_now = (j == len(xy)) or (j < len(xy) and breaks[j])
                if end_now and start is not None:
                    if j - start >= 2:
                        ax.plot(
                            xy[start:j, 0], xy[start:j, 1], t[start:j],
                            linewidth=0.55, alpha=0.82, color=colors[i],
                            label=f"animal_{i} track" if first_segment else None,
                        )
                        first_segment = False
                    start = None
                if j < len(xy) and finite[j] and start is None:
                    start = j

            if finite.any():
                first = int(np.where(finite)[0][0])
                last = int(np.where(finite)[0][-1])
                ax.scatter(xy[first, 0], xy[first, 1], t[first], s=42, marker="o",
                           color=colors[i], edgecolor="black", linewidth=0.35,
                           label=f"animal_{i} start", depthshade=False)
                ax.scatter(xy[last, 0], xy[last, 1], t[last], s=42, marker="s",
                           color=colors[i], edgecolor="black", linewidth=0.35,
                           label=f"animal_{i} end", depthshade=False)

        if interaction_mask is not None and len(xys) >= 2:
            n = min(len(xys[0]), len(xys[1]), len(interaction_mask))
            xy0 = localize(xys[0][:n]); xy1 = localize(xys[1][:n])
            mask = np.asarray(interaction_mask[:n], dtype=bool)
            valid = mask & np.isfinite(xy0).all(axis=1) & np.isfinite(xy1).all(axis=1)
            idx = np.where(valid)[0]
            max_pts = int(getattr(args, "map_max_interaction_points", 600))
            if max_pts > 0 and len(idx) > max_pts:
                idx = idx[np.linspace(0, len(idx) - 1, max_pts).astype(int)]
            if len(idx):
                mid = (xy0[idx] + xy1[idx]) / 2.0
                ax.scatter(mid[:, 0], mid[:, 1], idx / fps, s=18, marker="*",
                           color="crimson", edgecolor="black", linewidth=0.20,
                           alpha=0.88, label="contact location", depthshade=False)

        # Place labels manually so they remain outside the trajectory volume.
        # X is labelled along the high-numbered Y edge; Z tick numbers and its
        # title are placed along the low-numbered Y edge, away from the tracks.
        ax.set_xlabel("")
        ax.set_ylabel(ylabel, labelpad=8)
        ax.set_zlabel("")
        analysis_label = "Behavioral assay" if len(xys) == 1 else "Fight"
        ax.set_title(f"{analysis_label} tracks through time (corner view)", pad=16)
        ax.set_xlim(xmin, xmax)
        ax.set_ylim(ymax, ymin)  # Match the image-coordinate orientation of 2-D maps.
        max_len = max((len(xy) for xy in xys), default=1)
        zmax=max(1.0, (max_len - 1) / fps)
        ax.set_zlim(0, zmax)

        xspan=max(float(xmax-xmin),1.0)
        yspan=max(float(ymax-ymin),1.0)
        # X label: far/high-numbered end of Y, clear of the floor tracks.
        ax.text3D((xmin+xmax)/2.0, ymax + 0.09*yspan, 0,
                  xlabel, ha="center", va="top")
        # Z values/title: low-numbered end of Y and slightly outside X.
        zticks=ax.get_zticks()
        ax.set_zticklabels([])
        z_x=xmin - 0.075*xspan
        z_y=ymin - 0.025*yspan
        for tick in zticks:
            if -1e-9 <= tick <= zmax + 1e-9:
                ax.text3D(z_x, z_y, float(tick), f"{tick:g}",
                          ha="right", va="center", fontsize=7)
        ax.text3D(z_x - 0.055*xspan, z_y, zmax/2.0,
                  "time from analysis start (s)",
                  ha="center", va="center", rotation=90)

        # Tall box and oblique corner view closely mimic the classic idTracker plot.
        try:
            ax.set_box_aspect((1.0, 1.0, 1.45))
        except Exception:
            pass
        try:
            ax.set_proj_type("persp", focal_length=0.9)
        except Exception:
            pass
        ax.view_init(elev=28, azim=-52)
        ax.grid(True, alpha=0.25)
        for pane in (ax.xaxis.pane, ax.yaxis.pane, ax.zaxis.pane):
            pane.set_alpha(0.08)

        handles, labels = ax.get_legend_handles_labels()
        if handles:
            seen = set(); h2 = []; l2 = []
            for h, lab in zip(handles, labels):
                if lab and lab not in seen:
                    h2.append(h); l2.append(lab); seen.add(lab)
            ax.legend(h2, l2, frameon=False, fontsize=8, loc="upper left",
                      bbox_to_anchor=(0.0, 1.0))

    def finalize_figure(fig):
        run_name = str(getattr(args, "map_run_name", "") or "").strip()
        if run_name:
            fig.text(
                0.5, 0.018,
                f"Run: {run_name}",
                ha="center", va="bottom", fontsize=9, fontweight="bold",
            )
        fig.tight_layout(rect=[0, 0.16 if run_name else 0.10, 1, 1])

    fig, ax = plt.subplots(figsize=(8, 8))
    draw_one(ax, list(range(len(xys))), ("Behavioral assay track" if len(xys) == 1 else "Fight tracks: both beetles"))
    finalize_figure(fig)
    fig.savefig(path_png, dpi=200)
    plt.close(fig)

    # Also write one PNG per animal. These are much easier to inspect when the
    # two beetles have many interpolated/turtling frames.
    for i in range(len(xys)):
        indiv_png = path_png.with_name(f"{path_png.stem}_animal{i}{path_png.suffix}")
        fig, ax = plt.subplots(figsize=(8, 8))
        draw_one(ax, [i], ((f"Behavioral assay track: animal_{i}") if len(xys) == 1 else f"Fight track: animal_{i}"))
        finalize_figure(fig)
        fig.savefig(indiv_png, dpi=200)
        plt.close(fig)

    if interaction_mask is not None and len(xys) >= 2:
        interaction_png = path_png.with_name(f"{path_png.stem}_interactions{path_png.suffix}")
        fig, ax = plt.subplots(figsize=(8, 8))
        draw_interaction_map(ax)
        finalize_figure(fig)
        fig.savefig(interaction_png, dpi=200)
        plt.close(fig)

    # Every analysis receives an x-y-time view. Previously this was generated
    # only for fights because it was nested under the interaction-map branch.
    track_3d_png = path_png.with_name(f"{path_png.stem}_3d{path_png.suffix}")
    fig = plt.figure(figsize=(9, 8))
    draw_track_3d(fig)
    finalize_figure(fig)
    fig.savefig(track_3d_png, dpi=200)
    plt.close(fig)

    with PdfPages(path_pdf) as pdf:
        fig, ax = plt.subplots(figsize=(8, 8))
        draw_one(ax, list(range(len(xys))), ("Behavioral assay track" if len(xys) == 1 else "Fight tracks: both beetles"))
        finalize_figure(fig)
        pdf.savefig(fig)
        plt.close(fig)
        for i in range(len(xys)):
            fig, ax = plt.subplots(figsize=(8, 8))
            draw_one(ax, [i], ((f"Behavioral assay track: animal_{i}") if len(xys) == 1 else f"Fight track: animal_{i}"))
            finalize_figure(fig)
            pdf.savefig(fig)
            plt.close(fig)
        if interaction_mask is not None and len(xys) >= 2:
            fig, ax = plt.subplots(figsize=(8, 8))
            draw_interaction_map(ax)
            finalize_figure(fig)
            pdf.savefig(fig)
            plt.close(fig)
        fig = plt.figure(figsize=(9, 8))
        draw_track_3d(fig)
        finalize_figure(fig)
        pdf.savefig(fig)
        plt.close(fig)


def load_metadata_table(path: Optional[str]) -> Optional[pd.DataFrame]:
    if not path:
        return None
    p = Path(path).expanduser().resolve()
    if not p.exists():
        raise FileNotFoundError(f"Metadata CSV not found: {p}")
    return pd.read_csv(p)


def match_metadata(metadata: Optional[pd.DataFrame], session_name: str, traj_path: Path, animal_index: Optional[int] = None) -> Dict[str, Any]:
    if metadata is None or metadata.empty:
        return {}
    df = metadata.copy()
    candidates = []
    for col in ["session_name", "session", "folder", "trajectory_file", "input_dir"]:
        if col in df.columns:
            val = str(session_name if col in ["session_name", "session"] else traj_path)
            m = df[df[col].astype(str).apply(lambda x: x in val or val in x)]
            if animal_index is not None and "animal_index_0_based" in m.columns:
                m = m[m["animal_index_0_based"].astype(str) == str(animal_index)]
            if not m.empty:
                candidates.append(m.iloc[0].to_dict())
    if candidates:
        return {f"metadata_{k}": v for k, v in candidates[0].items()}
    return {}


def build_data_dictionary(path: Path) -> None:
    rows = [
        ("combat_pair_summary.csv", "one row per combat session/fight", "Pairwise distance/contact/fight-like metrics"),
        ("combat_individual_summary.csv", "two rows per combat session", "BA-style movement, QC, ROI, and turtling metrics for each beetle"),
        ("per_frame_pairwise.csv", "one row per frame", "Distance between beetles and contact threshold flag"),
        ("animal*_per_frame_kinematics.csv", "one row per frame per beetle", "Interpolated coordinates, step distance, movement flags, ROI/border, turtling flag"),
        ("contact_events.csv", "one row per event", "Contact/proximity and invalid-pair runs"),
        ("possible_fight_events.csv", "one row per possible fight", "Very close-contact runs based on --fight-px"),
        ("turtling_events_animal*.csv", "one row per event", "Centroid-based turtling-like events, not a visual posture classifier"),
        ("track_map.png/pdf", "image", "QC maps with both tracks and ROI outline"),
        ("InqScribe.txt", "tab-delimited annotation file", "Contact, invalid-pair, and possible fight-like events formatted for InqScribe"),
        ("secondary_roi_* columns", "frames/seconds/proportions", "Occupancy of the second ROI from session.json or TOML when present; used for inner combat zone summaries"),
    ]
    pd.DataFrame(rows, columns=["file_or_column_group", "unit_or_type", "definition"]).to_csv(path, index=False)



def process_session(args: argparse.Namespace) -> Tuple[Path, Optional[Path]]:
    """Process one session through a shared one- or two-animal stream.

    Shared for BA and fights:
    trajectory discovery/loading, ROI loading, artifact interpolation, movement,
    turtling, ROI-wall metrics, per-frame outputs, and track plotting.

    Fight-only:
    pairwise distance, contact events, possible-fight events, and InqScribe output.
    """
    input_dir = Path(args.input_dir).expanduser().resolve()
    traj_path = discover_trajectory_file(input_dir, args.trajectories)
    arr_full = load_trajectory_array(traj_path)
    n_frames_total, n_animals, _ = arr_full.shape
    if n_animals < 1:
        raise ValueError(
            f"Unified script requires at least one animal; found {n_animals} in {traj_path}"
        )

    requested = str(args.analysis_type).lower()
    if requested == "auto":
        analysis_type = "ba" if n_animals == 1 else "fight"
    else:
        analysis_type = requested

    if analysis_type == "ba" and n_animals < 1:
        raise ValueError("BA analysis requires at least one animal")
    if analysis_type == "fight" and n_animals < 2:
        raise ValueError(
            f"Fight analysis requires at least two animals; found {n_animals}"
        )

    start_frame = max(0, int(args.analysis_start_frame))
    end_frame = (
        n_frames_total
        if args.window_frames is None
        else min(n_frames_total, start_frame + int(args.window_frames))
    )
    if start_frame >= end_frame:
        raise ValueError(
            f"Analysis window is empty: start={start_frame}, "
            f"end={end_frame}, total_frames={n_frames_total}"
        )
    arr = arr_full[start_frame:end_frame, :, :]

    session_json = (
        Path(args.session_json).expanduser().resolve()
        if args.session_json
        else find_nearby_file(traj_path, "session.json")
    )
    attributes_json = (
        Path(args.attributes_json).expanduser().resolve()
        if args.attributes_json
        else find_nearby_file(traj_path, "attributes.json")
    )
    session_data = read_json_file(session_json)
    attributes_data = read_json_file(attributes_json)
    default_session_name = (
        traj_path.parent.parent.name
        if traj_path.parent.name == "trajectories"
        else traj_path.parent.name
    )
    session_name = (
        args.prefix
        or session_data.get("name")
        or session_data.get("session_name")
        or default_session_name
    )
    prefix = safe_stem(args.prefix or session_name or traj_path.stem)
    output_dir = Path(args.output_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    toml_path = (
        Path(args.roi_toml).expanduser().resolve()
        if args.roi_toml
        else None
    )
    if toml_path is None:
        matches = (
            list(traj_path.parent.glob("*.toml"))
            + list(traj_path.parent.parent.glob("*.toml"))
        )
        toml_path = matches[0] if matches else None

    rois = load_rois(toml_path)
    if not rois:
        rois = load_rois_from_session(session_data)

    metadata = load_metadata_table(args.metadata_csv)
    flat_session: Dict[str, Any] = {
        "analysis_type": analysis_type,
        "trajectory_file": str(traj_path),
        "session_json": str(session_json) if session_json else "",
        "attributes_json": str(attributes_json) if attributes_json else "",
        "roi_toml": str(toml_path) if toml_path else "",
        "session_name": str(session_name),
    }
    small_json: Dict[str, Any] = {}
    flatten_json("session_json_", session_data, small_json)
    flatten_json("attributes_json_", attributes_data, small_json)
    flat_session.update(small_json)
    flat_session.update(
        match_metadata(metadata, str(session_name), traj_path, None)
    )

    if analysis_type == "ba":
        animal_indices = [int(args.animal0)]
    else:
        animal_indices = [int(args.animal0), int(args.animal1)]

    for idx in animal_indices:
        if idx < 0 or idx >= n_animals:
            raise IndexError(
                f"Animal index {idx} is invalid for {n_animals} animals"
            )

    summaries: List[Dict[str, Any]] = []
    xys: List[np.ndarray] = []
    turt_masks: List[np.ndarray] = []
    interp_masks: List[np.ndarray] = []
    onset_indices: List[Optional[int]] = []

    for position, animal_idx in enumerate(animal_indices):
        ind_meta = dict(flat_session)
        ind_meta.update(
            match_metadata(metadata, str(session_name), traj_path, animal_idx)
        )
        (
            summary,
            per_frame,
            turtling_events,
            xy,
            tmask,
            imask,
            onset_idx,
        ) = analyze_individual(
            arr[:, animal_idx, :],
            animal_idx,
            start_frame,
            n_animals,
            rois,
            ind_meta,
            args,
        )
        summary["analysis_type"] = analysis_type
        summary["analysis_role"] = (
            "individual"
            if analysis_type == "ba"
            else ("animal0" if position == 0 else "animal1")
        )
        summaries.append(summary)

        per_frame.to_csv(
            output_dir
            / f"{prefix}_animal{animal_idx}_per_frame_kinematics.csv",
            index=False,
        )
        turtling_events.to_csv(
            output_dir
            / f"{prefix}_turtling_events_animal{animal_idx}.csv",
            index=False,
        )
        xys.append(xy)
        turt_masks.append(tmask)
        interp_masks.append(imask)
        onset_indices.append(onset_idx)

    # For fights, classify animals by where they STARTED, not by where they
    # spent most of the assay.  We use the median valid X coordinate within the
    # first configurable number of analyzed frames.  The median makes the
    # starting-side assignment resistant to one-frame tracking jitter while
    # retaining the meaning of "starting position".
    starting_side_by_position: Dict[int, str] = {}
    starting_x_by_position: Dict[int, float] = {}
    starting_position_frames = max(1, int(args.starting_position_frames))
    if analysis_type == "fight" and len(xys) >= 2:
        for position, xy in enumerate(xys[:2]):
            # Use the first N VALID positions after the analysis begins, rather
            # than requiring valid data inside the first N absolute frames.
            # IDtracker sessions often contain leading NaNs before the animal
            # receives a valid identity, even though the plotted start marker is
            # clear later.  This mirrors the visible start marker on the QC map.
            all_x = np.asarray(xy[:, 0], dtype=float)
            valid_idx = np.flatnonzero(np.isfinite(all_x))
            selected_idx = valid_idx[:starting_position_frames]
            valid_initial_x = all_x[selected_idx]
            starting_x_by_position[position] = (
                float(np.median(valid_initial_x))
                if len(valid_initial_x)
                else float("nan")
            )
            summaries[position]["starting_position_first_valid_frame_local"] = (
                int(selected_idx[0]) if len(selected_idx) else np.nan
            )
            summaries[position]["starting_position_valid_frames_used"] = int(len(selected_idx))
        x0 = starting_x_by_position[0]
        x1 = starting_x_by_position[1]
        if np.isfinite(x0) and np.isfinite(x1):
            if x0 <= x1:
                starting_side_by_position = {0: "left", 1: "right"}
            else:
                starting_side_by_position = {0: "right", 1: "left"}
        else:
            starting_side_by_position = {0: "unknown", 1: "unknown"}
        for position, summary in enumerate(summaries[:2]):
            summary["starting_side"] = starting_side_by_position[position]
            summary["starting_x_position_px"] = starting_x_by_position[position]
            summary["starting_position_window_frames"] = starting_position_frames

    pair_result = None
    interaction_mask = None
    if analysis_type == "fight":
        pair_result = pairwise_metrics(xys[0], xys[1], start_frame, rois, args)
        pair_frame_for_plot = pair_result[1]
        interaction_mask = pair_frame_for_plot["contact_le_threshold_px"].to_numpy(dtype=bool)

    png_path = output_dir / f"{prefix}_track_map.png"
    pdf_path = output_dir / f"{prefix}_tracks.pdf"
    args.map_analysis_label = (
        "Behavioral assay" if analysis_type == "ba" else "Fight"
    )
    # Printed directly beneath the legend on every PNG and PDF page.
    args.map_run_name = prefix
    plot_tracks(
        xys,
        turt_masks,
        interp_masks,
        onset_indices,
        rois,
        png_path,
        pdf_path,
        args,
        interaction_mask=interaction_mask,
    )

    individual_summary_path = output_dir / (
        f"{prefix}_ba_individual_summary.csv"
        if analysis_type == "ba"
        else f"{prefix}_combat_individual_summary.csv"
    )
    pd.DataFrame(summaries).to_csv(individual_summary_path, index=False)

    if analysis_type == "ba":
        dictionary_rows = [
            (
                individual_summary_path.name,
                "one row per BA animal/session",
                "Movement, ROI, wall-buffer, interpolation, turtling, and QC metrics",
            ),
            (
                f"{prefix}_animal{animal_indices[0]}_per_frame_kinematics.csv",
                "one row per analyzed frame",
                "Cleaned position and frame-level movement/ROI metrics",
            ),
            (
                png_path.name,
                "one image per session",
                "Detailed ROI-local 2-D track map with start, end, onset, interpolation, and turtling markers",
            ),
            (
                pdf_path.name,
                "multipage PDF per session",
                "BA QC packet containing the detailed 2-D map, animal map, and x-y-time 3-D track map",
            ),
        ]
        pd.DataFrame(
            dictionary_rows,
            columns=["file", "row_unit", "description"],
        ).to_csv(
            output_dir / f"{prefix}_summary_data_dictionary.csv",
            index=False,
        )
        print(f"OK BA: {session_name}")
        print(f"  individual summary: {individual_summary_path}")
        return individual_summary_path, None

    assert pair_result is not None
    pair_summary, pair_frame, events_df, fight_df = pair_result
    pair_summary.update(flat_session)
    pair_summary["analysis_type"] = analysis_type
    pair_summary["animal0_index_0_based"] = animal_indices[0]
    pair_summary["animal1_index_0_based"] = animal_indices[1]
    pair_summary["animal0_starting_x_position_px"] = starting_x_by_position.get(0, np.nan)
    pair_summary["animal1_starting_x_position_px"] = starting_x_by_position.get(1, np.nan)
    pair_summary["starting_position_window_frames"] = starting_position_frames
    pair_summary["starting_position_valid_frames_used_animal0"] = summaries[0].get("starting_position_valid_frames_used", 0)
    pair_summary["starting_position_valid_frames_used_animal1"] = summaries[1].get("starting_position_valid_frames_used", 0)
    pair_summary["starting_position_first_valid_frame_local_animal0"] = summaries[0].get("starting_position_first_valid_frame_local", np.nan)
    pair_summary["starting_position_first_valid_frame_local_animal1"] = summaries[1].get("starting_position_first_valid_frame_local", np.nan)
    pair_summary["left_starting_animal_index_0_based"] = (
        animal_indices[0] if starting_side_by_position.get(0) == "left"
        else animal_indices[1] if starting_side_by_position.get(1) == "left"
        else np.nan
    )
    pair_summary["right_starting_animal_index_0_based"] = (
        animal_indices[0] if starting_side_by_position.get(0) == "right"
        else animal_indices[1] if starting_side_by_position.get(1) == "right"
        else np.nan
    )
    pair_summary["left_starting_analysis_role"] = (
        "animal0" if starting_side_by_position.get(0) == "left"
        else "animal1" if starting_side_by_position.get(1) == "left"
        else "unknown"
    )
    pair_summary["right_starting_analysis_role"] = (
        "animal0" if starting_side_by_position.get(0) == "right"
        else "animal1" if starting_side_by_position.get(1) == "right"
        else "unknown"
    )

    pair_summary_path = output_dir / f"{prefix}_combat_pair_summary.csv"
    pair_frame_path = output_dir / f"{prefix}_per_frame_pairwise.csv"
    events_path = output_dir / f"{prefix}_contact_events.csv"
    fight_path = output_dir / f"{prefix}_possible_fight_events.csv"
    inq_path = (
        output_dir
        / f"{prefix}_InqScribe_{int(args.contact_px)}px_{args.fps:.0f}fps.txt"
    )

    pd.DataFrame([pair_summary]).to_csv(pair_summary_path, index=False)
    pair_frame.to_csv(pair_frame_path, index=False)
    events_df.to_csv(events_path, index=False)
    fight_df.to_csv(fight_path, index=False)

    if not events_df.empty:
        write_inqscribe(events_df, inq_path)
    else:
        pd.DataFrame(
            columns=["Start Time", "End Time", "Title", "Comment"]
        ).to_csv(inq_path, sep="\t", index=False)

    build_data_dictionary(
        output_dir / f"{prefix}_summary_data_dictionary.csv"
    )

    print(f"OK fight: {session_name}")
    print(f"  pair summary: {pair_summary_path}")
    print(f"  individual summary: {individual_summary_path}")
    return pair_summary_path, individual_summary_path


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Unified BA/fight IDtracker.ai post-processing for one session.")
    p.add_argument("--analysis-type", choices=["auto", "ba", "fight"], default="auto", help="Auto detects BA for one animal and fight for two or more animals.")
    p.add_argument("--input-dir", default=".", help="Folder containing IDtracker.ai trajectory output, or a parent session folder.")
    p.add_argument("--trajectories", default=None, help="Explicit trajectories.npy/.h5/.csv path. Overrides --input-dir discovery.")
    p.add_argument("--session-json", default=None, help="Optional explicit session.json path.")
    p.add_argument("--attributes-json", default=None, help="Optional explicit attributes.json path.")
    p.add_argument("--roi-toml", default=None, help="Optional explicit ROI TOML file. If omitted, nearby .toml files are searched.")
    p.add_argument("--metadata-csv", default=None, help="Optional CSV with session/beetle metadata to merge into outputs.")
    p.add_argument("--animal0", type=int, default=0, help="First combat animal index, 0-based.")
    p.add_argument("--animal1", type=int, default=1, help="Second combat animal index, 0-based.")
    p.add_argument("--analysis-start-frame", type=int, default=0, help="First global frame to analyze. Default is 0. IDtracker.ai pre-start NaN frames are retained as missing/invalid, not filled as real positions.")
    p.add_argument("--window-frames", type=int, default=None, help="Number of frames to analyze. Omit for all available frames after start.")
    p.add_argument("--fps", type=float, default=30.0, help="Frames per second, used only for event seconds/InqScribe.")
    p.add_argument("--output-dir", default="postprocessing", help="Output folder.")
    p.add_argument("--prefix", default=None, help="Output filename prefix. Default comes from session/folder name.")

    p.add_argument("--contact-px", type=float, default=60.0, help="Pairwise distance threshold for contact/proximity.")
    p.add_argument("--min-contact-s", type=float, default=0.2, help="Minimum contact duration in seconds.")
    p.add_argument("--min-contact-frames", type=int, default=None, help="Minimum contact duration in frames. Overrides --min-contact-s if supplied.")
    p.add_argument("--fight-px", type=float, default=35.0, help="Stricter threshold for possible fight-like close contact. Use <=0 to disable.")
    p.add_argument("--min-fight-frames", type=int, default=6, help="Minimum frames for possible fight close-contact event.")

    p.add_argument("--move-threshold-px", type=float, default=30.0, help="Displacement threshold for movement onset.")
    p.add_argument("--movement-onset-consecutive-frames", type=int, default=30, help="Consecutive frames needed for sustained movement onset.")
    p.add_argument("--starting-position-frames", type=int, default=30, help="Fight only: classify starting left/right side from the median X coordinate of the first N valid tracked positions after analysis begins (default: 30).")
    p.add_argument("--max-step-px", type=float, default=50.0, help="Large frame-to-frame jump threshold for artifact interpolation.")
    p.add_argument("--interpolation-warning-fraction", type=float, default=0.05, help="Interpolated-frame fraction that triggers QC warning.")
    p.add_argument("--interpolation-warning-frames", type=int, default=300, help="Interpolated-frame count that triggers QC warning.")
    p.add_argument("--speed-moving-threshold-px-frame", type=float, default=None, help="Optional speed threshold for moving frame pairs.")
    p.add_argument("--roi-wall-buffer-px", type=float, default=50.0, help="Width of inward ROI wall/border zone.")
    p.add_argument("--roi-padding-px", type=float, default=30.0, help="Padding around local ROI/track maps.")
    p.add_argument("--track-linewidth", type=float, default=0.4, help="Track line width in maps.")
    p.add_argument("--turtled-linewidth", type=float, default=1.2, help="Deprecated. Kept for compatibility; turtling is now shown as points on BA-style maps.")
    p.add_argument("--turtled-point-size", type=float, default=5.0, help="Point size for turtling/stationary-frame highlights on maps.")
    p.add_argument("--turtled-point-alpha", type=float, default=0.45, help="Point transparency for turtling/stationary-frame highlights on maps.")
    p.add_argument("--animal0-color", default="tab:orange", help="Map color for animal 0 normal movement, start/end, and sustained-onset markers. Default: tab:orange.")
    p.add_argument("--animal1-color", default="tab:blue", help="Map color for animal 1 normal movement, start/end, and sustained-onset markers. Default: tab:blue.")
    p.add_argument("--turtled-color", default="black", help="Map color for turtling-like frame markers. Default: black.")
    p.add_argument("--interpolated-color", default="0.55", help="Map color for interpolated-position markers. Default: 0.55, a neutral gray.")
    p.add_argument("--show-map-points", action="store_true", help="Add dense trajectory points to maps.")
    p.add_argument("--full-frame-map", action="store_true", help="Use observed full track extent rather than first ROI crop.")
    p.add_argument("--map-max-overlay-points", type=int, default=400, help="Maximum interpolated or turtling marker points drawn per animal on QC maps. Counts in CSV outputs are not capped.")
    p.add_argument("--map-max-interaction-points", type=int, default=600, help="Fight only: maximum contact-location stars shown on interaction maps. Event calculations are not capped.")

    p.add_argument("--disable-turtling", action="store_true", help="Disable centroid-based turtling-like detector.")
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
    args = build_arg_parser().parse_args(argv)
    if args.fight_px is not None and args.fight_px <= 0:
        args.fight_px = None
    try:
        process_session(args)
        return 0
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
