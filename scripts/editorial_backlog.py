#!/usr/bin/env python3
"""Recover valuable stories until publication, with explicit editorial decisions.

The ledger is derived from source snapshots and final cards, never from nested
generation metadata. Drafts do not count as publication. No network or SMTP.
"""
from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from ai_daily_common import write_json_object
from source_freshness import candidate_in_window

DEFAULT_POLICY = {"enabled": True, "lookback_days": 7, "news_min_score": 80,
                  "research_min_score": 65, "max_recalled_candidates": 12}


def read_object(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected an object: {path}")
    return value


def identity(url: str) -> str:
    parsed = urlsplit(url.strip())
    host = parsed.netloc.lower().removeprefix("www.")
    path = parsed.path.rstrip("/")
    if host in {"x.com", "twitter.com"}:
        match = re.search(r"/status/(\d+)", path)
        if match:
            return "x:" + match[1]
    if host in {"aihot.news", "aihot.virxact.com"} and path.startswith("/items/"):
        return "aihot:" + path.rsplit("/", 1)[-1]
    if host == "arxiv.org":
        match = re.search(r"/(?:abs|pdf|html)/(\d{4}\.\d{4,5})", path)
        if match:
            return "arxiv:" + match[1]
    query = [(k, v) for k, v in parse_qsl(parsed.query)
             if not k.lower().startswith("utm_") and k not in {"entry", "ref", "source"}]
    return urlunsplit(("https", host, path, urlencode(query), "")) if host else ""


def item_urls(item: dict[str, Any]) -> list[str]:
    urls = [item.get(k) for k in ("url", "primary_url", "source_url", "aihot_url")]
    urls += item.get("coverage_source_urls", [])
    urls += [link.get("url") for link in item.get("links", []) if isinstance(link, dict)]
    return sorted({v for v in urls if isinstance(v, str) and v.startswith(("https://", "http://"))})


def identities(item: dict[str, Any]) -> set[str]:
    return {key for url in item_urls(item) if (key := identity(url))}


def publication_items(content: dict[str, Any]) -> list[dict[str, Any]]:
    """Only actual rendered cards; dropped_cards and editorial notes are excluded."""
    cards = [x for x in content.get("news", []) if isinstance(x, dict)]
    if isinstance(content.get("research_items"), list):
        cards += [x for x in content["research_items"] if isinstance(x, dict)]
    elif isinstance(content.get("paper"), dict):
        cards.append(content["paper"])
    return [{"title": str(x.get("title", "")), "coverage_source_urls": item_urls(x)} for x in cards]


def published_records(run_root: Path, send_ledger: Path, run_date: str,
                      lookback_days: int = 7) -> list[dict[str, Any]]:
    current = date.fromisoformat(run_date)
    output = []
    for day, record in read_object(send_ledger).get("prod", {}).items():
        try:
            age = (current - date.fromisoformat(day)).days
        except ValueError:
            continue
        if not isinstance(record, dict) or not record.get("sent_at") or not 0 <= age <= lookback_days:
            continue
        cards = record.get("published_items")
        if not isinstance(cards, list):
            # Legacy ledger compatibility: read only the real final card list.
            value = record.get("normalized_content") or record.get("content")
            path = Path(value) if isinstance(value, str) and value else None
            if path is not None and not path.is_absolute():
                path = run_root.parent / path
            if path is None or not path.exists():
                continue
            cards = publication_items(read_object(path))
        output.extend({**card, "published_on": day} for card in cards if isinstance(card, dict))
    return output


def effective_score(candidate: dict[str, Any]) -> float:
    value = float(candidate.get("scores", {}).get("final_news") or 0)
    breakdown = candidate.get("score_breakdown", {})
    for field in ("roundup_news_score_cap", "secondary_news_score_cap"):
        cap = breakdown.get(field)
        if isinstance(cap, (int, float)):
            value = min(value, cap)
    return value


def sync_backlog(run_root: Path, run_date: str, *, send_ledger: Path | None = None,
                 policy: dict[str, Any] | None = None) -> dict[str, Any]:
    policy = {**DEFAULT_POLICY, **(policy or {})}
    current = date.fromisoformat(run_date)
    path = run_root / "editorial_backlog.json"
    ledger = read_object(path) or {"schema_version": 1, "items": {}}
    items = ledger.setdefault("items", {})
    published = published_records(run_root, send_ledger or run_root / "send_ledger.json",
                                  run_date, int(policy["lookback_days"]))
    published_sets = [(identities(x), x["published_on"]) for x in published]
    for folder in sorted(run_root.iterdir()) if run_root.exists() else []:
        try:
            day = date.fromisoformat(folder.name)
        except ValueError:
            continue
        if not folder.is_dir() or not 0 <= (current - day).days <= int(policy["lookback_days"]):
            continue
        selections = list(folder.glob("source_selection*.json"))
        # Each historic snapshot remains evidence; a newer draft never erases a miss.
        final_path = folder / "ai_daily_email_content.normalized.json"
        if not final_path.exists():
            final_path = folder / "ai_daily_email_content.json"
        content = read_object(final_path)
        drafts = [identities(x) for x in publication_items(content)]
        drops = content.get("generation", {}).get("response", {}).get("dropped_cards", [])
        failed_titles = {x.get("source_title"): x for x in drops if isinstance(x, dict)}
        for selection_path in sorted(selections):
            selection = read_object(selection_path)
            selected = selection.get("selected_news", []) + selection.get("selected_research", [])
            selected_keys = set().union(*(identities(x) for x in selected)) if selected else set()
            for candidate in selection.get("all_candidates_scored", selected):
                keys = identities(candidate)
                if not keys:
                    continue
                scores = candidate.get("scores", {})
                was_selected = bool(keys & selected_keys)
                unfinished = (candidate.get('editorial_assessment') and
                              candidate.get('editorial_decision', {}).get('action') == 'defer')
                if not (unfinished or was_selected or float(scores.get("final_news") or 0) >= policy["news_min_score"]
                        or (candidate.get("research_eligible") and
                            float(scores.get("final_research") or 0) >= policy["research_min_score"])):
                    continue
                key = next((k for k, old in items.items() if keys & set(old["identities"])), None)
                key = key or hashlib.sha256("|".join(sorted(keys)).encode()).hexdigest()[:20]
                entry = items.setdefault(key, {"id": key, "first_seen": folder.name,
                                              "identities": [], "decisions": []})
                entry["identities"] = sorted(set(entry["identities"]) | keys)
                entry.update({"last_seen": folder.name, "candidate": candidate,
                              "raw_news_score": scores.get("final_news", 0),
                              "effective_news_score": effective_score(candidate)})
                if entry.get("status") in {"published", "dismissed"}:
                    continue
                if any(keys & d for d in drafts):
                    entry.update(status="draft_ready", reason="Present in final draft; production delivery unconfirmed.")
                elif candidate.get("title") in failed_titles:
                    drop = failed_titles[candidate["title"]]
                    entry.update(status="generation_failed", reason="; ".join(
                        str(x.get("reason", "")) for x in drop.get("attempts", [])))
                elif effective_score(candidate) < float(policy["news_min_score"]) and not candidate.get("research_eligible"):
                    entry.update(status="needs_source_review", reason="Source/score gate requires editorial review.")
                else:
                    entry.update(status="pending_review", reason="High-value candidate not in final draft.")
    for entry in items.values():
        matching = [day for keys, day in published_sets if keys & set(entry["identities"])]
        if matching:
            entry.update(status="published", published_on=max(matching), reason="Matched actual production card.")
        elif entry.get("status") not in {"published", "dismissed"}:
            age = (current - date.fromisoformat(entry["first_seen"])).days
            if age > int(policy["lookback_days"]):
                entry.update(status="expired_review", reason="Recall window elapsed; retained for editorial audit.")
    ledger.update(updated_at=datetime.now(timezone.utc).isoformat(), policy=policy)
    write_json_object(path, ledger)
    return ledger


def recall_candidates(
    ledger: dict[str, Any], run_date: str, *,
    start_at: datetime | None = None, end_at: datetime | None = None,
    freshness_exclusions: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Recall eligible drafts, applying the publication window before the quota.

    Omit both timestamps for historical ledger review. Production supplies the
    bounded issue window so stale failures cannot crowd out fresh candidates.
    """
    if (start_at is None) != (end_at is None):
        raise ValueError("Recall freshness filtering needs both start_at and end_at.")
    current = date.fromisoformat(run_date)
    eligible = []
    for entry in ledger.get("items", {}).values():
        if entry["status"] in {"published", "dismissed", "expired_review"}:
            continue
        if entry.get("defer_until") and current < date.fromisoformat(entry["defer_until"]):
            continue
        if entry["first_seen"] >= run_date:
            continue
        candidate = dict(entry["candidate"])
        candidate.update(description=candidate.get("source_excerpt", ""),
                         categories=candidate.get("source_categories", []),
                         backlog_id=entry["id"], backlog_first_seen=entry["first_seen"],
                         research_window_strategy="unpublished_backlog")
        if start_at is not None and end_at is not None and not candidate_in_window(candidate, start_at, end_at):
            if freshness_exclusions is not None:
                freshness_exclusions.append({
                    "id": entry["id"], "title": candidate.get("title", ""),
                    "published_at": candidate.get("published_at", ""),
                    "reason": "Original publication time is outside the maximum lookback or unavailable.",
                })
            continue
        eligible.append((entry["status"] == "generation_failed", entry["raw_news_score"] or 0, candidate))
    eligible.sort(key=lambda x: (x[0], x[1]), reverse=True)
    limit = int(ledger.get("policy", DEFAULT_POLICY)["max_recalled_candidates"])
    return [x[2] for x in eligible[:limit]]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-root", type=Path, default=Path("daily_runs"))
    parser.add_argument("--date", required=True)
    parser.add_argument("--send-ledger", type=Path)
    parser.add_argument("--resolve-id")
    parser.add_argument("--decision", choices=["dismiss", "defer"])
    parser.add_argument("--reason")
    parser.add_argument("--defer-until")
    args = parser.parse_args()
    ledger = sync_backlog(args.run_root, args.date, send_ledger=args.send_ledger)
    if args.resolve_id:
        if not args.decision or not args.reason or (args.decision == "defer" and not args.defer_until):
            parser.error("Resolution needs a decision, reason, and defer-until for deferral.")
        if args.defer_until:
            date.fromisoformat(args.defer_until)
        entry = ledger["items"][args.resolve_id]
        entry["decisions"].append({"date": args.date, "decision": args.decision, "reason": args.reason})
        if args.decision == "dismiss":
            entry.update(status="dismissed", reason=args.reason)
        else:
            entry.update(defer_until=args.defer_until, reason=args.reason)
        write_json_object(args.run_root / "editorial_backlog.json", ledger)
    counts: dict[str, int] = {}
    for item in ledger["items"].values():
        counts[item["status"]] = counts.get(item["status"], 0) + 1
    print(json.dumps({"status_counts": counts, "recall_count": len(recall_candidates(ledger, args.date))}, ensure_ascii=False))


if __name__ == "__main__":
    main()
