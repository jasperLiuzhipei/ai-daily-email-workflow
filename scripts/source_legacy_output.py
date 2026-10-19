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
from source_legacy_scoring import (
    pack_candidate,
)
from source_legacy_selection import (
    selected_reading_estimate,
)
from source_selection_constants import (
    TZ,
)
from source_selection_context import (
    active_audience_profile,
    policy_value,
)


from source_selection_output import source_mix_summary

def build_output(
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
            "gap_audit_min_score": context.GAP_AUDIT_MIN_SCORE,
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
                f"major model/security signals since last prod send, minimum score {context.GAP_AUDIT_MIN_SCORE}"
                if gap_audit_enabled and gap_audit_start_at
                else "inactive"
            ),
        },
        "scoring_policy": {
            "reader_preference_weight": policy_value("scoring", "weights", "reader_preference", default=30),
            "industry_impact_weight": policy_value("scoring", "weights", "industry_impact", default=30),
            "translation_value_weight": policy_value("scoring", "weights", "translation_value", default=25),
            "source_reliability_weight": policy_value("scoring", "weights", "source_reliability", default=10),
            "freshness_diversity_weight": policy_value("scoring", "weights", "freshness_diversity", default=5),
            "news_high_score_threshold": context.NEWS_HIGH_SCORE_THRESHOLD,
            "technique_sparse_bonus": (
                f"若当天没有技巧/方法类资讯达到 {context.NEWS_HIGH_SCORE_THRESHOLD} 分，"
                f"则给技巧/方法类候选 final_news +{context.TECHNIQUE_SPARSE_BONUS}，"
                "用于补偿大厂发布对方法内容的天然挤压。"
            ),
            "controversy_heat_bonus": (
                "爆料、泄露、丑闻、安全风险、隐私争议、开发者工具信任危机等高讨论度事件，"
                "在五项加权后可获得 3-8 分编辑加分；该加分只代表值得进入编辑视野，"
                "不代表事实已被完全证实，正文必须保留来源边界。"
            ),
            "foundation_model_priority_bonus": (
                "OpenAI/GPT、Claude、Gemini、通义/Qwen、DeepSeek、Kimi、豆包/Seed、"
                "腾讯混元、Llama、Grok 等通用大模型的大版本发布、重要能力升级、开源/API 变化、"
                "Agent 能力更新或安全风险，可获得 6 分编辑加分；普通教程、二次转载和小功能更新不自动加分。"
            ),
            "aihot_daily_editorial_signal": (
                f"AIHot Daily 命中的已验证候选可获得 +{context.AIHOT_DAILY_EDITORIAL_BONUS} 分编辑加权；"
                "它只用于提升发现与排序，不替代原始来源、时效校验或内容去重。"
            ),
            "military_geo_risk_penalty": (
                "军事、地缘冲突、战争/国防用途、误炸、Palantir/五角大楼等争议信号，"
                "默认直接下调新闻分；除非用户明确要求关注政策/安全治理，"
                "否则不优先进入面向产品、运营和内部提效的日报。"
            ),
            "controversy_heat_bonus_applied_count": sum(
                1
                for item in candidates
                if item.get("score_breakdown", {}).get("controversy_heat_bonus", 0) > 0
            ),
            "military_geo_risk_penalty_applied_count": sum(
                1
                for item in candidates
                if item.get("score_breakdown", {}).get("military_geo_risk_penalty", 0) > 0
            ),
            "foundation_model_priority_bonus_applied_count": sum(
                1
                for item in candidates
                if item.get("score_breakdown", {}).get("foundation_model_priority_bonus", 0) > 0
            ),
            "technique_sparse_bonus_applied_count": sum(
                1
                for item in candidates
                if item.get("score_breakdown", {}).get("technique_sparse_bonus") == context.TECHNIQUE_SPARSE_BONUS
            ),
            "duplicate_policy": (
                "只剔除内容级重复：同 URL、同 AIHot item、标题高度相似、"
                "同一篇论文/报告或同一条消息复述。"
                "同公司/同产品连续出现不视为重复，但必须讲清新增信息点。"
            ),
            "research_rule": "研究前沿优先论文、benchmark、方法论和能说明 Agent/模型能力边界的条目。",
            "news_rule": (
                f"行业动态目标至少 {context.DEFAULT_MIN_NEWS_COUNT} 条，默认无固定条数上限；"
                f"所有不重复、未命中硬过滤且达到 "
                f"{float(policy_value('selection', 'news_auto_include_score_threshold', default=80)):.0f} "
                "分的独立新闻自动入选；质量阈值模式不按同主题名额或阅读时长淘汰达标条目。"
                "普通新闻不使用研究回看兜底，默认只看上一期正式发送后且最多 "
                f"{news_lookback_hours} 小时内的新内容；"
                "回看只用于研究前沿。"
            ),
        },
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
                    "role": "editorial_prefilter_and_priority_signal",
                    "policy": "Only boosts matching candidates already discovered from normal feeds. It never replaces primary-source resolution, freshness checks, or duplicate filtering.",
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
            "reading_estimate": selected_reading_estimate(selected_research, selected_news),
            "research_pick": selected_research[0]["title"] if selected_research else "",
            "news_mix": " / ".join(item["selection_group"] for item in selected_news),
        },
        "config_snapshot": {
            "selection_policy": config_snapshot(context.ACTIVE_SELECTION_POLICY),
            "audience_profile_id": context.ACTIVE_AUDIENCE_PROFILE_ID,
            "audience_profile": config_snapshot(active_audience_profile()),
        },
        "selected_research": [pack_candidate(item, idx + 1) for idx, item in enumerate(selected_research)],
        "selected_news": [pack_candidate(item, idx + 1) for idx, item in enumerate(selected_news)],
        "rejected_items": rejected,
        "excluded_duplicates": duplicates,
        "all_candidates_scored": [pack_candidate(item) for item in candidates],
    }

