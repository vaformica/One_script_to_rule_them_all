import re
import shlex
from pathlib import Path
from typing import Any
import tomlkit

from .models import VideoRecord, TomlRecord, SessionRecord


def _find_values(obj: Any, keys: set[str]):
    found = []
    if isinstance(obj, dict):
        for key, value in obj.items():
            if str(key).lower() in keys:
                found.append(value)
            found.extend(_find_values(value, keys))
    elif isinstance(obj, list):
        for value in obj:
            found.extend(_find_values(value, keys))
    return found


def _pair(values):
    for value in values:
        if isinstance(value, (list, tuple)) and len(value) >= 2:
            try:
                return float(value[0]), float(value[1])
            except Exception:
                pass
    return None, None


def _cell(stem):
    match = re.search(r"(?:^|[_-])([A-Z]{1,3}\d{1,2})$", stem, re.I)
    return match.group(1) if match else None


def parse_toml(path, text):
    p = Path(path)
    row = TomlRecord(path=path, filename=p.name, stem=p.stem, cell_label=_cell(p.stem))
    try:
        doc = tomlkit.parse(text)

        candidates = []
        for value in _find_values(doc, {
            "video_path", "video_paths", "video", "videos",
            "video_file", "video_files", "input_video"
        }):
            if isinstance(value, str):
                candidates.append(value)
            elif isinstance(value, list):
                candidates.extend(str(x) for x in value if isinstance(x, str))
        for candidate in candidates:
            if candidate.lower().endswith((".mp4", ".avi")):
                row.embedded_video_filename = Path(candidate).name
                break

        for value in _find_values(doc, {
            "number_of_animals", "n_animals", "number_of_blobs"
        }):
            try:
                row.number_of_animals = int(value)
                break
            except Exception:
                pass

        for value in _find_values(doc, {"rois", "roi_list", "regions_of_interest"}):
            if isinstance(value, list):
                row.roi_count = len(value)
                break

        row.area_min, row.area_max = _pair(
            _find_values(doc, {"area_ths", "area_thresholds"})
        )
        _, row.background_difference = _pair(
            _find_values(doc, {"intensity_ths", "intensity_thresholds"})
        )
    except Exception as exc:
        row.parse_error = str(exc)
    return row


def scan(backend, root):
    command = (
        f"find {shlex.quote(root)} "
        r"\( -type f \( -iname '*.mp4' -o -iname '*.avi' -o -iname '*.toml' \) "
        r"-o -type d -name 'session_*' \) -print"
    )
    output = backend.run(command, timeout=900).check().stdout

    videos, toml_paths, sessions = [], [], []
    for line in output.splitlines():
        path = line.strip()
        if not path:
            continue
        p = Path(path)
        if p.name.lower().startswith("session_"):
            sessions.append(SessionRecord(path=path, folder_name=p.name))
        elif p.suffix.lower() in (".mp4", ".avi"):
            videos.append(VideoRecord(path=path, filename=p.name, stem=p.stem))
        elif p.suffix.lower() == ".toml":
            toml_paths.append(path)

    tomls = []
    for path in toml_paths:
        try:
            tomls.append(parse_toml(path, backend.read_text(path)))
        except Exception as exc:
            p = Path(path)
            tomls.append(TomlRecord(
                path=path,
                filename=p.name,
                stem=p.stem,
                parse_error=str(exc),
            ))
    return videos, tomls, sessions
