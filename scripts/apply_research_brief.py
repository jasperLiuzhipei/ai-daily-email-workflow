#!/usr/bin/env python3
"""Apply parsed research bridge fields to daily email content JSON."""

from __future__ import annotations

import argparse
import json
from copy import deepcopy
from pathlib import Path

from ai_daily_common import JsonObjectError, load_json_object, write_json_object


class ApplyError(Exception):
    pass


def main() -> None:
    parser = argparse.ArgumentParser(description="Patch content.paper from parsed research brief JSON.")
    parser.add_argument("--content", required=True, type=Path, help="Daily email content JSON to patch.")
    parser.add_argument("--brief-json", required=True, type=Path, help="Parsed brief JSON from parse_ai_frontier_brief.py.")
    parser.add_argument("--out-content", type=Path, help="Patched content output. Defaults to overwrite --content.")
    parser.add_argument("--keep-existing-image", action="store_true", help="Keep existing paper.image and paper.image_alt.")
    parser.add_argument("--image-path", help="Override paper.image.")
    parser.add_argument("--image-alt", help="Override paper.image_alt.")
    args = parser.parse_args()

    try:
        content = load_json_object(args.content)
        brief = load_json_object(args.brief_json)
        paper = brief.get("paper")
        if not isinstance(paper, dict):
            raise ApplyError("brief-json.paper must be an object.")

        next_paper = deepcopy(paper)
        existing = content.get("paper") if isinstance(content.get("paper"), dict) else {}

        if args.keep_existing_image and existing.get("image"):
            next_paper["image"] = existing["image"]
            next_paper["image_alt"] = existing.get("image_alt") or next_paper.get("image_alt")
        if args.image_path:
            next_paper["image"] = args.image_path
        if args.image_alt:
            next_paper["image_alt"] = args.image_alt

        content["paper"] = next_paper
        out_content = args.out_content or args.content
        write_json_object(out_content, content)
    except (OSError, json.JSONDecodeError, JsonObjectError, ApplyError) as exc:
        raise SystemExit(f"Apply failed: {exc}") from exc

    print(f"Patched content: {out_content}")


if __name__ == "__main__":
    main()
