#!/usr/bin/env python3
"""Compile reviewed selection diagnostics into a portable JSONL replay set."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def load_json(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"JSON root must be an object: {path}")
    return data


def first_url(item: dict[str, Any]) -> str:
    urls = item.get("urls")
    if isinstance(urls, list) and urls:
        return str(urls[0])
    return str(item.get("url") or "")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build JSONL replay cases from AI Daily selection diagnostics.")
    parser.add_argument("--runs-root", type=Path, default=Path("daily_runs"))
    parser.add_argument("--output", type=Path, default=Path("daily_runs") / "selection_replay.jsonl")
    parser.add_argument("--include-pending", action="store_true", help="Include diagnostics whose editor review is still pending.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    rows: list[dict[str, Any]] = []
    pending_runs = 0
    for diagnostic_path in sorted(args.runs_root.glob("*/selection_diagnostic.json")):
        diagnostic = load_json(diagnostic_path)
        review_path = Path(diagnostic.get("editor_review") or diagnostic_path.with_name("selection_review.json"))
        review = load_json(review_path) if review_path.exists() else {"review_status": "missing"}
        review_status = str(review.get("review_status") or "pending")
        if review_status != "completed" and not args.include_pending:
            pending_runs += 1
            continue
        for item in diagnostic.get("final_item_audit", []):
            if not isinstance(item, dict):
                continue
            candidate = item.get("matched_selected") or item.get("matched_candidate") or {}
            rows.append(
                {
                    "date": diagnostic.get("date"),
                    "kind": item.get("kind"),
                    "title": item.get("title"),
                    "url": first_url(item),
                    "selector_status": item.get("selector_status"),
                    "selector_score": candidate.get("score") if isinstance(candidate, dict) else None,
                    "selection_group": candidate.get("selection_group") if isinstance(candidate, dict) else "",
                    "editor_label": "published",
                    "review_status": review_status,
                }
            )
        for item in diagnostic.get("auto_selected_not_published", []):
            if not isinstance(item, dict):
                continue
            rows.append(
                {
                    "date": diagnostic.get("date"),
                    "kind": item.get("kind"),
                    "title": item.get("title"),
                    "url": first_url(item),
                    "selector_status": "auto_selected",
                    "selector_score": item.get("score"),
                    "selection_group": item.get("selection_group"),
                    "editor_label": "dropped_before_publish",
                    "review_status": review_status,
                }
            )
        for item in review.get("missed_high_value", []):
            if not isinstance(item, dict) or not (item.get("title") or item.get("url")):
                continue
            rows.append(
                {
                    "date": diagnostic.get("date"),
                    "kind": "editor_reported",
                    "title": item.get("title", ""),
                    "url": item.get("url", ""),
                    "selector_status": "missed_high_value",
                    "selector_score": None,
                    "selection_group": "",
                    "editor_label": "missed_high_value",
                    "reason": item.get("why_missed", ""),
                    "suggested_rule_change": item.get("suggested_rule_change", ""),
                    "review_status": review_status,
                }
            )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")
    print(f"Wrote replay set: {args.output}")
    print(f"Cases: {len(rows)} | skipped pending reviews: {pending_runs}")


if __name__ == "__main__":
    main()
