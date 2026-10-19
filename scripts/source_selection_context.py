"""Source selection context."""
from __future__ import annotations
from typing import Any
from daily_config import load_audience_profiles
from daily_config import load_legacy_selection_policy
from source_identity import (
    SourceSelectionError,
    normalize_x_handle,
)
from source_selection_constants import DEFAULT_AUDIENCE_PROFILES


DEFAULT_NEWS_LOOKBACK_HOURS = 48


DEFAULT_MIN_NEWS_COUNT = 5


DEFAULT_MAX_NEWS_COUNT = None


DEFAULT_DAILY_READING_BUDGET_MINUTES = 10.0


NEWS_EXPANSION_SCORE_THRESHOLD = 82.0


NEWS_OVER_BUDGET_SCORE_THRESHOLD = 90.0


NEWS_EXCEPTIONAL_SCORE_THRESHOLD = 94.0


AIHOT_DAILY_EDITORIAL_BONUS = 4.0


GAP_AUDIT_MIN_SCORE = 86.0


NEWS_HIGH_SCORE_THRESHOLD = 76


TECHNIQUE_SPARSE_BONUS = 5


ACTIVE_OFFICIAL_X_HANDLES: set[str] = set()


def configure_source_quality_policy(source_registry: dict[str, Any]) -> None:
    """Activate the configured official social accounts for this selection run."""

    global ACTIVE_OFFICIAL_X_HANDLES
    ACTIVE_OFFICIAL_X_HANDLES = {
        normalize_x_handle(str(account.get("handle", "")))
        for account in source_registry.get("official_social_accounts", [])
        if str(account.get("platform", "")).lower() == "x"
        and normalize_x_handle(str(account.get("handle", "")))
    }


ACTIVE_SELECTION_POLICY = load_legacy_selection_policy()


ACTIVE_AUDIENCE_PROFILES = load_audience_profiles(DEFAULT_AUDIENCE_PROFILES)


ACTIVE_AUDIENCE_PROFILE_ID = str(ACTIVE_AUDIENCE_PROFILES.get("default_profile", "balanced_product_ops"))


def policy_value(*keys: str, default: Any = None) -> Any:
    value: Any = ACTIVE_SELECTION_POLICY
    for key in keys:
        if not isinstance(value, dict):
            return default
        value = value.get(key)
    return default if value is None else value


def active_audience_profile() -> dict[str, Any]:
    profiles = ACTIVE_AUDIENCE_PROFILES.get("profiles", {})
    if not isinstance(profiles, dict):
        return {}
    baseline = profiles.get(str(ACTIVE_AUDIENCE_PROFILES.get("default_profile", "")), {})
    profile = profiles.get(ACTIVE_AUDIENCE_PROFILE_ID, {})
    if not isinstance(baseline, dict) or not isinstance(profile, dict):
        return {}
    if profile is baseline:
        return profile
    merged = {**baseline, **profile}
    merged["reader_preference_by_group"] = {
        **baseline.get("reader_preference_by_group", {}),
        **profile.get("reader_preference_by_group", {}),
    }
    merged["title_adjustments"] = [
        *baseline.get("title_adjustments", []),
        *profile.get("title_adjustments", []),
    ]
    return merged


def configure_runtime_policy(
    selection_policy: dict[str, Any],
    audience_profiles: dict[str, Any],
    audience_profile_id: str | None = None,
) -> None:
    """Apply one explicit policy/profile pair for the current selector run."""
    global ACTIVE_SELECTION_POLICY, ACTIVE_AUDIENCE_PROFILES, ACTIVE_AUDIENCE_PROFILE_ID
    global DEFAULT_NEWS_LOOKBACK_HOURS, DEFAULT_MIN_NEWS_COUNT, DEFAULT_MAX_NEWS_COUNT
    global DEFAULT_DAILY_READING_BUDGET_MINUTES, NEWS_EXPANSION_SCORE_THRESHOLD
    global NEWS_OVER_BUDGET_SCORE_THRESHOLD, NEWS_EXCEPTIONAL_SCORE_THRESHOLD
    global NEWS_HIGH_SCORE_THRESHOLD, TECHNIQUE_SPARSE_BONUS, AIHOT_DAILY_EDITORIAL_BONUS
    global GAP_AUDIT_MIN_SCORE

    ACTIVE_SELECTION_POLICY = selection_policy
    ACTIVE_AUDIENCE_PROFILES = audience_profiles
    configured_profile = audience_profile_id or str(audience_profiles.get("default_profile", ""))
    profiles = audience_profiles.get("profiles", {})
    if not isinstance(profiles, dict) or configured_profile not in profiles:
        raise SourceSelectionError(f"Unknown audience profile: {configured_profile!r}")
    ACTIVE_AUDIENCE_PROFILE_ID = configured_profile

    DEFAULT_NEWS_LOOKBACK_HOURS = int(policy_value("freshness", "news_lookback_hours", default=48))
    DEFAULT_MIN_NEWS_COUNT = int(policy_value("selection", "min_news_count", default=5))
    DEFAULT_MAX_NEWS_COUNT = policy_value("selection", "max_news_count", default=None)
    DEFAULT_DAILY_READING_BUDGET_MINUTES = float(policy_value("reading", "soft_budget_minutes", default=10))
    NEWS_EXPANSION_SCORE_THRESHOLD = float(
        policy_value("selection", "news_expansion_score_threshold", default=82)
    )
    NEWS_OVER_BUDGET_SCORE_THRESHOLD = float(
        policy_value("selection", "news_over_budget_score_threshold", default=90)
    )
    NEWS_EXCEPTIONAL_SCORE_THRESHOLD = float(
        policy_value("selection", "news_exceptional_score_threshold", default=94)
    )
    NEWS_HIGH_SCORE_THRESHOLD = float(policy_value("selection", "news_min_score", default=76))
    TECHNIQUE_SPARSE_BONUS = float(policy_value("scoring", "bonuses", "technique_sparse", default=5))
    AIHOT_DAILY_EDITORIAL_BONUS = float(
        policy_value("scoring", "bonuses", "aihot_daily_editorial", default=4)
    )
    GAP_AUDIT_MIN_SCORE = float(policy_value("freshness", "gap_audit_min_score", default=86))


configure_runtime_policy(ACTIVE_SELECTION_POLICY, ACTIVE_AUDIENCE_PROFILES)

DISCOVERY_ONLY = False

