"""Source candidates."""
from __future__ import annotations
from typing import Any
from copy import deepcopy
from source_identity import (
    candidate_identities,
    normalize_text,
    normalize_url,
)


def direct_candidate_from_row(
    row: dict[str, str],
    *,
    source: dict[str, Any],
    research_window_strategy: str,
) -> dict[str, Any]:
    """Build the shared candidate shape used by first-party HTML adapters."""

    pool = str(source.get("pool", "news"))
    candidate: dict[str, Any] = {
        "title": row["title"],
        "published_at": row["published_at"],
        "aihot_url": "",
        "primary_url": row["url"],
        "description": row["description"] or row["title"],
        "categories": [f"Official {pool.title()}"],
        "feed_source": f"direct:{source['id']}",
        "source_name": source.get("name", source["id"]),
        "source_pool": pool,
        "source_authority": source.get("authority", "official_first_party"),
        "source_quality_tier": source.get("quality_tier", "primary"),
        "research_window_strategy": research_window_strategy,
        "research_eligible": pool == "research",
    }
    if source.get("source_type_override"):
        candidate["source_type"] = source["source_type_override"]
    return candidate


def candidate_merge_key(candidate: dict[str, Any]) -> str:
    """Merge same-message rewrites before topic diversity is applied."""

    identities = candidate_identities(candidate)
    message_identities = sorted(identity for identity in identities if identity.startswith("msg:"))
    if message_identities:
        return message_identities[0]
    return (
        normalize_url(candidate.get("primary_url"))
        or normalize_url(candidate.get("aihot_url"))
        or normalize_text(candidate.get("title", ""))
    )


def source_variant_preference(candidate: dict[str, Any]) -> tuple[int, int, int, float, float, float]:
    """Prefer a direct first-party research variant for the same source URL."""

    return (
        int(candidate.get("source_authority") == "official_research"),
        int(candidate.get("source_quality_tier") == "primary"),
        int(candidate.get("source_pool") == "research"),
        float(candidate.get("score_breakdown", {}).get("source_reliability") or 0),
        float(candidate.get("scores", {}).get("final_research") or 0),
        float(candidate.get("scores", {}).get("final_news") or 0),
    )


def merge_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    from event_editorial import candidate_urls
    merged: dict[str, dict[str, Any]] = {}
    for original in candidates:
        candidate = deepcopy(original)
        candidate.setdefault('source_time_variants', [{
            'published_at': str(candidate.get('published_at') or ''),
            'discovered_at': candidate.get('discovered_at'), 'aihot_url': candidate.get('aihot_url'),
            'feed_source': candidate.get('feed_source'),
        }])
        key = candidate_merge_key(candidate)
        if not key:
            continue
        existing = merged.get(key)
        if existing is None:
            merged[key] = candidate
            continue

        previous = existing
        times = list(previous.get('source_time_variants', []))
        for variant in candidate.get('source_time_variants', []):
            if variant not in times:
                times.append(variant)
        hot_topics = sorted(set(previous.get('aihot_hot_topics', [])) | set(candidate.get('aihot_hot_topics', [])))
        aihot_selected = bool(previous.get('aihot_selected') or candidate.get('aihot_selected'))
        backlog_fields = {k: previous.get(k) or candidate.get(k) for k in
                          ('backlog_id', 'backlog_first_seen', 'backlog_recall_lane')}
        aliases = sorted(set(candidate_urls(previous)) | set(candidate_urls(candidate)))
        existing_sources = set(str(previous.get("feed_source", "")).split("+"))
        candidate_sources = set(str(candidate.get("feed_source", "")).split("+"))
        sources = existing_sources | candidate_sources
        research_eligible = bool(previous.get("research_eligible", True)) or bool(
            candidate.get("research_eligible", True)
        )
        previous_categories = list(previous.get("categories", []))
        candidate_categories = list(candidate.get("categories", []))
        previous_source_name = previous.get("source_name", "")
        candidate_source_name = candidate.get("source_name", "")
        previous_bestblogs_url = previous.get("bestblogs_url", "")
        candidate_bestblogs_url = candidate.get("bestblogs_url", "")
        bestblogs_score = max(
            int(previous.get("bestblogs_score") or 0),
            int(candidate.get("bestblogs_score") or 0),
        )
        existing_priority = source_variant_preference(previous)
        candidate_priority = source_variant_preference(candidate)
        preferred = candidate if candidate_priority > existing_priority else previous
        if "paper" in candidate_sources and "paper" not in existing_sources:
            preferred = candidate
        if preferred is not previous:
            merged[key] = preferred
            existing = preferred
        existing["feed_source"] = "+".join(sorted(source for source in sources if source))
        existing['coverage_source_urls'] = aliases
        existing['source_time_variants'] = times
        existing['aihot_hot_topics'] = hot_topics
        existing['aihot_selected'] = aihot_selected
        existing.update({k: v for k, v in backlog_fields.items() if v})
        # Keep the preferred source's publication time. Conflicting variants
        # require review; a new receipt or repost is not a new release date.
        if len({v.get('published_at') for v in times if v.get('published_at')}) > 1:
            existing['timing_review_required'] = True
        source_names = [
            value
            for value in [previous_source_name, candidate_source_name]
            if value
        ]
        if source_names:
            existing["source_name"] = " + ".join(dict.fromkeys(source_names))
        categories = list(dict.fromkeys(previous_categories + candidate_categories))
        existing["categories"] = categories
        if previous_bestblogs_url or candidate_bestblogs_url:
            existing["bestblogs_url"] = previous_bestblogs_url or candidate_bestblogs_url
        if bestblogs_score:
            existing["bestblogs_score"] = bestblogs_score
        existing["research_eligible"] = research_eligible
        if (
            previous.get("research_window_strategy") == "exact_window"
            or candidate.get("research_window_strategy") == "exact_window"
        ):
            existing["research_window_strategy"] = "exact_window"
    return list(merged.values())


def merge_packed_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for candidate in candidates:
        key = candidate_merge_key(candidate)
        if key and key not in merged:
            merged[key] = candidate
    return list(merged.values())


def candidate_selection_key(candidate: dict[str, Any]) -> str:
    """Return one stable identity for selection across heterogeneous feeds."""

    for value in (
        candidate.get("primary_url"),
        candidate.get("aihot_url"),
        candidate.get("bestblogs_url"),
    ):
        normalized = normalize_url(value)
        if normalized:
            return normalized
    return normalize_text(candidate.get("title", ""))



def prepare_candidate(candidate, previous_items):
    """Classify and reconcile discovery records; content scoring comes later."""
    import source_selection_context as context
    if not context.DISCOVERY_ONLY:
        from source_legacy_scoring import score_candidate
        return score_candidate(candidate, previous_items)
    from source_taxonomy import (source_type_for, topic_tags_for, selection_group_for,
                                 source_quality_tier_for, apply_content_research_routing)
    from source_history import content_duplicate_check, continuity_note
    from source_identity import host_of
    candidate['primary_host'] = host_of(candidate['primary_url'])
    candidate['source_type'] = candidate.get('source_type') or source_type_for(candidate)
    candidate['topic_tags'] = topic_tags_for(candidate)
    candidate['selection_group'] = selection_group_for(candidate)
    candidate['source_quality_tier'] = source_quality_tier_for(candidate)
    for field in ('scores', 'score_breakdown', 'model_update_priority', 'total_score',
                  'industry_impact_score', 'internal_relevance_score', 'penalty'):
        candidate.pop(field, None)
    candidate['scores'] = {}
    candidate['score_breakdown'] = {}
    apply_content_research_routing(candidate)
    candidate['content_duplicate_check'] = content_duplicate_check(candidate, previous_items)
    candidate['continuity_note'] = continuity_note(candidate, previous_items)
    return candidate, dict(candidate) if candidate['content_duplicate_check']['is_duplicate'] else None


def mark_daily_features(candidates, entries):
    """Preserve discovery recommendations without turning them into score bonuses."""
    import source_selection_context as context
    if not context.DISCOVERY_ONLY:
        from source_legacy_scoring import apply_aihot_daily_signal
        return apply_aihot_daily_signal(candidates, entries)
    from source_identity import extract_aihot_id
    by_id = {x['item_id']: x for x in entries}
    matched = []
    for c in candidates:
        ids = [extract_aihot_id(c.get('aihot_url', ''))]
        ids += [extract_aihot_id(u) for u in c.get('coverage_source_urls', [])]
        entry = next((by_id[i] for i in ids if i in by_id), None)
        c.update(aihot_daily_featured=bool(entry), aihot_daily_rank=entry['rank'] if entry else None)
        if entry:
            matched.append({'rank':entry['rank'], 'title':c['title'], 'aihot_url':c.get('aihot_url',''),
                            'primary_url':c['primary_url']})
    return matched
