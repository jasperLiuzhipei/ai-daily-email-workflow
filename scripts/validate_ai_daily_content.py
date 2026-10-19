#!/usr/bin/env python3
"""Validate AI daily email content JSON before rendering."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

from ai_daily_common import JsonObjectError, NEWS_FORBIDDEN_IMAGE_KEYS, load_json_object
from daily_config import DailyConfigError, load_editorial_policy
from event_editorial import candidate_urls, event_family_key
from daily_prose import daily_editorial_issues, news_prose_tier
from newspaper_sections import planned_news_sources, section_indices


class ValidationError(Exception):
    pass


MAX_NEWS_TITLE_CHARS = 64
RAW_TITLE_PATTERNS = (
    re.compile(r"^\s*(?:github|gitlab)\s*[-:：]", re.IGNORECASE),
    re.compile(r"\b(?:skip to content|navigation menu|sign in|read full article|read more)\b", re.IGNORECASE),
    re.compile(r"\b100\s*%", re.IGNORECASE),
    re.compile(r"[<≤]\s*\d+\s*(?:ms|毫秒)\b", re.IGNORECASE),
)
EMOJI_RE = re.compile(r"[\U0001F300-\U0001FAFF]")
PLACEHOLDER_NEWS_TITLE_RE = re.compile(
    r"(?:^|[：:])\s*(?:AI\s*行业动态待核验|模型更新待核验|智能体安全动态待核验|开源智能体工具动态)\s*$",
    re.IGNORECASE,
)
VISUAL_RELATION_RE = re.compile(r"关系[：:]\s*[^\n。；]{0,240}[|→]")


def has_visual_relation_syntax(value: str) -> bool:
    """Catch complete or truncated diagram edges in reader-facing prose."""
    # A single full-width pipe can be the tail of a relation whose prefix was
    # cut off by a model or an editor. It has no prose role in these fields.
    return "｜" in value or bool(VISUAL_RELATION_RE.search(value))


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


def validate_paper(paper: dict[str, Any], *, text_only: bool = False) -> None:
    required = [
        "section_title",
        "title",
        "insight",
        "boundary",
    ]
    if not text_only:
        required.extend(["image", "image_alt"])
    for key in required:
        require_string(paper, key, "paper")

    require_any_string(paper, ["conclusion", "one_liner"], "paper")
    require_any_string(paper, ["problem", "question"], "paper")
    require_any_string(paper, ["mechanism", "core_idea"], "paper")

    for key in ("conclusion", "one_liner", "problem", "question", "mechanism", "core_idea", "evidence", "insight", "boundary"):
        value = paper.get(key)
        if isinstance(value, str) and has_visual_relation_syntax(value):
            raise ValidationError(f"paper.{key} contains visual handoff relation syntax; rewrite it as prose.")

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
            if has_visual_relation_syntax(item["label"] + "：" + item["text"]):
                raise ValidationError(f"paper.evidence[{idx}] contains visual handoff relation syntax; rewrite it as prose.")
    else:
        raise ValidationError("paper.evidence must be a non-empty string.")

    links = require_list(paper, "links", "paper", min_len=1)
    for idx, link in enumerate(links):
        validate_link(link, f"paper.links[{idx}]")

def validate_research(data: dict[str, Any]) -> None:
    text_only = data.get("presentation", {}).get("mode") == "newspaper_text"
    items = data.get("research_items")
    if isinstance(items, list) and items:
        for idx, item in enumerate(items):
            if not isinstance(item, dict):
                raise ValidationError(f"research_items[{idx}] must be an object.")
            validate_paper(item, text_only=text_only)
        return
    if items is not None and not isinstance(items, list):
        raise ValidationError("root.research_items must be a list when present.")

    paper = data.get("paper")
    if paper is None:
        return
    if not isinstance(paper, dict):
        raise ValidationError("root.paper must be an object when present.")
    validate_paper(paper, text_only=text_only)


def validate_news_item(item: Any, idx: int, allowed_observation_labels: set[str]) -> None:
    path = f"news[{idx}]"
    if not isinstance(item, dict):
        raise ValidationError(f"{path} must be an object.")
    if 'display_tier' in item and item['display_tier'] not in {'brief', 'deep'}:
        raise ValidationError(f"{path}.display_tier must be brief or deep.")

    for key in NEWS_FORBIDDEN_IMAGE_KEYS:
        if key in item:
            raise ValidationError(f"{path}.{key} is not allowed. News items must stay text-only.")

    required = ["title", "one_liner", "why", "url"]
    if news_prose_tier(item) != "brief":
        required.append("observation")
    elif "observation" in item and not isinstance(item["observation"], str):
        raise ValidationError(f"{path}.observation must be a string when present.")
    for key in required:
        require_string(item, key, path)

    title = " ".join(str(item["title"]).split()).strip()
    chinese_chars = sum("\u4e00" <= char <= "\u9fff" for char in title)
    latin_words = len(re.findall(r"[A-Za-z][A-Za-z0-9._-]*", title))
    if len(title) > MAX_NEWS_TITLE_CHARS or chinese_chars < 4 or latin_words > chinese_chars:
        raise ValidationError(
            f"{path}.title must be a concise Chinese editorial title (4+ Chinese characters, at most {MAX_NEWS_TITLE_CHARS} characters)."
        )
    if EMOJI_RE.search(title) or any(pattern.search(title) for pattern in RAW_TITLE_PATTERNS):
        raise ValidationError(f"{path}.title appears to contain raw webpage or repository copy and cannot be published.")
    if PLACEHOLDER_NEWS_TITLE_RE.search(title):
        raise ValidationError(
            f"{path}.title is a generation fallback placeholder, not a publishable news title."
        )

    tags = require_list(item, "tags", path, min_len=1)
    if not all(isinstance(tag, str) and tag.strip() for tag in tags):
        raise ValidationError(f"{path}.tags must contain only non-empty strings.")

    if not item["url"].startswith(("http://", "https://")):
        raise ValidationError(f"{path}.url must start with http:// or https://.")

    if "boundary" in item and (not isinstance(item["boundary"], str) or not item["boundary"].strip()):
        raise ValidationError(f"{path}.boundary must be a non-empty string when present.")

    if "observation_label" in item:
        label = item["observation_label"]
        if label not in allowed_observation_labels:
            raise ValidationError(
                f"{path}.observation_label must be one of {sorted(allowed_observation_labels)} when present."
            )


def validate_unique_events(data: dict[str, Any]) -> None:
    """Catch repeated selected events even when cards use different URLs."""

    seen: dict[str, str] = {}
    items = [(f"news[{index}]", item) for index, item in enumerate(data.get("news", []))]
    papers = data.get("research_items")
    if not isinstance(papers, list) or not papers:
        papers = [data["paper"]] if isinstance(data.get("paper"), dict) else []
    items.extend((f"research[{index}]", item) for index, item in enumerate(papers))
    for label, item in items:
        keys = {"url:" + url for url in candidate_urls(item)}
        keys.update("url:" + url for link in item.get("links", []) if isinstance(link, dict)
                    for url in candidate_urls(link))
        event_id = str(item.get("event_id") or "").strip()
        if event_id:
            keys.add("event:" + event_id)
        family = event_family_key(item)
        if family:
            keys.add("family:" + family)
        for key in keys:
            if key in seen:
                raise ValidationError(f"Repeated event in {seen[key]} and {label}: {key}.")
            seen[key] = label


def validate_content(data: dict[str, Any], *, editorial_policy: dict[str, Any] | None = None) -> None:
    for key in ["date", "display_date", "title", "subtitle", "eyebrow", "editor_note", "summary"]:
        require_string(data, key, "root")

    validate_research(data)

    news_section = require_object(data, "news_section", "root")
    require_string(news_section, "title", "news_section")
    require_string(news_section, "subtitle", "news_section")

    news = require_list(data, "news", "root", min_len=0 if data.get('paper') or data.get('research_items') else 1)
    if any(not isinstance(item, dict) for item in news):
        raise ValidationError('news items must be objects.')
    validation_data = data
    try:
        if data.get('presentation', {}).get('mode') in {'newspaper_text', 'newspaper_illustrated'}:
            # Validate the displayed feature roles even for older content that
            # lacked role metadata; do not alter historical content signatures.
            news = planned_news_sources(news)
            validation_data = {**data, 'news': news}
        elif any(item.get('newspaper_role') for item in news):
            section_indices(news)
    except ValueError as exc:
        raise ValidationError(str(exc)) from exc
    observation = (editorial_policy or {}).get("observation", {})
    allowed_labels = {
        str(label)
        for label in observation.get("allowed_labels", ["行业视角", "工作启发"])
        if str(label).strip()
    }
    if not allowed_labels:
        allowed_labels = {"行业视角", "工作启发"}
    for idx, item in enumerate(news):
        validate_news_item(item, idx, allowed_labels)
    validate_unique_events(data)

    prose_issues = daily_editorial_issues(validation_data)
    if prose_issues:
        details = "; ".join(f"{item['field']}: {item['message']}" for item in prose_issues[:8])
        raise ValidationError("Daily editorial rewrite required: " + details)

    footer = require_object(data, "footer", "root")
    for key in ["title", "body", "name", "email"]:
        require_string(footer, key, "footer")


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate AI daily email content JSON.")
    parser.add_argument("content", type=Path, help="Path to content JSON.")
    parser.add_argument("--editorial-policy", type=Path, help="Optional editorial policy JSON.")
    args = parser.parse_args()

    try:
        validate_content(
            load_json_object(args.content),
            editorial_policy=load_editorial_policy(args.editorial_policy),
        )
    except JsonObjectError as exc:
        raise SystemExit("Invalid content: Root must be a JSON object.") from exc
    except (json.JSONDecodeError, OSError, DailyConfigError, ValidationError) as exc:
        raise SystemExit(f"Invalid content: {exc}") from exc

    print(f"Valid content: {args.content}")


if __name__ == "__main__":
    main()
