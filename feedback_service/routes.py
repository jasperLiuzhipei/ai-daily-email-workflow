"""Canonical public routes for the AI Daily feedback service.

Email generation and the deployed HTTP service import this module so route
changes cannot silently break already-generated links.
"""

from __future__ import annotations

from urllib.parse import quote, urlencode


HEALTH_PATH = "/healthz"
METADATA_PATH = "/.well-known/ai-daily-feedback.json"
RATING_PAGE_PREFIX = "/f"
COMMENT_PAGE_PREFIX = "/comment"
OPEN_PIXEL_PREFIX = "/open"
CLICK_PREFIX = "/click"
ADMIN_PATH = "/admin"
ADMIN_ISSUE_PREFIX = "/admin/issue"
API_RATING_PATH = "/api/rating"
API_COMMENT_PATH = "/api/comment"
API_DELIVERY_PATH = "/api/delivery"
API_ISSUES_PATH = "/api/issues"
API_SUMMARY_PREFIX = "/api/summary"
API_COMMENTS_PREFIX = "/api/comments"


def normalize_base_url(base_url: str) -> str:
    value = str(base_url or "").strip().rstrip("/")
    if not value.startswith(("https://", "http://")):
        raise ValueError("Feedback base URL must start with http:// or https://.")
    return value


def absolute_url(base_url: str, path: str) -> str:
    return f"{normalize_base_url(base_url)}{path}"


def rating_url(base_url: str, encoded_token: str, score: int) -> str:
    if score not in range(1, 6):
        raise ValueError("Feedback score must be between 1 and 5.")
    return absolute_url(base_url, f"{RATING_PAGE_PREFIX}/{encoded_token}#score={score}")


def comment_url(base_url: str, encoded_token: str) -> str:
    return absolute_url(base_url, f"{COMMENT_PAGE_PREFIX}/{encoded_token}")


def open_pixel_url(base_url: str, encoded_token: str) -> str:
    return absolute_url(base_url, f"{OPEN_PIXEL_PREFIX}/{encoded_token}.gif")


def click_url(
    base_url: str,
    encoded_token: str,
    link_id: str,
    destination_url: str,
    link_title: str,
    signature: str,
) -> str:
    """Build a signed, transparent redirect URL for one article link."""

    path = f"{CLICK_PREFIX}/{encoded_token}/{quote(link_id, safe='')}"
    query = urlencode(
        {"u": destination_url, "t": link_title, "s": signature},
        quote_via=quote,
    )
    return f"{absolute_url(base_url, path)}?{query}"


def public_endpoint_manifest(base_url: str) -> dict[str, str]:
    base = normalize_base_url(base_url)
    sample_token = quote("<signed-token>", safe="")
    return {
        "base_url": base,
        "health": absolute_url(base, HEALTH_PATH),
        "metadata": absolute_url(base, METADATA_PATH),
        "rating_template": rating_url(base, sample_token, 5),
        "comment_template": comment_url(base, sample_token),
        "open_pixel_template": open_pixel_url(base, sample_token),
        "click_template": absolute_url(base, f"{CLICK_PREFIX}/{sample_token}/<link-id>"),
        "admin": absolute_url(base, ADMIN_PATH),
        "issues_api": absolute_url(base, API_ISSUES_PATH),
        "delivery_api": absolute_url(base, API_DELIVERY_PATH),
        "summary_api_template": absolute_url(base, f"{API_SUMMARY_PREFIX}/<issue-id>"),
        "comments_api_template": absolute_url(base, f"{API_COMMENTS_PREFIX}/<issue-id>"),
    }
