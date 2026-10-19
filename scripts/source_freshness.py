"""Shared freshness bounds for all daily source-discovery paths."""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

DEFAULT_MAX_LOOKBACK_HOURS = 96


def lookback_floor(end_at: datetime, max_hours: int = DEFAULT_MAX_LOOKBACK_HOURS) -> datetime:
    if type(max_hours) is not int or max_hours <= 0:
        raise ValueError("Maximum source lookback hours must be a positive integer")
    return end_at - timedelta(hours=max_hours)


def candidate_in_window(candidate: dict[str, Any], start_at: datetime, end_at: datetime) -> bool:
    """Use the original publication time, never a draft or recall timestamp."""
    value = candidate.get("published_at")
    if not isinstance(value, str) or not value:
        return False
    try:
        published_at = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if published_at.tzinfo is None:
            published_at = published_at.replace(tzinfo=end_at.tzinfo)
    except ValueError:
        return False
    return start_at <= published_at <= end_at
