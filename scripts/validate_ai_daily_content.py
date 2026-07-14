#!/usr/bin/env python3
"""Validate AI daily email content JSON before rendering."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from ai_daily_common import JsonObjectError, NEWS_FORBIDDEN_IMAGE_KEYS, load_json_object


class ValidationError(Exception):
    pass


def require_string(obj: dict[str, Any], key: str, path: str) -> None:
    value = obj.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"{path}.{key} must be a non-empty string.")


def require_any_string(obj: dict[str, Any], keys: list[str], path: str) -> None:
    for key in keys:
        value = obj.get(key)
        if isinstance(value, str) and value.strip():
            return
    joined = " or ".join(f"{path}.{key}" for key in keys)
    raise ValidationError(f"{joined} must be a non-empty string.")


def require_list(obj: dict[str, Any], key: str, path: str, min_len: int = 1) -> list[Any]:
    value = obj.get(key)
    if not isinstance(value, list) or len(value) < min_len:
        raise ValidationError(f"{path}.{key} must be a list with at least {min_len} item(s).")
    return value


def require_object(obj: dict[str, Any], key: str, path: str) -> dict[str, Any]:
    value = obj.get(key)
    if not isinstance(value, dict):
        raise ValidationError(f"{path}.{key} must be an object.")
    return value


def validate_link(link: Any, path: str) -> None:
    if not isinstance(link, dict):
        raise ValidationError(f"{path} must be an object.")
    require_string(link, "label", path)
    require_string(link, "url", path)
    if not link["url"].startswith(("http://", "https://")):
        raise ValidationError(f"{path}.url must start with http:// or https://.")


def validate_paper(paper: dict[str, Any]) -> None:
    required = [
        "section_title",
        "title",
        "image",
        "image_alt",
        "insight",
        "boundary",
    ]
    for key in required:
        require_string(paper, key, "paper")

    require_any_string(paper, ["conclusion", "one_liner"], "paper")
    require_any_string(paper, ["problem", "question"], "paper")
    require_any_string(paper, ["mechanism", "core_idea"], "paper")

    evidence = paper.get("evidence")
    if isinstance(evidence, str):
        if not evidence.strip():
            raise ValidationError("paper.evidence must be a non-empty string.")
    elif isinstance(evidence, list):
        if not evidence:
            raise ValidationError("paper.evidence must be a non-empty string or non-empty legacy list.")
        for idx, item in enumerate(evidence):
            if not isinstance(item, dict):
                raise ValidationError(f"paper.evidence[{idx}] must be an object.")
            require_string(item, "label", f"paper.evidence[{idx}]")
            require_string(item, "text", f"paper.evidence[{idx}]")
    else:
        raise ValidationError("paper.evidence must be a non-empty string.")

    links = require_list(paper, "links", "paper", min_len=1)
    for idx, link in enumerate(links):
        validate_link(link, f"paper.links[{idx}]")


def validate_research(data: dict[str, Any]) -> None:
    items = data.get("research_items")
    if isinstance(items, list):
        if not items:
            raise ValidationError("root.research_items must contain at least one item when present.")
        for idx, item in enumerate(items):
            if not isinstance(item, dict):
                raise ValidationError(f"research_items[{idx}] must be an object.")
            validate_paper(item)
        return

    paper = require_object(data, "paper", "root")
    validate_paper(paper)


def validate_news_item(item: Any, idx: int) -> None:
    path = f"news[{idx}]"
    if not isinstance(item, dict):
        raise ValidationError(f"{path} must be an object.")

    for key in NEWS_FORBIDDEN_IMAGE_KEYS:
        if key in item:
            raise ValidationError(f"{path}.{key} is not allowed. News items must stay text-only.")

    for key in ["title", "one_liner", "why", "observation", "url"]:
        require_string(item, key, path)

    tags = require_list(item, "tags", path, min_len=1)
    if not all(isinstance(tag, str) and tag.strip() for tag in tags):
        raise ValidationError(f"{path}.tags must contain only non-empty strings.")

    if not item["url"].startswith(("http://", "https://")):
        raise ValidationError(f"{path}.url must start with http:// or https://.")

    if "boundary" in item and (not isinstance(item["boundary"], str) or not item["boundary"].strip()):
        raise ValidationError(f"{path}.boundary must be a non-empty string when present.")

    if "observation_label" in item:
        label = item["observation_label"]
        if label not in {"行业视角", "工作启发"}:
            raise ValidationError(
                f"{path}.observation_label must be either 行业视角 or 工作启发 when present."
            )


def validate_content(data: dict[str, Any]) -> None:
    for key in ["date", "display_date", "title", "subtitle", "eyebrow", "editor_note", "summary"]:
        require_string(data, key, "root")

    validate_research(data)

    news_section = require_object(data, "news_section", "root")
    require_string(news_section, "title", "news_section")
    require_string(news_section, "subtitle", "news_section")

    news = require_list(data, "news", "root", min_len=1)
    for idx, item in enumerate(news):
        validate_news_item(item, idx)

    footer = require_object(data, "footer", "root")
    for key in ["title", "body", "name", "email"]:
        require_string(footer, key, "footer")


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate AI daily email content JSON.")
    parser.add_argument("content", type=Path, help="Path to content JSON.")
    args = parser.parse_args()

    try:
        validate_content(load_json_object(args.content))
    except JsonObjectError as exc:
        raise SystemExit("Invalid content: Root must be a JSON object.") from exc
    except (json.JSONDecodeError, OSError, ValidationError) as exc:
        raise SystemExit(f"Invalid content: {exc}") from exc

    print(f"Valid content: {args.content}")


if __name__ == "__main__":
    main()
