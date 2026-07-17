from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, List, Tuple

RERUN_STATUSES = {"NEEDS RERUN", "RERUNNING", "RERUN"}


def normalize_status(value: object) -> str:
    text = str(value or "PENDING").strip().upper()
    return {"DONE": "APPROVED", "RERUN": "NEEDS RERUN"}.get(text, text)


def _video_key(value: object) -> str:
    name = Path(str(value or "").strip()).name.lower()
    # QC files from older releases sometimes omitted the extension.
    return re.sub(r"\.(mp4|avi|mov|mkv)$", "", name)


def comparison_key(record: Dict[str, object]) -> Tuple[str, str, str]:
    return (
        _video_key(record.get("video") or record.get("pipeline_video_filename")),
        str(record.get("cell") or record.get("pipeline_cell_label") or "").strip().upper(),
        str(record.get("analysis") or record.get("pipeline_analysis_type") or "").strip().upper(),
    )


def attempt_number(record: Dict[str, object]) -> int:
    for field in ("attempt_index", "pipeline_attempt_index", "run_index", "pipeline_run_index"):
        value = record.get(field)
        if value not in (None, ""):
            match = re.search(r"\d+", str(value))
            if match:
                return int(match.group())
    for field in ("record_id", "run_dir"):
        match = re.search(r"(?:attempt_|_A|run_)(\d+)", str(record.get(field) or ""), re.I)
        if match:
            return int(match.group(1))
    return 0


def _date_rank(record: Dict[str, object]) -> float:
    for field in ("collected_at", "date_run", "submitted_at", "completed_at"):
        text = str(record.get(field) or "").strip()
        if not text:
            continue
        normalized = text.replace("Z", "+00:00")
        try:
            return datetime.fromisoformat(normalized).timestamp()
        except ValueError:
            pass
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%Y%m%d_%H%M%S"):
            try:
                return datetime.strptime(text, fmt).timestamp()
            except ValueError:
                continue
    return 0.0


def chronology_rank(record: Dict[str, object]) -> Tuple[int, float, str]:
    return (attempt_number(record), _date_rank(record), str(record.get("record_id") or ""))


def build_rerun_comparisons(records: Iterable[Dict[str, object]]) -> List[Dict[str, object]]:
    """Return flagged reruns and all later same-video/cell/analysis attempts.

    Each returned dictionary contains the original record fields plus private GUI
    annotations: ``_comparison_group``, ``_comparison_role``,
    ``_comparison_label``, and ``_attempt_number``.
    """
    rows = [dict(r) for r in records]
    grouped: Dict[Tuple[str, str, str], List[Dict[str, object]]] = {}
    for row in rows:
        row["_normalized_status"] = normalize_status(row.get("qc_decision"))
        row["_attempt_number"] = attempt_number(row)
        grouped.setdefault(comparison_key(row), []).append(row)

    output: List[Dict[str, object]] = []
    seen = set()
    group_number = 0
    for key in sorted(grouped):
        candidates = sorted(grouped[key], key=chronology_rank)
        for original in candidates:
            if original["_normalized_status"] not in {"NEEDS RERUN", "RERUNNING"}:
                continue
            later = [r for r in candidates if chronology_rank(r) > chronology_rank(original)]
            group_number += 1
            group_id = f"R{group_number:04d}"
            original_copy = dict(original)
            original_copy["_comparison_group"] = group_id
            original_copy["_comparison_role"] = "original"
            if later:
                original_copy["_comparison_label"] = f"FLAGGED — {len(later)} later attempt(s) found"
            else:
                original_copy["_comparison_label"] = "FLAGGED — no later attempt found"
            identity = (group_id, str(original_copy.get("record_id")), "original")
            if identity not in seen:
                output.append(original_copy); seen.add(identity)

            for replacement in later:
                replacement_copy = dict(replacement)
                replacement_copy["_comparison_group"] = group_id
                replacement_copy["_comparison_role"] = "later"
                status = replacement_copy["_normalized_status"]
                pipeline = str(replacement_copy.get("pipeline_status") or "").strip().upper()
                if status == "APPROVED":
                    state = "APPROVED — original may be superseded after review"
                elif pipeline in {"COMPLETED", "COLLECTED", "DONE"}:
                    state = f"LATER ATTEMPT COMPLETE — QC {status}"
                else:
                    state = f"LATER ATTEMPT — {pipeline or status}"
                replacement_copy["_comparison_label"] = state
                identity = (group_id, str(replacement_copy.get("record_id")), "later")
                if identity not in seen:
                    output.append(replacement_copy); seen.add(identity)
    return output
