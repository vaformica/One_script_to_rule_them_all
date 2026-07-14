from __future__ import annotations

import hashlib
import re
from pathlib import Path
from collections import defaultdict

from .models import RemoteVideo, RemoteToml, AnalysisUnit


CELL_RE = re.compile(r"(?:^|[_-])([A-Za-z]{1,3}\d{1,3})(?:$|[_-])")


def normalize_stem(value: str) -> str:
    value = value.lower()
    value = re.sub(r"\.(mp4|avi|toml)$", "", value)
    value = re.sub(r"[^a-z0-9]+", "_", value)
    value = re.sub(r"_+", "_", value).strip("_")
    return value


def infer_cell_label(toml: RemoteToml) -> str:
    if toml.cell_label:
        return toml.cell_label
    match = CELL_RE.search(toml.stem)
    if match:
        return match.group(1)
    return toml.stem.split("_")[-1]


def infer_assay_type(toml: RemoteToml) -> str:
    if toml.number_of_animals == 1:
        return "behavioral_assay"
    if toml.number_of_animals == 2:
        return "fight"
    if (toml.roi_count or 0) >= 2:
        return "fight"
    return "unknown"


def make_analysis_unit_id(video_path: str, toml_path: str, cell_label: str) -> str:
    raw = f"{Path(video_path).name}|{Path(toml_path).name}|{cell_label}"
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]
    readable = normalize_stem(Path(video_path).stem)[:40]
    return f"{readable}_{cell_label}_{digest}"


def match_files(videos: list[RemoteVideo], tomls: list[RemoteToml]) -> tuple[list[AnalysisUnit], list[dict]]:
    videos_by_filename = defaultdict(list)
    videos_by_stem = defaultdict(list)
    for v in videos:
        videos_by_filename[v.filename.lower()].append(v)
        videos_by_stem[normalize_stem(v.stem)].append(v)

    units: dict[str, AnalysisUnit] = {}
    unmatched = []

    for t in tomls:
        candidates: list[tuple[RemoteVideo, str, int]] = []

        if t.embedded_video_path:
            for v in videos:
                if v.path == t.embedded_video_path:
                    candidates.append((v, "embedded_full_path", 100))

        if t.embedded_video_filename:
            for v in videos_by_filename.get(t.embedded_video_filename.lower(), []):
                candidates.append((v, "embedded_filename", 95))

        tnorm = normalize_stem(t.stem)
        for v in videos:
            vnorm = normalize_stem(v.stem)
            if tnorm == vnorm:
                candidates.append((v, "exact_normalized_stem", 90))
            elif tnorm.startswith(vnorm + "_"):
                candidates.append((v, "toml_starts_with_video_stem", 80))
            elif vnorm and vnorm in tnorm:
                candidates.append((v, "video_stem_contained_in_toml", 70))

        if not candidates:
            unmatched.append({"toml_path": t.path, "reason": "no_video_match"})
            continue

        dedup = {}
        for v, method, score in candidates:
            current = dedup.get(v.path)
            if current is None or score > current[1]:
                dedup[v.path] = (method, score)

        ranked = sorted(
            [(path, method, score) for path, (method, score) in dedup.items()],
            key=lambda x: (-x[2], x[0]),
        )
        best_score = ranked[0][2]
        best = [x for x in ranked if x[2] == best_score]

        if len(best) != 1:
            unmatched.append({
                "toml_path": t.path,
                "reason": "ambiguous_video_match",
                "candidate_paths": [x[0] for x in best],
            })
            continue

        video_path, method, score = best[0]
        v = next(x for x in videos if x.path == video_path)
        cell = infer_cell_label(t)
        auid = make_analysis_unit_id(v.path, t.path, cell)

        unit = AnalysisUnit(
            analysis_unit_id=auid,
            video_path=v.path,
            toml_path=t.path,
            video_filename=v.filename,
            toml_filename=t.filename,
            cell_label=cell,
            assay_type=infer_assay_type(t),
            animal_count=t.number_of_animals,
            roi_count=t.roi_count,
            match_method=method,
            match_score=score,
        )

        existing = units.get(auid)
        if existing is None or unit.match_score > existing.match_score:
            units[auid] = unit

    return list(units.values()), unmatched
