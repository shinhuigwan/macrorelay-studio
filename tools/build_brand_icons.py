from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw


def fit_mark(source: Image.Image, size: int) -> Image.Image:
    rgba = source.convert("RGBA")
    alpha_box = rgba.getchannel("A").getbbox()
    if alpha_box:
        rgba = rgba.crop(alpha_box)
    target = int(size * 0.76)
    rgba.thumbnail((target, target), Image.Resampling.LANCZOS)
    return rgba


def make_icon(source: Image.Image, size: int = 1024, runner: bool = False) -> Image.Image:
    icon = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(icon)
    inset = int(size * 0.045)
    radius = int(size * 0.22)
    draw.rounded_rectangle(
        (inset, inset, size - inset, size - inset),
        radius=radius,
        fill="#101218",
        outline="#2A3040",
        width=max(2, int(size * 0.012)),
    )
    mark = fit_mark(source, size)
    x = (size - mark.width) // 2
    y = (size - mark.height) // 2
    icon.alpha_composite(mark, (x, y))
    if runner:
        dot_radius = int(size * 0.105)
        cx = int(size * 0.79)
        cy = int(size * 0.79)
        ring = int(size * 0.025)
        draw = ImageDraw.Draw(icon)
        draw.ellipse(
            (cx - dot_radius - ring, cy - dot_radius - ring, cx + dot_radius + ring, cy + dot_radius + ring),
            fill="#101218",
        )
        draw.ellipse(
            (cx - dot_radius, cy - dot_radius, cx + dot_radius, cy + dot_radius),
            fill="#35C89A",
        )
    return icon


def main() -> int:
    if len(sys.argv) != 3:
        raise SystemExit("usage: build_brand_icons.py SOURCE_PNG OUTPUT_DIR")
    source_path = Path(sys.argv[1])
    output_dir = Path(sys.argv[2])
    output_dir.mkdir(parents=True, exist_ok=True)
    source = Image.open(source_path)
    studio = make_icon(source)
    runner = make_icon(source, runner=True)
    sizes = [(16, 16), (20, 20), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
    studio.save(output_dir / "macrorelay-studio.png")
    runner.save(output_dir / "macrorelay-runner.png")
    studio.save(output_dir / "macrorelay-studio.ico", format="ICO", sizes=sizes)
    runner.save(output_dir / "macrorelay-runner.ico", format="ICO", sizes=sizes)
    runner.resize((32, 32), Image.Resampling.LANCZOS).save(output_dir / "macrorelay-tray.png")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
