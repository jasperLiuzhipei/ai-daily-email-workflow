#!/usr/bin/env python3
"""Add exact, readable Chinese editorial labels to a text-free image-model base.

This intentionally keeps factual labels out of the image model: its job is the
scientific visual language, while Pillow renders the evidence and scope text
from a reviewable plan.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont


NAVY = "#123B66"
INK = "#152238"
MUTED = "#52657B"
CORAL = "#D9574A"


def font(size: int, *, bold: bool = False) -> ImageFont.FreeTypeFont:
    candidates = (
        [
            "/System/Library/Fonts/Hiragino Sans GB.ttc",
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
        ]
        if bold
        else [
            "/System/Library/Fonts/Hiragino Sans GB.ttc",
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        ]
    )
    for candidate in candidates:
        if Path(candidate).exists():
            return ImageFont.truetype(candidate, size=size)
    return ImageFont.load_default()


def scaled_box(raw: list[float], sx: float, sy: float) -> tuple[int, int, int, int]:
    x, y, width, height = raw
    return round(x * sx), round(y * sy), round((x + width) * sx), round((y + height) * sy)


def centered(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], text: str, face: ImageFont.FreeTypeFont, color: str) -> None:
    left, top, right, bottom = draw.textbbox((0, 0), text, font=face)
    width, height = right - left, bottom - top
    x0, y0, x1, y1 = box
    draw.text((x0 + (x1 - x0 - width) / 2, y0 + (y1 - y0 - height) / 2 - 2), text, font=face, fill=color)


def draw_label(overlay: Image.Image, item: dict[str, Any], sx: float, sy: float) -> None:
    draw = ImageDraw.Draw(overlay)
    box = scaled_box(item["box"], sx, sy)
    role = str(item.get("role") or "navy")
    palette = {
        "navy": (NAVY, "#FFFFFF", 235),
        "white": ("#FFFFFF", INK, 225),
        "coral": ("#FFF6F3", CORAL, 238),
        "outline": ("#FFFFFF", NAVY, 215),
    }
    fill, color, alpha = palette[role]
    fill_alpha = fill + f"{alpha:02X}"
    outline = None if role in {"navy", "coral"} else "#8FA9C2E6"
    radius = max(8, round((box[3] - box[1]) * 0.23))
    draw.rounded_rectangle(box, radius=radius, fill=fill_alpha, outline=outline, width=max(1, round(1.5 * sx)))
    face = font(max(14, round(float(item.get("size") or 24) * min(sx, sy))), bold=bool(item.get("bold", True)))
    centered(draw, box, str(item["text"]), face, color)


def draw_text(overlay: Image.Image, item: dict[str, Any], sx: float, sy: float) -> None:
    draw = ImageDraw.Draw(overlay)
    x, y = round(float(item["x"]) * sx), round(float(item["y"]) * sy)
    face = font(max(14, round(float(item.get("size") or 24) * min(sx, sy))), bold=bool(item.get("bold", False)))
    color = {"ink": INK, "muted": MUTED, "coral": CORAL, "navy": NAVY}.get(str(item.get("color") or "ink"), INK)
    draw.text((x, y), str(item["text"]), font=face, fill=color)


def compose(background: Path, plan: dict[str, Any], output: Path) -> None:
    # Built-in image generation can return a mostly translucent PNG even when
    # the requested canvas is visually white.  Converting that image directly
    # to RGB turns transparent pixels black in email clients.  Flatten it onto
    # the editorial white canvas before any deterministic labels are added.
    source = Image.open(background).convert("RGBA")
    base = Image.new("RGBA", source.size, (255, 255, 255, 255))
    base.alpha_composite(source)
    width, height = base.size
    design_width, design_height = plan.get("design_size", [1920, 1080])
    sx, sy = width / float(design_width), height / float(design_height)
    overlay = Image.new("RGBA", base.size, (255, 255, 255, 0))
    for item in plan.get("labels", []):
        draw_label(overlay, item, sx, sy)
    for item in plan.get("text", []):
        draw_text(overlay, item, sx, sy)
    output.parent.mkdir(parents=True, exist_ok=True)
    Image.alpha_composite(base, overlay).convert("RGB").save(output, format="PNG", optimize=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--background", required=True, type=Path)
    parser.add_argument("--plan", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    compose(args.background, json.loads(args.plan.read_text(encoding="utf-8")), args.out)
    print(f"Composed labelled infographic: {args.out}")


if __name__ == "__main__":
    main()
