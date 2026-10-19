"""Historical v1 scoring; excluded from the production decision path."""
from __future__ import annotations
from typing import Any
import source_selection_context as context
from source_candidates import (
    candidate_selection_key,
)
from source_identity import (
    SourceSelectionError,
    has_any,
)
from source_legacy_scoring import (
    news_selection_score,
    pack_candidate,
)
from source_selection_constants import (
    EVENT_ACTION_PATTERNS,
    EVENT_PRODUCT_PATTERNS,
)
from source_selection_context import (
    policy_value,
)
from source_taxonomy import (
    candidate_text,
    is_high_impact_unverified_model_signal,
    is_research_eligible,
    is_scope_limited_cryptography_research,
    news_window_eligible,
)


def same_day_event_key(candidate: dict[str, Any]) -> str:
    """Collapse syndicated coverage of one event without merging company updates.

    The daily may contain several valid stories from one company. This key is
    deliberately narrow: it only joins recognizable event identities that are
    commonly republished across official posts, media summaries, and X threads.
    """

    text = candidate_text(candidate).replace("huggingface", "hugging face")
    if has_any(text, ["openai", "gpt-6", "gpt 6"]) and has_any(
        text,
        ["沙箱", "sandbox", "入侵", "攻破", "零日", "zero-day", "生产环境", "逃逸"],
    ):
        return "openai-huggingface-evaluation-security-incident"
    if has_any(text, ["anthropic", "claude"]) and has_any(
        text,
        [
            "网络安全评估",
            "网络安全评测",
            "网络安全测试",
            "cybersecurity eval",
            "cybersecurity evaluation",
            "cybersecurity test",
            "安全评估",
            "安全评测",
        ],
    ) and has_any(
        text,
        [
            "入侵",
            "越权",
            "未经授权访问",
            "三起事件",
            "三家组织",
            "three organizations",
            "真实系统",
            "真实互联网",
            "生产基础设施",
        ],
    ):
        # The official incident report and Anthropic's social summary are two
        # representations of the same evaluation event. Keep the higher-score
        # primary report, rather than showing the reader both versions.
        return "anthropic-claude-cybersecurity-evals-incident"
    if "gemini" in text and has_any(text, ["3.6 flash", "3.5 flash-lite", "3.5 flash cyber"]):
        return "gemini-flash-2026-07-release"
    if "anthropic" in text and has_any(
        text,
        ["ai-native software", "ai 原生软件", "ai原生软件", "研发安全控制", "development lifecycle"],
    ):
        return "anthropic-ai-native-sdlc-security"
    published_day = str(candidate.get("published_at", ""))[:10]
    if not published_day:
        return ""
    product = next(
        (
            product_key
            for product_key, terms in EVENT_PRODUCT_PATTERNS
            if has_any(text, terms)
        ),
        "",
    )
    action = next(
        (
            action_key
            for action_key, terms in EVENT_ACTION_PATTERNS
            if has_any(text, terms)
        ),
        "",
    )
    if product and action:
        return f"event:{product}:{action}:{published_day}"
    return ""


def estimated_reading_minutes(candidate: dict[str, Any], *, research: bool = False) -> float:
    """Estimate scan-and-understand time for the detailed email treatment.

    These are deliberately transparent editorial estimates rather than measured
    analytics. They make the "more items only when worth it" tradeoff visible
    without imposing a hard item cap.
    """

    reading = policy_value("reading", default={})
    if research:
        return float(reading.get("research_minutes", 3.0))
    group = candidate.get("selection_group", "")
    news_minutes = reading.get("news_minutes", {})
    if group in set(news_minutes.get("complex_groups", [])):
        return float(news_minutes.get("complex_minutes", 1.2))
    if group in set(news_minutes.get("standard_groups", [])):
        return float(news_minutes.get("standard_minutes", 1.0))
    return float(news_minutes.get("default", 0.9))


def selected_reading_estimate(selected_research: list[dict[str, Any]], selected_news: list[dict[str, Any]]) -> dict[str, Any]:
    research_minutes = round(sum(estimated_reading_minutes(item, research=True) for item in selected_research), 1)
    news_minutes = round(sum(estimated_reading_minutes(item) for item in selected_news), 1)
    daily_minutes = round(research_minutes + news_minutes, 1)
    return {
        "estimate_kind": "editorial_scan_and_understand_estimate",
        "research_minutes": research_minutes,
        "news_minutes": news_minutes,
        "total_minutes": daily_minutes,
        "soft_budget_minutes": context.DEFAULT_DAILY_READING_BUDGET_MINUTES,
        "over_soft_budget": daily_minutes > context.DEFAULT_DAILY_READING_BUDGET_MINUTES,
    }


def select_items(
    candidates: list[dict[str, Any]],
    research_count: int,
    min_news_count: int,
    max_news_count: int | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    configured_max = policy_value("selection", "max_news_count", default=context.DEFAULT_MAX_NEWS_COUNT)
    max_news_count = configured_max if max_news_count is None else max_news_count
    if max_news_count is None:
        max_news_count = max(1, len(candidates))
    if max_news_count < 1:
        raise SourceSelectionError("max_news_count must be at least 1.")
    min_news_count = min(min_news_count, max_news_count)
    research_relevance_floor = float(
        policy_value("selection", "research_min_internal_relevance", default=15)
    )
    research_candidates = sorted(
        [
            c
            for c in candidates
            if c["scores"]["final_research"] >= float(policy_value("selection", "research_min_score", default=70))
            and c["scores"].get("internal_relevance", 0) >= research_relevance_floor
            and is_research_eligible(c)
        ],
        key=lambda c: (
            c["scores"]["final_research"],
            c.get("score_breakdown", {}).get("source_reliability", 0),
            c.get("score_breakdown", {}).get("industry_impact", 0),
            c["scores"]["final_news"],
            c["scores"]["total"],
        ),
        reverse=True,
    )
    selected_research = research_candidates[:research_count]
    for candidate in selected_research:
        candidate["research_selection_mode"] = "standard"

    # A day with no Research Frontier item is worse than a clearly labelled,
    # high-confidence paper that narrowly misses the ordinary relevance floor.
    # This is intentionally a constrained fallback, not a second broad paper
    # feed: source quality, research score, and a lower-but-still-meaningful
    # relevance floor must all pass.
    if not selected_research and research_count > 0:
        fallback_min_score = float(
            policy_value("selection", "research_empty_slot_fallback_min_score", default=85)
        )
        fallback_min_relevance = float(
            policy_value(
                "selection",
                "research_empty_slot_fallback_min_internal_relevance",
                default=12,
            )
        )
        fallback_min_reliability = float(
            policy_value(
                "selection",
                "research_empty_slot_fallback_min_source_reliability",
                default=80,
            )
        )
        fallback_candidates = sorted(
            [
                candidate
                for candidate in candidates
                if candidate["scores"]["final_research"] >= fallback_min_score
                and candidate["scores"].get("internal_relevance", 0) >= fallback_min_relevance
                and candidate.get("score_breakdown", {}).get("source_reliability", 0)
                >= fallback_min_reliability
                and is_research_eligible(candidate)
                and not is_scope_limited_cryptography_research(candidate)
            ],
            key=lambda candidate: (
                candidate["scores"]["final_research"],
                candidate.get("score_breakdown", {}).get("source_reliability", 0),
                candidate["scores"].get("internal_relevance", 0),
                candidate["scores"]["final_news"],
                candidate["scores"]["total"],
            ),
            reverse=True,
        )
        selected_research = fallback_candidates[:research_count]
        for candidate in selected_research:
            candidate["research_selection_mode"] = "empty_slot_fallback"

    used = {candidate_selection_key(item) for item in selected_research}
    used_event_keys = {key for item in selected_research if (key := same_day_event_key(item))}

    news_min_score = float(policy_value("selection", "news_min_score", default=76))
    unverified_signal_min_score = float(
        policy_value("selection", "high_impact_unverified_signal_min_score", default=72)
    )
    news_pool = sorted(
        [
            c
            for c in candidates
            if candidate_selection_key(c) not in used
            and (
                news_selection_score(c) >= news_min_score
                or (
                    is_high_impact_unverified_model_signal(c)
                    and news_selection_score(c) >= unverified_signal_min_score
                )
            )
            and news_window_eligible(c)
        ],
        key=lambda c: (news_selection_score(c), c["scores"]["total"]),
        reverse=True,
    )

    selected_news: list[dict[str, Any]] = []
    group_counts: dict[str, int] = {}
    group_limits = {
        # Product/user-entry stories can carry different lessons on the same
        # day, e.g. Cursor mobile vs. OpenClaw mobile. Keep a small allowance
        # while preserving daily topic variety.
        "consumer_entry": 2,
        # When the day has genuinely distinct, high-quality engineering
        # methods, allow both an evaluation/quality workflow and a production
        # deployment lesson. This follows the team's stated reading preference.
        "methodology": 2,
    }
    configured_group_limits = policy_value("selection", "group_limits", default={})
    if isinstance(configured_group_limits, dict):
        group_limits.update({str(key): int(value) for key, value in configured_group_limits.items()})
    excluded_groups = set(policy_value("selection", "excluded_groups", default=["general_router"]))
    auto_include_score = float(
        policy_value("selection", "news_auto_include_score_threshold", default=80)
    )
    if policy_value("selection", "news_count_mode", default="legacy") == "quality_threshold":
        auto_include_score = news_min_score

    def can_add(item: dict[str, Any]) -> bool:
        group = item["selection_group"]
        if group in excluded_groups:
            return False
        if not policy_value("selection", "enforce_group_limits", default=True):
            return True
        group_limit = group_limits.get(group, 1)
        return group == "other" or group_counts.get(group, 0) < group_limit

    # In quality-threshold mode all distinct qualifying news enters, independent
    # of topic quotas and estimated reading time. Explicit CLI caps remain supported.
    for item in news_pool:
        if len(selected_news) >= max_news_count:
            break
        if news_selection_score(item) < auto_include_score:
            continue
        if not can_add(item):
            continue
        event_key = same_day_event_key(item)
        if event_key and event_key in used_event_keys:
            continue
        selected_news.append(item)
        used.add(candidate_selection_key(item))
        if event_key:
            used_event_keys.add(event_key)
        group = item["selection_group"]
        if group != "other":
            group_counts[group] = group_counts.get(group, 0) + 1

    # If a quiet day has fewer than the requested minimum, fill the remaining
    # slots from the normal 76+ pool while preserving topic diversity.
    for item in news_pool:
        if len(selected_news) >= min_news_count or len(selected_news) >= max_news_count:
            break
        if candidate_selection_key(item) in used:
            continue
        event_key = same_day_event_key(item)
        if event_key and event_key in used_event_keys:
            continue
        group = item["selection_group"]
        if not can_add(item):
            continue
        selected_news.append(item)
        used.add(candidate_selection_key(item))
        if event_key:
            used_event_keys.add(event_key)
        if group != "other":
            group_counts[group] = group_counts.get(group, 0) + 1

    if len(selected_news) < min_news_count:
        fallback_pool = sorted(
            [
                c
                for c in candidates
                if candidate_selection_key(c) not in used
                and news_window_eligible(c)
                and news_selection_score(c) >= float(
                    policy_value("selection", "news_fallback_min_score", default=70)
                )
            ],
            key=lambda c: (news_selection_score(c), c["scores"]["total"]),
            reverse=True,
        )
        for item in fallback_pool:
            if len(selected_news) >= max_news_count:
                break
            # Topic diversity is preferred, but the explicit minimum takes
            # precedence when a quiet day has too few distinct groups.
            if item["selection_group"] in excluded_groups:
                continue
            event_key = same_day_event_key(item)
            if event_key and event_key in used_event_keys:
                continue
            selected_news.append(item)
            used.add(candidate_selection_key(item))
            if event_key:
                used_event_keys.add(event_key)
            group = item["selection_group"]
            if group != "other":
                group_counts[group] = group_counts.get(group, 0) + 1
            if len(selected_news) >= min_news_count:
                break

    # Fill any remaining editorial capacity only with stronger, diverse items.
    for item in news_pool:
        if len(selected_news) >= max_news_count:
            break
        if candidate_selection_key(item) in used:
            continue
        event_key = same_day_event_key(item)
        if event_key and event_key in used_event_keys:
            continue
        score = news_selection_score(item)
        current_cost = selected_reading_estimate(selected_research, selected_news)
        required_score = (
            context.NEWS_OVER_BUDGET_SCORE_THRESHOLD
            if current_cost["total_minutes"] >= context.DEFAULT_DAILY_READING_BUDGET_MINUTES
            else context.NEWS_EXPANSION_SCORE_THRESHOLD
        )
        if item["selection_group"] in excluded_groups:
            continue
        high_impact_unverified_signal = is_high_impact_unverified_model_signal(item)
        if (
            score < required_score
            and not (high_impact_unverified_signal and score >= unverified_signal_min_score)
        ) or not can_add(item):
            continue
        selected_news.append(item)
        used.add(candidate_selection_key(item))
        if event_key:
            used_event_keys.add(event_key)
        group = item["selection_group"]
        if group != "other":
            group_counts[group] = group_counts.get(group, 0) + 1

    editorial_order = {
        "enterprise_integration": 0,
        "deterministic_router": 1,
        "inference_speed": 2,
        "creative_ops": 3,
        "cost_governance": 4,
        "consumer_entry": 5,
        "model_release": 6,
        "methodology": 7,
        "security_boundary": 8,
        "agent_memory": 9,
        "developer_tool": 10,
        "usage_research": 11,
        "governance_risk": 12,
        "research_frontier": 13,
        "media_product": 14,
    }
    selected_news.sort(
        key=lambda c: (
            not c.get("model_update_priority", {}).get("lead_eligible", True),
            -news_selection_score(c),
            editorial_order.get(c["selection_group"], 99),
        )
    )

    rejected: list[dict[str, Any]] = []
    selected_group_counts: dict[str, int] = {}
    for item in selected_news:
        group = item["selection_group"]
        selected_group_counts[group] = selected_group_counts.get(group, 0) + 1
    for item in sorted(
        candidates,
        key=lambda c: max(c["scores"]["final_news"], c["scores"]["final_research"]),
        reverse=True,
    ):
        if candidate_selection_key(item) in used:
            continue
        if item.get("score_breakdown", {}).get("military_geo_risk_penalty", 0) > 0:
            reason = "军事/地缘冲突类争议已降权，不优先进入面向产品、运营和内部提效的日报。"
        elif item["selection_group"] == "general_router" and selected_group_counts.get("deterministic_router", 0):
            reason = "同属模型路由，已选更有差异化的确定性路由条目，避免内容重复。"
        elif (
            policy_value("selection", "enforce_group_limits", default=True)
            and item["selection_group"] != "other"
            and selected_group_counts.get(item["selection_group"], 0) >= group_limits.get(item["selection_group"], 1)
        ):
            reason = "同主题已有更高优先级条目，避免当天内部重复。"
        elif (
            item["scores"]["final_research"]
            >= float(policy_value("selection", "research_min_score", default=70))
            and is_research_eligible(item)
            and item["scores"].get("internal_relevance", 0) < research_relevance_floor
        ):
            reason = (
                "研究分达标，但内部相关性 "
                f"{item['scores'].get('internal_relevance', 0)} 低于准入线 "
                f"{research_relevance_floor:g}。"
            )
        elif item["scores"]["final_research"] >= float(
            policy_value("selection", "research_min_score", default=70)
        ):
            reason = "研究候选达标，但已有更高优先级研究入选。"
        elif item["scores"]["final_news"] >= float(
            policy_value("selection", "news_min_score", default=76)
        ):
            reason = "新闻价值尚可，但综合优先级略低。"
        else:
            reason = "更偏工具猎奇、硬件/政策/泛生态或来源强度不足。"
        packed = pack_candidate(item)
        packed["reason"] = reason
        rejected.append(packed)

    return selected_research, selected_news, rejected

