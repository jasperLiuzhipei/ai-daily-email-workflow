"""Validate a deployed feedback service and print all resolved endpoints."""

from __future__ import annotations

import argparse
import json
import os
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from feedback_service.routes import HEALTH_PATH, METADATA_PATH, absolute_url, public_endpoint_manifest


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Check an AI Daily feedback deployment.")
    parser.add_argument(
        "--base-url",
        default=os.environ.get("AI_DAILY_FEEDBACK_BASE_URL"),
        help="Public service origin. Defaults to AI_DAILY_FEEDBACK_BASE_URL.",
    )
    parser.add_argument("--timeout", type=float, default=8.0)
    return parser.parse_args()


def fetch_json(url: str, timeout: float) -> dict[str, object]:
    request = Request(url, headers={"Accept": "application/json", "User-Agent": "ai-daily-deploy-check/1"})
    with urlopen(request, timeout=timeout) as response:  # noqa: S310 - operator-provided deployment URL
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Expected a JSON object from {url}.")
    return payload


def main() -> int:
    args = parse_args()
    if not args.base_url:
        raise SystemExit("Missing --base-url or AI_DAILY_FEEDBACK_BASE_URL.")
    parsed = urlparse(args.base_url)
    if parsed.scheme != "https" and parsed.hostname not in {"127.0.0.1", "localhost"}:
        raise SystemExit("Production feedback base URL must use HTTPS.")
    endpoints = public_endpoint_manifest(args.base_url)
    try:
        health = fetch_json(absolute_url(args.base_url, HEALTH_PATH), args.timeout)
        metadata = fetch_json(absolute_url(args.base_url, METADATA_PATH), args.timeout)
    except (HTTPError, URLError, TimeoutError, ValueError, json.JSONDecodeError) as exc:
        print(f"Feedback deployment check failed: {exc}", file=sys.stderr)
        return 1
    if health.get("ok") is not True or metadata.get("service") != "ai-daily-feedback":
        print("Feedback deployment returned an unexpected health or metadata response.", file=sys.stderr)
        return 1
    print("AI Daily feedback deployment is reachable.")
    for name, url in endpoints.items():
        print(f"{name}: {url}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
