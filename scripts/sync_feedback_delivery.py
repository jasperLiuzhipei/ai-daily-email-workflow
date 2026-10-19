#!/usr/bin/env python3
"""Register one production delivery with the deployed feedback service."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


WORKFLOW_DIR = Path(__file__).resolve().parents[1]
if str(WORKFLOW_DIR) not in sys.path:
    sys.path.insert(0, str(WORKFLOW_DIR))

from feedback_service.routes import API_DELIVERY_PATH, absolute_url  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Sync AI Daily delivery metadata to feedback analytics.")
    parser.add_argument("--issue-id", required=True)
    parser.add_argument("--issue-number", type=int)
    parser.add_argument("--series")
    parser.add_argument("--sent-at", required=True)
    parser.add_argument("--recipient-count", required=True, type=int)
    parser.add_argument("--recipient-group")
    parser.add_argument("--recipient-fingerprint")
    parser.add_argument("--base-url", default=os.environ.get("AI_DAILY_FEEDBACK_BASE_URL"))
    parser.add_argument("--admin-key", default=os.environ.get("AI_DAILY_FEEDBACK_ADMIN_KEY"))
    parser.add_argument("--timeout", type=float, default=10.0)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.base_url:
        raise SystemExit("Missing --base-url or AI_DAILY_FEEDBACK_BASE_URL.")
    if not args.admin_key:
        raise SystemExit("Missing --admin-key or AI_DAILY_FEEDBACK_ADMIN_KEY.")
    payload = {
        "issue_id": args.issue_id,
        "issue_number": args.issue_number,
        "series": args.series,
        "sent_at": args.sent_at,
        "recipient_count": args.recipient_count,
        "recipient_group": args.recipient_group,
        "recipient_fingerprint": args.recipient_fingerprint,
    }
    request = Request(
        absolute_url(args.base_url, API_DELIVERY_PATH),
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "X-Admin-Key": args.admin_key,
            "User-Agent": "ai-daily-delivery-sync/1",
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=args.timeout) as response:  # noqa: S310 - configured deployment URL
            result = json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
        print(f"Feedback delivery sync failed: {exc}", file=sys.stderr)
        return 1
    print(
        "Feedback delivery synced: "
        f"issue={result.get('issue_id', args.issue_id)} recipients={result.get('recipient_count', args.recipient_count)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
