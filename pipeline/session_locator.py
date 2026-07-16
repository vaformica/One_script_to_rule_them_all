from __future__ import annotations

import argparse
import ast
import json
import os
import re
from pathlib import Path
from typing import Any

IDENTITY_KEYS = {"video_path", "video_paths", "video", "videos", "name", "session_name", "roi_list", "rois", "ROIS"}


def _strip_toml_comments(text: str) -> str:
    output: list[str] = []
    quote: str | None = None
    escaped = False
    in_comment = False
    for char in text:
        if in_comment:
            if char == "\n":
                in_comment = False
                output.append(char)
            continue
        if escaped:
            output.append(char); escaped = False; continue
        if char == "\\" and quote == '"':
            output.append(char); escaped = True; continue
        if char in {'"', "'"}:
            if quote is None: quote = char
            elif quote == char: quote = None
            output.append(char); continue
        if char == "#" and quote is None:
            in_comment = True; continue
        output.append(char)
    return "".join(output)


def _extract_assignment(text: str, wanted_key: str) -> str | None:
    match = re.search(rf"(?m)^\s*{re.escape(wanted_key)}\s*=\s*", text)
    if not match:
        return None
    start = match.end(); quote = None; escaped = False; square = curly = 0; i = start
    while i < len(text):
        char = text[i]
        if escaped: escaped = False
        elif char == "\\" and quote == '"': escaped = True
        elif char in {'"', "'"}:
            if quote is None: quote = char
            elif quote == char: quote = None
        elif quote is None:
            if char == "[": square += 1
            elif char == "]": square = max(0, square - 1)
            elif char == "{": curly += 1
            elif char == "}": curly = max(0, curly - 1)
            elif char == "\n" and square == 0 and curly == 0: break
        i += 1
    return text[start:i].strip().rstrip(",").strip()


def _parse_value(raw: str | None) -> Any:
    if raw is None or not raw.strip(): return None
    try: return ast.literal_eval(raw.strip())
    except (ValueError, SyntaxError): return raw.strip().strip('"\'')


def _read_fields(toml_path: Path) -> dict[str, Any]:
    if not toml_path.is_file(): raise FileNotFoundError(f"TOML file not found: {toml_path}")
    text = _strip_toml_comments(toml_path.read_text(encoding="utf-8"))
    return {k: v for k in IDENTITY_KEYS if (v := _parse_value(_extract_assignment(text, k))) is not None}


def toml_identity(toml_path: Path) -> tuple[Path, str]:
    fields = _read_fields(toml_path)
    video_value = next((fields[k] for k in ("video_paths", "video_path", "videos", "video") if k in fields), None)
    if isinstance(video_value, (list, tuple)): video_value = video_value[0] if video_value else None
    if not isinstance(video_value, str) or not video_value.strip(): raise ValueError(f"No video path found in TOML: {toml_path}")
    name_value = next((fields[k] for k in ("name", "session_name") if k in fields), None)
    name = name_value.strip() if isinstance(name_value, str) and name_value.strip() else toml_path.stem
    return Path(video_value).expanduser(), name


def _canonical_path(value: Any) -> str:
    if isinstance(value, (list, tuple)): value = value[0] if value else ""
    if not isinstance(value, str): return ""
    return os.path.realpath(os.path.expanduser(value))


def _roi_signature(value: Any) -> tuple[float, ...]:
    """Return rounded polygon coordinates, tolerant of IDtracker string/list storage."""
    if value is None: return ()
    text = json.dumps(value, sort_keys=True) if not isinstance(value, str) else value
    nums = re.findall(r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?", text)
    return tuple(round(float(x), 3) for x in nums)


def _toml_roi(toml_path: Path) -> tuple[float, ...]:
    fields = _read_fields(toml_path)
    return _roi_signature(next((fields[k] for k in ("roi_list", "rois", "ROIS") if k in fields), None))


def validate_session(path: Path) -> bool:
    return path.is_dir() and (path / "session.json").is_file() and (path / "trajectories" / "trajectories.npy").is_file()


def expected_session_from_toml(toml_path: Path) -> Path:
    video_path, session_name = toml_identity(toml_path)
    return video_path.parent / f"session_{session_name}"


def _session_metadata(path: Path) -> dict[str, Any]:
    try:
        data = json.loads((path / "session.json").read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _candidate_score(path: Path, target_video: str, target_roi: tuple[float, ...], newer_than: float | None) -> tuple[int, float] | None:
    if not validate_session(path): return None
    data = _session_metadata(path)
    candidate_video = _canonical_path(data.get("video_paths") or data.get("video_path") or data.get("videos") or data.get("video"))
    if candidate_video and target_video and candidate_video != target_video: return None
    candidate_roi = _roi_signature(data.get("roi_list") or data.get("rois") or data.get("ROIS"))
    if target_roi and candidate_roi and candidate_roi != target_roi: return None
    mtime = max((path / "session.json").stat().st_mtime, (path / "trajectories" / "trajectories.npy").stat().st_mtime)
    score = 0
    if candidate_video == target_video: score += 100
    if target_roi and candidate_roi == target_roi: score += 1000
    if newer_than is not None and mtime >= newer_than - 5: score += 50
    return score, mtime


def locate(toml_path: Path, explicit: Path | None = None, newer_than_file: Path | None = None) -> Path:
    if explicit is not None:
        if validate_session(explicit): return explicit.resolve()
        raise FileNotFoundError(f"Explicit session is incomplete: {explicit}")

    video_path, _ = toml_identity(toml_path)
    target_video = _canonical_path(str(video_path))
    target_roi = _toml_roi(toml_path)
    newer_than = newer_than_file.stat().st_mtime if newer_than_file and newer_than_file.exists() else None

    candidates: list[tuple[int, float, Path]] = []
    for path in video_path.parent.glob("session_*"):
        scored = _candidate_score(path, target_video, target_roi, newer_than)
        if scored is not None:
            candidates.append((scored[0], scored[1], path))

    if candidates:
        candidates.sort(key=lambda x: (x[0], x[1]), reverse=True)
        best = candidates[0]
        # Refuse ambiguous same-score/same-time matches when ROI metadata is unavailable.
        if len(candidates) > 1 and candidates[1][0] == best[0] and abs(candidates[1][1] - best[1]) < 1 and not target_roi:
            raise FileNotFoundError("Ambiguous complete IDtracker sessions; no ROI was available to distinguish them.")
        return best[2].resolve()

    expected = expected_session_from_toml(toml_path)
    raise FileNotFoundError("\n".join([
        "Complete IDtracker session not found.", f"TOML: {toml_path}", f"Video encoded in TOML: {video_path}",
        f"Expected-name fallback: {expected}", f"Target ROI values found: {len(target_roi)}",
        f"Complete session directories scanned beside video: {sum(1 for p in video_path.parent.glob('session_*') if validate_session(p))}",
    ]))


def main() -> None:
    p = argparse.ArgumentParser(description="Locate the complete IDtracker session produced for a TOML.")
    p.add_argument("--toml", required=True); p.add_argument("--session"); p.add_argument("--run-dir")
    p.add_argument("--newer-than-file"); p.add_argument("--expected-only", action="store_true"); p.add_argument("--json", action="store_true")
    args = p.parse_args(); toml = Path(args.toml)
    path = expected_session_from_toml(toml) if args.expected_only else locate(toml, Path(args.session) if args.session else None, Path(args.newer_than_file) if args.newer_than_file else None)
    print(json.dumps({"session_path": str(path)}) if args.json else path)


if __name__ == "__main__": main()
