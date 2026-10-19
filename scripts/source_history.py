"""Source history."""
from __future__ import annotations
from typing import Any
from pathlib import Path
from datetime import datetime
import difflib
import json
from editorial_backlog import publication_items
from event_editorial import candidate_urls
from source_identity import (
    PreviousItem,
    SourceSelectionError,
    candidate_identities,
    clean_text,
    extract_aihot_id,
    extract_identities,
    normalize_text,
    normalize_url,
    parse_datetime,
)
from source_selection_constants import (
    DEFAULT_SEND_LEDGER,
    URL_RE,
)


def collect_string_text(obj: Any) -> str:
    values: list[str] = []

    def walk(value: Any) -> None:
        if isinstance(value, str):
            if value.startswith(("http://", "https://")):
                return
            values.append(value)
        elif isinstance(value, dict):
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(obj)
    return clean_text(" ".join(values))


def collect_urls(obj: Any) -> set[str]:
    urls: set[str] = set()

    def walk(value: Any) -> None:
        if isinstance(value, str):
            if value.startswith(("http://", "https://")):
                urls.add(normalize_url(value))
            else:
                for url in URL_RE.findall(value):
                    urls.add(normalize_url(url))
        elif isinstance(value, dict):
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(obj)
    return {url for url in urls if url}


def previous_item_from_object(obj: dict[str, Any]) -> PreviousItem | None:
    title = clean_text(obj.get("title") if isinstance(obj.get("title"), str) else "")
    urls = collect_urls(obj)
    text = collect_string_text(obj)
    if not title and not urls:
        return None
    identities = extract_identities(title, text, " ".join(urls))
    for url in urls:
        item_id = extract_aihot_id(url)
        if item_id:
            identities.add(f"aihot:{item_id}")
    return PreviousItem(title=title, urls=urls, text=text, identities=identities)


def load_previous_items(paths: list[Path]) -> list[PreviousItem]:
    items: list[PreviousItem] = []

    for path in paths:
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        # Generation metadata contains dropped source titles and URLs. Those
        # are misses, not published cards, and must never poison deduplication.
        for card in publication_items(data):
            item = previous_item_from_object(card)
            if item:
                items.append(item)
    return items


def auto_previous_content(run_date: str) -> list[Path]:
    # Only successful production sends can close yesterday's coverage gap.
    # Unsent drafts are deliberately not used as the automatic dedupe history.
    return auto_previous_official_content(run_date)


def auto_previous_official_content(run_date: str) -> list[Path]:
    """Return the latest normalized content from a successful prod send.

    Test sends and local previews can create normalized JSON files. Those files
    should not affect tomorrow's source dedupe unless the corresponding issue
    was actually sent to the prod list.
    """

    if not DEFAULT_SEND_LEDGER.exists():
        return []

    current = datetime.strptime(run_date, "%Y-%m-%d").date()
    try:
        data = json.loads(DEFAULT_SEND_LEDGER.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []

    prod = data.get("prod")
    if not isinstance(prod, dict):
        return []

    candidates: list[tuple[str, Path]] = []
    for date_key, entry in prod.items():
        try:
            day = datetime.strptime(date_key, "%Y-%m-%d").date()
        except ValueError:
            continue
        if day >= current or not isinstance(entry, dict):
            continue

        content_value = entry.get("normalized_content") or entry.get("content")
        if not isinstance(content_value, str) or not content_value.strip():
            continue
        path = Path(content_value)
        if path.exists():
            candidates.append((date_key, path))

    return [path for day, path in sorted(candidates, reverse=True)
            if (current - datetime.strptime(day, "%Y-%m-%d").date()).days <= 7]


def latest_previous_prod_send_at(run_date: str) -> datetime | None:
    """Return the last prod-send timestamp before run_date, if recorded."""

    if not DEFAULT_SEND_LEDGER.exists():
        return None

    current = datetime.strptime(run_date, "%Y-%m-%d").date()
    try:
        data = json.loads(DEFAULT_SEND_LEDGER.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None

    prod = data.get("prod")
    if not isinstance(prod, dict):
        return None

    candidates: list[datetime] = []
    for date_key, entry in prod.items():
        try:
            day = datetime.strptime(date_key, "%Y-%m-%d").date()
        except ValueError:
            continue
        if day >= current or not isinstance(entry, dict):
            continue

        sent_at = entry.get("sent_at")
        if not isinstance(sent_at, str) or not sent_at.strip():
            continue
        try:
            parsed = parse_datetime(sent_at)
        except (ValueError, SourceSelectionError):
            continue
        candidates.append(parsed)

    return max(candidates) if candidates else None


def content_duplicate_check(candidate: dict[str, Any], previous_items: list[PreviousItem]) -> dict[str, Any]:
    # Recalled and merged sources may carry a published URL only as an alias.
    # Compare the same explicit provenance fields on both sides of history.
    current_urls = {normalize_url(url) for url in candidate_urls(candidate)}
    current_urls = {url for url in current_urls if url}
    current_title = normalize_text(candidate.get("title", ""))
    current_text = normalize_text(f"{candidate.get('title', '')} {candidate.get('description', '')}")
    identities = candidate_identities(candidate)
    source_type = candidate.get("source_type", "")

    for previous in previous_items:
        url_overlap = current_urls & previous.urls
        if url_overlap:
            return {
                "is_duplicate": True,
                "reason": "same_url",
                "matched_previous": previous.title or sorted(url_overlap)[0],
                "matched_value": sorted(url_overlap)[0],
            }

        identity_overlap = identities & previous.identities
        if identity_overlap:
            match = sorted(identity_overlap)[0]
            if match.startswith("msg:"):
                reason = "same_message_key"
            elif match.startswith(("arxiv:", "doi:")):
                reason = "same_research_identity"
            else:
                reason = "same_item_identity"
            return {
                "is_duplicate": True,
                "reason": reason,
                "matched_previous": previous.title,
                "matched_value": match,
            }

        if current_title and previous.title:
            title_ratio = difflib.SequenceMatcher(None, current_title, normalize_text(previous.title)).ratio()
            if title_ratio >= 0.9:
                return {
                    "is_duplicate": True,
                    "reason": "near_same_title",
                    "matched_previous": previous.title,
                    "matched_value": round(title_ratio, 3),
                }

        # Only use body similarity as a hard duplicate when the title is also
        # close. This avoids killing a new story from the same company/product.
        if current_text and previous.text:
            title_ratio = difflib.SequenceMatcher(None, current_title, normalize_text(previous.title)).ratio()
            body_ratio = difflib.SequenceMatcher(None, current_text, normalize_text(previous.text)).ratio()
            if title_ratio >= 0.78 and body_ratio >= 0.82:
                return {
                    "is_duplicate": True,
                    "reason": "same_message_content",
                    "matched_previous": previous.title,
                    "matched_value": {"title_similarity": round(title_ratio, 3), "body_similarity": round(body_ratio, 3)},
                }

        # For papers/reports, a highly similar title usually means the same
        # publication even if the source URL changed from AIHot to a media page.
        if source_type in {"paper", "benchmark", "report"} and current_title and previous.title:
            title_ratio = difflib.SequenceMatcher(None, current_title, normalize_text(previous.title)).ratio()
            if title_ratio >= 0.84:
                return {
                    "is_duplicate": True,
                    "reason": "same_research_publication",
                    "matched_previous": previous.title,
                    "matched_value": round(title_ratio, 3),
                }

    return {
        "is_duplicate": False,
        "reason": "",
        "matched_previous": "",
        "matched_value": "",
    }


def continuity_note(candidate: dict[str, Any], previous_items: list[PreviousItem]) -> str:
    title = candidate.get("title", "").lower()
    entities = [
        "Claude Code",
        "OpenRouter",
        "Codex",
        "Cursor",
        "GPT-5.6",
        "DeepSeek",
        "Runway",
        "Qwen",
        "通义",
        "千问",
        "OpenAI",
        "Google",
        "Anthropic",
        "Gemini",
    ]
    previous_text = " ".join(item.title for item in previous_items).lower()
    for entity in entities:
        if entity.lower() in title and entity.lower() in previous_text:
            return f"同主体连续出现：{entity}。允许入选，但正文必须讲清本次新增信息点。"
    return ""

