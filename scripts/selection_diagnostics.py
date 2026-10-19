#!/usr/bin/env python3
"""Compare source-selection output with the final daily content.

The result is an editorial diagnostics report rather than an automated score
change. It makes selection misses, manual promotions, dropped auto-picks and
duplicate-guard overrides explicit, then creates a small human-review file
that can later be compiled into a replay set.
"""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


SCHEMA_VERSION = 1
TRACKING_QUERY_KEYS = {"fbclid", "gclid", "mc_cid", "mc_eid", "ref", "source"}


class DiagnosticsError(Exception):
    pass


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise DiagnosticsError(f"Missing JSON input: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise DiagnosticsError(f"Invalid JSON: {path}") from exc
    if not isinstance(data, dict):
        raise DiagnosticsError(f"JSON root must be an object: {path}")
    return data


def normalize_url(raw: str | None) -> str:
    if not raw:
        return ""
    try:
        parsed = urlsplit(raw.strip())
    except ValueError:
        return raw.strip().lower()
    if not parsed.scheme or not parsed.netloc:
        return raw.strip().lower()
    query = [(key, value) for key, value in parse_qsl(parsed.query, keep_blank_values=True) if key.lower() not in TRACKING_QUERY_KEYS]
    path = parsed.path.rstrip("/") or "/"
    return urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), path, urlencode(query), ""))


def aihot_id(raw: str | None) -> str:
    match = re.search(r"/items/([a-z0-9]+)", raw or "", flags=re.IGNORECASE)
    return match.group(1).lower() if match else ""


def normalize_title(raw: str | None) -> str:
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", (raw or "").lower())


def item_urls(item: dict[str, Any]) -> set[str]:
    values: list[str] = []
    for key in ("url", "primary_url", "source_url", "aihot_url", "bestblogs_url"):
        value = item.get(key)
        if isinstance(value, str):
            values.append(value)
    links = item.get("links")
    # Event merges preserve observed aliases so coverage does not invent a
    # missing story for each syndicated article. Send admission is checked
    # separately against the approved primary URLs, not these discovery aliases.
    aliases = item.get('coverage_source_urls')
    if isinstance(aliases, list):
        values.extend(v for v in aliases if isinstance(v, str))
    if isinstance(links, list):
        for link in links:
            if isinstance(link, dict) and isinstance(link.get("url"), str):
                values.append(link["url"])
    return {normalized for value in values if (normalized := normalize_url(value))}


def compact_candidate(item: dict[str, Any], kind: str, *, selected: bool) -> dict[str, Any]:
    scores = item.get("scores") if isinstance(item.get("scores"), dict) else {}
    score_key = "final_research" if kind == "research" else "final_news"
    return {
        "kind": kind,
        "selected": selected,
        "rank": item.get("rank"),
        "title": str(item.get("title") or "").strip(),
        "urls": sorted(item_urls(item)),
        "aihot_id": aihot_id(str(item.get("aihot_url") or "")),
        "primary_host": str(item.get("primary_host") or ""),
        "selection_group": str(item.get("selection_group") or ""),
        "score": scores.get(score_key),
        "score_breakdown": item.get("score_breakdown") if isinstance(item.get("score_breakdown"), dict) else {},
        "duplicate": item.get("content_duplicate_check") if isinstance(item.get("content_duplicate_check"), dict) else {},
    }


def final_items(content: dict[str, Any]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    research = content.get("research_items")
    if not isinstance(research, list) or not research:
        paper = content.get("paper")
        research = [paper] if isinstance(paper, dict) else []
    for item in research:
        if isinstance(item, dict):
            items.append({"kind": "research", "title": str(item.get("title") or "").strip(), "urls": sorted(item_urls(item))})
    for item in content.get("news", []):
        if isinstance(item, dict):
            items.append({"kind": "news", "title": str(item.get("title") or "").strip(), "urls": sorted(item_urls(item))})
    return items


def title_match(left: str, right: str) -> bool:
    left_norm, right_norm = normalize_title(left), normalize_title(right)
    if not left_norm or not right_norm:
        return False
    return left_norm == right_norm or (len(left_norm) >= 18 and left_norm in right_norm) or (len(right_norm) >= 18 and right_norm in left_norm)


def matches(final_item: dict[str, Any], candidate: dict[str, Any]) -> bool:
    final_urls = set(final_item["urls"])
    candidate_urls = set(candidate["urls"])
    if final_urls & candidate_urls:
        return True
    final_ids = {aihot_id(url) for url in final_urls}
    final_ids.discard("")
    if candidate.get("aihot_id") and candidate["aihot_id"] in final_ids:
        return True
    return title_match(final_item["title"], candidate["title"])


def candidate_match(final_item: dict[str, Any], candidates: list[dict[str, Any]]) -> dict[str, Any] | None:
    same_kind = [candidate for candidate in candidates if candidate["kind"] == final_item["kind"]]
    direct = [candidate for candidate in same_kind if set(final_item["urls"]) & set(candidate["urls"])]
    if direct:
        return max(direct, key=lambda item: item["score"] if isinstance(item["score"], (int, float)) else -1)
    by_id = [candidate for candidate in same_kind if matches(final_item, candidate) and candidate.get("aihot_id")]
    if by_id:
        return max(by_id, key=lambda item: item["score"] if isinstance(item["score"], (int, float)) else -1)
    by_title = [candidate for candidate in same_kind if title_match(final_item["title"], candidate["title"])]
    if by_title:
        return max(by_title, key=lambda item: item["score"] if isinstance(item["score"], (int, float)) else -1)
    return None


def candidate_summary(candidate: dict[str, Any] | None) -> dict[str, Any] | None:
    if not candidate:
        return None
    return {
        key: candidate[key]
        for key in ("title", "kind", "selected", "rank", "urls", "primary_host", "selection_group", "score", "score_breakdown", "duplicate")
    }


def review_template(date: str, diagnostics: dict[str, Any]) -> dict[str, Any]:
    audits = diagnostics["final_item_audit"]
    manual_promotions = [
        {
            "title": item["title"],
            "url": item["urls"][0] if item["urls"] else "",
            "selector_status": item["selector_status"],
            "why_publish": "",
            "expected_priority": "",
        }
        for item in audits
        if item["selector_status"] != "auto_selected"
    ]
    auto_drops = [
        {
            "title": item["title"],
            "url": item["urls"][0] if item["urls"] else "",
            "why_drop": "",
        }
        for item in diagnostics["auto_selected_not_published"]
    ]
    return {
        "schema_version": SCHEMA_VERSION,
        "date": date,
        "review_status": "pending",
        "manual_promotions": manual_promotions,
        "auto_selected_drops": auto_drops,
        "missed_high_value": [
            {
                "title": "",
                "url": "",
                "why_missed": "",
                "expected_priority": "high",
                "suggested_rule_change": "",
            }
        ],
        "false_duplicate_reports": [],
        "scoring_feedback": [],
        "editor_notes": "",
    }


def default_selection_path(date: str) -> Path:
    dated = Path("daily_runs") / date / f"source_selection_auto_{date}.json"
    return dated if dated.exists() else Path("daily_runs") / date / "source_selection.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Diagnose AI Daily source selection against final email content.")
    parser.add_argument("--date", required=True, help="Run date YYYY-MM-DD.")
    parser.add_argument("--selection", type=Path, help="Source selection JSON path.")
    parser.add_argument("--content", type=Path, help="Final daily content JSON path.")
    parser.add_argument("--output", type=Path, help="Diagnostics output JSON path.")
    parser.add_argument("--review", type=Path, help="Editor review JSON path.")
    parser.add_argument("--overwrite-review", action="store_true", help="Regenerate review template even if it already exists.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    selection_path = args.selection or default_selection_path(args.date)
    content_path = args.content or Path("daily_runs") / args.date / "ai_daily_email_content.json"
    output_path = args.output or Path("daily_runs") / args.date / "selection_diagnostic.json"
    review_path = args.review or Path("daily_runs") / args.date / "selection_review.json"

    try:
        selection = load_json(selection_path)
        content = load_json(content_path)
        selected: list[dict[str, Any]] = []
        selected.extend(compact_candidate(item, "research", selected=True) for item in selection.get("selected_research", []) if isinstance(item, dict))
        selected.extend(compact_candidate(item, "news", selected=True) for item in selection.get("selected_news", []) if isinstance(item, dict))
        all_candidates = [
            compact_candidate(item, "research" if item.get("source_pool") == "research" else "news", selected=False)
            for item in selection.get("all_candidates_scored", [])
            if isinstance(item, dict)
        ]
        rejected = [
            compact_candidate(item, "research" if item.get("source_pool") == "research" else "news", selected=False)
            for item in selection.get("rejected_items", [])
            if isinstance(item, dict)
        ]
    except DiagnosticsError as exc:
        raise SystemExit(f"Selection diagnostics failed: {exc}") from exc

    final = final_items(content)
    audits: list[dict[str, Any]] = []
    for item in final:
        selected_match = candidate_match(item, selected)
        pool_match = candidate_match(item, all_candidates)
        rejected_match = candidate_match(item, rejected)
        status = "auto_selected" if selected_match else "candidate_not_selected" if pool_match else "manual_or_unseen"
        audits.append(
            {
                **item,
                "selector_status": status,
                "matched_selected": candidate_summary(selected_match),
                "matched_candidate": candidate_summary(pool_match),
                "matched_rejected": candidate_summary(rejected_match),
            }
        )

    drops = [
        candidate_summary(candidate)
        for candidate in selected
        if not any(matches(item, candidate) for item in final)
    ]
    duplicate_overrides = [
        item for item in audits if item["matched_rejected"] and item["matched_rejected"].get("duplicate", {}).get("is_duplicate")
    ]
    summary = {
        "final_research": sum(item["kind"] == "research" for item in final),
        "final_news": sum(item["kind"] == "news" for item in final),
        "auto_selected_and_published": sum(item["selector_status"] == "auto_selected" for item in audits),
        "published_from_unselected_pool": sum(item["selector_status"] == "candidate_not_selected" for item in audits),
        "published_manual_or_unseen": sum(item["selector_status"] == "manual_or_unseen" for item in audits),
        "auto_selected_not_published": len(drops),
        "published_after_duplicate_guard": len(duplicate_overrides),
    }
    diagnostics = {
        "schema_version": SCHEMA_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "date": args.date,
        "inputs": {"selection": str(selection_path), "content": str(content_path)},
        "summary": summary,
        "final_item_audit": audits,
        "auto_selected_not_published": drops,
        "published_after_duplicate_guard": duplicate_overrides,
        "editor_review": str(review_path),
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(diagnostics, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if args.overwrite_review or not review_path.exists():
        review_path.write_text(json.dumps(review_template(args.date, diagnostics), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(f"Wrote diagnostics: {output_path}")
    print(f"Review file: {review_path}")
    print(
        "Selection audit: "
        f"published={summary['final_research'] + summary['final_news']} "
        f"auto={summary['auto_selected_and_published']} "
        f"unselected_pool={summary['published_from_unselected_pool']} "
        f"manual_or_unseen={summary['published_manual_or_unseen']} "
        f"auto_drops={summary['auto_selected_not_published']}"
    )


if __name__ == "__main__":
    main()
