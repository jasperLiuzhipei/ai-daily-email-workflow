"""Source selection output."""
from __future__ import annotations
from typing import Any
from pathlib import Path
from daily_config import snapshot as config_snapshot
from datetime import datetime
import source_selection_context as context
from source_identity import (
    PreviousItem,
)
from source_selection_constants import (
    TZ,
)
from source_selection_context import active_audience_profile


def source_mix_summary(items: list[dict[str, Any]]) -> dict[str, Any]:
    """Compact provenance metrics for daily source-health monitoring."""

    def counts_for(field: str, default: str) -> list[dict[str, Any]]:
        counts: dict[str, int] = {}
        for item in items:
            value = str(item.get(field) or default)
            counts[value] = counts.get(value, 0) + 1
        return [
            {"value": value, "count": count}
            for value, count in sorted(counts.items(), key=lambda entry: (-entry[1], entry[0]))
        ]

    return {
        "item_count": len(items),
        "direct_item_count": sum(
            1 for item in items if str(item.get("feed_source", "")).startswith("direct:")
        ),
        "by_quality_tier": counts_for("source_quality_tier", "discovery"),
        "by_discovery_feed": counts_for("feed_source", "unknown"),
        "top_primary_hosts": counts_for("primary_host", "unknown")[:10],
    }





def pack_discovery(candidate, rank=None):
    from copy import deepcopy
    out = deepcopy(candidate)
    out['source_excerpt'] = out.get('source_excerpt') or out.get('description', '')
    out['source_categories'] = out.get('source_categories') or out.get('categories', [])
    if rank is not None:out['rank'] = rank
    return out


def build_discovery_output(
    run_date: str,
    feed_url: str,
    feed_path: Path,
    all_feed_url: str,
    all_feed_path: Path,
    all_feed_enabled: bool,
    daily_url: str,
    daily_path: Path,
    daily_enabled: bool,
    daily_entries: list[dict[str, Any]],
    daily_matches: list[dict[str, Any]],
    daily_fetch_error: str,
    paper_feed_url: str,
    paper_feed_path: Path,
    bestblogs_feed_url: str,
    bestblogs_feed_path: Path,
    bestblogs_enabled: bool,
    bestblogs_resolution_errors: list[dict[str, str]],
    direct_research_sources: list[dict[str, str]],
    source_registry_path: Path,
    source_registry: dict[str, Any],
    official_source_errors: list[dict[str, str]],
    official_source_health: list[dict[str, Any]],
    paper_lookback_hours: int,
    paper_lookback_start_at: datetime,
    start_at: datetime,
    news_start_at: datetime,
    news_lookback_hours: int,
    allow_wide_news_window: bool,
    gap_audit_enabled: bool,
    gap_audit_start_at: datetime | None,
    end_at: datetime,
    previous_paths: list[Path],
    previous_items: list[PreviousItem],
    candidates: list[dict[str, Any]],
    duplicates: list[dict[str, Any]],
    selected_research: list[dict[str, Any]],
    selected_news: list[dict[str, Any]],
    rejected: list[dict[str, Any]],
    selection_policy_path: Path,
    audience_profiles_path: Path,
) -> dict[str, Any]:
    return {
        "run_meta": {
            "generated_at": datetime.now(TZ).isoformat(),
            "output_role": "source_selection_only",
            "feed_url": feed_url,
            "feed_saved_path": str(feed_path),
            "all_feed_url": all_feed_url if all_feed_enabled else "",
            "all_feed_saved_path": str(all_feed_path) if all_feed_enabled else "",
            "daily_url": daily_url if daily_enabled else "",
            "daily_saved_path": str(daily_path) if daily_enabled and not daily_fetch_error else "",
            "daily_entry_count": len(daily_entries),
            "daily_matched_candidate_count": len(daily_matches),
            "daily_fetch_error": daily_fetch_error,
            "paper_feed_url": paper_feed_url,
            "paper_feed_saved_path": str(paper_feed_path),
            "bestblogs_feed_url": bestblogs_feed_url if bestblogs_enabled else "",
            "bestblogs_feed_saved_path": str(bestblogs_feed_path) if bestblogs_enabled else "",
            "bestblogs_resolution_error_count": len(bestblogs_resolution_errors),
            "direct_research_sources": direct_research_sources,
            "source_registry_path": str(source_registry_path),
            "selection_policy_path": str(selection_policy_path),
            "audience_profiles_path": str(audience_profiles_path),
            "audience_profile_id": context.ACTIVE_AUDIENCE_PROFILE_ID,
            "official_source_error_count": len(official_source_errors),
            "paper_lookback_hours": paper_lookback_hours,
            "news_lookback_hours": news_lookback_hours,
            "gap_audit_enabled": gap_audit_enabled,
            "previous_content_paths": [str(path) for path in previous_paths],
            "previous_item_count": len(previous_items),
        },
        "window": {
            "start_at": start_at.isoformat(),
            "news_start_at": news_start_at.isoformat(),
            "end_at": end_at.isoformat(),
            "timezone": "Asia/Shanghai",
            "paper_lookback_start_at": paper_lookback_start_at.isoformat(),
            "gap_audit_start_at": gap_audit_start_at.isoformat() if gap_audit_start_at else "",
            "news_window_policy": (
                "wide_window_allowed"
                if allow_wide_news_window
                else f"news is clamped to at most {news_lookback_hours} hours; paper lookback is research-only"
            ),
            "gap_audit_policy": (
                "major model/security discovery since last send; final event assessment required"
                if gap_audit_enabled and gap_audit_start_at
                else "inactive"
            ),
        },
        "scoring_policy": {"engine": "event_v2", "phase": "discovery_unscored"},
        "source": {
            "name": "Layered AI source registry",
            "url": feed_url,
            "all_url": all_feed_url if all_feed_enabled else "",
            "paper_url": paper_feed_url,
            "registry_version": source_registry.get("version", 1),
            "registry": source_registry.get("sources", []),
            "official_source_errors": official_source_errors,
            "health": {
                "official_direct_sources": sorted(
                    official_source_health,
                    key=lambda item: str(item["source_id"]),
                ),
                "candidate_provenance": source_mix_summary(candidates),
                "selected_provenance": source_mix_summary(selected_research + selected_news),
            },
            "supplemental_news_sources": [
                {
                    "name": "AIHot Daily",
                    "url": daily_url,
                    "enabled": daily_enabled,
                    "role": "discovery_and_coverage_signal",
                    "policy": "Marks recommended candidates for coverage review without changing their score. It never replaces primary-source resolution, freshness checks, or duplicate filtering.",
                    "entry_count": len(daily_entries),
                    "featured_entries": daily_entries,
                    "matched_candidates": daily_matches,
                    "fetch_error": daily_fetch_error,
                },
                {
                    "name": "BestBlogs high-score AI RSS",
                    "url": bestblogs_feed_url,
                    "enabled": bestblogs_enabled,
                    "role": "news_candidate_supplement",
                    "policy": "Resolve the original publisher URL, then deduplicate and score with the normal policy. Never select from BestBlogs score alone.",
                    "primary_url_resolution_errors": bestblogs_resolution_errors,
                }
            ],
            "direct_research_urls": [item["url"] for item in direct_research_sources],
            "candidate_count_after_dedupe": len(candidates),
            "excluded_duplicate_count": len(duplicates),
        },
        "selection_summary": {
            "research_count": len(selected_research),
            "news_count": len(selected_news),
            "reading_estimate": {'research_minutes': 0, 'news_minutes': 0, 'total_minutes': 0, 'soft_budget_minutes': context.DEFAULT_DAILY_READING_BUDGET_MINUTES},
            "research_pick": selected_research[0]["title"] if selected_research else "",
            "news_mix": " / ".join(item["selection_group"] for item in selected_news),
        },
        "config_snapshot": {
            "selection_policy": config_snapshot(context.ACTIVE_SELECTION_POLICY),
            "audience_profile_id": context.ACTIVE_AUDIENCE_PROFILE_ID,
            "audience_profile": config_snapshot(active_audience_profile()),
        },
        "selected_research": [pack_discovery(item, idx + 1) for idx, item in enumerate(selected_research)],
        "selected_news": [pack_discovery(item, idx + 1) for idx, item in enumerate(selected_news)],
        "rejected_items": rejected,
        "excluded_duplicates": duplicates,
        "all_candidates_scored": [pack_discovery(item) for item in candidates],
    }

