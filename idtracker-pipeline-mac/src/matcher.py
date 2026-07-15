import re
from .models import MatchRecord


CELL_SUFFIX = re.compile(r"(?:[_-](?:cell[_-]?)?[A-Z]{0,3}\d{1,2})$", re.I)


def normalized(stem):
    value = re.sub(r"[\s-]+", "_", stem.lower())
    value = re.sub(r"_+", "_", value).strip("_")
    return CELL_SUFFIX.sub("", value)


def match_all(videos, tomls, sessions):
    by_name, by_stem = {}, {}
    for video in videos:
        by_name.setdefault(video.filename.lower(), []).append(video)
        by_stem.setdefault(normalized(video.stem), []).append(video)

    results = []
    for toml in tomls:
        candidates, reason = [], ""

        if toml.embedded_video_filename:
            candidates = by_name.get(toml.embedded_video_filename.lower(), [])
            if candidates:
                reason = "Exact video filename embedded in TOML"

        if not candidates:
            candidates = by_stem.get(normalized(toml.stem), [])
            if candidates:
                reason = "Normalized TOML/video stem match"

        if len(candidates) == 1:
            video = candidates[0]
            session_matches = [
                session for session in sessions
                if video.stem.lower() in session.path.lower()
                and (
                    not toml.cell_label
                    or toml.cell_label.lower() in session.path.lower()
                )
            ]
            session = session_matches[0] if len(session_matches) == 1 else None
            status, selected = "Matched", True
        elif len(candidates) > 1:
            video = session = None
            status, selected = "Ambiguous", False
            reason = f"{reason}; {len(candidates)} possible videos"
        else:
            video = session = None
            status, selected = "Unmatched", False
            reason = "No sufficiently confident video match"

        if toml.parse_error:
            status, selected, reason = "TOML error", False, toml.parse_error

        assay = "Auto"
        if toml.number_of_animals == 1:
            assay = "Behavioral assay"
        elif toml.number_of_animals == 2:
            assay = "Fight"

        results.append(MatchRecord(
            selected=selected,
            status=status,
            reason=reason,
            video_path=video.path if video else None,
            video_filename=video.filename if video else None,
            toml_path=toml.path,
            toml_filename=toml.filename,
            cell_label=toml.cell_label,
            session_path=session.path if session else None,
            number_of_animals=toml.number_of_animals,
            roi_count=toml.roi_count,
            area_min=toml.area_min,
            area_max=toml.area_max,
            background_difference=toml.background_difference,
            assay_type=assay,
        ))
    return results
