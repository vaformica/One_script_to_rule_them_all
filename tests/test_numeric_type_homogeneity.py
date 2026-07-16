from pathlib import Path
import ast
import math


def load_helpers(gui_path):
    tree = ast.parse(Path(gui_path).read_text(encoding="utf-8"))
    wanted = {
        "_is_number",
        "_coerce_homogeneous_numeric_pair",
        "_update_threshold_pairs",
        "_validate_threshold_array",
    }
    body = [
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name in wanted
    ]
    namespace = {"math": math}
    exec(
        compile(ast.Module(body=body, type_ignores=[]), str(gui_path), "exec"),
        namespace,
    )
    return namespace


def run_tests(gui_path):
    h = load_helpers(gui_path)

    intensity = [0, 255]
    h["_update_threshold_pairs"](intensity, minimum=25.0)
    assert intensity == [25, 255]
    assert type(intensity[0]) is type(intensity[1]) is int
    h["_validate_threshold_array"](intensity, "intensity_ths")

    area = [100.0, float("inf")]
    h["_update_threshold_pairs"](area, minimum=185.0)
    assert area[0] == 185.0
    assert math.isinf(area[1])
    assert type(area[0]) is type(area[1]) is float
    h["_validate_threshold_array"](area, "area_ths")

    nested = [[0, 255], [5, 250]]
    h["_update_threshold_pairs"](nested, minimum=25.0)
    assert nested == [[25, 255], [25, 250]]
    for pair in nested:
        assert type(pair[0]) is type(pair[1]) is int
    h["_validate_threshold_array"](nested, "intensity_ths")

    try:
        h["_validate_threshold_array"]([25.0, 255], "intensity_ths")
    except ValueError as exc:
        assert "mixes numeric TOML types" in str(exc)
    else:
        raise AssertionError("Mixed numeric pair was not rejected")
