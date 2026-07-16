#!/usr/bin/env python3
import argparse
from pathlib import Path
import sys
import tomllib


KEYS = {
    "area_ths",
    "area_thresholds",
    "intensity_ths",
    "intensity_thresholds",
}


def is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def validate_array(value, path):
    if not isinstance(value, list) or not value:
        raise ValueError(f"{path} must be a non-empty array")
    numeric = [is_number(x) for x in value]
    nested = [isinstance(x, list) for x in value]
    if any(numeric) and any(nested):
        raise ValueError(
            f"{path} mixes numbers and nested arrays "
            "(the cause of 'Not a homogeneous array')"
        )
    if all(numeric):
        if len(value) != 2:
            raise ValueError(f"{path} must be [minimum, maximum]")
        numeric_types = {type(item) for item in value}
        if len(numeric_types) != 1:
            rendered_types = ", ".join(
                sorted(item_type.__name__ for item_type in numeric_types)
            )
            raise ValueError(
                f"{path} mixes numeric TOML types ({rendered_types}); "
                "IDtracker.ai 6.0.10 requires homogeneous arrays"
            )
        if value[0] > value[1]:
            raise ValueError(
                f"{path} minimum {value[0]} exceeds maximum {value[1]}"
            )
        return
    if all(nested):
        for i, item in enumerate(value):
            validate_array(item, f"{path}[{i}]")
        return
    raise ValueError(f"{path} contains unsupported values")


def walk(obj, path="root"):
    if isinstance(obj, dict):
        for key, value in obj.items():
            child = f"{path}.{key}"
            if str(key).lower() in KEYS:
                validate_array(value, child)
            walk(value, child)
    elif isinstance(obj, list):
        for i, value in enumerate(obj):
            walk(value, f"{path}[{i}]")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("toml_path")
    args = parser.parse_args()

    path = Path(args.toml_path)
    try:
        with path.open("rb") as handle:
            doc = tomllib.load(handle)
        walk(doc)
    except Exception as exc:
        print(f"INVALID TOML: {path}", file=sys.stderr)
        print(str(exc), file=sys.stderr)
        return 2

    print(f"VALID TOML: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
