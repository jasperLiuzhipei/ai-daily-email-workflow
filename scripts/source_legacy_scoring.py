"""Historical v1 scoring; excluded from the production decision path."""
from __future__ import annotations
from typing import Any
from model_update_priority import assess_model_update
from dataclasses import field
import source_selection_context as context
from source_history import (
    content_duplicate_check,
    continuity_note,
)
from source_identity import (
    PreviousItem,
    clean_text,
    extract_aihot_id,
    has_any,
    host_of,
)
from source_selection_constants import (
    COMMENTARY_ONLY_DISCOVERY_HOSTS,
    FOUNDATION_MODEL_KEYWORDS,
    MILITARY_GEO_RISK_KEYWORDS,
    RESEARCH_ONLY_WINDOW_STRATEGIES,
    RESEARCH_SOURCE_HOSTS,
    SECONDARY_NEWS_HOSTS,
)
from source_selection_context import (
    active_audience_profile,
    policy_value,
)
from source_taxonomy import (
    apply_content_research_routing,
    candidate_text,
    is_high_impact_unverified_model_signal,
    is_research_eligible,
    is_roundup_source,
    is_scope_limited_cryptography_research,
    is_search_augmented_content_integrity_research,
    is_secondary_news_source,
    is_technique_candidate,
    is_unverified_x_model_release,
    is_verified_applied_science_capability_research,
    looks_like_controversy_item,
    looks_like_foundation_model_priority_item,
    selection_group_for,
    source_quality_tier_for,
    source_type_for,
    topic_tags_for,
)


def apply_aihot_daily_signal(
    candidates: list[dict[str, Any]],
    daily_entries: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Apply a modest editorial boost to verified candidates featured in Daily."""

    entries_by_id = {entry["item_id"]: entry for entry in daily_entries}
    matched: list[dict[str, Any]] = []
    for candidate in candidates:
        candidate["aihot_daily_featured"] = False
        candidate["aihot_daily_rank"] = None
        candidate["aihot_daily_title"] = ""
        entry = entries_by_id.get(extract_aihot_id(candidate.get("aihot_url", "")))
        if not entry:
            candidate.setdefault("score_breakdown", {})["aihot_daily_editorial_bonus"] = 0
            continue

        candidate["aihot_daily_featured"] = True
        candidate["aihot_daily_rank"] = entry["rank"]
        candidate["aihot_daily_title"] = entry["title"]
        before = candidate["scores"]["final_news"]
        candidate["scores"]["final_news"] = round(
            min(100, before + context.AIHOT_DAILY_EDITORIAL_BONUS),
            1,
        )
        breakdown = candidate.setdefault("score_breakdown", {})
        breakdown["final_news_before_aihot_daily_bonus"] = before
        breakdown["aihot_daily_editorial_bonus"] = context.AIHOT_DAILY_EDITORIAL_BONUS
        # Editorial inclusion is not public attention and cannot bypass a
        # model-update importance ceiling.
        cap = breakdown.get("model_update_score_cap")
        if isinstance(cap, (int, float)):
            candidate["scores"]["final_news"] = min(candidate["scores"]["final_news"], cap)
        breakdown["aihot_daily_effective_bonus"] = candidate["scores"]["final_news"] - before
        matched.append(
            {
                "rank": entry["rank"],
                "title": candidate["title"],
                "aihot_url": candidate.get("aihot_url", ""),
                "primary_url": candidate.get("primary_url", ""),
            }
        )
    return matched


def military_geo_risk_penalty(candidate: dict[str, Any]) -> float:
    """Downgrade military/geopolitical controversy for this product/ops daily."""

    text = candidate_text(candidate)
    if not has_any(text, MILITARY_GEO_RISK_KEYWORDS):
        return 0.0
    return float(policy_value("scoring", "penalties", "military_geo_risk", default=22))


def base_scores(candidate: dict[str, Any]) -> tuple[int, int, int, int]:
    text = candidate_text(candidate)
    title = candidate.get("title", "").lower()
    source_type = candidate["source_type"]

    industry = 20
    brand_weights = [
        (["openai", "chatgpt"], 22),
        (["google", "gemini"], 22),
        (["anthropic", "claude"], 20),
        (["microsoft", "微软", "纳德拉", "satya nadella"], 18),
        (FOUNDATION_MODEL_KEYWORDS, 20),
        (["deepseek"], 18),
        (["runway"], 18),
        (["阿里"], 16),
        (["小红书", "redknot"], 16),
        (["cursor"], 16),
        (["wayfinder"], 14),
        (["everos"], 8),
    ]
    for words, weight in brand_weights:
        if has_any(text, words):
            industry += weight
            break
    model_priority = candidate.get("model_update_priority")
    if model_priority:
        # A long list of model names must not confer the same brand weight
        # on every launch, or borrow reach from a comparison target.
        industry = 20 + (20 if model_priority["reach_prior"] == "established" else 6)
    if source_type in {"benchmark", "paper", "report"}:
        industry += 14
    if source_type in {"tool", "product_launch"}:
        industry += 11
    if has_any(text, ["agent", "智能体", "模型路由", "router", "成本", "广告", "输入法", "投机解码", "推理引擎", "安全", "攻击", "使用节奏", "kv cache"]):
        industry += 9
    if (model_priority["lead_eligible"] if model_priority else looks_like_foundation_model_priority_item(text, title)):
        industry += 7
    if looks_like_controversy_item(text):
        industry += 8
    if has_any(text, ["芯片", "内存", "数据中心", "教育规划", "自动驾驶", "商标", "报纸起诉"]):
        industry -= 18
    industry = max(0, min(60, industry))

    relevance = 6
    if has_any(text, ["广告", "素材", "runway", "营销", "本地化", "内容生产"]):
        relevance += 12
    if has_any(text, ["输入法", "c端", "用户", "入口", "app", "体验"]):
        relevance += 11
    if has_any(text, ["router", "路由", "成本", "token", "api", "deepseek", "投机解码", "加速", "模型选择", "kv cache", "长文本"]):
        relevance += 13
    if has_any(text, ["agent", "智能体", "工作流", "runtime", "记忆", "中台", "企业", "工具", "推理引擎"]):
        relevance += 9
    if has_any(text, ["安全", "攻击", "恶意", "权限", "隐私", "评测", "benchmark", "基准",
                      "cybersecurity", "alignment assessment", "unauthorized access"]):
        relevance += 7
    if has_any(text, ["cybersecurity incidents", "unauthorized access"]):
        relevance += 9
    if is_search_augmented_content_integrity_research(candidate, text):
        # A controlled study of poisoned retrieved content maps directly to
        # product search, RAG, recommendation, and content-trust decisions.
        relevance += int(
            policy_value(
                "selection",
                "search_augmented_content_integrity_relevance_bonus",
                default=9,
            )
        )
    if is_verified_applied_science_capability_research(candidate, text):
        # This keeps empirical, first-party tests of general model capability
        # eligible for the research feature without treating generic biotech
        # news as an internal workflow signal.
        relevance += 10
    if (model_priority["lead_eligible"] if model_priority else looks_like_foundation_model_priority_item(text, title)):
        relevance += 5
    if looks_like_controversy_item(text):
        relevance += 8
    if is_scope_limited_cryptography_research(candidate):
        # Keep niche cryptography research in the candidate pool, but require
        # genuine reader fit before it can take the one research-feature slot.
        relevance = min(
            relevance,
            int(policy_value("selection", "scope_limited_research_relevance_cap", default=10)),
        )
    relevance = max(0, min(40, relevance))

    penalty = 0
    if candidate["primary_host"] in SECONDARY_NEWS_HOSTS:
        penalty += 3
    if is_roundup_source(candidate):
        # A roundup is discovery-only: its many unrelated paragraphs must not
        # accumulate enough keywords to become a publishable daily card.
        penalty += 60
    if has_any(text, ["芯片", "内存", "数据中心", "商标", "报纸起诉", "自动驾驶"]) and not has_any(text, ["agent", "模型", "工具", "广告"]):
        penalty += 12
    if has_any(text, ["artifacts 22", "生态系统广度", "融资"]):
        penalty += 8
    if len(candidate["description"]) < 60:
        penalty += 5

    return industry, relevance, penalty, industry + relevance - penalty


def final_scores(candidate: dict[str, Any]) -> tuple[float, float]:
    text = candidate_text(candidate)
    title = candidate.get("title", "").lower()
    group = candidate["selection_group"]
    source_type = candidate["source_type"]

    research = 0.0
    if source_type in {"benchmark", "paper", "methodology"}:
        research += 38
    # The research slot is deliberately not a generic "mentions benchmark"
    # contest. First-party research and paper-feed material need to outrank a
    # main-feed model-launch rewrite with similar keywords.
    if candidate.get("source_pool") == "research":
        research += 30
    if candidate.get("source_authority") == "official_research":
        research += 15
    if "paper" in str(candidate.get("feed_source", "")).split("+"):
        research += 14
    if candidate.get("primary_host") in RESEARCH_SOURCE_HOSTS:
        research += 12
    if has_any(text, ["ceo-bench", "swe-bench", "vibethinker", "benchmark", "基准", "评测", "长期策略", "reward attack"]):
        research += 28
    if group == "agent_benchmark":
        research += 28
    if has_any(text, ["agent", "智能体", "模型", "推理", "coding", "代码", "创业模拟", "500天"]):
        research += 20
    if has_any(text, ["论文", "arxiv", "研究", "报告"]):
        research += 8
    if group in {"deterministic_router", "general_router", "creative_ops", "consumer_entry"}:
        research -= 40
    if has_any(text, ["ceo-bench", "500天创业", "长期策略", "创业测试"]):
        research += 28
    if has_any(text, ["cursor", "swe-bench", "奖励攻击", "vibethinker", "推理可压缩"]):
        research += 12
    if group == "usage_research":
        # Usage/economic studies are useful background signals, but they are
        # not the default technical Research Frontier pick.
        research -= 45

    feed_sources = set(str(candidate.get("feed_source", "")).split("+"))
    content_route_reason = str(candidate.get("content_research_route_reason") or "")
    reported_benchmark_cap: float | None = None
    is_reported_result_without_research_contribution = (
        source_type in {"benchmark", "report"}
        and not content_route_reason
        and "paper" not in feed_sources
        and candidate.get("source_pool") != "research"
    )
    if is_reported_result_without_research_contribution:
        reported_benchmark_cap = float(
            policy_value(
                "selection",
                "reported_benchmark_research_score_cap",
                default=45,
            )
        )
        research = min(research, reported_benchmark_cap)

    research_before_secondary_cap = research
    secondary_research_cap: float | None = None
    is_secondary_content_routed_research = (
        content_route_reason
        in {
            "new_benchmark_or_evaluation_artifact",
            "new_technical_or_research_report",
        }
        and "paper" not in feed_sources
        and candidate.get("source_pool") != "research"
        and candidate.get("source_authority") != "official_research"
        and candidate.get("primary_host") not in RESEARCH_SOURCE_HOSTS
    )
    if is_secondary_content_routed_research:
        secondary_research_cap = float(
            policy_value("selection", "secondary_research_score_cap", default=84)
        )
        research = min(research, secondary_research_cap)

    reader_preference = reader_preference_score(candidate)
    industry = round(candidate["industry_impact_score"] / 60 * 100, 1)
    translation = translation_value_score(candidate)
    reliability = source_reliability_score(candidate)
    freshness = freshness_diversity_score(candidate)
    controversy_heat = controversy_heat_score(candidate)
    controversy_bonus = controversy_heat_bonus(controversy_heat)
    military_penalty = military_geo_risk_penalty(candidate)
    foundation_model_bonus = (
        float(policy_value("scoring", "bonuses", "foundation_model_priority", default=6))
        if looks_like_foundation_model_priority_item(text, title)
        else 0
    )
    model_priority = candidate.get("model_update_priority")
    attention_bonus = 0
    if model_priority:
        foundation_model_bonus = model_priority["priority_bonus"]
        attention_bonus = model_priority["attention_bonus"]
    weights = policy_value("scoring", "weights", default={})
    reader_weight = float(weights.get("reader_preference", 30)) / 100
    industry_weight = float(weights.get("industry_impact", 30)) / 100
    translation_weight = float(weights.get("translation_value", 25)) / 100
    reliability_weight = float(weights.get("source_reliability", 10)) / 100
    freshness_weight = float(weights.get("freshness_diversity", 5)) / 100
    news = (
        reader_preference * reader_weight
        + industry * industry_weight
        + translation * translation_weight
        + reliability * reliability_weight
        + freshness * freshness_weight
        + controversy_bonus
        + foundation_model_bonus
        + attention_bonus
        - military_penalty
    )
    if source_type in {"paper", "benchmark"} and group not in {"security_boundary", "methodology"}:
        news -= 8
    if has_any(text, ["网红", "峰哥", "分身", "菜单栏工具", "grok 私测"]):
        news -= 18
    if has_any(title, ["净创造就业", "net job creating"]):
        # Executive labour-market commentary is a useful background signal,
        # but it should not outrank observable product, workflow, or safety
        # changes in this daily's product/operations-focused news mix.
        news -= 8
    if is_unverified_x_model_release(candidate):
        news -= 12
    if has_any(text, ["芯片", "内存", "数据中心", "商标", "报纸起诉", "自动驾驶", "教育规划"]):
        news -= 12

    secondary_news_score_cap: float | None = None
    roundup_news_score_cap: float | None = None
    if is_roundup_source(candidate):
        # Roundups remain in diagnostics as discovery leads, but are never
        # eligible as one self-contained story in the final daily.
        roundup_news_score_cap = float(
            policy_value("selection", "roundup_news_score_cap", default=0)
        )
    elif is_secondary_news_source(candidate):
        # A focused media report can fill a quiet edition, yet may not bypass
        # topic diversity and source review through the 80-point auto-include
        # lane. Prefer a matching direct source whenever one is present.
        secondary_news_score_cap = float(
            policy_value("selection", "secondary_news_score_cap", default=79)
        )

    candidate["score_breakdown"] = {
        "reader_preference": round(reader_preference, 1),
        "industry_impact": industry,
        "translation_value": round(translation, 1),
        "source_reliability": round(reliability, 1),
        "freshness_diversity": round(freshness, 1),
        "controversy_heat": round(controversy_heat, 1),
        "controversy_heat_bonus": round(controversy_bonus, 1),
        "foundation_model_priority_bonus": round(foundation_model_bonus, 1),
        "model_update_priority": model_priority,
        "model_attention_bonus": attention_bonus,
        "model_update_score_cap": model_priority["score_cap"] if model_priority else None,
        "news_before_model_update_cap": round(news, 1),
        "military_geo_risk_penalty": round(military_penalty, 1),
        "unverified_x_model_release_penalty": 12 if is_unverified_x_model_release(candidate) else 0,
        "high_impact_unverified_model_signal": is_high_impact_unverified_model_signal(candidate),
        "scope_limited_research_relevance_cap": (
            int(policy_value("selection", "scope_limited_research_relevance_cap", default=10))
            if is_scope_limited_cryptography_research(candidate)
            else None
        ),
        "reported_benchmark_research_score_cap": reported_benchmark_cap,
        "final_research_before_secondary_cap": round(
            max(0, min(100, research_before_secondary_cap)),
            1,
        ),
        "secondary_research_score_cap": secondary_research_cap,
        "secondary_news_score_cap": secondary_news_score_cap,
        "roundup_news_score_cap": roundup_news_score_cap,
        "weights": {
            "reader_preference": round(reader_weight * 100, 1),
            "industry_impact": round(industry_weight * 100, 1),
            "translation_value": round(translation_weight * 100, 1),
            "source_reliability": round(reliability_weight * 100, 1),
            "freshness_diversity": round(freshness_weight * 100, 1),
        },
    }

    if model_priority:
        news = min(news, model_priority["score_cap"])
    return round(max(0, min(100, research)), 1), round(max(0, min(100, news)), 1)


def news_selection_score(candidate: dict[str, Any]) -> float:
    """Return the editorial score after provenance-only selection caps.

    Keep the raw score intact for freshness and major-event diagnostics. The
    cap is applied only when selecting a daily card, so a secondary publisher
    cannot win the auto-include lane merely through an aggregated summary.
    """

    raw_score = float(candidate.get("scores", {}).get("final_news", 0))
    host = str(candidate.get("primary_host") or host_of(candidate.get("primary_url")))
    if host in COMMENTARY_ONLY_DISCOVERY_HOSTS and candidate.get("source_quality_tier") not in {
        "primary",
        "verified_social",
    }:
        return 0.0
    breakdown = candidate.get("score_breakdown", {})
    if not isinstance(breakdown, dict):
        return raw_score
    for field in ("roundup_news_score_cap", "secondary_news_score_cap", "model_update_score_cap"):
        cap = breakdown.get(field)
        if isinstance(cap, (int, float)):
            raw_score = min(raw_score, float(cap))
    return raw_score


def apply_sparse_technique_bonus(candidates: list[dict[str, Any]]) -> int:
    """Give technique/methodology items a small lift only when they are scarce.

    The goal is not to force a technique item into every issue. It only helps
    method/practice items survive the natural attention advantage of major
    company launches when no technique item already clears the news threshold.
    """

    technique_candidates = [
        candidate
        for candidate in candidates
        if is_technique_candidate(candidate)
        and candidate.get("research_window_strategy") not in RESEARCH_ONLY_WINDOW_STRATEGIES
    ]
    if not technique_candidates:
        return 0

    has_high_score_technique = any(
        candidate["scores"]["final_news"] >= context.NEWS_HIGH_SCORE_THRESHOLD
        for candidate in technique_candidates
    )
    if has_high_score_technique:
        for candidate in technique_candidates:
            candidate.setdefault("score_breakdown", {})["technique_sparse_bonus"] = 0
        return 0

    for candidate in technique_candidates:
        old_score = candidate["scores"]["final_news"]
        candidate["scores"]["final_news"] = round(
            min(100, old_score + context.TECHNIQUE_SPARSE_BONUS),
            1,
        )
        candidate.setdefault("score_breakdown", {})["final_news_before_technique_bonus"] = old_score
        candidate["score_breakdown"]["technique_sparse_bonus"] = context.TECHNIQUE_SPARSE_BONUS
    return len(technique_candidates)


def reader_preference_score(candidate: dict[str, Any]) -> float:
    """Score against the latest reader feedback."""

    title = candidate.get("title", "").lower()
    group = candidate["selection_group"]
    profile = active_audience_profile()
    group_bases = profile.get("reader_preference_by_group", {})
    base = float(group_bases.get(group, group_bases.get("other", 45)))
    for adjustment in profile.get("title_adjustments", []):
        if not isinstance(adjustment, dict):
            continue
        terms = [str(term).lower() for term in adjustment.get("terms", [])]
        if any(term and term in title for term in terms):
            base += float(adjustment.get("delta", 0))

    return max(0, min(100, base))


def translation_value_score(candidate: dict[str, Any]) -> float:
    """How easily the item can become a useful product/ops/internal-AI observation."""

    title = candidate.get("title", "").lower()
    text = candidate_text(candidate)
    group = candidate["selection_group"]
    base = {
        "methodology": 100,
        "enterprise_integration": 94,
        "consumer_entry": 88,
        "developer_tool": 86,
        "security_boundary": 84,
        "cost_governance": 84,
        "creative_ops": 82,
        "model_release": 74,
        "usage_research": 76,
        "governance_risk": 70,
        "inference_speed": 56,
        "agent_memory": 52,
        "research_frontier": 46,
        "media_product": 46,
        "general_router": 62,
        "other": 45,
    }.get(group, 45)

    if has_any(text, ["openrouter", "claude code", "openclaw", "成本", "路由", "评估", "eval"]):
        base += 8
    if "redknot" in title or "kv cache" in title:
        base -= 12
    if "longcat" in title or "owl alpha" in title:
        base += 8
    if "everos" in title:
        base -= 10

    return max(0, min(100, base))


def source_reliability_score(candidate: dict[str, Any]) -> float:
    quality_tier = candidate.get("source_quality_tier", "")
    if quality_tier == "primary":
        return 95
    if quality_tier == "verified_social":
        # A configured organization account is a valid first-party launch
        # source, while remaining distinct from a release note or paper.
        return 88
    host = candidate["primary_host"]
    text = candidate_text(candidate)
    if host in {
        "openai.com",
        "anthropic.com",
        "claude.com",
        "blog.google",
        "deepmind.google",
        "research.google",
        "microsoft.com",
        "huggingface.co",
        "ai.meta.com",
        "cursor.com",
        "runwayml.com",
        "blog.cloudflare.com",
    }:
        return 95
    if host in {"github.com", "arxiv.org"}:
        return 92
    if host == "mp.weixin.qq.com":
        return 72
    if host == "x.com":
        return 58
    if host in SECONDARY_NEWS_HOSTS:
        # Credible enough to seed discovery, but not a substitute for a company
        # release, product page, or technical post.
        return 55
    if has_any(text, ["官方", "论文", "报告", "github", "开源"]):
        return 75
    return 60


def freshness_diversity_score(candidate: dict[str, Any]) -> float:
    title = candidate.get("title", "").lower()
    text = candidate_text(candidate)
    score = 72
    if has_any(text, ["发布", "上线", "推出", "正式可用", "公测", "alpha", "现已"]):
        score += 14
    if has_any(text, ["新增", "更新", "首次", "原生", "移动", "gateway"]):
        score += 8
    if candidate["continuity_note"]:
        score -= 8
    if "everos" in title:
        score -= 6
    return max(0, min(100, score))


def controversy_heat_score(candidate: dict[str, Any]) -> float:
    """Discussion heat for controversies, leaks, scandals, and trust events.

    This is an editorial attention signal, not a truth-confidence signal.
    High scores must still be paired with a conservative boundary.
    """

    text = candidate_text(candidate)
    if not looks_like_controversy_item(text):
        return 0

    score = 54
    if has_any(text, ["openai", "anthropic", "google", "claude", "chatgpt", "gemini", "cursor"]):
        score += 16
    if has_any(text, ["claude code", "cursor", "codex", "ai coding", "开发者工具", "编程工具"]):
        score += 12
    if has_any(text, ["隐写", "steganography", "中国用户", "识别中国", "地域识别", "后门", "监控"]):
        score += 10
    if has_any(text, ["安全", "隐私", "权限", "供应链", "审计", "隐藏代码", "脚本"]):
        score += 8
    if candidate["primary_host"] in {"mp.weixin.qq.com", "x.com"}:
        score -= 4
    return max(0, min(100, score))


def controversy_heat_bonus(score: float) -> float:
    tiers = policy_value("scoring", "bonuses", "controversy_heat_tiers", default=[])
    if not isinstance(tiers, list):
        return 0
    for tier in sorted(
        (tier for tier in tiers if isinstance(tier, dict)),
        key=lambda tier: float(tier.get("min_heat", 0)),
        reverse=True,
    ):
        if score >= float(tier.get("min_heat", 101)):
            return float(tier.get("bonus", 0))
    return 0


def business_value_for(group: str) -> str:
    values = {
        "deterministic_router": "可用于观察内部 AI 工具的本地/云端模型路由、成本控制和隐私边界。",
        "inference_speed": "对 API 产品和内部 Agent 的响应速度、吞吐、成本优化有直接参考价值。",
        "creative_ops": "贴近广告投放和运营素材生产，可观察多市场素材适配如何 API 化。",
        "cost_governance": "说明企业 AI 应用正在从“默认最强模型”转向按任务匹配模型和控制 Token 成本。",
        "consumer_entry": "可观察 AI 从聊天框进入高频输入入口后的触发时机和用户体验变化。",
        "usage_research": "适合补充 AI 使用习惯和自动化心态变化，帮助判断产品功能的真实使用场景。",
        "security_boundary": "提醒内部 Agent 工具关注权限、仓库信任、自动执行和供应链安全边界。",
        "agent_memory": "可观察 Agent 记忆、检索和技能沉淀这类基础设施化趋势。",
        "agent_benchmark": "适合用作 Agent 长周期任务和策略稳定性的边界案例。",
        "enterprise_integration": "可观察大模型进入企业云、权限和应用分发体系的方式。",
        "model_release": "适合观察国产模型、开源模型或本地模型在成本、部署和可用性上的变化。",
        "methodology": "适合沉淀内部 AI 提效的方法论，尤其是提示词调试、评估和生产可控性。",
        "developer_tool": "可观察开发者和内部提效工具的入口、协作方式和工作流形态。",
        "governance_risk": "适合提醒高风险 AI 应用需要审计、责任边界和人类复核机制。",
        "research_frontier": "适合补充前沿研究观察，但需要判断与当前产品/运营场景的距离。",
        "media_product": "适合观察 AI 信息产品形态，但内部日报优先级通常低于一手产品和技术变化。",
    }
    return values.get(group, "适合补充 AI 产品、技术和行业方向判断。")


def boundary_for(candidate: dict[str, Any]) -> str:
    if is_high_impact_unverified_model_signal(candidate):
        return (
            "该条来自第三方社交来源，尚未取得 OpenAI 官方公告、论文或技术报告佐证；"
            "仅作为待核验的高价值能力信号跟踪，不能据此确认模型发布或证明结论。"
        )
    if looks_like_controversy_item(candidate_text(candidate)):
        return "该信息属于高讨论度争议/风险信号，当前应以社区或媒体披露口径理解，需等待更多一手证据或官方回应。"
    if candidate.get("selection_group") == "security_boundary":
        return "该信息涉及安全或数据处理风险；应区分已复现行为、受影响版本和官方后续处置，避免把单一测试结果外推为通用结论。"
    if candidate["source_type"] in {"paper", "benchmark", "report"}:
        return "评测和报告结论需要看数据集、任务设定和模型范围，不能直接外推。"
    return ""


def pack_candidate(candidate: dict[str, Any], rank: int | None = None) -> dict[str, Any]:
    research_selection_mode = str(candidate.get("research_selection_mode") or "")
    if research_selection_mode == "empty_slot_fallback":
        why_selected = (
            "研究前沿空缺兜底入选：一手研究来源、研究分和最低内部相关性均达标；"
            f"最终新闻分 {candidate['scores']['final_news']} / "
            f"研究分 {candidate['scores']['final_research']}。"
        )
    else:
        why_selected = (
            f"主题组 {candidate['selection_group']}；"
            f"最终新闻分 {candidate['scores']['final_news']} / "
            f"研究分 {candidate['scores']['final_research']}。"
        )
    packed = {
        "title": candidate["title"],
        "published_at": candidate["published_at"],
        "aihot_url": candidate["aihot_url"],
        "bestblogs_url": candidate.get("bestblogs_url", ""),
        "bestblogs_score": candidate.get("bestblogs_score"),
        "source_url": candidate["primary_url"],
        "primary_url": candidate["primary_url"],
        "primary_host": candidate["primary_host"],
        "source_excerpt": clean_text(candidate.get("description", ""))[:5000],
        "source_categories": [str(category) for category in candidate.get("categories", []) if str(category).strip()],
        "source_type": candidate["source_type"],
        "feed_source": candidate.get("feed_source", ""),
        "source_name": candidate.get("source_name", ""),
        "source_pool": candidate.get("source_pool", ""),
        "source_authority": candidate.get("source_authority", ""),
        "source_quality_tier": candidate.get("source_quality_tier", "discovery"),
        "research_eligible": is_research_eligible(candidate),
        "research_window_strategy": candidate.get("research_window_strategy", ""),
        "content_research_route_reason": candidate.get("content_research_route_reason", ""),
        "gap_audit_reason": candidate.get("gap_audit_reason", ""),
        "aihot_daily_featured": bool(candidate.get("aihot_daily_featured")),
        "aihot_daily_rank": candidate.get("aihot_daily_rank"),
        "topic_tags": candidate["topic_tags"],
        "selection_group": candidate["selection_group"],
        "scores": candidate["scores"],
        "effective_news_score": news_selection_score(candidate),
        "score_breakdown": candidate.get("score_breakdown", {}),
        "research_selection_mode": research_selection_mode,
        "content_duplicate_check": candidate["content_duplicate_check"],
        "continuity_note": candidate["continuity_note"],
        "why_selected": why_selected,
        "business_value": business_value_for(candidate["selection_group"]),
    }
    boundary = boundary_for(candidate)
    for field in ("backlog_id", "backlog_first_seen", "coverage_source_urls",
                  "primary_verification", "high_value_reasons", "source_text", "discovery_excerpt"):
        if field in candidate:
            packed[field] = candidate[field]
    if boundary:
        packed["boundary"] = boundary
    if rank is not None:
        packed = {"rank": rank, **packed}
    return packed


def score_candidate(candidate: dict[str, Any], previous_items: list[PreviousItem]) -> tuple[dict[str, Any], dict[str, Any] | None]:
    candidate["primary_host"] = host_of(candidate["primary_url"])
    candidate["source_type"] = candidate.get("source_type") or source_type_for(candidate)
    candidate["topic_tags"] = topic_tags_for(candidate)
    candidate["selection_group"] = selection_group_for(candidate)
    candidate["source_quality_tier"] = source_quality_tier_for(candidate)
    candidate.pop("model_update_priority", None)
    if candidate["source_type"] == "model_release" or candidate["selection_group"] == "model_release":
        candidate["model_update_priority"] = assess_model_update(
            candidate, policy_value("model_update_priority", default={})
        )
    apply_content_research_routing(candidate)
    duplicate = content_duplicate_check(candidate, previous_items)
    candidate["content_duplicate_check"] = duplicate
    candidate["continuity_note"] = continuity_note(candidate, previous_items)

    industry, relevance, penalty, total = base_scores(candidate)
    candidate.update(
        {
            "industry_impact_score": industry,
            "internal_relevance_score": relevance,
            "penalty": penalty,
            "total_score": total,
        }
    )
    research_score, news_score = final_scores(candidate)
    candidate["scores"] = {
        "industry_impact": industry,
        "internal_relevance": relevance,
        "penalty": penalty,
        "total": total,
        "final_research": research_score,
        "final_news": news_score,
    }

    if duplicate["is_duplicate"]:
        return candidate, pack_candidate(candidate)
    return candidate, None

