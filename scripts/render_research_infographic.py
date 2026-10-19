#!/usr/bin/env python3
"""Render a Chinese-labelled AI Daily research infographic deterministically.

Image models are useful for decorative artwork but cannot reliably render the
title, evidence and scope constraints of an editorial research diagram.  This
renderer keeps those information-bearing elements as exact, reviewable text.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont


W, H = 1920, 1080
NAVY = "#163A63"
BLUE = "#2F6FA9"
INK = "#162234"
MUTED = "#5D6A7A"
LINE = "#C8D3E0"
PALE_BLUE = "#EAF2FB"
PALE_CORAL = "#FFF2EE"
CORAL = "#E26D5A"
WHITE = "#FFFFFF"
BG = "#F8FAFC"


def font(size: int, *, bold: bool = False) -> ImageFont.FreeTypeFont:
    names = (
        [
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
            "/System/Library/Fonts/Hiragino Sans GB.ttc",
        ]
        if bold
        else [
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
            "/System/Library/Fonts/Hiragino Sans GB.ttc",
        ]
    )
    for name in names:
        if Path(name).exists():
            return ImageFont.truetype(name, size=size)
    return ImageFont.load_default()


def rounded(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], radius: int, *, fill: str, outline: str | None = None, width: int = 1) -> None:
    draw.rounded_rectangle(box, radius=radius, fill=fill, outline=outline, width=width)


def text_size(draw: ImageDraw.ImageDraw, text: str, face: ImageFont.FreeTypeFont) -> tuple[int, int]:
    left, top, right, bottom = draw.textbbox((0, 0), text, font=face)
    return right - left, bottom - top


def centered(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], text: str, face: ImageFont.FreeTypeFont, color: str) -> None:
    width, height = text_size(draw, text, face)
    x0, y0, x1, y1 = box
    draw.text((x0 + (x1 - x0 - width) / 2, y0 + (y1 - y0 - height) / 2 - 2), text, font=face, fill=color)


def wrapped(draw: ImageDraw.ImageDraw, xy: tuple[int, int], text: str, face: ImageFont.FreeTypeFont, color: str, *, max_width: int, line_gap: int = 6) -> int:
    lines: list[str] = []
    for raw_line in text.split("\n"):
        line = ""
        for char in raw_line:
            candidate = line + char
            if line and text_size(draw, candidate, face)[0] > max_width:
                lines.append(line)
                line = char
            else:
                line = candidate
        if line:
            lines.append(line)
    x, y = xy
    _, line_height = text_size(draw, "测", face)
    for line in lines:
        draw.text((x, y), line, font=face, fill=color)
        y += line_height + line_gap
    return y


def line(draw: ImageDraw.ImageDraw, points: list[tuple[int, int]], *, color: str = NAVY, width: int = 3) -> None:
    draw.line(points, fill=color, width=width, joint="curve")


def arrow(draw: ImageDraw.ImageDraw, start: tuple[int, int], end: tuple[int, int], *, color: str = NAVY, width: int = 3) -> None:
    line(draw, [start, end], color=color, width=width)
    x, y = end
    draw.polygon([(x, y), (x - 13, y - 7), (x - 13, y + 7)], fill=color)


def scope_card(draw: ImageDraw.ImageDraw, plan: dict[str, Any]) -> None:
    x, y, w, h = 82, 215, 300, 136
    rounded(draw, (x, y, x + w, y + h), 14, fill=WHITE, outline=LINE, width=2)
    rounded(draw, (x + 18, y - 16, x + 114, y + 16), 9, fill=NAVY)
    centered(draw, (x + 18, y - 16, x + 114, y + 16), plan["scope"]["label"], font(17, bold=True), WHITE)
    wrapped(draw, (x + 22, y + 36), plan["scope"]["text"], font(22), INK, max_width=210, line_gap=8)
    draw.ellipse((x + 241, y + 43, x + 279, y + 81), outline=BLUE, width=3)
    draw.rectangle((x + 247, y + 51, x + 273, y + 73), outline=BLUE, width=2)


def hub(draw: ImageDraw.ImageDraw, plan: dict[str, Any]) -> None:
    x, y, w, h = 498, 220, 430, 118
    rounded(draw, (x, y, x + w, y + h), 14, fill=NAVY)
    draw.ellipse((x + 25, y + 27, x + 78, y + 80), outline=WHITE, width=3)
    draw.ellipse((x + 39, y + 41, x + 64, y + 66), outline=WHITE, width=2)
    line(draw, [(x + 65, y + 68), (x + 83, y + 86)], color=WHITE, width=3)
    draw.text((x + 104, y + 23), plan["hub"]["title"], font=font(31, bold=True), fill=WHITE)
    draw.text((x + 104, y + 67), plan["hub"]["subtitle"], font=font(20), fill="#DCEBFA")


def method_node(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], item: dict[str, str]) -> None:
    x0, y0, x1, y1 = box
    rounded(draw, box, 13, fill=WHITE, outline=LINE, width=2)
    draw.ellipse((x0 + 18, y0 + 25, x0 + 58, y0 + 65), fill=PALE_BLUE, outline=BLUE, width=2)
    draw.ellipse((x0 + 31, y0 + 38, x0 + 44, y0 + 51), fill=BLUE)
    draw.text((x0 + 74, y0 + 22), item["title"], font=font(23, bold=True), fill=INK)
    wrapped(draw, (x0 + 74, y0 + 60), item["detail"], font(17), MUTED, max_width=x1 - x0 - 94, line_gap=4)


def evidence_panel(draw: ImageDraw.ImageDraw, plan: dict[str, Any]) -> None:
    x, y, w, h = 1262, 205, 566, 478
    rounded(draw, (x, y, x + w, y + h), 16, fill=WHITE, outline=NAVY, width=2)
    draw.rectangle((x, y, x + w, y + 58), fill=NAVY)
    draw.text((x + 23, y + 15), plan["evidence"]["title"], font=font(23, bold=True), fill=WHITE)
    for idx, item in enumerate(plan["evidence"]["cards"]):
        top = y + 76 + idx * 183
        fill = PALE_BLUE if idx == 0 else "#F5F7FA"
        accent = BLUE if idx == 0 else CORAL
        rounded(draw, (x + 18, top, x + w - 18, top + 162), 12, fill=fill, outline=LINE, width=1)
        visual = str(item.get("visual") or "").strip().lower()
        if visual == "protein":
            # A compact protein/binder motif: abstract residues and a highlighted binding site.
            nodes = [(39, 47), (55, 29), (77, 32), (90, 53), (72, 72), (48, 67)]
            for start, end in ((0, 1), (1, 2), (2, 3), (3, 4), (4, 5), (5, 0), (1, 4)):
                line(
                    draw,
                    [(x + nodes[start][0], top + nodes[start][1]), (x + nodes[end][0], top + nodes[end][1])],
                    color=accent,
                    width=2,
                )
            for node_x, node_y in nodes:
                draw.ellipse((x + node_x - 5, top + node_y - 5, x + node_x + 5, top + node_y + 5), fill=accent)
            draw.ellipse((x + 63, top + 43, x + 75, top + 55), fill=CORAL)
        elif visual == "spectrum":
            # Sparse spectral peaks retain the analytical-chemistry cue without fabricating an axis.
            baseline_y = top + 72
            line(draw, [(x + 30, baseline_y), (x + 92, baseline_y)], color=accent, width=2)
            for peak_x, peak_h in ((40, 18), (55, 42), (70, 26), (84, 55)):
                line(draw, [(x + peak_x, baseline_y), (x + peak_x, baseline_y - peak_h)], color=accent, width=3)
            draw.ellipse((x + 46, top + 22, x + 78, top + 54), outline=accent, width=2)
        elif idx == 0:
            # Sparse lattice: a visual reference to the HAWK lattice structure.
            nodes = [(46, 37), (68, 29), (75, 54), (49, 65), (35, 53)]
            for start, end in ((0, 1), (1, 2), (2, 3), (3, 4), (4, 0), (0, 2), (1, 3)):
                line(
                    draw,
                    [(x + nodes[start][0], top + nodes[start][1]), (x + nodes[end][0], top + nodes[end][1])],
                    color=accent,
                    width=2,
                )
            for node_x, node_y in nodes:
                draw.ellipse((x + node_x - 4, top + node_y - 4, x + node_x + 4, top + node_y + 4), fill=accent)
        else:
            # Small block layers: a visual reference to a reduced-round AES transformation.
            for row in range(3):
                for column in range(3):
                    left = x + 35 + column * 14
                    upper = top + 29 + row * 14
                    fill_color = accent if (row, column) == (1, 1) else WHITE
                    draw.rounded_rectangle((left, upper, left + 10, upper + 10), radius=2, fill=fill_color, outline=accent, width=1)
        draw.text((x + 98, top + 18), item["title"], font=font(24, bold=True), fill=INK)
        draw.text((x + 98, top + 57), item["mechanism"], font=font(18), fill=MUTED)
        rounded(draw, (x + 98, top + 96, x + w - 38, top + 139), 8, fill=WHITE, outline=accent, width=1)
        centered(draw, (x + 110, top + 96, x + w - 46, top + 139), item["result"], font(18, bold=True), accent)


def boundary_panel(draw: ImageDraw.ImageDraw, plan: dict[str, Any]) -> None:
    x, y, w, h = 1262, 710, 566, 192
    rounded(draw, (x, y, x + w, y + h), 16, fill=PALE_CORAL, outline="#EDC6BC", width=2)
    draw.polygon([(x + 28, y + 52), (x + 50, y + 14), (x + 72, y + 52)], outline=CORAL)
    draw.text((x + 43, y + 22), "!", font=font(22, bold=True), fill=CORAL)
    draw.text((x + 98, y + 22), plan["boundary"]["title"], font=font(24, bold=True), fill=CORAL)
    y_cursor = y + 67
    for item in plan["boundary"]["items"]:
        draw.ellipse((x + 103, y_cursor + 8, x + 111, y_cursor + 16), fill=CORAL)
        draw.text((x + 124, y_cursor), item, font=font(18), fill=INK)
        y_cursor += 34


def render(plan: dict[str, Any], output: Path) -> None:
    image = Image.new("RGB", (W, H), BG)
    draw = ImageDraw.Draw(image)
    rounded(draw, (34, 32, W - 34, H - 32), 20, fill=BG, outline=LINE, width=2)

    centered(draw, (100, 62, W - 100, 142), plan["title"], font(60, bold=True), INK)
    centered(draw, (100, 143, W - 100, 191), plan["subtitle"], font(28), MUTED)
    scope_card(draw, plan)
    hub(draw, plan)

    arrow(draw, (383, 282), (488, 282), color="#8391A1", width=2)
    line(draw, [(713, 338), (713, 382), (278, 382)], width=2)
    line(draw, [(713, 382), (1078, 382)], width=2)
    nodes = [(118, 420, 432, 525), (490, 420, 804, 525), (862, 420, 1176, 525)]
    for box, item in zip(nodes, plan["methods"]):
        line(draw, [((box[0] + box[2]) // 2, 382), ((box[0] + box[2]) // 2, 420)], width=2)
        method_node(draw, box, item)

    x0, y0, x1, y1 = 118, 575, 1176, 770
    rounded(draw, (x0, y0, x1, y1), 16, fill=WHITE, outline=LINE, width=2)
    draw.rectangle((x0, y0, x0 + 92, y1), fill=NAVY)
    centered(draw, (x0 + 10, y0 + 15, x0 + 82, y1 - 15), plan["mechanism"]["label"], font(21, bold=True), WHITE)
    draw.text((x0 + 126, y0 + 25), plan["mechanism"]["title"], font=font(28, bold=True), fill=INK)
    draw.text((x0 + 126, y0 + 69), plan["mechanism"]["text"], font=font(20), fill=MUTED)
    arrow(draw, (x0 + 142, y0 + 134), (x1 - 118, y0 + 134), color=CORAL, width=5)
    draw.ellipse((x0 + 113, y0 + 110, x0 + 149, y0 + 146), fill=PALE_BLUE, outline=BLUE, width=2)
    draw.ellipse((x1 - 151, y0 + 108, x1 - 107, y0 + 152), fill="#FFE3DC", outline=CORAL, width=2)

    rounded(draw, (118, 800, 1176, 877), 12, fill="#F1F5F9", outline=LINE, width=1)
    draw.text((154, 824), plan["validation"], font=font(20, bold=True), fill=NAVY)
    draw.ellipse((123, 822, 143, 842), outline=NAVY, width=2)
    draw.line((128, 832, 135, 839), fill=NAVY, width=2)
    draw.line((135, 839, 141, 826), fill=NAVY, width=2)

    evidence_panel(draw, plan)
    boundary_panel(draw, plan)

    rounded(draw, (190, 935, 1730, 1016), 14, fill=NAVY)
    draw.ellipse((222, 954, 268, 1000), outline="#F2C94C", width=3)
    draw.line((245, 943, 245, 952), fill="#F2C94C", width=3)
    draw.line((245, 1002, 245, 1011), fill="#F2C94C", width=3)
    wrapped(draw, (292, 953), plan["conclusion"], font(22, bold=True), WHITE, max_width=1390, line_gap=2)

    output.parent.mkdir(parents=True, exist_ok=True)
    image.save(output, format="PNG", optimize=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", required=True, type=Path, help="JSON text ledger and layout plan.")
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    plan = json.loads(args.plan.read_text(encoding="utf-8"))
    render(plan, args.out)
    print(f"Rendered deterministic infographic: {args.out}")


if __name__ == "__main__":
    main()
