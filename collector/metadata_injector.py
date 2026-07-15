from __future__ import annotations
import argparse
from pathlib import Path
import pandas as pd
from PIL import Image, ImageDraw, ImageFont

from pipeline.run_metadata import RunMetadata


SUMMARY_NAME_HINTS = (
    "summary", "manifest", "data_dictionary"
)
TRACK_NAME_HINTS = (
    "track_map", "track", "trajectory"
)


def font(size: int):
    for candidate in (
        "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/liberation/LiberationSans-Bold.ttf",
    ):
        if Path(candidate).exists():
            try:
                return ImageFont.truetype(candidate, size)
            except Exception:
                pass
    return ImageFont.load_default()


def enrich_csv(path: Path, metadata: RunMetadata) -> None:
    try:
        frame = pd.read_csv(path)
    except Exception:
        return
    for key, value in metadata.csv_columns().items():
        frame[key] = value
    frame.to_csv(path, index=False)


def label_png(path: Path, metadata: RunMetadata) -> None:
    with Image.open(path) as source:
        image = source.convert("RGBA")
        draw = ImageDraw.Draw(image, "RGBA")
        label = metadata.png_label()
        size = max(16, min(28, image.width // 55))
        selected_font = font(size)
        margin = max(8, size // 2)
        try:
            bbox = draw.textbbox((0, 0), label, font=selected_font)
            width = bbox[2] - bbox[0]
            height = bbox[3] - bbox[1]
        except Exception:
            width = int(len(label) * size * 0.6)
            height = size + 4
        box = (
            margin,
            image.height - height - margin * 3,
            margin + width + margin * 2,
            image.height - margin,
        )
        draw.rounded_rectangle(box, radius=8, fill=(0, 0, 0, 190))
        draw.text(
            (margin * 2, image.height - height - margin * 2),
            label,
            fill=(255, 255, 255, 255),
            font=selected_font,
        )
        image.convert("RGB").save(path, "PNG", optimize=True, compress_level=9)


def enrich_tree(output_root: Path, metadata: RunMetadata) -> dict:
    csv_count = 0
    png_count = 0
    for path in output_root.rglob("*"):
        if not path.is_file():
            continue
        lower = path.name.lower()
        if path.suffix.lower() == ".csv" and any(x in lower for x in SUMMARY_NAME_HINTS):
            enrich_csv(path, metadata)
            csv_count += 1
        elif path.suffix.lower() == ".png" and any(x in lower for x in TRACK_NAME_HINTS):
            label_png(path, metadata)
            png_count += 1
    return {"csv_enriched": csv_count, "png_labeled": png_count}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-root", required=True)
    parser.add_argument("--run-metadata-json", required=True)
    args = parser.parse_args()

    metadata = RunMetadata.from_json(args.run_metadata_json)
    counts = enrich_tree(Path(args.output_root), metadata)
    print(counts)


if __name__ == "__main__":
    main()
