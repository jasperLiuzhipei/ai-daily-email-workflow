"""Orchestrate source discovery and event assessment; v1 replay is explicit."""
from __future__ import annotations
from source_legacy_output import build_output
from source_candidates import prepare_candidate, mark_daily_features
from source_selection_output import build_discovery_output
from typing import Any
from daily_config import DailyConfigError
import xml.etree.ElementTree as ET
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import argparse
from concurrent.futures import as_completed
from datetime import datetime
import json
import time
from daily_config import load_audience_profiles
from daily_config import load_selection_policy, load_legacy_selection_policy
from source_freshness import DEFAULT_MAX_LOOKBACK_HOURS, candidate_in_window, lookback_floor
from editorial_backlog import published_records
from editorial_backlog import recall_candidates
from editorial_backlog import sync_backlog
from datetime import timedelta
import source_selection_context as context
from source_blog_adapters import (
    parse_anthropic_listing_page,
    parse_dated_link_time_cards,
    parse_generic_listing_page,
    parse_release_notes_page,
)
from source_candidates import (
    merge_candidates,
    merge_packed_candidates,
)
from source_feed_adapters import (
    parse_aihot_daily_entries,
    parse_bestblogs_feed,
    parse_feed,
    parse_official_rss_feed,
    run_curl,
)
from source_history import (
    auto_previous_content,
    latest_previous_prod_send_at,
    load_previous_items,
    previous_item_from_object,
)
from source_identity import (
    SourceSelectionError,
    host_of,
    parse_datetime,
)
from source_legacy_scoring import apply_sparse_technique_bonus
from source_legacy_selection import (
    select_items,
)
from source_selection_constants import (
    DEFAULT_ALL_FEED_URL,
    DEFAULT_AUDIENCE_PROFILES,
    DEFAULT_BESTBLOGS_AI_FEED_URL,
    DEFAULT_BESTBLOGS_MAX_ITEMS,
    DEFAULT_DAILY_URL,
    DEFAULT_FEED_URL,
    DEFAULT_PAPER_FEED_URL,
    DEFAULT_SELECTION_POLICY,
    DEFAULT_SEND_LEDGER,
    DEFAULT_SOURCE_REGISTRY,
    TZ,
)
from source_selection_context import (
    configure_runtime_policy,
    configure_source_quality_policy,
    policy_value,
)
from source_selection_output import (
    source_mix_summary,
)
from source_selection_paths import (
    default_all_feed_path,
    default_bestblogs_feed_path,
    default_daily_path,
    default_feed_path,
    default_official_source_path,
    default_output_path,
    default_paper_feed_path,
    load_source_registry,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Select layered sources for the AI daily workflow.")
    parser.add_argument("--date", default=datetime.now(TZ).strftime("%Y-%m-%d"))
    parser.add_argument("--start-at", help="Inclusive start time, ISO format. Naive values are Asia/Shanghai.")
    parser.add_argument("--end-at", help="Inclusive end time, ISO format. Defaults to now in Asia/Shanghai.")
    parser.add_argument("--feed-url", default=DEFAULT_FEED_URL)
    parser.add_argument("--paper-feed-url", default=DEFAULT_PAPER_FEED_URL)
    parser.add_argument('--no-aihot-api', action='store_true', help='Use legacy RSS discovery for offline replay.')
    parser.add_argument(
        "--all-feed-url",
        default=DEFAULT_ALL_FEED_URL,
        help="AIHot all-items feed used only as a scored and deduplicated recall supplement.",
    )
    parser.add_argument(
        "--no-aihot-all",
        action="store_true",
        help="Disable the AIHot all-items recall supplement for this run.",
    )
    parser.add_argument(
        "--daily-url",
        default=None,
        help="AIHot Daily page used only as an editorial prefilter and priority signal.",
    )
    parser.add_argument(
        "--no-aihot-daily",
        action="store_true",
        help="Disable the AIHot Daily editorial prefilter for this run.",
    )
    parser.add_argument(
        "--bestblogs-feed-url",
        default=DEFAULT_BESTBLOGS_AI_FEED_URL,
        help="High-score AI RSS used only as a supplemental industry-news candidate source.",
    )
    parser.add_argument(
        "--no-bestblogs",
        action="store_true",
        help="Disable the BestBlogs supplemental news source for this run.",
    )
    parser.add_argument(
        "--bestblogs-max-items",
        type=int,
        default=DEFAULT_BESTBLOGS_MAX_ITEMS,
        help="Maximum in-window BestBlogs items to resolve to primary sources.",
    )
    parser.add_argument(
        "--direct-research-url",
        action="append",
        default=[],
        help="Additional high-quality research listing URL. May be repeated.",
    )
    parser.add_argument(
        "--no-direct-research-sources",
        action="store_true",
        help="Disable built-in direct research listing sources such as Anthropic Research.",
    )
    parser.add_argument("--feed-path", type=Path)
    parser.add_argument("--paper-feed-path", type=Path)
    parser.add_argument("--all-feed-path", type=Path)
    parser.add_argument("--daily-path", type=Path)
    parser.add_argument("--bestblogs-feed-path", type=Path)
    parser.add_argument(
        "--source-registry",
        type=Path,
        default=DEFAULT_SOURCE_REGISTRY,
        help="JSON registry for core, direct first-party, and indirect-coverage sources.",
    )
    parser.add_argument(
        "--selection-policy",
        type=Path,
        default=DEFAULT_SELECTION_POLICY,
        help="Versioned scoring, freshness, selection, and reading-budget policy JSON.",
    )
    parser.add_argument(
        "--audience-profiles",
        type=Path,
        default=DEFAULT_AUDIENCE_PROFILES,
        help="Versioned audience preference profile registry JSON.",
    )
    parser.add_argument(
        "--audience-profile",
        help="Audience profile id. Defaults to audience_profiles.default_profile.",
    )
    parser.add_argument(
        "--no-official-sources",
        action="store_true",
        help="Disable all optional first-party sources registered as official_direct for this run.",
    )
    parser.add_argument("--paper-lookback-hours", type=int)
    parser.add_argument(
        "--news-lookback-hours",
        type=int,
        default=None,
        help=(
            "Maximum lookback for ordinary news. Paper/research lookback is controlled "
            "separately by --paper-lookback-hours."
        ),
    )
    parser.add_argument(
        "--allow-wide-news-window",
        action="store_true",
        help="Allow the wider news window within the global maximum lookback. Use for explicit backfills.",
    )
    parser.add_argument("--previous-content", type=Path, action="append", default=[])
    parser.add_argument("--editorial-runtime-config", type=Path, help="Text gateway used for event-level assessment.")
    parser.add_argument("--editorial-policy", type=Path, help="Event scoring policy; old keyword weights do not apply.")
    parser.add_argument("--legacy-scoring", action="store_true", help="Historical diagnosis only; future production requires event assessments.")
    parser.add_argument("--no-auto-previous", action="store_true")
    parser.add_argument("--research-count", type=int)
    parser.add_argument(
        "--min-news-count",
        type=int,
        default=None,
        help="Historical --legacy-scoring only; event scoring never fills a minimum count.",
    )
    parser.add_argument(
        "--news-count",
        type=int,
        dest="legacy_min_news_count",
        help="Compatibility alias for --min-news-count.",
    )
    parser.add_argument(
        "--max-news-count",
        type=int,
        default=None,
        help="Hard maximum number of industry-news cards. Defaults to selection policy.",
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    try:
        configure_runtime_policy(
            (load_legacy_selection_policy if args.legacy_scoring else load_selection_policy)(args.selection_policy),
            load_audience_profiles(args.audience_profiles),
            args.audience_profile,
        )
    except DailyConfigError as exc:
        raise SystemExit(f"source selection configuration failed: {exc}") from exc

    context.DISCOVERY_ONLY = not args.legacy_scoring

    if args.paper_lookback_hours is None:
        args.paper_lookback_hours = int(policy_value("freshness", "research_lookback_hours", default=72))
    if args.news_lookback_hours is None:
        args.news_lookback_hours = context.DEFAULT_NEWS_LOOKBACK_HOURS
    if args.research_count is None:
        args.research_count = int(policy_value("selection", "research_count", default=1))
    if args.min_news_count is None:
        args.min_news_count = 0 if context.DISCOVERY_ONLY else context.DEFAULT_MIN_NEWS_COUNT
    if args.max_news_count is None:
        args.max_news_count = context.DEFAULT_MAX_NEWS_COUNT

    run_date = args.date
    midnight_start = datetime.strptime(run_date, "%Y-%m-%d").replace(tzinfo=TZ)
    default_start = latest_previous_prod_send_at(run_date) or midnight_start
    start_at = parse_datetime(args.start_at, default_start)
    end_at = parse_datetime(args.end_at, datetime.now(TZ))
    requested_start_at = start_at
    requested_max_lookback_hours = int(policy_value("freshness", "max_lookback_hours", default=DEFAULT_MAX_LOOKBACK_HOURS))
    max_lookback_hours = min(requested_max_lookback_hours, DEFAULT_MAX_LOOKBACK_HOURS)
    lookback_floor_at = lookback_floor(end_at, max_lookback_hours)
    start_at = max(start_at, lookback_floor_at)
    if requested_start_at < start_at:
        print(f"Source lookback capped at {max_lookback_hours} hours: {start_at.isoformat()}")
    news_start_at = start_at
    if not args.allow_wide_news_window and args.news_lookback_hours >= 0:
        news_start_at = max(start_at, end_at - timedelta(hours=args.news_lookback_hours))
    gap_audit_enabled = bool(policy_value("freshness", "gap_audit_enabled", default=True))
    gap_audit_start_at = (
        start_at
        if gap_audit_enabled
        and not args.allow_wide_news_window
        and start_at < news_start_at
        else None
    )
    feed_path = args.feed_path or default_feed_path(run_date)
    paper_feed_path = args.paper_feed_path or default_paper_feed_path(run_date)
    all_feed_path = args.all_feed_path or default_all_feed_path(run_date)
    daily_path = args.daily_path or default_daily_path(run_date)
    bestblogs_feed_path = args.bestblogs_feed_path or default_bestblogs_feed_path(run_date)
    output_path = args.output or default_output_path(run_date)
    paper_lookback_start_at = max(
        lookback_floor_at,
        min(start_at, end_at - timedelta(hours=max(args.paper_lookback_hours, 0))),
    )
    aihot_recall_start_at = min(
        news_start_at,
        paper_lookback_start_at,
        gap_audit_start_at or news_start_at,
    )

    previous_paths = list(args.previous_content)
    if not args.no_auto_previous and not previous_paths:
        previous_paths = auto_previous_content(run_date)
    previous_items = load_previous_items(previous_paths)
    if not args.no_auto_previous and not args.previous_content:
        # Prefer immutable card identities from newer production ledgers.
        previous_items = [item for card in published_records(
            Path("daily_runs"), DEFAULT_SEND_LEDGER, run_date)
            if (item := previous_item_from_object(card))]

    source_registry = load_source_registry(args.source_registry)
    configure_source_quality_policy(source_registry)
    daily_source = next(
        (
            source
            for source in source_registry["sources"]
            if source.get("enabled") and source.get("integration") == "daily_editorial"
        ),
        None,
    )
    daily_url = args.daily_url or str((daily_source or {}).get("url") or DEFAULT_DAILY_URL)
    if daily_url.startswith('https://aihot.virxact.com/'):
        daily_url = daily_url.replace('https://aihot.virxact.com/', 'https://aihot.news/', 1)
    official_sources = [
        source
        for source in source_registry["sources"]
        if source.get("enabled") and source.get("integration") == "official_direct"
    ]
    if args.no_official_sources:
        official_sources = []
    if args.no_direct_research_sources:
        official_sources = [source for source in official_sources if source.get("pool") != "research"]

    direct_research_sources: list[dict[str, Any]] = [
        source for source in official_sources if source.get("pool") == "research"
    ]
    for url in args.direct_research_url:
        name = host_of(url).replace(".", "_") or "direct_research"
        direct_research_sources.append(
            {
                "id": name,
                "name": name,
                "url": url,
                "pool": "research",
                "authority": "official_research",
                "transport": "html",
                "parser": "anthropic_listing",
                "path_prefixes": ["/research/"],
                "exclude_prefixes": ["/research/team/"],
                "source_type_override": "paper",
            }
        )

    xml_text = run_curl(args.feed_url, feed_path)
    exact_candidates, exact_duplicates = parse_feed(
        xml_text,
        aihot_recall_start_at,
        end_at,
        previous_items,
        feed_source="main",
        research_window_strategy="exact_window",
        news_start_at=news_start_at,
        gap_audit_start_at=gap_audit_start_at,
    )

    all_candidates: list[dict[str, Any]] = []
    all_duplicates: list[dict[str, Any]] = []
    from aihot_discovery import fetch_window, candidates_from_items, fetch_hot_topics, discover_pending, coverage_receipts, FetchBudget
    api_policy = policy_value('aihot_api', default={})
    api_enabled = (bool(api_policy.get('enabled')) and context.DISCOVERY_ONLY
                   and not args.no_aihot_api and not args.no_aihot_all)
    api_audit, hot_audit = {'status': 'disabled', 'complete': False}, {'status': 'disabled', 'topics': []}
    hot_candidates, hot_duplicates = [], []
    api_fetch = FetchBudget(run_curl, int(api_policy.get('budget_seconds', 180)))
    if api_enabled:
        api_start = min(news_start_at, gap_audit_start_at or news_start_at)
        rows, api_audit = fetch_window(api_start, end_at, all_feed_path.parent / 'aihot_api', api_fetch,
                                       max_pages=int(api_policy.get('max_pages', 12)))
        all_candidates, all_duplicates, api_excluded = candidates_from_items(rows, api_start, end_at, previous_items)
        api_audit['exclusions'] = api_excluded
        hot_candidates, hot_duplicates, hot_audit = fetch_hot_topics(
            lookback_floor_at, end_at, all_feed_path.parent / 'aihot_hot_topics', previous_items, api_fetch)
    if not args.no_aihot_all and not api_audit['complete']:
        all_xml_text = run_curl(args.all_feed_url, all_feed_path)
        fallback_candidates, fallback_duplicates = parse_feed(
            all_xml_text,
            aihot_recall_start_at,
            end_at,
            previous_items,
            feed_source="all",
            research_window_strategy="exact_window",
            news_start_at=news_start_at,
            gap_audit_start_at=gap_audit_start_at,
        )
        all_candidates.extend(fallback_candidates)
        all_duplicates.extend(fallback_duplicates)
        api_audit['fallback'] = 'rss_snapshot; full_window_coverage_not_confirmed'

    daily_entries: list[dict[str, Any]] = []
    daily_matches: list[dict[str, Any]] = []
    daily_fetch_error = ""
    if not args.no_aihot_daily:
        try:
            daily_html = run_curl(daily_url, daily_path)
            daily_entries = parse_aihot_daily_entries(daily_html)
            if not daily_entries:
                daily_fetch_error = 'Daily returned no parsed entries; editorial signal unavailable.'
        except SourceSelectionError as exc:
            # Daily is an optional editorial signal. Its temporary failure must
            # never prevent the primary feed selector from producing a daily.
            daily_fetch_error = str(exc)

    paper_xml_text = run_curl(args.paper_feed_url, paper_feed_path)
    paper_exact_candidates, paper_exact_duplicates = parse_feed(
        paper_xml_text,
        start_at,
        end_at,
        previous_items,
        feed_source="paper",
        research_window_strategy="exact_window",
    )
    exact_merged_candidates = merge_candidates(exact_candidates + all_candidates + paper_exact_candidates)

    paper_lookback_candidates: list[dict[str, Any]] = []
    paper_lookback_duplicates: list[dict[str, Any]] = []
    if args.paper_lookback_hours > 0:
        paper_lookback_candidates, paper_lookback_duplicates = parse_feed(
            paper_xml_text,
            paper_lookback_start_at,
            end_at,
            previous_items,
            feed_source="paper",
            research_window_strategy="paper_lookback",
        )

    direct_candidates: list[dict[str, Any]] = []
    direct_duplicates: list[dict[str, Any]] = []
    official_source_errors: list[dict[str, str]] = []
    official_source_health: list[dict[str, Any]] = []

    def fetch_official_source(source: dict[str, Any]) -> tuple[dict[str, Any], str]:
        source_path = default_official_source_path(run_date, source)
        return source, run_curl(
            str(source["url"]),
            source_path,
            max_time_seconds=int(source.get("request_timeout_seconds", 60)),
            attempts=int(source.get("request_attempts", 3)),
        )

    # Direct sources are independent network calls. Fetching them together
    # keeps daily latency bounded; each is optional and cannot break AIHot.
    with ThreadPoolExecutor(max_workers=min(4, max(1, len(official_sources)))) as executor:
        future_map = {executor.submit(fetch_official_source, source): source for source in official_sources}
        for future in as_completed(future_map):
            source = future_map[future]
            try:
                _, source_text = future.result()
                pool = str(source.get("pool", "news"))
                source_start_at = paper_lookback_start_at if pool == "research" else news_start_at
                strategy = "direct_research_lookback" if pool == "research" else "exact_window"
                # A first-party release note can be posted shortly before a
                # previous daily runs or be missed while a feed is transiently
                # unavailable.  Give only explicitly configured official
                # release sources a short catch-up window.  Previous-send
                # de-duplication still prevents already-covered releases from
                # returning in later issues.
                catchup_hours = int(source.get("catchup_lookback_hours", 0) or 0)
                if pool != "research" and catchup_hours > 0:
                    source_start_at = max(
                        lookback_floor_at,
                        min(source_start_at, end_at - timedelta(hours=catchup_hours)),
                    )
                    strategy = "official_release_catchup"
                if source.get("transport") == "rss":
                    parsed_candidates, parsed_duplicates = parse_official_rss_feed(
                        source_text,
                        source_start_at,
                        end_at,
                        previous_items,
                        source=source,
                        research_window_strategy=strategy,
                    )
                elif source.get("parser") == "anthropic_listing":
                    parsed_candidates, parsed_duplicates = parse_anthropic_listing_page(
                        source_text,
                        source_start_at,
                        end_at,
                        previous_items,
                        source=source,
                        research_window_strategy=strategy,
                    )
                elif source.get("parser") == "generic_listing":
                    parsed_candidates, parsed_duplicates = parse_generic_listing_page(
                        source_text,
                        source_start_at,
                        end_at,
                        previous_items,
                        source=source,
                        research_window_strategy=strategy,
                    )
                elif source.get("parser") == "dated_link_time_cards":
                    parsed_candidates, parsed_duplicates = parse_dated_link_time_cards(
                        source_text,
                        source_start_at,
                        end_at,
                        previous_items,
                        source=source,
                        research_window_strategy=strategy,
                    )
                elif source.get("parser") == "release_notes":
                    parsed_candidates, parsed_duplicates = parse_release_notes_page(
                        source_text,
                        source_start_at,
                        end_at,
                        previous_items,
                        source=source,
                        research_window_strategy=strategy,
                    )
                else:
                    raise SourceSelectionError(f"Unsupported official source adapter: {source.get('id')}")
            except (SourceSelectionError, ET.ParseError, OSError) as exc:
                error = {
                    "source_id": str(source.get("id", "")),
                    "url": str(source.get("url", "")),
                    "reason": str(exc),
                }
                official_source_errors.append(error)
                official_source_health.append(
                    {
                        "source_id": error["source_id"],
                        "source_name": source.get("name", source.get("id", "")),
                        "status": "error",
                        "candidate_count": 0,
                        "duplicate_count": 0,
                        "error": error["reason"],
                    }
                )
                continue
            official_source_health.append(
                {
                    "source_id": str(source.get("id", "")),
                    "source_name": source.get("name", source.get("id", "")),
                    "status": "ok" if parsed_candidates else "empty",
                    "candidate_count": len(parsed_candidates),
                    "duplicate_count": len(parsed_duplicates),
                    "error": "",
                }
            )
            direct_candidates.extend(parsed_candidates)
            direct_duplicates.extend(parsed_duplicates)

    bestblogs_candidates: list[dict[str, Any]] = []
    bestblogs_duplicates: list[dict[str, Any]] = []
    bestblogs_resolution_errors: list[dict[str, str]] = []
    if not args.no_bestblogs:
        bestblogs_xml = run_curl(args.bestblogs_feed_url, bestblogs_feed_path)
        (
            bestblogs_candidates,
            bestblogs_duplicates,
            bestblogs_resolution_errors,
        ) = parse_bestblogs_feed(
            bestblogs_xml,
            news_start_at,
            end_at,
            previous_items,
            run_date=run_date,
            max_items=args.bestblogs_max_items,
        )

    candidates = merge_candidates(
        exact_merged_candidates + paper_lookback_candidates + direct_candidates + bestblogs_candidates + hot_candidates
    )
    backlog_policy = policy_value("backlog", default={})
    backlog_summary = {"enabled": bool(backlog_policy.get("enabled", True)), "recalled": [],
                       "excluded_by_freshness": [], "excluded_by_quota": []}
    if backlog_summary["enabled"]:
        backlog = sync_backlog(Path("daily_runs"), run_date, policy=backlog_policy)
        for recalled in recall_candidates(
            backlog, run_date, start_at=lookback_floor_at, end_at=end_at,
            freshness_exclusions=backlog_summary["excluded_by_freshness"],
            quota_exclusions=backlog_summary['excluded_by_quota'],
        ):
            if not candidate_in_window(recalled, lookback_floor_at, end_at):
                backlog_summary["excluded_by_freshness"].append({
                    "id": recalled["backlog_id"], "title": recalled["title"],
                    "published_at": recalled.get("published_at", ""),
                    "reason": "Original publication time is outside the maximum lookback or unavailable.",
                })
                continue
            recalled, duplicate = prepare_candidate(recalled, previous_items)
            if not duplicate:
                candidates.append(recalled)
                backlog_summary["recalled"].append({"id": recalled["backlog_id"],
                    "title": recalled["title"], "first_seen": recalled["backlog_first_seen"]})
        candidates = merge_candidates(candidates)
    pending_discovery_audit = []
    # Every discovery adapter and recall route must satisfy the same final
    # bound before source reading or model selection (including research).
    freshness_exclusions = [
        {"title": item.get("title", ""), "primary_url": item.get("primary_url", ""),
         "published_at": item.get("published_at", ""),
         "reason": "Original publication time is outside the maximum lookback or unavailable."}
        for item in candidates if not candidate_in_window(item, lookback_floor_at, end_at)
    ]
    candidates = [item for item in candidates if candidate_in_window(item, lookback_floor_at, end_at)]
    duplicates = merge_packed_candidates(
        exact_duplicates
        + all_duplicates
        + paper_exact_duplicates
        + paper_lookback_duplicates
        + direct_duplicates
        + bestblogs_duplicates
        + hot_duplicates
    )
    if not context.DISCOVERY_ONLY:
        apply_sparse_technique_bonus(candidates)
    if not args.no_aihot_daily and daily_entries:
        daily_matches = mark_daily_features(candidates, daily_entries)
    else:
        for candidate in candidates:
            candidate["aihot_daily_featured"] = False
            candidate["aihot_daily_rank"] = None
            candidate.setdefault("score_breakdown", {})["aihot_daily_editorial_bonus"] = 0
    from source_verification import enrich_high_value_candidates
    candidates = enrich_high_value_candidates(
        candidates, policy=policy_value("source_verification", default={}),
        model_policy=policy_value("model_update_priority", default={}),
        output_dir=output_path.parent / "primary_verification",
        rescore=lambda candidate: prepare_candidate(candidate, previous_items)[0],
    )
    # Search after verification so today's unresolved important leads receive
    # the same alternate-report discovery as recalled pending items. Results
    # remain independent candidates until the normal event grouping step.
    if api_enabled:
        recovered, recovered_duplicates, pending_discovery_audit = discover_pending(
            candidates, lookback_floor_at, end_at, all_feed_path.parent / 'aihot_pending_search',
            previous_items, api_fetch, max_queries=int(api_policy.get('pending_search_queries', 4)))
        candidates = merge_candidates(candidates + recovered)
        duplicates.extend(recovered_duplicates)
    # Recompute one-time editorial bonuses after rescore, never accumulate them.
    rescored = []
    for candidate in candidates:
        candidate, duplicate = prepare_candidate(candidate, previous_items)
        if duplicate:
            duplicates.append(duplicate)
        else:
            rescored.append(candidate)
    candidates = merge_candidates(rescored)
    if not context.DISCOVERY_ONLY:
        apply_sparse_technique_bonus(candidates)
    if not args.no_aihot_daily and daily_entries:
        daily_matches = mark_daily_features(candidates, daily_entries)
    min_news_count = args.legacy_min_news_count or args.min_news_count
    semantic_mode = context.DISCOVERY_ONLY
    if semantic_mode:
        # Legacy scores above only support historical discovery adapters; they
        # cannot admit, rank, or fill a slot in the event-based production path.
        selected_research, selected_news, rejected = [], [], []
    else:
        selected_research, selected_news, rejected = select_items(
            candidates, research_count=args.research_count,
            min_news_count=min_news_count, max_news_count=args.max_news_count)

    output = (build_discovery_output if semantic_mode else build_output)(
        run_date=run_date,
        feed_url=args.feed_url,
        feed_path=feed_path,
        all_feed_url=args.all_feed_url,
        all_feed_path=all_feed_path,
        all_feed_enabled=not args.no_aihot_all,
        daily_url=daily_url,
        daily_path=daily_path,
        daily_enabled=not args.no_aihot_daily,
        daily_entries=daily_entries,
        daily_matches=daily_matches,
        daily_fetch_error=daily_fetch_error,
        paper_feed_url=args.paper_feed_url,
        paper_feed_path=paper_feed_path,
        bestblogs_feed_url=args.bestblogs_feed_url,
        bestblogs_feed_path=bestblogs_feed_path,
        bestblogs_enabled=not args.no_bestblogs,
        bestblogs_resolution_errors=bestblogs_resolution_errors,
        direct_research_sources=direct_research_sources,
        source_registry_path=args.source_registry,
        source_registry=source_registry,
        official_source_errors=official_source_errors,
        official_source_health=official_source_health,
        paper_lookback_hours=args.paper_lookback_hours,
        paper_lookback_start_at=paper_lookback_start_at,
        start_at=start_at,
        news_start_at=news_start_at,
        news_lookback_hours=args.news_lookback_hours,
        allow_wide_news_window=args.allow_wide_news_window,
        gap_audit_enabled=gap_audit_enabled,
        gap_audit_start_at=gap_audit_start_at,
        end_at=end_at,
        previous_paths=previous_paths,
        previous_items=previous_items,
        candidates=candidates,
        duplicates=duplicates,
        selected_research=selected_research,
        selected_news=selected_news,
        rejected=rejected,
        selection_policy_path=args.selection_policy,
        audience_profiles_path=args.audience_profiles,
    )
    output["window"].update(
        max_lookback_hours=max_lookback_hours,
        requested_max_lookback_hours=requested_max_lookback_hours,
        lookback_floor_at=lookback_floor_at.isoformat(),
        requested_start_at=requested_start_at.isoformat(),
    )
    output["source"]["freshness_exclusions"] = freshness_exclusions
    api_audit['http_seconds_used'] = round(int(api_policy.get('budget_seconds', 180)) - api_fetch.remaining, 3)
    output['source']['aihot_api'] = api_audit
    output['source']['aihot_hot_topics'] = hot_audit
    output['source']['pending_discovery'] = pending_discovery_audit
    if semantic_mode:
        from event_editorial_runtime import evaluate_candidates, apply_selection
        from event_editorial import news_window_eligible as event_news_window_eligible
        for candidate in candidates:
            candidate['editorial_news_window_eligible'] = event_news_window_eligible(candidate, output['window'])
        event_policy = json.loads(args.editorial_policy.read_text()) if args.editorial_policy else None
        evaluated, event_summary = evaluate_candidates(
            candidates, output_dir=output_path.parent / 'event_editorial',
            policy=event_policy, runtime_config=args.editorial_runtime_config)
        # Material retrieval may reveal a different URL/title than discovery.
        # Recheck the resolved event before admitting it or paying for prose.
        from publication_dedup import check_candidates
        evaluated, publication_check = check_candidates(evaluated, run_date)
        output['publication_dedup'] = publication_check
        output = apply_selection(output, evaluated, event_summary,
                                 research_count=args.research_count, max_news_count=args.max_news_count)
        selected_research, selected_news = output['selected_research'], output['selected_news']
        output['source']['health']['selected_provenance'] = source_mix_summary(selected_research + selected_news)
        output['source']['hot_topic_coverage'] = coverage_receipts(hot_audit, output, duplicates)
    output["backlog"] = backlog_summary
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if backlog_summary["enabled"]:
        sync_backlog(Path("daily_runs"), run_date, policy=backlog_policy)

    print(f"wrote {output_path}")
    if api_enabled:
        print(f"aihot_window_coverage={api_audit['status']} pages={api_audit.get('pages', 0)} "
              f"records={api_audit.get('item_count', 0)} hot_topics={hot_audit['status']}")
        if not api_audit['complete']:
            print('WARNING: AIHOT full-window coverage is incomplete; see source.aihot_api in the selection receipt.')
    print(
        f"candidates_after_dedupe={len(candidates)} "
        f"excluded_duplicates={len(duplicates)} "
        f"research={len(selected_research)} news={len(selected_news)}"
    )
    reading = output["selection_summary"]["reading_estimate"]
    print(
        "estimated_reading="
        f"{reading['total_minutes']:.1f} min "
        f"(research {reading['research_minutes']:.1f} + news {reading['news_minutes']:.1f}; "
        f"soft_budget {reading['soft_budget_minutes']:.1f})"
    )
    for item in selected_research:
        print(f"RESEARCH {item['scores']['final_research']}: {item['title']}")
    for item in selected_news:
        print(f"NEWS {item['scores']['final_news']}: {item['title']}")
