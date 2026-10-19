from __future__ import annotations

import argparse
import os
from urllib.parse import quote

from feedback_service.core import FeedbackError, create_feedback_token
from feedback_service.routes import comment_url, open_pixel_url, rating_url


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create one signed AI Daily feedback token.")
    parser.add_argument("--issue", required=True)
    parser.add_argument("--recipient", required=True, help="Email or stable recipient ID; not stored in the token.")
    parser.add_argument("--secret", default=os.environ.get("AI_DAILY_FEEDBACK_SECRET"))
    parser.add_argument("--base-url", default=os.environ.get("AI_DAILY_FEEDBACK_BASE_URL", "http://127.0.0.1:8765"))
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.secret:
        raise SystemExit("Missing --secret or AI_DAILY_FEEDBACK_SECRET.")
    try:
        token = create_feedback_token(args.issue, args.recipient, args.secret)
    except FeedbackError as exc:
        raise SystemExit(str(exc)) from exc
    encoded_token = quote(token, safe="")
    print(f"TOKEN={token}")
    print(f"RATING={rating_url(args.base_url, encoded_token, 4)}")
    print(f"COMMENT={comment_url(args.base_url, encoded_token)}")
    print(f"OPEN_PIXEL={open_pixel_url(args.base_url, encoded_token)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
