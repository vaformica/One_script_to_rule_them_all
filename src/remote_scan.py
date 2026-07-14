from __future__ import annotations

import json
import shlex
from typing import Any

from .models import RemoteVideo, RemoteToml
from .ssh import SSHClient


REMOTE_SCRIPT = r"""
import json, os, sys
from pathlib import Path

try:
    import tomllib
except Exception:
    tomllib = None

video_roots = json.loads(sys.argv[1])
toml_roots = json.loads(sys.argv[2])

def files_under(roots, suffixes):
    out = []
    seen = set()
    for root in roots:
        p = Path(root).expanduser()
        if not p.exists():
            continue
        for dirpath, dirnames, filenames in os.walk(p):
            dirnames[:] = [d for d in dirnames if not d.startswith('.')]
            for name in filenames:
                path = Path(dirpath) / name
                if path.suffix.lower() not in suffixes:
                    continue
                resolved = str(path.resolve())
                if resolved in seen:
                    continue
                seen.add(resolved)
                try:
                    st = path.stat()
                    out.append({
                        "path": resolved,
                        "filename": path.name,
                        "stem": path.stem,
                        "size_bytes": st.st_size,
                        "modified_epoch": st.st_mtime,
                    })
                except OSError:
                    pass
    return out

def flatten_find(obj, wanted):
    hits = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if str(k).lower() in wanted:
                hits.append(v)
            hits.extend(flatten_find(v, wanted))
    elif isinstance(obj, list):
        for v in obj:
            hits.extend(flatten_find(v, wanted))
    return hits

def scalar_number(value):
    try:
        if isinstance(value, bool):
            return None
        if isinstance(value, (int, float)):
            return float(value)
    except Exception:
        pass
    return None

videos = files_under(video_roots, {".mp4", ".avi"})
tomls = files_under(toml_roots, {".toml"})

for rec in tomls:
    rec.update({
        "embedded_video_path": None,
        "embedded_video_filename": None,
        "cell_label": None,
        "number_of_animals": None,
        "roi_count": None,
        "area_min": None,
        "area_max": None,
        "background_difference_threshold": None,
    })
    if tomllib is None:
        continue
    try:
        with open(rec["path"], "rb") as fh:
            doc = tomllib.load(fh)
    except Exception:
        continue

    path_hits = flatten_find(doc, {
        "video_path", "video_paths", "video", "videos", "input_video",
        "video_file", "video_files", "video_paths_list"
    })
    candidates = []
    for value in path_hits:
        if isinstance(value, str):
            candidates.append(value)
        elif isinstance(value, list):
            candidates.extend(str(x) for x in value if isinstance(x, str))
    for value in candidates:
        lower = value.lower()
        if lower.endswith(".mp4") or lower.endswith(".avi"):
            rec["embedded_video_path"] = value
            rec["embedded_video_filename"] = Path(value).name
            break

    for key in ("number_of_animals", "n_animals", "number_of_blobs"):
        hits = flatten_find(doc, {key})
        if hits:
            try:
                rec["number_of_animals"] = int(hits[0])
                break
            except Exception:
                pass

    roi_hits = flatten_find(doc, {"rois", "roi_list", "regions_of_interest"})
    for value in roi_hits:
        if isinstance(value, list):
            rec["roi_count"] = len(value)
            break

    area_hits = flatten_find(doc, {"area_ths", "area_thresholds"})
    for value in area_hits:
        if isinstance(value, list) and len(value) >= 2:
            a = scalar_number(value[0])
            b = scalar_number(value[1])
            rec["area_min"], rec["area_max"] = a, b
            break

    intensity_hits = flatten_find(doc, {"intensity_ths", "intensity_thresholds"})
    for value in intensity_hits:
        if isinstance(value, list) and len(value) >= 2:
            rec["background_difference_threshold"] = scalar_number(value[1])
            break

    cell = Path(rec["path"]).stem.split("_")[-1]
    if 1 <= len(cell) <= 6:
        rec["cell_label"] = cell

print(json.dumps({"videos": videos, "tomls": tomls}))
"""


def scan_remote(
    ssh: SSHClient,
    video_roots: list[str],
    toml_roots: list[str],
    timeout: int = 600,
) -> tuple[list[RemoteVideo], list[RemoteToml]]:
    payload1 = shlex.quote(json.dumps(video_roots))
    payload2 = shlex.quote(json.dumps(toml_roots))
    script = shlex.quote(REMOTE_SCRIPT)
    command = f"python3 -c {script} {payload1} {payload2}"
    result = ssh.run(command, timeout=timeout).check()
    data = json.loads(result.stdout)

    videos = [RemoteVideo(**x) for x in data.get("videos", [])]
    tomls = [RemoteToml(**x) for x in data.get("tomls", [])]
    return videos, tomls
