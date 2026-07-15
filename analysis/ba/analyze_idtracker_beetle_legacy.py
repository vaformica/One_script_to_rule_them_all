#!/usr/bin/env python3
"""
Analyze one-animal idtracker.ai trajectories for beetle behavioral assay videos.

What it computes by default
---------------------------
- Total distance traveled over a fixed analysis window, default 7200 frames.
- Speed and acceleration summaries in pixels/frame only. Body-length and seconds-based outputs are intentionally excluded.
- Movement latency: frames from analysis_start_frame until the beetle is at least N pixels from its starting position.
- Cumulative movement latency: frames from analysis_start_frame until cumulative path length reaches N pixels.
- Track map PNG with ROI outline if session.json contains an idtracker.ai polygon ROI.
- Summary CSV and per-frame kinematics CSV.

Typical use
-----------
python analyze_idtracker_beetle.py \
  --input-dir /path/to/idtracker/session_or_outputs \
  --analysis-start-frame 1540 \
  --window-frames 7200 \
  --move-threshold-px 30 \
  --output-dir beetle_analysis

Dependencies: numpy, pandas, matplotlib, h5py.
Optional: scipy for Savitzky-Golay smoothing. The script works without scipy.
"""

from __future__ import annotations

import argparse
import json
import math
import re
from pathlib import Path
from typing import Any, Optional, Tuple

import h5py
import numpy as np
import pandas as pd

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt


def read_json(path: Optional[Path]) -> dict[str, Any]:
    if path is None or not path.exists():
        return {}
    with path.open("r") as f:
        return json.load(f)


def find_file(input_dir: Path, candidates: list[str]) -> Optional[Path]:
    """Find the first candidate file directly inside input_dir."""
    for name in candidates:
        p = input_dir / name
        if p.exists():
            return p
    return None


def find_file_near(input_dir: Path, candidates: list[str], max_parent_levels: int = 3) -> Optional[Path]:
    """
    Find a candidate file in input_dir or a few parent directories.

    This is needed because some idtracker.ai runs put trajectories in:
        session_X/trajectories/trajectories.npy
    while session.json and attributes.json live in:
        session_X/session.json
        session_X/attributes.json
    """
    input_dir = Path(input_dir).resolve()
    current = input_dir
    for _ in range(max_parent_levels + 1):
        hit = find_file(current, candidates)
        if hit is not None:
            return hit
        if current.parent == current:
            break
        current = current.parent
    return None


def load_trajectories(path: Path) -> Tuple[np.ndarray, dict[str, Any]]:
    """
    Return trajectories as shape (frames, animals, 2), with metadata when available.
    Supports idtracker.ai .npy object dict, .h5, and simple CSV outputs.
    """
    suffix = path.suffix.lower()
    metadata: dict[str, Any] = {}

    if suffix == ".npy":
        obj = np.load(path, allow_pickle=True)
        if obj.shape == () and isinstance(obj.item(), dict):
            d = obj.item()
            traj = np.asarray(d["trajectories"], dtype=float)
            metadata = {k: v for k, v in d.items() if k != "trajectories"}
        else:
            traj = np.asarray(obj, dtype=float)

    elif suffix in {".h5", ".hdf5"}:
        with h5py.File(path, "r") as h:
            traj = np.asarray(h["trajectories"], dtype=float)
            # Pull common attrs if present, but idtracker often stores these elsewhere.
            metadata = {k: h.attrs[k] for k in h.attrs.keys()}

    elif suffix == ".csv":
        df = pd.read_csv(path)
        # idtracker one-animal CSV often has columns: time, trajectories1, trajectories2
        xy_cols = [c for c in df.columns if c.lower().startswith("trajectories")]
        if len(xy_cols) < 2:
            raise ValueError(f"Could not find trajectory x/y columns in {path}")
        xy = df[xy_cols[:2]].to_numpy(dtype=float)
        traj = xy[:, None, :]
        if "time" in df.columns and len(df) > 1:
            dt = float(np.nanmedian(np.diff(df["time"].to_numpy(dtype=float))))
            if dt > 0:
                metadata["frames_per_second"] = 1.0 / dt

    else:
        raise ValueError(f"Unsupported trajectory file type: {path}")

    if traj.ndim == 2 and traj.shape[1] == 2:
        traj = traj[:, None, :]
    if traj.ndim != 3 or traj.shape[2] != 2:
        raise ValueError(f"Expected trajectories shape (frames, animals, 2), got {traj.shape}")
    return traj, metadata


def interpolate_nan_xy(xy: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """
    Linearly interpolate NaNs within x and y.
    Returns interpolated xy and a boolean array marking frames that were valid before interpolation.
    """
    xy = np.asarray(xy, dtype=float)
    valid_original = np.isfinite(xy).all(axis=1)

    out = xy.copy()
    idx = np.arange(len(out))
    for dim in range(2):
        good = np.isfinite(out[:, dim])
        if good.sum() == 0:
            raise ValueError("No finite coordinates found.")
        if good.sum() == 1:
            out[:, dim] = out[good, dim][0]
        else:
            out[:, dim] = np.interp(idx, idx[good], out[good, dim])
    return out, valid_original




def filter_artifact_jumps_and_interpolate(
    xy_raw: np.ndarray,
    max_step_px: float = 50.0,
    max_iterations: int = 10,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """
    Remove likely tracking artifacts before any behavioral calculations.

    Any frame-to-frame movement larger than max_step_px is treated as a likely
    centroid/tracking artifact. The implicated positions are set to NaN and then
    linearly interpolated. This function first handles isolated one-frame spikes
    (large jump out followed by large jump back), then iteratively removes any
    remaining step whose length is still above max_step_px.

    Returns
    -------
    xy_clean : np.ndarray
        Interpolated trajectory after removing missing points and artifact jumps.
    valid_original : np.ndarray
        TRUE for frames that had finite x/y in the raw IDtracker output.
    artifact_interpolated : np.ndarray
        TRUE for frames interpolated specifically because of a > max_step_px jump.
    any_interpolated : np.ndarray
        TRUE for frames interpolated either because IDtracker had missing values
        or because they were flagged as artifact jumps.
    """
    xy_raw = np.asarray(xy_raw, dtype=float)
    if xy_raw.ndim != 2 or xy_raw.shape[1] != 2:
        raise ValueError('xy_raw must have shape (n_frames, 2).')

    valid_original = np.isfinite(xy_raw).all(axis=1)
    xy_work = xy_raw.copy()
    max_step_px = float(max_step_px)
    artifact = np.full(len(xy_work), False)

    # If disabled, only interpolate original missing values.
    if not np.isfinite(max_step_px) or max_step_px <= 0:
        xy_clean, valid_after = interpolate_nan_xy(xy_work)
        return xy_clean, valid_original, artifact, ~valid_original

    # Pass 1: catch isolated one-frame spikes without also marking the return frame.
    xy_tmp, _ = interpolate_nan_xy(xy_work)
    if len(xy_tmp) >= 3:
        steps = np.linalg.norm(np.diff(xy_tmp, axis=0), axis=1)
        for i in range(len(xy_tmp) - 2):
            if steps[i] > max_step_px and steps[i + 1] > max_step_px:
                bridge = float(np.linalg.norm(xy_tmp[i + 2] - xy_tmp[i]))
                if bridge <= max_step_px:
                    artifact[i + 1] = True
        xy_work[artifact] = np.nan

    # Pass 2: iteratively remove remaining extreme steps. Mark the second frame
    # in each large step because that is usually the new bad location.
    for _ in range(max_iterations):
        xy_tmp, _ = interpolate_nan_xy(xy_work)
        steps = np.linalg.norm(np.diff(xy_tmp, axis=0), axis=1)
        bad_steps = np.flatnonzero(steps > max_step_px)
        if bad_steps.size == 0:
            break
        new_bad = bad_steps + 1
        before = int(artifact.sum())
        artifact[new_bad] = True
        xy_work[new_bad] = np.nan
        if int(artifact.sum()) == before:
            break

    any_interpolated = ~np.isfinite(xy_work).all(axis=1)
    xy_clean, _valid_after = interpolate_nan_xy(xy_work)
    return xy_clean, valid_original, artifact, any_interpolated


def maybe_smooth_xy(xy: np.ndarray, window: int, polyorder: int) -> np.ndarray:
    """
    Smooth positions before derivative calculation. Uses scipy if available.
    If scipy is unavailable or window <= 1, returns xy unchanged.
    """
    if window <= 1:
        return xy

    # Savitzky-Golay requires odd window length and window > polyorder.
    if window % 2 == 0:
        window += 1
    if window <= polyorder:
        window = polyorder + 2 + ((polyorder + 2) % 2 == 0)

    if window >= len(xy):
        return xy

    try:
        from scipy.signal import savgol_filter
    except Exception:
        return xy

    smoothed = xy.copy()
    for dim in range(2):
        smoothed[:, dim] = savgol_filter(xy[:, dim], window_length=window, polyorder=polyorder, mode="interp")
    return smoothed


def parse_roi_polygon(session: dict[str, Any]) -> Optional[np.ndarray]:
    """
    Parse idtracker.ai ROI string like:
    '+ Polygon [[1122.4, 357.2], [1425.5, 353.5], ...]'
    """
    roi_list = session.get("roi_list") or []
    if not roi_list:
        return None
    text = str(roi_list[0])
    nums = [float(x) for x in re.findall(r"[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?", text)]
    if len(nums) < 6 or len(nums) % 2 != 0:
        return None
    return np.array(nums, dtype=float).reshape(-1, 2)




def point_to_segment_distances(points: np.ndarray, a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Distance from each point to one line segment a-b."""
    points = np.asarray(points, dtype=float)
    a = np.asarray(a, dtype=float)
    b = np.asarray(b, dtype=float)
    ab = b - a
    denom = float(np.dot(ab, ab))
    if denom == 0:
        return np.linalg.norm(points - a, axis=1)
    t = np.clip(((points - a) @ ab) / denom, 0.0, 1.0)
    closest = a + t[:, None] * ab
    return np.linalg.norm(points - closest, axis=1)


def distance_to_polygon_boundary(points: np.ndarray, polygon: np.ndarray) -> np.ndarray:
    """Minimum Euclidean distance from each point to the polygon boundary."""
    polygon = np.asarray(polygon, dtype=float)
    closed = np.vstack([polygon, polygon[0]])
    dists = []
    for i in range(len(closed) - 1):
        dists.append(point_to_segment_distances(points, closed[i], closed[i + 1]))
    return np.min(np.vstack(dists), axis=0)


def points_in_polygon(points: np.ndarray, polygon: np.ndarray) -> np.ndarray:
    """Boolean mask for points inside polygon using matplotlib.path."""
    from matplotlib.path import Path as MplPath
    return MplPath(np.asarray(polygon, dtype=float)).contains_points(np.asarray(points, dtype=float))


def roi_buffer_metrics(
    xy: np.ndarray,
    roi: Optional[np.ndarray],
    buffer_px: float,
    move_hit_rel: Optional[int],
    sustained_move_hit_rel: Optional[int] = None,
) -> tuple[dict[str, Any], dict[str, np.ndarray]]:
    """
    Compute wall-buffer/center-use metrics.

    The ROI border buffer is the outer zone inside the ROI whose distance to the ROI boundary
    is < buffer_px. "not_in_roi_border_buffer" means the beetle is inside the ROI and at least
    buffer_px from the nearest ROI boundary; biologically, this is the crude "not wall-following"
    metric requested by Vince.
    """
    n = len(xy)
    if roi is None or buffer_px <= 0:
        nan_summary = {
            "roi_wall_buffer_px": float(buffer_px),
            "roi_buffer_available": False,
            "frames_inside_roi": np.nan,
            "frames_in_roi_border_buffer": np.nan,
            "frames_not_in_roi_border_buffer": np.nan,
            "fraction_frames_not_in_roi_border_buffer_total": np.nan,
            "active_position_frames_after_displacement_threshold": np.nan,
            "active_frames_in_roi_border_buffer": np.nan,
            "active_frames_not_in_roi_border_buffer": np.nan,
            "fraction_active_frames_in_roi_border_buffer": np.nan,
            "fraction_active_frames_not_in_roi_border_buffer": np.nan,
            "active_position_frames_after_sustained_displacement_threshold": np.nan,
            "active_frames_in_roi_border_buffer_after_sustained_threshold": np.nan,
            "active_frames_not_in_roi_border_buffer_after_sustained_threshold": np.nan,
            "fraction_active_frames_in_roi_border_buffer_after_sustained_threshold": np.nan,
            "fraction_active_frames_not_in_roi_border_buffer_after_sustained_threshold": np.nan,
        }
        arrays = {
            "inside_roi": np.full(n, False),
            "distance_to_roi_boundary_px": np.full(n, np.nan),
            "in_roi_border_buffer": np.full(n, False),
            "not_in_roi_border_buffer": np.full(n, False),
            "active_after_displacement_threshold": np.full(n, False),
            "active_after_sustained_displacement_threshold": np.full(n, False),
        }
        return nan_summary, arrays

    finite = np.isfinite(xy).all(axis=1)
    inside = np.full(n, False)
    dist = np.full(n, np.nan)
    if finite.any():
        inside[finite] = points_in_polygon(xy[finite], roi)
        dist[finite] = distance_to_polygon_boundary(xy[finite], roi)

    in_buffer = inside & np.isfinite(dist) & (dist < float(buffer_px))
    not_in_buffer = inside & np.isfinite(dist) & (dist >= float(buffer_px))

    active = np.full(n, False)
    if move_hit_rel is not None and np.isfinite(move_hit_rel):
        active[int(move_hit_rel):] = True
    active_valid = active & finite
    n_active = int(active_valid.sum())

    active_in_buffer = active_valid & in_buffer
    active_not_in_buffer = active_valid & not_in_buffer

    active_sustained = np.full(n, False)
    if sustained_move_hit_rel is not None and np.isfinite(sustained_move_hit_rel):
        active_sustained[int(sustained_move_hit_rel):] = True
    active_sustained_valid = active_sustained & finite
    n_active_sustained = int(active_sustained_valid.sum())
    active_sustained_in_buffer = active_sustained_valid & in_buffer
    active_sustained_not_in_buffer = active_sustained_valid & not_in_buffer

    metrics = {
        "roi_wall_buffer_px": float(buffer_px),
        "roi_buffer_available": True,
        "frames_inside_roi": int((finite & inside).sum()),
        "frames_in_roi_border_buffer": int(in_buffer.sum()),
        "frames_not_in_roi_border_buffer": int(not_in_buffer.sum()),
        "fraction_frames_not_in_roi_border_buffer_total": float(not_in_buffer.sum() / finite.sum()) if finite.sum() else np.nan,
        "active_position_frames_after_displacement_threshold": n_active,
        "active_frames_in_roi_border_buffer": int(active_in_buffer.sum()),
        "active_frames_not_in_roi_border_buffer": int(active_not_in_buffer.sum()),
        "fraction_active_frames_in_roi_border_buffer": float(active_in_buffer.sum() / n_active) if n_active > 0 else np.nan,
        "fraction_active_frames_not_in_roi_border_buffer": float(active_not_in_buffer.sum() / n_active) if n_active > 0 else np.nan,
        "active_position_frames_after_sustained_displacement_threshold": n_active_sustained,
        "active_frames_in_roi_border_buffer_after_sustained_threshold": int(active_sustained_in_buffer.sum()),
        "active_frames_not_in_roi_border_buffer_after_sustained_threshold": int(active_sustained_not_in_buffer.sum()),
        "fraction_active_frames_in_roi_border_buffer_after_sustained_threshold": float(active_sustained_in_buffer.sum() / n_active_sustained) if n_active_sustained > 0 else np.nan,
        "fraction_active_frames_not_in_roi_border_buffer_after_sustained_threshold": float(active_sustained_not_in_buffer.sum() / n_active_sustained) if n_active_sustained > 0 else np.nan,
    }
    arrays = {
        "inside_roi": inside,
        "distance_to_roi_boundary_px": dist,
        "in_roi_border_buffer": in_buffer,
        "not_in_roi_border_buffer": not_in_buffer,
        "active_after_displacement_threshold": active,
        "active_after_sustained_displacement_threshold": active_sustained,
    }
    return metrics, arrays

def summarize(values: np.ndarray, prefix: str) -> dict[str, float]:
    values = np.asarray(values, dtype=float)
    values = values[np.isfinite(values)]
    if values.size == 0:
        return {
            f"{prefix}_mean": np.nan,
            f"{prefix}_median": np.nan,
            f"{prefix}_sd": np.nan,
            f"{prefix}_min": np.nan,
            f"{prefix}_max": np.nan,
            f"{prefix}_p95": np.nan,
            f"{prefix}_p99": np.nan,
        }
    return {
        f"{prefix}_mean": float(np.mean(values)),
        f"{prefix}_median": float(np.median(values)),
        f"{prefix}_sd": float(np.std(values, ddof=1)) if values.size > 1 else 0.0,
        f"{prefix}_min": float(np.min(values)),
        f"{prefix}_max": float(np.max(values)),
        f"{prefix}_p95": float(np.percentile(values, 95)),
        f"{prefix}_p99": float(np.percentile(values, 99)),
    }


def first_threshold_crossing(values: np.ndarray, threshold: float) -> Optional[int]:
    hits = np.flatnonzero(np.asarray(values) >= threshold)
    if hits.size == 0:
        return None
    return int(hits[0])


def first_sustained_threshold_crossing(values: np.ndarray, threshold: float, consecutive_frames: int) -> Optional[int]:
    """
    First frame where values remain >= threshold for N consecutive frames.

    This avoids counting a one-frame centroid jump or interpolation artifact as movement onset.
    If consecutive_frames <= 1, this reduces to first_threshold_crossing().
    """
    vals = np.asarray(values, dtype=float)
    n_consec = max(1, int(consecutive_frames))
    above = np.isfinite(vals) & (vals >= float(threshold))
    if n_consec <= 1:
        return first_threshold_crossing(vals, threshold)
    if above.size < n_consec:
        return None
    # Rolling sum of TRUE values over the required run length.
    window_hits = np.convolve(above.astype(int), np.ones(n_consec, dtype=int), mode="valid")
    hits = np.flatnonzero(window_hits >= n_consec)
    if hits.size == 0:
        return None
    return int(hits[0])



def contiguous_true_runs(mask: np.ndarray) -> list[tuple[int, int]]:
    """Return inclusive (start, stop) runs where mask is True."""
    mask = np.asarray(mask, dtype=bool)
    runs: list[tuple[int, int]] = []
    i = 0
    n = len(mask)
    while i < n:
        if not mask[i]:
            i += 1
            continue
        j = i
        while j < n and mask[j]:
            j += 1
        runs.append((i, j - 1))
        i = j
    return runs


def merge_runs(runs: list[tuple[int, int]], max_gap: int) -> list[tuple[int, int]]:
    if not runs:
        return []
    merged: list[list[int]] = [[runs[0][0], runs[0][1]]]
    for start, stop in runs[1:]:
        gap = start - merged[-1][1] - 1
        if gap <= max_gap:
            merged[-1][1] = stop
        else:
            merged.append([start, stop])
    return [(int(a), int(b)) for a, b in merged]


def compute_turning_angles(xy: np.ndarray) -> np.ndarray:
    """
    Absolute turning angle between consecutive step vectors.
    Length is n_frames - 2. Units are radians.
    """
    steps = np.diff(xy, axis=0)
    angles = np.arctan2(steps[:, 1], steps[:, 0])
    return np.abs(np.diff(np.unwrap(angles)))


def detect_turtling_events(
    xy: np.ndarray,
    analysis_start_frame: int,
    move_hit_rel: Optional[int],
    disabled: bool = False,
    window_frames: int = 300,
    min_duration_frames: int = 300,
    merge_gap_frames: int = 60,
    max_net_displacement_px: float = 80.0,
    max_radius_gyration_px: float = 50.0,
    max_straightness: float = 0.25,
    min_path_px: float = 80.0,
    min_abs_turn_rad: float = 0.30,
    start_buffer_frames: int = 300,
) -> tuple[dict[str, Any], pd.DataFrame, dict[str, np.ndarray]]:
    """
    Heuristic detector for a beetle that has flipped/turtled and wiggles locally.

    This uses centroid trajectories only. It does not visually classify posture. A frame is
    flagged when surrounding trajectory windows show sustained confined movement: small net
    displacement, small radius of gyration, low straightness, enough path length to indicate
    wiggling, and high turning angle.
    """
    n = len(xy)
    false = np.full(n, False)
    nan = np.full(n, np.nan)

    base_summary: dict[str, Any] = {
        "turtling_detection_enabled": not disabled,
        "turtling_window_frames": int(window_frames) if window_frames is not None else np.nan,
        "turtling_min_duration_frames": int(min_duration_frames) if min_duration_frames is not None else np.nan,
        "turtling_merge_gap_frames": int(merge_gap_frames) if merge_gap_frames is not None else np.nan,
        "turtling_max_net_displacement_px": float(max_net_displacement_px) if max_net_displacement_px is not None else np.nan,
        "turtling_max_radius_gyration_px": float(max_radius_gyration_px) if max_radius_gyration_px is not None else np.nan,
        "turtling_max_straightness": float(max_straightness) if max_straightness is not None else np.nan,
        "turtling_min_path_px": float(min_path_px) if min_path_px is not None else np.nan,
        "turtling_min_abs_turn_rad": float(min_abs_turn_rad) if min_abs_turn_rad is not None else np.nan,
        "turtling_start_buffer_frames": int(start_buffer_frames) if start_buffer_frames is not None else np.nan,
    }

    empty_events = pd.DataFrame(columns=[
        "event_index", "start_frame_in_analysis_window", "stop_frame_in_analysis_window",
        "start_global_frame", "stop_global_frame", "duration_frames",
    ])

    arrays = {
        "is_turtled": false.copy(),
        "turtling_window_path_px": nan.copy(),
        "turtling_window_net_displacement_px": nan.copy(),
        "turtling_window_radius_gyration_px": nan.copy(),
        "turtling_window_straightness": nan.copy(),
        "turtling_window_mean_abs_turn_rad": nan.copy(),
    }

    if disabled or n < 3 or window_frames is None or window_frames < 5 or window_frames > n:
        summary = {
            **base_summary,
            "n_turtling_events": 0,
            "frames_spent_turtled": 0,
            "fraction_frames_turtled_total": 0.0,
            "active_frames_turtled_after_displacement_threshold": 0,
            "fraction_active_frames_turtled": np.nan,
            "first_turtling_start_global_frame": np.nan,
            "first_turtling_stop_global_frame": np.nan,
            "longest_turtling_event_frames": 0,
        }
        return summary, empty_events, arrays

    window_frames = int(window_frames)
    min_duration_frames = int(min_duration_frames)
    merge_gap_frames = int(merge_gap_frames)
    start_buffer_frames = int(start_buffer_frames)

    step_px = np.linalg.norm(np.diff(xy, axis=0), axis=1)
    abs_turn = compute_turning_angles(xy)
    candidate = np.full(n, False)

    allowed_start = 0
    if move_hit_rel is not None and np.isfinite(move_hit_rel):
        allowed_start = int(move_hit_rel) + start_buffer_frames

    for start in range(0, n - window_frames + 1):
        stop = start + window_frames
        seg = xy[start:stop]
        path = float(np.nansum(step_px[start:stop - 1]))
        net = float(np.linalg.norm(seg[-1] - seg[0]))
        center = np.nanmean(seg, axis=0)
        rg = float(np.sqrt(np.nanmean(np.sum((seg - center) ** 2, axis=1))))
        straight = float(net / path) if path > 0 else np.nan
        turn_slice = abs_turn[start:max(start, min(stop - 2, len(abs_turn)))]
        mean_abs_turn = float(np.nanmean(turn_slice)) if turn_slice.size else np.nan

        center_idx = start + window_frames // 2
        arrays["turtling_window_path_px"][center_idx] = path
        arrays["turtling_window_net_displacement_px"][center_idx] = net
        arrays["turtling_window_radius_gyration_px"][center_idx] = rg
        arrays["turtling_window_straightness"][center_idx] = straight
        arrays["turtling_window_mean_abs_turn_rad"][center_idx] = mean_abs_turn

        if stop < allowed_start:
            continue
        if (
            net <= max_net_displacement_px
            and rg <= max_radius_gyration_px
            and np.isfinite(straight) and straight <= max_straightness
            and path >= min_path_px
            and np.isfinite(mean_abs_turn) and mean_abs_turn >= min_abs_turn_rad
        ):
            candidate[max(start, allowed_start):stop] = True

    runs = contiguous_true_runs(candidate)
    runs = merge_runs(runs, merge_gap_frames)
    runs = [(a, b) for a, b in runs if (b - a + 1) >= min_duration_frames]

    is_turtled = np.full(n, False)
    records = []
    for idx, (a, b) in enumerate(runs, start=1):
        is_turtled[a:b + 1] = True
        dur = int(b - a + 1)
        records.append({
            "event_index": idx,
            "start_frame_in_analysis_window": int(a),
            "stop_frame_in_analysis_window": int(b),
            "start_global_frame": int(analysis_start_frame + a),
            "stop_global_frame": int(analysis_start_frame + b),
            "duration_frames": dur,
        })

    events = pd.DataFrame.from_records(records) if records else empty_events
    arrays["is_turtled"] = is_turtled

    active = np.full(n, False)
    if move_hit_rel is not None and np.isfinite(move_hit_rel):
        active[int(move_hit_rel):] = True
    n_active = int(active.sum())
    active_turtled = int((active & is_turtled).sum())
    total_turtled = int(is_turtled.sum())
    longest = int(events["duration_frames"].max()) if not events.empty else 0

    summary = {
        **base_summary,
        "n_turtling_events": int(len(events)),
        "frames_spent_turtled": total_turtled,
        "fraction_frames_turtled_total": float(total_turtled / n) if n else np.nan,
        "active_frames_turtled_after_displacement_threshold": active_turtled,
        "fraction_active_frames_turtled": float(active_turtled / n_active) if n_active > 0 else np.nan,
        "first_turtling_start_global_frame": int(events.iloc[0]["start_global_frame"]) if not events.empty else np.nan,
        "first_turtling_stop_global_frame": int(events.iloc[0]["stop_global_frame"]) if not events.empty else np.nan,
        "longest_turtling_event_frames": longest,
    }
    return summary, events, arrays


def write_summary_data_dictionary(path: Path, columns: list[str]) -> None:
    """Write a practical data dictionary for summary CSV columns."""
    definitions: dict[str, tuple[str, str]] = {
        "trajectory_file": ("path", "Trajectory file used for this analysis."),
        "session_json": ("path", "session.json metadata file used when available."),
        "attributes_json": ("path", "attributes.json metadata file used when available."),
        "session_name": ("text", "IDtracker.ai session name from session.json."),
        "animal_index_0_based": ("index", "Animal index analyzed. For one-beetle videos this should be 0."),
        "n_animals_in_file": ("count", "Number of animals stored in the trajectory file."),
        "analysis_start_frame": ("frame", "Global video frame where the analysis window begins."),
        "analysis_end_frame_inclusive": ("frame", "Final global video frame included in the analysis window."),
        "requested_window_frames": ("frames", "Requested analysis window length."),
        "actual_window_frames": ("frames", "Actual number of analyzed frames. Can be shorter if the trajectory file ends first."),
        "valid_position_fraction_before_interpolation": ("proportion", "Fraction of frames with finite x/y before interpolation."),
        "n_missing_position_frames_interpolated": ("frames", "Frames with missing coordinates that were linearly interpolated."),
        "move_threshold_px": ("pixels", "Displacement threshold used to define first movement onset."),
        "movement_onset_consecutive_frames": ("frames", "Number of consecutive frames that displacement must remain at or above move_threshold_px to count as sustained movement onset."),
        "latency_to_displacement_threshold_frames": ("frames", "Frames from analysis start until beetle is first at least move_threshold_px from its start position. This can be sensitive to one-frame artifacts."),
        "global_frame_displacement_threshold_crossed": ("frame", "Global video frame where displacement threshold was first crossed."),
        "latency_to_sustained_displacement_threshold_frames": ("frames", "Frames from analysis start until beetle is at least move_threshold_px from its start position and remains above that threshold for movement_onset_consecutive_frames."),
        "global_frame_sustained_displacement_threshold_crossed": ("frame", "Global video frame where sustained displacement threshold was crossed."),
        "latency_to_cumulative_distance_threshold_frames": ("frames", "Frames until cumulative path length reaches move_threshold_px."),
        "global_frame_cumulative_threshold_crossed": ("frame", "Global video frame where cumulative path threshold was crossed."),
        "total_distance_px": ("pixels", "Total path length across the analysis window."),
        "net_displacement_px": ("pixels", "Straight-line displacement from first to last analyzed position."),
        "max_displacement_from_start_px": ("pixels", "Maximum straight-line distance from the starting position."),
        "path_straightness_net_over_total": ("ratio", "Net displacement divided by total path length. Near 1 is straight; near 0 is tortuous/local movement."),
        "speed_threshold_for_moving_px_per_frame": ("pixels/frame", "Frame-based speed threshold used for moving/resting summaries."),
        "moving_frame_pairs": ("frame pairs", "Number of consecutive-frame intervals with speed at or above the moving threshold."),
        "moving_fraction_frame_pairs": ("proportion", "Moving frame pairs divided by all frame pairs in the analysis window."),
        "remaining_distance_after_displacement_threshold_px": ("pixels", "Path length after the beetle first crossed the displacement threshold."),
        "available_frames_after_displacement_threshold": ("frames", "Frames remaining in the analysis window after displacement threshold crossing."),
        "distance_per_available_frame_after_displacement_threshold_px": ("pixels/frame", "Remaining path distance divided by frames available after first displacement threshold crossing."),
        "remaining_distance_after_sustained_displacement_threshold_px": ("pixels", "Path length after sustained displacement-threshold crossing."),
        "available_frames_after_sustained_displacement_threshold": ("frames", "Frames remaining in the analysis window after sustained displacement-threshold crossing."),
        "distance_per_available_frame_after_sustained_displacement_threshold_px": ("pixels/frame", "Remaining path distance divided by frames available after sustained movement onset. This is the preferred post-latency movement-rate metric."),
        "roi_wall_buffer_px": ("pixels", "Width of inward ROI border buffer used to classify wall-zone frames."),
        "roi_buffer_available": ("boolean", "TRUE if ROI polygon and buffer metrics were available."),
        "frames_inside_roi": ("frames", "Position frames inside the ROI polygon."),
        "frames_in_roi_border_buffer": ("frames", "Frames inside the ROI and within roi_wall_buffer_px of the ROI boundary."),
        "frames_not_in_roi_border_buffer": ("frames", "Frames inside the ROI and at least roi_wall_buffer_px away from the boundary; crude not-wall-following metric."),
        "fraction_frames_not_in_roi_border_buffer_total": ("proportion", "frames_not_in_roi_border_buffer divided by all finite position frames."),
        "active_position_frames_after_displacement_threshold": ("frames", "Finite position frames from displacement-threshold crossing through the end of the window."),
        "active_frames_in_roi_border_buffer": ("frames", "Active-position frames inside the ROI border buffer."),
        "active_frames_not_in_roi_border_buffer": ("frames", "Active-position frames not in the ROI border buffer."),
        "fraction_active_frames_in_roi_border_buffer": ("proportion", "active_frames_in_roi_border_buffer divided by active_position_frames_after_displacement_threshold."),
        "fraction_active_frames_not_in_roi_border_buffer": ("proportion", "active_frames_not_in_roi_border_buffer divided by active_position_frames_after_displacement_threshold."),
        "active_position_frames_after_sustained_displacement_threshold": ("frames", "Finite position frames from sustained displacement-threshold crossing through the end of the analysis window."),
        "active_frames_in_roi_border_buffer_after_sustained_threshold": ("frames", "Sustained-active frames inside the ROI border buffer."),
        "active_frames_not_in_roi_border_buffer_after_sustained_threshold": ("frames", "Sustained-active frames inside the ROI but at least roi_wall_buffer_px away from the ROI boundary. Preferred not-wall-following numerator."),
        "fraction_active_frames_in_roi_border_buffer_after_sustained_threshold": ("proportion", "active_frames_in_roi_border_buffer_after_sustained_threshold divided by active_position_frames_after_sustained_displacement_threshold."),
        "fraction_active_frames_not_in_roi_border_buffer_after_sustained_threshold": ("proportion", "active_frames_not_in_roi_border_buffer_after_sustained_threshold divided by active_position_frames_after_sustained_displacement_threshold. Preferred not-wall-following fraction."),
        "turtling_detection_enabled": ("boolean", "TRUE if centroid-based turtling-like event detection was run."),
        "turtling_window_frames": ("frames", "Rolling window size used for turtling detection."),
        "turtling_min_duration_frames": ("frames", "Minimum duration required for a turtling event to be retained."),
        "turtling_merge_gap_frames": ("frames", "Maximum gap between candidate runs that will be merged into one turtling event."),
        "turtling_max_net_displacement_px": ("pixels", "Maximum net displacement allowed within a candidate turtling window."),
        "turtling_max_radius_gyration_px": ("pixels", "Maximum radius of gyration allowed within a candidate turtling window."),
        "turtling_max_straightness": ("ratio", "Maximum net/path straightness allowed for candidate turtling windows."),
        "turtling_min_path_px": ("pixels", "Minimum path length required within a candidate turtling window."),
        "turtling_min_abs_turn_rad": ("radians", "Minimum mean absolute turning angle required within a candidate turtling window."),
        "turtling_start_buffer_frames": ("frames", "Frames after movement-onset threshold before turtling detection is allowed."),
        "n_turtling_events": ("count", "Number of detected turtling-like events."),
        "frames_spent_turtled": ("frames", "Total frames classified as turtling-like."),
        "fraction_frames_turtled_total": ("proportion", "frames_spent_turtled divided by actual_window_frames."),
        "active_frames_turtled_after_displacement_threshold": ("frames", "Frames classified as turtling-like after displacement threshold crossing."),
        "fraction_active_frames_turtled": ("proportion", "active turtled frames divided by active_position_frames_after_displacement_threshold."),
        "first_turtling_start_global_frame": ("frame", "Global frame where the first turtling event starts."),
        "first_turtling_stop_global_frame": ("frame", "Global frame where the first turtling event stops."),
        "longest_turtling_event_frames": ("frames", "Duration of the longest detected turtling event."),
        "max_step_px_for_artifact_filter": ("pixels/frame", "Maximum allowed frame-to-frame movement before a point is treated as a likely tracking artifact and interpolated before all calculations."),
        "n_artifact_jump_frames_interpolated": ("frames", "Number of position frames interpolated because they were implicated in a frame-to-frame movement greater than max_step_px_for_artifact_filter."),
        "n_total_interpolated_position_frames": ("frames", "Total position frames interpolated for any reason: missing IDtracker positions plus large-step artifact filtering."),
        "fraction_total_interpolated_position_frames": ("proportion", "n_total_interpolated_position_frames divided by actual_window_frames."),
        "interpolation_warning": ("boolean", "TRUE when the number or fraction of interpolated position frames exceeds the warning thresholds. Inspect these videos before biological interpretation."),
        "interpolation_warning_fraction_threshold": ("proportion", "Fraction threshold used to trigger interpolation_warning."),
        "interpolation_warning_frame_threshold": ("frames", "Frame-count threshold used to trigger interpolation_warning."),
    }

    def infer(col: str) -> tuple[str, str]:
        if col in definitions:
            return definitions[col]
        if col.startswith("speed_px_per_frame_"):
            return ("pixels/frame", f"Summary statistic for speed per frame: {col.rsplit('_', 1)[-1]}.")
        if col.startswith("acceleration_px_per_frame2_"):
            return ("pixels/frame^2", f"Summary statistic for frame-to-frame change in velocity vector: {col.rsplit('_', 1)[-1]}.")
        return ("", "Column produced by the analysis script; inspect script or per-frame output for derivation.")

    rows = []
    for col in columns:
        unit, definition = infer(col)
        rows.append({"column": col, "unit_or_type": unit, "definition": definition})
    pd.DataFrame(rows).to_csv(path, index=False)

def make_track_map(
    xy: np.ndarray,
    valid_original: np.ndarray,
    output_png: Path,
    title: str,
    session: dict[str, Any],
    analysis_start_frame: int,
    move_hit_rel: Optional[int],
    width: Optional[int],
    height: Optional[int],
    track_linewidth: float = 0.25,
    roi_padding_px: float = 30.0,
    full_frame_map: bool = False,
    show_map_points: bool = False,
    roi_buffer_arrays: Optional[dict[str, np.ndarray]] = None,
    turtling_mask: Optional[np.ndarray] = None,
    turtled_linewidth: float = 0.7,
) -> None:
    """Write a track map PNG.

    By default, maps are ROI-local: the plot is cropped to the ROI bounding box plus
    `roi_padding_px`, and coordinates are shifted so the lower-left of that cropped
    view is local x=0 and local y=0. This avoids plotting small arena/cell tracks on
    the full global video frame. Use --full-frame-map to draw global video axes.
    """
    roi = parse_roi_polygon(session)
    local_roi_map = (roi is not None) and (not full_frame_map)

    if local_roi_map:
        xmin, ymin = np.nanmin(roi, axis=0)
        xmax, ymax = np.nanmax(roi, axis=0)
        pad = float(roi_padding_px)
        x_offset = xmin - pad
        y_offset = ymin - pad
        plot_xy = xy - np.array([x_offset, y_offset])
        plot_roi = roi - np.array([x_offset, y_offset])
        x_max_local = (xmax - xmin) + 2 * pad
        y_max_local = (ymax - ymin) + 2 * pad
        xlabel = "local x position within ROI crop (pixels)"
        ylabel = "local y position within ROI crop (pixels)"
        title = title + " [ROI-local map]"
    else:
        plot_xy = xy
        plot_roi = roi
        x_max_local = None
        y_max_local = None
        xlabel = "x position in full video frame (pixels)"
        ylabel = "y position in full video frame (pixels)"

    fig, ax = plt.subplots(figsize=(8, 8))

    # Draw thin trajectory. Points are optional because dense points obscure beetle tracks.
    frame_rel = np.arange(len(plot_xy))
    ax.plot(plot_xy[:, 0], plot_xy[:, 1], linewidth=track_linewidth, alpha=0.75, label="track")
    if show_map_points:
        sc = ax.scatter(plot_xy[:, 0], plot_xy[:, 1], c=frame_rel, s=1)
        cbar = fig.colorbar(sc, ax=ax, fraction=0.046, pad=0.04)
        cbar.set_label("Frame within analysis window")

    if roi_buffer_arrays is not None and "not_in_roi_border_buffer" in roi_buffer_arrays:
        center_mask = np.asarray(roi_buffer_arrays["not_in_roi_border_buffer"], dtype=bool)
        if np.any(center_mask):
            ax.scatter(plot_xy[center_mask, 0], plot_xy[center_mask, 1], s=2, alpha=0.35, label="not in wall buffer")

    if turtling_mask is not None and np.any(turtling_mask):
        txy = plot_xy.copy()
        txy[~np.asarray(turtling_mask, dtype=bool)] = np.nan
        ax.plot(txy[:, 0], txy[:, 1], linewidth=turtled_linewidth, alpha=0.95, label="turtled-like")

    # Mark original missing/interpolated points.
    if (~valid_original).any():
        miss = ~valid_original
        ax.scatter(plot_xy[miss, 0], plot_xy[miss, 1], s=8, marker="x", label="interpolated")

    ax.scatter(plot_xy[0, 0], plot_xy[0, 1], s=55, marker="o", label=f"start {analysis_start_frame}")
    ax.scatter(plot_xy[-1, 0], plot_xy[-1, 1], s=55, marker="s", label=f"end {analysis_start_frame + len(plot_xy) - 1}")

    if move_hit_rel is not None:
        ax.scatter(
            plot_xy[move_hit_rel, 0],
            plot_xy[move_hit_rel, 1],
            s=80,
            marker="*",
            label=f"sustained/first ≥ threshold: +{move_hit_rel} frames",
        )

    if plot_roi is not None:
        closed = np.vstack([plot_roi, plot_roi[0]])
        ax.plot(closed[:, 0], closed[:, 1], linewidth=1.5, label="ROI")

    ax.set_xlabel(xlabel)
    ax.set_ylabel(ylabel)
    ax.set_title(title)
    ax.set_aspect("equal", adjustable="box")
    ax.invert_yaxis()  # image coordinates: y increases downward

    if local_roi_map:
        ax.set_xlim(0, x_max_local)
        ax.set_ylim(y_max_local, 0)
    elif width and height:
        ax.set_xlim(0, width)
        ax.set_ylim(height, 0)

    handles, labels = ax.get_legend_handles_labels()
    if handles:
        ax.legend(
            handles,
            labels,
            loc="upper center",
            bbox_to_anchor=(0.5, -0.12),
            ncol=2,
            fontsize=8,
            frameon=False,
        )
    fig.tight_layout(rect=[0, 0.08, 1, 1])
    fig.savefig(output_png, dpi=300)
    plt.close(fig)


def main() -> None:
    parser = argparse.ArgumentParser(description="Analyze a one-animal idtracker.ai beetle trajectory.")
    parser.add_argument("--input-dir", type=Path, default=Path("."), help="Folder containing trajectories and session files.")
    parser.add_argument("--trajectories", type=Path, default=None, help="Path to trajectories.npy/.h5/.csv. Overrides --input-dir search.")
    parser.add_argument("--session-json", type=Path, default=None, help="Path to session.json.")
    parser.add_argument("--attributes-json", type=Path, default=None, help="Path to attributes.json.")
    parser.add_argument("--animal-index", type=int, default=0, help="0-based animal index. For one beetle, keep 0.")
    parser.add_argument("--analysis-start-frame", type=int, default=1540, help="Global video frame where behavioral analysis starts.")
    parser.add_argument("--window-frames", type=int, default=7200, help="Number of frames to analyze from analysis start.")
    parser.add_argument("--move-threshold-px", type=float, default=30.0, help="Pixel displacement threshold for movement latency.")
    parser.add_argument("--movement-onset-consecutive-frames", type=int, default=30,
                        help="Require displacement to stay above --move-threshold-px for this many consecutive frames before scoring sustained movement onset. Default: 30 frames.")
    parser.add_argument("--fps", type=float, default=None, help=argparse.SUPPRESS)
    parser.add_argument("--speed-moving-threshold-px-frame", type=float, default=None,
                        help="Optional frame-based threshold for moving/resting summaries. Default: 5 px/frame.")
    parser.add_argument("--speed-moving-threshold-px-s", type=float, default=None, help=argparse.SUPPRESS)
    parser.add_argument("--max-step-px", type=float, default=50.0,
                        help="Maximum allowed movement between consecutive frames before that step is treated as a likely tracking artifact and interpolated. Default: 50 px/frame. Use <=0 to disable.")
    parser.add_argument("--interpolation-warning-fraction", type=float, default=0.05,
                        help="Warn in the summary if this fraction of frames or more were interpolated after missing-position and artifact-jump filtering. Default: 0.05.")
    parser.add_argument("--interpolation-warning-frames", type=int, default=300,
                        help="Warn in the summary if this many frames or more were interpolated after missing-position and artifact-jump filtering. Default: 300 frames.")
    parser.add_argument("--smooth-window", type=int, default=0,
                        help="Optional Savitzky-Golay smoothing window in frames before derivatives. 0 disables smoothing.")
    parser.add_argument("--smooth-polyorder", type=int, default=2)
    parser.add_argument("--output-dir", type=Path, default=Path("beetle_analysis"), help="Folder for outputs.")
    parser.add_argument("--prefix", type=str, default=None, help="Output filename prefix. Default uses session name or trajectory filename.")
    parser.add_argument("--roi-wall-buffer-px", type=float, default=50.0,
                        help="Inward ROI border buffer in pixels. Frames outside this border zone are 'not wall-following'. Default: 50 px.")
    parser.add_argument("--roi-padding-px", type=float, default=30.0,
                        help="Padding around ROI in track-map PNG when using ROI-cropped map.")
    parser.add_argument("--track-linewidth", type=float, default=0.25, help="Trajectory line width in map PNG.")
    parser.add_argument("--turtled-linewidth", type=float, default=0.7, help="Accepted for compatibility with older batch wrapper; turtling map line width if implemented.")
    parser.add_argument("--full-frame-map", action="store_true", help="Show full video frame instead of ROI-cropped map.")
    parser.add_argument("--show-map-points", action="store_true", help="Overlay trajectory points colored by frame on map PNG.")
    parser.add_argument("--disable-turtling", action="store_true", help="Turn off centroid-based turtling-like event detection.")
    parser.add_argument("--turtling-window-frames", type=int, default=300, help="Rolling window size for turtling detection.")
    parser.add_argument("--turtling-min-duration-frames", type=int, default=300, help="Minimum retained turtling-event duration.")
    parser.add_argument("--turtling-merge-gap-frames", type=int, default=60, help="Merge candidate turtling runs separated by this many frames or fewer.")
    parser.add_argument("--turtling-max-net-displacement-px", type=float, default=80.0, help="Maximum net displacement in a turtling candidate window.")
    parser.add_argument("--turtling-max-radius-gyration-px", type=float, default=50.0, help="Maximum radius of gyration in a turtling candidate window.")
    parser.add_argument("--turtling-max-straightness", type=float, default=0.25, help="Maximum net/path straightness in a turtling candidate window.")
    parser.add_argument("--turtling-min-path-px", type=float, default=80.0, help="Minimum path length in a turtling candidate window.")
    parser.add_argument("--turtling-min-abs-turn-rad", type=float, default=0.30, help="Minimum mean absolute turning angle in radians in a candidate window.")
    parser.add_argument("--turtling-start-buffer-frames", type=int, default=300, help="Do not allow turtling calls until this many frames after movement onset.")
    parser.add_argument("--no-full-frame-axes", action="store_true",
                        help="Deprecated alias. Prefer --full-frame-map to force full frame; default is ROI-cropped when ROI exists.")
    args = parser.parse_args()

    input_dir = args.input_dir
    traj_path = args.trajectories or find_file(input_dir, ["trajectories.npy", "trajectories.h5", "trajectories.csv"])
    if traj_path is None:
        raise FileNotFoundError("Could not find trajectories.npy, trajectories.h5, or trajectories.csv. Use --trajectories.")

    session_path = args.session_json or find_file_near(input_dir, ["session.json"], max_parent_levels=3)
    attrs_path = args.attributes_json or find_file_near(input_dir, ["attributes.json"], max_parent_levels=3)

    session = read_json(session_path)
    attrs = read_json(attrs_path)
    traj, traj_meta = load_trajectories(traj_path)

    # Frames are the primary unit for this workflow. FPS is read only so deprecated
    # commands using --speed-moving-threshold-px-s can be converted to px/frame.
    fps = args.fps or session.get("frames_per_second") or attrs.get("frames_per_second") or traj_meta.get("frames_per_second")
    fps = float(fps) if fps is not None else None

    n_frames, n_animals, _ = traj.shape
    if args.animal_index < 0 or args.animal_index >= n_animals:
        raise IndexError(f"--animal-index {args.animal_index} out of range for {n_animals} animal(s).")

    start = int(args.analysis_start_frame)
    end_exclusive = min(start + int(args.window_frames), n_frames)
    if start < 0 or start >= n_frames:
        raise ValueError(f"analysis_start_frame {start} is outside trajectory length {n_frames}.")
    if end_exclusive <= start + 1:
        raise ValueError("Analysis window must contain at least 2 frames.")

    xy_raw = traj[start:end_exclusive, args.animal_index, :]
    xy_filtered, valid_original, artifact_jump_interpolated, any_interpolated = filter_artifact_jumps_and_interpolate(
        xy_raw,
        max_step_px=args.max_step_px,
    )
    xy = maybe_smooth_xy(xy_filtered, args.smooth_window, args.smooth_polyorder)

    n_missing_position_frames_interpolated = int((~valid_original).sum())
    n_artifact_jump_frames_interpolated = int(artifact_jump_interpolated.sum())
    n_total_interpolated_position_frames = int(any_interpolated.sum())
    fraction_total_interpolated_position_frames = float(n_total_interpolated_position_frames / len(xy)) if len(xy) else np.nan
    interpolation_warning = bool(
        n_total_interpolated_position_frames >= int(args.interpolation_warning_frames)
        or (
            np.isfinite(fraction_total_interpolated_position_frames)
            and fraction_total_interpolated_position_frames >= float(args.interpolation_warning_fraction)
        )
    )

    # Frame-to-frame kinematics. All behavioral calculations below use xy after
    # missing-value interpolation and >max_step_px artifact-jump interpolation.
    step_px = np.linalg.norm(np.diff(xy, axis=0), axis=1)
    speed_px_frame = step_px
    velocity_px_frame = np.diff(xy, axis=0)

    # Acceleration from frame-to-frame velocity differences. Length is window_frames - 2.
    acceleration_vec_px_frame2 = np.diff(velocity_px_frame, axis=0)
    acceleration_px_frame2 = np.linalg.norm(acceleration_vec_px_frame2, axis=1)

    cumulative_distance_px = np.concatenate([[0.0], np.cumsum(step_px)])
    displacement_from_start_px = np.linalg.norm(xy - xy[0, :], axis=1)

    move_hit_rel = first_threshold_crossing(displacement_from_start_px, args.move_threshold_px)
    sustained_move_hit_rel = first_sustained_threshold_crossing(
        displacement_from_start_px,
        args.move_threshold_px,
        args.movement_onset_consecutive_frames,
    )
    cum_hit_rel = first_threshold_crossing(cumulative_distance_px, args.move_threshold_px)

    moving_threshold = args.speed_moving_threshold_px_frame
    if moving_threshold is None and args.speed_moving_threshold_px_s is not None:
        if fps is None or fps <= 0:
            raise ValueError("--speed-moving-threshold-px-s was supplied, but FPS is unavailable. Prefer --speed-moving-threshold-px-frame.")
        moving_threshold = float(args.speed_moving_threshold_px_s) / fps
    if moving_threshold is None:
        moving_threshold = 5.0

    moving_step = speed_px_frame >= moving_threshold
    moving_frames = int(moving_step.sum())
    # speed has one fewer value than positions; report frame-pair percentage.
    moving_fraction = float(moving_step.mean()) if moving_step.size else np.nan

    roi = parse_roi_polygon(session)
    wall_summary, wall_arrays = roi_buffer_metrics(
        xy=xy,
        roi=roi,
        buffer_px=args.roi_wall_buffer_px,
        move_hit_rel=move_hit_rel,
        sustained_move_hit_rel=sustained_move_hit_rel,
    )

    turtling_summary, turtling_events, turtling_arrays = detect_turtling_events(
        xy=xy,
        analysis_start_frame=start,
        move_hit_rel=sustained_move_hit_rel if sustained_move_hit_rel is not None else move_hit_rel,
        disabled=args.disable_turtling,
        window_frames=args.turtling_window_frames,
        min_duration_frames=args.turtling_min_duration_frames,
        merge_gap_frames=args.turtling_merge_gap_frames,
        max_net_displacement_px=args.turtling_max_net_displacement_px,
        max_radius_gyration_px=args.turtling_max_radius_gyration_px,
        max_straightness=args.turtling_max_straightness,
        min_path_px=args.turtling_min_path_px,
        min_abs_turn_rad=args.turtling_min_abs_turn_rad,
        start_buffer_frames=args.turtling_start_buffer_frames,
    )

    # Distance traveled after first displacement threshold divided by frames still available.
    if move_hit_rel is not None:
        remaining_distance_after_displacement_threshold_px = float(cumulative_distance_px[-1] - cumulative_distance_px[move_hit_rel])
        available_frames_after_displacement_threshold = int(len(xy) - move_hit_rel)
        distance_per_available_frame_after_displacement_threshold_px = (
            remaining_distance_after_displacement_threshold_px / available_frames_after_displacement_threshold
            if available_frames_after_displacement_threshold > 0 else np.nan
        )
    else:
        remaining_distance_after_displacement_threshold_px = np.nan
        available_frames_after_displacement_threshold = np.nan
        distance_per_available_frame_after_displacement_threshold_px = np.nan

    if sustained_move_hit_rel is not None:
        remaining_distance_after_sustained_displacement_threshold_px = float(cumulative_distance_px[-1] - cumulative_distance_px[sustained_move_hit_rel])
        available_frames_after_sustained_displacement_threshold = int(len(xy) - sustained_move_hit_rel)
        distance_per_available_frame_after_sustained_displacement_threshold_px = (
            remaining_distance_after_sustained_displacement_threshold_px / available_frames_after_sustained_displacement_threshold
            if available_frames_after_sustained_displacement_threshold > 0 else np.nan
        )
    else:
        remaining_distance_after_sustained_displacement_threshold_px = np.nan
        available_frames_after_sustained_displacement_threshold = np.nan
        distance_per_available_frame_after_sustained_displacement_threshold_px = np.nan

    summary: dict[str, Any] = {
        "trajectory_file": str(traj_path),
        "session_json": str(session_path) if session_path else "",
        "attributes_json": str(attrs_path) if attrs_path else "",
        "session_name": session.get("name", ""),
        "animal_index_0_based": args.animal_index,
        "n_animals_in_file": n_animals,
        "analysis_start_frame": start,
        "analysis_end_frame_inclusive": end_exclusive - 1,
        "requested_window_frames": int(args.window_frames),
        "actual_window_frames": int(end_exclusive - start),
        "valid_position_fraction_before_interpolation": float(valid_original.mean()),
        "n_missing_position_frames_interpolated": n_missing_position_frames_interpolated,
        "max_step_px_for_artifact_filter": float(args.max_step_px),
        "n_artifact_jump_frames_interpolated": n_artifact_jump_frames_interpolated,
        "n_total_interpolated_position_frames": n_total_interpolated_position_frames,
        "fraction_total_interpolated_position_frames": fraction_total_interpolated_position_frames,
        "interpolation_warning": interpolation_warning,
        "interpolation_warning_fraction_threshold": float(args.interpolation_warning_fraction),
        "interpolation_warning_frame_threshold": int(args.interpolation_warning_frames),
        "move_threshold_px": float(args.move_threshold_px),
        "movement_onset_consecutive_frames": int(args.movement_onset_consecutive_frames),
        "latency_to_displacement_threshold_frames": move_hit_rel if move_hit_rel is not None else np.nan,
        "global_frame_displacement_threshold_crossed": (start + move_hit_rel) if move_hit_rel is not None else np.nan,
        "latency_to_sustained_displacement_threshold_frames": sustained_move_hit_rel if sustained_move_hit_rel is not None else np.nan,
        "global_frame_sustained_displacement_threshold_crossed": (start + sustained_move_hit_rel) if sustained_move_hit_rel is not None else np.nan,
        "latency_to_cumulative_distance_threshold_frames": cum_hit_rel if cum_hit_rel is not None else np.nan,
        "global_frame_cumulative_threshold_crossed": (start + cum_hit_rel) if cum_hit_rel is not None else np.nan,
        "total_distance_px": float(cumulative_distance_px[-1]),
        "net_displacement_px": float(displacement_from_start_px[-1]),
        "max_displacement_from_start_px": float(np.nanmax(displacement_from_start_px)),
        "path_straightness_net_over_total": float(displacement_from_start_px[-1] / cumulative_distance_px[-1]) if cumulative_distance_px[-1] > 0 else np.nan,
        "speed_threshold_for_moving_px_per_frame": float(moving_threshold),
        "moving_frame_pairs": moving_frames,
        "moving_fraction_frame_pairs": moving_fraction,
        "remaining_distance_after_displacement_threshold_px": remaining_distance_after_displacement_threshold_px,
        "available_frames_after_displacement_threshold": available_frames_after_displacement_threshold,
        "distance_per_available_frame_after_displacement_threshold_px": distance_per_available_frame_after_displacement_threshold_px,
        "remaining_distance_after_sustained_displacement_threshold_px": remaining_distance_after_sustained_displacement_threshold_px,
        "available_frames_after_sustained_displacement_threshold": available_frames_after_sustained_displacement_threshold,
        "distance_per_available_frame_after_sustained_displacement_threshold_px": distance_per_available_frame_after_sustained_displacement_threshold_px,
    }
    summary.update(wall_summary)
    summary.update(turtling_summary)

    summary.update(summarize(speed_px_frame, "speed_px_per_frame"))
    summary.update(summarize(acceleration_px_frame2, "acceleration_px_per_frame2"))



    prefix = args.prefix or session.get("name") or traj_path.stem
    safe_prefix = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(prefix)).strip("_")
    args.output_dir.mkdir(parents=True, exist_ok=True)

    # Per-frame table. Speed columns are aligned to the interval beginning at each frame;
    # the last frame has NaN speed because there is no next frame in the window.
    frame_global = np.arange(start, end_exclusive)
    per_frame = pd.DataFrame({
        "global_frame": frame_global,
        "frame_in_analysis_window": np.arange(len(xy)),
        "x_px": xy[:, 0],
        "y_px": xy[:, 1],
        "x_raw_px": xy_raw[:, 0],
        "y_raw_px": xy_raw[:, 1],
        "x_after_artifact_filter_px": xy_filtered[:, 0],
        "y_after_artifact_filter_px": xy_filtered[:, 1],
        "position_was_valid_before_interpolation": valid_original,
        "position_interpolated_from_missing_idtracker_value": ~valid_original,
        "position_interpolated_from_large_step_artifact": artifact_jump_interpolated,
        "position_interpolated_any_reason": any_interpolated,
        "displacement_from_start_px": displacement_from_start_px,
        "cumulative_distance_px": cumulative_distance_px,
        "step_to_next_frame_px": np.r_[step_px, np.nan],
        "speed_to_next_frame_px_per_frame": np.r_[speed_px_frame, np.nan],
        "moving_to_next_frame": np.r_[moving_step, False],
        "inside_roi": wall_arrays["inside_roi"],
        "distance_to_roi_boundary_px": wall_arrays["distance_to_roi_boundary_px"],
        "in_roi_border_buffer": wall_arrays["in_roi_border_buffer"],
        "not_in_roi_border_buffer": wall_arrays["not_in_roi_border_buffer"],
        "active_after_displacement_threshold": wall_arrays["active_after_displacement_threshold"],
        "active_after_sustained_displacement_threshold": wall_arrays["active_after_sustained_displacement_threshold"],
        "is_turtled": turtling_arrays["is_turtled"],
        "turtling_window_path_px": turtling_arrays["turtling_window_path_px"],
        "turtling_window_net_displacement_px": turtling_arrays["turtling_window_net_displacement_px"],
        "turtling_window_radius_gyration_px": turtling_arrays["turtling_window_radius_gyration_px"],
        "turtling_window_straightness": turtling_arrays["turtling_window_straightness"],
        "turtling_window_mean_abs_turn_rad": turtling_arrays["turtling_window_mean_abs_turn_rad"],
    })

    summary_csv = args.output_dir / f"{safe_prefix}_summary.csv"
    per_frame_csv = args.output_dir / f"{safe_prefix}_per_frame_kinematics.csv"
    turtling_events_csv = args.output_dir / f"{safe_prefix}_turtling_events.csv"
    data_dictionary_csv = args.output_dir / f"{safe_prefix}_summary_data_dictionary.csv"
    map_png = args.output_dir / f"{safe_prefix}_track_map.png"

    summary_df = pd.DataFrame([summary])
    summary_df.to_csv(summary_csv, index=False)
    per_frame.to_csv(per_frame_csv, index=False)
    turtling_events.to_csv(turtling_events_csv, index=False)
    write_summary_data_dictionary(data_dictionary_csv, list(summary_df.columns))

    width = int(session.get("width") or attrs.get("width") or traj_meta.get("width") or 0) or None
    height = int(session.get("height") or attrs.get("height") or traj_meta.get("height") or 0) or None
    full_frame_map = bool(args.full_frame_map)
    make_track_map(
        xy=xy,
        valid_original=~any_interpolated,
        output_png=map_png,
        title=f"{safe_prefix}: beetle track, frames {start}-{end_exclusive - 1}",
        session=session,
        analysis_start_frame=start,
        move_hit_rel=sustained_move_hit_rel if sustained_move_hit_rel is not None else move_hit_rel,
        width=width,
        height=height,
        track_linewidth=args.track_linewidth,
        roi_padding_px=args.roi_padding_px,
        full_frame_map=full_frame_map,
        show_map_points=args.show_map_points,
        roi_buffer_arrays=wall_arrays,
        turtling_mask=turtling_arrays["is_turtled"],
        turtled_linewidth=args.turtled_linewidth,
    )

    print("\nDone.")
    print(f"Summary CSV: {summary_csv}")
    print(f"Per-frame kinematics CSV: {per_frame_csv}")
    print(f"Turtling events CSV: {turtling_events_csv}")
    print(f"Summary data dictionary CSV: {data_dictionary_csv}")
    print(f"Track map PNG: {map_png}")
    print("\nKey results:")
    print(f"  Total distance: {summary['total_distance_px']:.2f} px")
    print(f"  Top speed after artifact filtering: {summary['speed_px_per_frame_max']:.2f} px/frame")
    print(f"  Frames interpolated from >{args.max_step_px:g} px jumps: {summary['n_artifact_jump_frames_interpolated']}")
    print(f"  Total interpolated position frames: {summary['n_total_interpolated_position_frames']} ({summary['fraction_total_interpolated_position_frames']:.3f})")
    if summary['interpolation_warning']:
        print("  WARNING: many interpolated positions. Inspect this video/track map before using movement metrics.")
    print(f"  Latency to {args.move_threshold_px:g} px displacement: {summary['latency_to_displacement_threshold_frames']} frames")
    print(f"  Sustained latency to {args.move_threshold_px:g} px displacement for {args.movement_onset_consecutive_frames} frames: {summary['latency_to_sustained_displacement_threshold_frames']} frames")
    print(f"  Latency to {args.move_threshold_px:g} px cumulative path: {summary['latency_to_cumulative_distance_threshold_frames']} frames")
    print(f"  Active frames after displacement threshold: {summary['active_position_frames_after_displacement_threshold']}")
    print(f"  Frames not in {args.roi_wall_buffer_px:g} px ROI wall buffer: {summary['frames_not_in_roi_border_buffer']}")
    print(f"  Active fraction not in ROI wall buffer: {summary['fraction_active_frames_not_in_roi_border_buffer']}")
    print(f"  Turtling events: {summary['n_turtling_events']}")
    print(f"  Frames spent turtled: {summary['frames_spent_turtled']} frames")


if __name__ == "__main__":
    main()
