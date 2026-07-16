from __future__ import annotations

import argparse
import ast
import json
import re
from pathlib import Path
from typing import Any


IDENTITY_KEYS = {"video_path", "video_paths", "video", "videos", "name", "session_name"}


def _strip_toml_comments(text: str) -> str:
    """Remove TOML comments while preserving hashes inside quoted strings."""
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
            output.append(char)
            escaped = False
            continue
        if char == "\\" and quote == '"':
            output.append(char)
            escaped = True
            continue
        if char in {'"', "'"}:
            if quote is None:
                quote = char
            elif quote == char:
                quote = None
            output.append(char)
            continue
        if char == "#" and quote is None:
            in_comment = True
            continue
        output.append(char)
    return "".join(output)


def _extract_assignment(text: str, wanted_key: str) -> str | None:
    """Extract a TOML assignment, including multiline arrays and strings."""
    pattern = re.compile(rf"(?m)^\s*{re.escape(wanted_key)}\s*=\s*")
    match = pattern.search(text)
    if not match:
        return None

    start = match.end()
    quote: str | None = None
    escaped = False
    square = curly = 0
    i = start
    while i < len(text):
        char = text[i]
        if escaped:
            escaped = False
        elif char == "\\" and quote == '"':
            escaped = True
        elif char in {'"', "'"}:
            if quote is None:
                quote = char
            elif quote == char:
                quote = None
        elif quote is None:
            if char == "[":
                square += 1
            elif char == "]":
                square = max(0, square - 1)
            elif char == "{":
                curly += 1
            elif char == "}":
                curly = max(0, curly - 1)
            elif char == "\n" and square == 0 and curly == 0:
                break
        i += 1
    return text[start:i].strip().rstrip(",").strip()


def _parse_value(raw: str | None) -> Any:
    if raw is None or not raw.strip():
        return None
    value = raw.strip()
    try:
        return ast.literal_eval(value)
    except (ValueError, SyntaxError):
        return value.strip('"\'')


def _read_identity_fields(toml_path: Path) -> dict[str, Any]:
    if not toml_path.is_file():
        raise FileNotFoundError(f"TOML file not found: {toml_path}")
    text = _strip_toml_comments(toml_path.read_text(encoding="utf-8"))
    found: dict[str, Any] = {}
    for key in IDENTITY_KEYS:
        value = _parse_value(_extract_assignment(text, key))
        if value is not None:
            found[key] = value
    return found


def toml_identity(toml_path: Path) -> tuple[Path, str]:
    """Return the video path and IDtracker session name encoded in the TOML."""
    fields = _read_identity_fields(toml_path)

    video_value: Any = None
    for key in ("video_paths", "video_path", "videos", "video"):
        if key in fields:
            video_value = fields[key]
            break
    if isinstance(video_value, (list, tuple)):
        video_value = video_value[0] if video_value else None
    if not isinstance(video_value, str) or not video_value.strip():
        raise ValueError(f"No video path found in TOML: {toml_path}")

    name_value: Any = None
    for key in ("name", "session_name"):
        if key in fields:
            name_value = fields[key]
            break
    name = name_value.strip() if isinstance(name_value, str) and name_value.strip() else toml_path.stem
    return Path(video_value).expanduser(), name


def validate_session(path: Path) -> bool:
    return (
        path.is_dir()
        and (path / "session.json").is_file()
        and (path / "trajectories" / "trajectories.npy").is_file()
    )


def expected_session_from_toml(toml_path: Path) -> Path:
    video_path, session_name = toml_identity(toml_path)
    return video_path.parent / f"session_{session_name}"


def locate(toml_path: Path, explicit: Path | None = None) -> Path:
    """Locate a complete session using the deterministic IDtracker rule.

    IDtracker writes ``session_<name>`` beside the video named in the TOML.
    The TOML file's own directory and the pipeline run directory are never used
    to infer session location.
    """
    expected = expected_session_from_toml(toml_path)

    if explicit is not None:
        if validate_session(explicit):
            return explicit.resolve()
        raise FileNotFoundError(
            f"Explicit session is incomplete: {explicit}. Expected session.json "
            "and trajectories/trajectories.npy"
        )

    if validate_session(expected):
        return expected.resolve()

    video_path, session_name = toml_identity(toml_path)
    details = [
        "Complete IDtracker session not found.",
        f"TOML: {toml_path}",
        f"Video encoded in TOML: {video_path}",
        f"Session name encoded in TOML: {session_name}",
        f"Expected session: {expected}",
        f"Missing session.json: {not (expected / 'session.json').is_file()}",
        "Missing trajectories/trajectories.npy: "
        f"{not (expected / 'trajectories' / 'trajectories.npy').is_file()}",
    ]
    raise FileNotFoundError("\n".join(details))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Locate the IDtracker session beside the video encoded in a TOML file."
    )
    parser.add_argument("--toml", required=True)
    parser.add_argument("--session", help="Optional explicit session folder override")
    parser.add_argument("--run-dir", help="Accepted for backward compatibility; not used")
    parser.add_argument("--expected-only", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    toml_path = Path(args.toml)
    if args.expected_only:
        path = expected_session_from_toml(toml_path)
    else:
        path = locate(toml_path, Path(args.session) if args.session else None)
    print(json.dumps({"session_path": str(path)}) if args.json else path)


if __name__ == "__main__":
    main()
