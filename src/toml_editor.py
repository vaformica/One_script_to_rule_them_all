from __future__ import annotations

from pathlib import Path
from typing import Any

import tomlkit


AREA_KEYS = {"area_ths", "area_thresholds"}
INTENSITY_KEYS = {"intensity_ths", "intensity_thresholds"}


def edit_thresholds(
    source_text: str,
    area_min: float | None,
    area_max: float | None,
    background_difference_threshold: float | None,
) -> str:
    doc = tomlkit.parse(source_text)
    area_changed = False
    intensity_changed = False

    def visit(node: Any) -> None:
        nonlocal area_changed, intensity_changed
        if isinstance(node, dict):
            for key in list(node.keys()):
                value = node[key]
                key_lower = str(key).lower()
                if key_lower in AREA_KEYS and isinstance(value, list) and len(value) >= 2:
                    if area_min is not None:
                        value[0] = area_min
                    if area_max is not None:
                        value[1] = area_max
                    area_changed = True
                elif key_lower in INTENSITY_KEYS and isinstance(value, list) and len(value) >= 2:
                    if background_difference_threshold is not None:
                        value[1] = background_difference_threshold
                    intensity_changed = True
                visit(value)
        elif isinstance(node, list):
            for value in node:
                visit(value)

    visit(doc)

    if (area_min is not None or area_max is not None) and not area_changed:
        raise KeyError("Could not find area_ths or area_thresholds in TOML.")
    if background_difference_threshold is not None and not intensity_changed:
        raise KeyError("Could not find intensity_ths or intensity_thresholds in TOML.")

    output = tomlkit.dumps(doc)
    tomlkit.parse(output)
    return output


def edit_threshold_file(
    input_path: Path,
    output_path: Path,
    area_min: float | None,
    area_max: float | None,
    background_difference_threshold: float | None,
) -> None:
    text = input_path.read_text(encoding="utf-8")
    updated = edit_thresholds(
        text,
        area_min=area_min,
        area_max=area_max,
        background_difference_threshold=background_difference_threshold,
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(updated, encoding="utf-8")
