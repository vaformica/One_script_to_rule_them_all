from typing import Any
import tomlkit


def edit_thresholds(text, area_min, area_max, background):
    doc = tomlkit.parse(text)
    found_area = found_intensity = False

    def visit(node: Any):
        nonlocal found_area, found_intensity
        if isinstance(node, dict):
            for key, value in node.items():
                lower = str(key).lower()
                if lower in {"area_ths", "area_thresholds"} and isinstance(value, list) and len(value) >= 2:
                    value[0], value[1] = area_min, area_max
                    found_area = True
                elif lower in {"intensity_ths", "intensity_thresholds"} and isinstance(value, list) and len(value) >= 2:
                    value[1] = background
                    found_intensity = True
                visit(value)
        elif isinstance(node, list):
            for value in node:
                visit(value)

    visit(doc)
    if not found_area:
        raise KeyError("No area threshold pair found")
    if not found_intensity:
        raise KeyError("No intensity threshold pair found")
    return tomlkit.dumps(doc)
