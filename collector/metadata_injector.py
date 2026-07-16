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
    """Burn a readable run/camera banner directly into the track image."""
    with Image.open(path) as source:
        image = source.convert("RGBA")
        draw = ImageDraw.Draw(image, "RGBA")
        lines = metadata.png_label_lines()
        size = max(18, min(30, image.width // 48))
        selected_font = font(size)
        margin = max(10, size // 2)
        line_gap = max(4, size // 5)
        measured = []
        for line in lines:
            try:
                bbox = draw.textbbox((0, 0), line, font=selected_font)
                measured.append((bbox[2] - bbox[0], bbox[3] - bbox[1]))
            except Exception:
                measured.append((int(len(line) * size * 0.6), size + 4))
        max_width = max(w for w, _ in measured)
        total_height = sum(h for _, h in measured) + line_gap * (len(lines) - 1)
        # Put the banner at the top, where it remains visible in PNG previews and PDF pages.
        box = (margin, margin, min(image.width - margin, margin * 3 + max_width), margin * 3 + total_height)
        draw.rounded_rectangle(box, radius=10, fill=(0, 0, 0, 205), outline=(255, 255, 255, 210), width=2)
        y = margin * 2
        for line, (_, h) in zip(lines, measured):
            draw.text((margin * 2, y), line, fill=(255, 255, 255, 255), font=selected_font)
            y += h + line_gap
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
