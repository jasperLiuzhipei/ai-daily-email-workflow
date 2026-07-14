#!/usr/bin/env python3
"""Find the latest built-in image_gen output after a marker file."""

from __future__ import annotations

import argparse
from pathlib import Path

from ai_daily_common import default_imagegen_output_root


IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}


class FindImagegenError(Exception):
    pass


def image_candidates(root: Path, min_mtime: float) -> list[Path]:
    if not root.exists():
        raise FindImagegenError(f"Imagegen output root does not exist: {root}")

    candidates: list[Path] = []
    for path in root.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in IMAGE_SUFFIXES:
            continue
        try:
            if path.stat().st_mtime >= min_mtime:
                candidates.append(path)
        except OSError:
            continue
    return sorted(candidates, key=lambda item: item.stat().st_mtime, reverse=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Find latest built-in image_gen output after a marker file.")
    parser.add_argument(
        "--after-file",
        required=True,
        type=Path,
        help="Marker file created immediately before calling image_gen.",
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=default_imagegen_output_root(),
        help="Built-in image_gen output root. Defaults to $CODEX_HOME/generated_images.",
    )
    parser.add_argument("--write-path", type=Path, help="Optional text file to write the selected image path.")
    parser.add_argument("--list-candidates", action="store_true", help="Print all matching candidates to stderr.")
    args = parser.parse_args()

    try:
        if not args.after_file.exists():
            raise FindImagegenError(f"Missing marker file: {args.after_file}")
        candidates = image_candidates(args.root, args.after_file.stat().st_mtime)
        if not candidates:
            raise FindImagegenError(f"No image_gen output found under {args.root} after {args.after_file}.")

        selected = candidates[0]
        if args.write_path:
            args.write_path.parent.mkdir(parents=True, exist_ok=True)
            args.write_path.write_text(str(selected) + "\n", encoding="utf-8")

        print(selected)
        if args.list_candidates:
            import sys

            for path in candidates:
                print(path, file=sys.stderr)
    except (OSError, FindImagegenError) as exc:
        raise SystemExit(f"Find imagegen output failed: {exc}") from exc


if __name__ == "__main__":
    main()
