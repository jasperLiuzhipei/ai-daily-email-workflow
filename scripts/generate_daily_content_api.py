#!/usr/bin/env python3
"""Compose the daily email content JSON through the configured text API.

This is the deployable replacement for the former interactive editorial step.
It writes only editorial fields; date, source URL, research image path and
section defaults are anchored by deterministic scripts and versioned config.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import threading
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import replace
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from ai_daily_common import file_sha256, load_json_object, utc_now_iso, write_json_object
from ai_gateway_client import (
    GatewayError,
    chat_json,
    load_runtime_config,
    provider_snapshot,
    resolve_provider_fallbacks,
)
from daily_config import DailyConfigError, load_content_sections, load_editorial_policy
from daily_prose import POLICY_VERSION as PROSE_POLICY_VERSION, news_editorial_issues, news_prose_tier, shell_editorial_issues
from newspaper_sections import planned_news_sources
from editorial_backlog import item_urls, sync_backlog
from event_editorial import candidate_urls, event_family_key
from editorial_evidence import EVIDENCE_GUIDANCE, evidence_for_item, compact_source_excerpt


class ContentApiError(RuntimeError):
    pass


DEFAULT_SYSTEM_PROMPT = Path(__file__).resolve().parent.parent / "prompts" / "api_daily_content_system_prompt.md"
MAX_NEWS_PER_TEXT_CALL = 2
MAX_NEWS_GENERATION_WORKERS = 4
MAX_MODEL_NEWS_COUNT = 6
DEFAULT_NEWS_CARD_WORKERS = 4
DEFAULT_FALLBACK_MODEL_WORKERS = 1
MAX_NEWS_TITLE_CHARS = 64
MAX_NEWS_SOURCE_CHARS = 12000

# A source title is discovery metadata, not publishable editorial copy.  This
# is deliberately a small, deterministic blocklist: the text model is asked to
# write the title, while this guard prevents page chrome and repository slogans
# from being emitted even when a source or a model response is malformed.
RAW_TITLE_PATTERNS = (
    re.compile(r"^\s*(?:github|gitlab)\s*[-:：]", re.IGNORECASE),
    re.compile(r"\b(?:skip to content|navigation menu|sign in|read full article|read more)\b", re.IGNORECASE),
    re.compile(r"\b100\s*%", re.IGNORECASE),
    re.compile(r"[<≤]\s*\d+\s*(?:ms|毫秒)\b", re.IGNORECASE),
)
EMOJI_RE = re.compile(r"[\U0001F300-\U0001FAFF]")


def nonempty(value: Any, field: str, *, limit: int = 1600) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ContentApiError(f"Content API output is missing {field}.")
    text = value.strip()
    if len(re.sub(r"\s+", "", text)) > limit:
        raise ContentApiError(f"{field} exceeds {limit} non-whitespace characters; rewrite instead of truncating.")
    return text


def tags(value: Any, fallback: str) -> list[str]:
    result: list[str] = []
    if isinstance(value, list):
        for item in value:
            if isinstance(item, str) and item.strip() and item.strip() not in result:
                result.append(item.strip()[:36])
    if not any(item.startswith("适合") for item in result):
        result.append(fallback)
    return result[:3]


MODEL_UPDATE_ACTIONS = ["发布", "推出", "上线", "开放", "开源", "升级", "更新", "release", "launch", "introducing", "available"]
MODEL_UPDATE_ARTIFACTS = ["模型", "model", "权重", "weights", "checkpoint", "model card", "模型卡", "版本", "version", "api"]
MODEL_UPDATE_SUBJECTS = [
    "openai", "chatgpt", "gpt-", "claude", "anthropic", "gemini", "google ai", "qwen", "通义", "千问",
    "deepseek", "glm", "chatglm", "z.ai", "智谱", "minimax", "stepfun", "阶跃星辰",
    "kimi", "moonshot", "llama", "grok", "xai", "大模型", "基础模型",
]
SECURITY_EVENT_TERMS = [
    "入侵", "攻击", "漏洞", "安全事件", "网络安全", "沙箱逃逸", "零日", "越权", "未经授权",
    "zero-day", "exploit", "intrusion", "unauthorized",
]
MARKET_SIGNAL_TERMS = [
    "收入", "营收", "年化收入", "arr", "融资", "估值", "竞争", "市场份额", "财务", "投资者", "算力建设",
]
ENGINEERING_TERMS = [
    "python", "教程", "tutorial", "构建", "开发", "工作流", "workflow", "connector", "工具调用", "评估", "注册表",
    "environment", "环境", "framework", "框架", "分布式", "distributed", "开源平台", "运行", "推理", "部署", "兼容 api",
]
MODEL_UPDATE_EXCLUSIONS = [
    "ai overviews", "ai overview", "搜索结果", "搜索入口", "公开信", "立场", "澄清", "声明", "禁止", "诉讼", "lawsuit",
    "模型硬件标准", "model hardware standard", "hardware standard", "设备连接标准",
]


def source_text_for_category(item: dict[str, Any]) -> str:
    """Return the bounded editorial evidence used for section routing.

    `source_text` can contain a full scraped page, including navigation,
    privacy links, and unrelated keywords.  Those tokens must never route a
    story into a section, so category decisions use only the selected title,
    compact source excerpt, description, and tags.
    """

    return " ".join(
        str(item.get(key) or "")
        for key in ("title", "fallback_excerpt", "description", "topic_tags")
    ).lower()


def source_identity_for_category(item: dict[str, Any]) -> str:
    """Return the compact identity used for incident and market routing.

    Long source excerpts can describe a security scanner's supported
    vulnerabilities or compare a framework with competitors.  Those examples
    are not themselves a security incident or a market event.  High-impact
    sections therefore use the editorial title, short description and tags
    only; the full bounded excerpt remains useful for capability routing.
    """

    return " ".join(
        str(item.get(key) or "")
        for key in ("title", "description", "topic_tags")
    ).lower()


def has_any_term(text: str, terms: list[str]) -> bool:
    return any(term in text for term in terms)


def generation_event_key(item: dict[str, Any]) -> str:
    """Return a narrow same-edition event identity as a second dedupe gate.

    Source selection normally removes syndicated coverage. Generation still
    needs a small defensive guard because discovery feeds can describe the
    same incident with different URLs and slightly different vocabulary.
    Keep this deliberately narrow so separate updates from one company remain
    eligible for the same edition.
    """

    # Full pages often contain background incidents or unrelated links. Only
    # the discovery synopsis may establish this narrow legacy event identity.
    text = " ".join(
        str(item.get(key) or "")
        for key in ("title", "fallback_excerpt", "description")
    ).lower()
    if has_any_term(text, ["anthropic", "claude"]) and has_any_term(
        text,
        ["网络安全评估", "网络安全评测", "网络安全测试", "cybersecurity eval", "cybersecurity test"],
    ) and has_any_term(
        text,
        ["入侵", "越权", "未经授权", "真实系统", "真实互联网", "生产基础设施"],
    ):
        return "anthropic-claude-cybersecurity-evals-incident"
    return ""


def material_event_keys(item: dict[str, Any]) -> set[str]:
    """Use selection identity and source aliases before falling back to known families."""

    keys = {"url:" + url for url in candidate_urls(item)}
    event_id = str(item.get("event_id") or "").strip()
    if event_id:
        keys.add("event:" + event_id)
    family = event_family_key(item)
    if family:
        keys.add("family:" + family)
    legacy_key = generation_event_key(item)
    if legacy_key:
        keys.add("incident:" + legacy_key)
    return keys


def deduplicate_material_news(items: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Merge duplicate identity components, preserving every coverage alias.

    A secondary story may bridge two already seen sources. Resolve the full
    component before choosing its first selected item; dropping aliases at an
    intermediate duplicate would let a later rewrite survive the dedupe gate.
    """
    parents = list(range(len(items)))
    keys = [material_event_keys(item) for item in items]
    seen: dict[str, int] = {}
    counts: dict[str, int] = {}

    def root(index: int) -> int:
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index

    for index, event_keys in enumerate(keys):
        for key in event_keys:
            counts[key] = counts.get(key, 0) + 1
            if key in seen:
                left, right = root(index), root(seen[key])
                parents[max(left, right)] = min(left, right)
            else:
                seen[key] = index
    groups: dict[int, list[int]] = {}
    for index in range(len(items)):
        groups.setdefault(root(index), []).append(index)

    kept: list[dict[str, Any]] = []
    dropped: list[dict[str, Any]] = []
    for first, members in sorted(groups.items()):
        representative = deepcopy(items[first])
        if len(members) > 1:
            representative["coverage_source_urls"] = sorted({url for index in members for url in item_urls(items[index])})
            feature_roles = {items[index].get("newspaper_role") for index in members} & {"lead", "supplement"}
            if len(feature_roles) > 1:
                raise ContentApiError("Duplicate event occupies both newspaper features; reassign the two distinct section topics.")
            if feature_roles:
                representative["newspaper_role"] = next(iter(feature_roles))
        kept.append(representative)
        for source_index in members[1:]:
            item = items[source_index]
            dropped.append(
                {
                    "mode": "dropped_duplicate_event",
                    "source_index": source_index,
                    "source_title": str(item.get("title") or ""),
                    "matched_title": str(representative.get("title") or ""),
                    "event_key": next((key for key in sorted(keys[source_index]) if counts[key] > 1), ""),
                }
            )
    return kept, sorted(dropped, key=lambda item: item["source_index"])


def is_strict_model_update(item: dict[str, Any]) -> bool:
    """Return true only for a concrete, current model release/update event."""

    text = source_text_for_category(item)
    # A tool or local-runtime project can mention a model, an OpenAI-compatible
    # API, and "开源" in its tags. That is engineering context, not a release
    # of the underlying model itself.
    source_type = str(item.get("source_type") or "")
    if source_type in {"tool", "methodology"}:
        return False
    # Search signals, policy positions, and company statements may name a
    # frontier model but are not model releases. Do not treat generic web-page
    # terms such as "policy" as evidence: they frequently come from page chrome.
    if has_any_term(text, MODEL_UPDATE_EXCLUSIONS):
        return False
    if source_type == "model_release" and str(item.get("source_quality_tier") or "") in {
        "primary",
        "verified_social",
    }:
        return True
    return (
        has_any_term(text, MODEL_UPDATE_ACTIONS)
        and has_any_term(text, MODEL_UPDATE_ARTIFACTS)
        and has_any_term(text, MODEL_UPDATE_SUBJECTS)
    )


def editorial_category_for(item: dict[str, Any]) -> str:
    """Use assessed content kind; legacy cards retain historical routing."""
    assessed = item.get('editorial_assessment', {})
    if assessed.get('assessment_status') == 'complete':
        kind = assessed.get('content_kind')
        return {'model_product': 'product_release', 'engineering': 'agent_engineering',
                'research': 'methods_and_views', 'industry': 'industry_signal',
                'opinion': 'methods_and_views'}[kind]

    text = source_text_for_category(item)
    identity = source_identity_for_category(item)
    group = str(item.get("selection_group") or "")
    source_type = str(item.get("source_type") or "")
    mandatory_boundary = str(item.get("boundary") or "").strip()
    # A third-party report of a named model's capability can be important
    # enough to track, but it is not a "model update" until a release note,
    # model card, paper, or official account confirms the underlying event.
    if mandatory_boundary and "待核验" in mandatory_boundary:
        return "industry_signal"
    # Security incidents and business/market reporting must win over generic
    # mentions of agents, models, frameworks, or an upstream stale group.
    if has_any_term(identity, SECURITY_EVENT_TERMS) or group in {"security_boundary", "governance_risk"}:
        return "industry_signal"
    if group == "cost_governance":
        return "industry_signal"
    if has_any_term(identity, MARKET_SIGNAL_TERMS):
        return "industry_signal"
    # Product/tool launches can mention a model/API and carry a semantic
    # version, but the released artifact is still the product rather than a
    # foundation model. Route these before the broad release-word heuristic.
    if source_type == "product_launch":
        return "product_release"
    if is_strict_model_update(item):
        return "model_update"
    if has_any_term(text, ["ai overviews", "ai overview", "搜索结果", "search results", "搜索入口"]) and has_any_term(
        text, ["出现率", "覆盖", "曝光", "流量", "点击", "访问", "关键词", "对话式", "search"]
    ):
        return "product_distribution"
    if (
        group in {"consumer_entry", "enterprise_integration", "creative_ops", "developer_tool"}
        and has_any_term(text, MODEL_UPDATE_ACTIONS)
    ):
        return "product_release"
    # A hands-on Codex/sub-agent recipe is an engineering-practice item even
    # when its upstream source type says only "industry news".  Requiring an
    # explicit orchestration cue prevents generic developer commentary from
    # flooding this section.
    if group == "developer_tool" and has_any_term(
        text,
        ["codex", "subagent", "子代理", "delegate", "任务拆解", "审代码", "worker", "代理配置"],
    ):
        return "agent_engineering"
    has_agent_engineering_identity = has_any_term(
        text, ["agent", "智能体", "skill", "skills", "mcp", "harness", "编排"]
    )
    if (
        has_agent_engineering_identity and has_any_term(text, ENGINEERING_TERMS)
    ) or (source_type == "tool" and (has_agent_engineering_identity or has_any_term(text, ENGINEERING_TERMS))):
        return "agent_engineering"
    if group in {"methodology", "usage_research"}:
        return "methods_and_views"
    return "industry_signal"


def audience_tag(selection_group: str) -> str:
    if selection_group in {"consumer_entry", "creative_ops", "usage_research"}:
        return "适合：产品 / 运营"
    if selection_group in {"methodology", "developer_tool", "enterprise_integration", "security_boundary"}:
        return "适合：产品 / 运营中台"
    return "适合：产品 / 运营"


def source_url(item: dict[str, Any]) -> str:
    value = str(item.get("primary_url") or "").strip()
    if not value.startswith(("https://", "http://")):
        raise ContentApiError(f"Selected news rank {item.get('rank')} has no valid primary URL.")
    return value


def source_review_notes(item: dict[str, Any]) -> list[str]:
    """Collect evidence constraints as quoted input, never as publishable copy.

    Older material files stored assessment limitations in ``boundary``. Keep
    accepting them as input so a retry of a saved issue does not resurrect the
    original leak or silently lose an important uncertainty.
    """
    result: list[str] = []
    assessment = item.get("editorial_assessment") or {}
    values = [item.get("source_review_notes"), item.get("boundary")]
    if isinstance(assessment, dict):
        values.append(assessment.get("limitations"))
    for value in values:
        for note in value if isinstance(value, list) else [value]:
            if isinstance(note, str) and note.strip() and note.strip() not in result:
                result.append(note.strip())
    return result


def anchored_news_metadata(source: dict[str, Any]) -> dict[str, Any]:
    """Selection owns identity, order and presentation tier; the model does not."""
    metadata = {key: source[key] for key in ("event_id", "display_tier", "daily_rank", "newspaper_role")
                if source.get(key) is not None}
    metadata["coverage_source_urls"] = item_urls(source)
    return metadata


def source_packet(materials: dict[str, Any]) -> dict[str, Any]:
    news = materials.get("news")
    if not isinstance(news, list):
        raise ContentApiError("materials.news must be a list.")
    news = planned_news_sources(news, categories=[editorial_category_for(item) for item in news])
    items: list[dict[str, Any]] = []
    for item in news:
        if not isinstance(item, dict):
            continue
        items.append(
            {
                "source_rank": item.get("rank"),
                "title": item.get("title"),
                "primary_url": item.get("primary_url"),
                "published_at": item.get("published_at"),
                "quiet_day_admission": item.get("quiet_day_admission"),
                "source_type": item.get("source_type"),
                "selection_group": item.get("selection_group"),
                "topic_tags": item.get("topic_tags"),
                "scores": item.get("scores"),
                "fetch_status": item.get("fetch_status"),
                **evidence_for_item(item),
                "fallback_excerpt": compact_source_excerpt(item),
                "event_id": item.get("event_id"),
                "display_tier": item.get("display_tier"),
                "daily_rank": item.get("daily_rank"),
                "newspaper_role": item.get("newspaper_role"),
                "prose_tier": news_prose_tier(item),
                "source_review_notes": source_review_notes(item),
            }
        )
    if not items and (news or not materials.get('research')):
        raise ContentApiError("materials.news has no usable selected items.")
    return {"news": items}


def system_prompt(path: Path, *, category_keys: list[str], labels: list[str]) -> str:
    from email_review_feedback import with_review_feedback
    template = path.read_text(encoding="utf-8").strip()
    if not template:
        raise ContentApiError(f"Daily content system prompt is empty: {path}")
    replacements = {
        "[[EXAMPLE_CATEGORY]]": category_keys[0],
        "[[EXAMPLE_LABEL]]": labels[0],
        "[[CATEGORY_KEYS]]": json.dumps(category_keys, ensure_ascii=False),
        "[[OBSERVATION_LABELS]]": json.dumps(labels, ensure_ascii=False),
    }
    for token, value in replacements.items():
        template = template.replace(token, value)
    return with_review_feedback(template + "\n" + EVIDENCE_GUIDANCE)


def user_prompt(packet: dict[str, Any], paper: dict[str, Any] | None) -> str:
    research_context: dict[str, Any] = (
        {
            "included": True,
            "title": paper.get("title"),
            "conclusion": paper.get("conclusion"),
            "boundary": paper.get("boundary"),
        }
        if paper
        else {"included": False, "note": "今日没有入选研究前沿；不得编造或补充研究内容。"}
    )
    return (
        "请基于以下已选新闻，产出日报的行业动态、编辑简评与小结。若 research_context.included 为 true，研究前沿仅用于判断当天整体主线，不要改写它；若为 false，只围绕新闻写作。\n\n"
        "<research_context>\n"
        + json.dumps(research_context, ensure_ascii=False, indent=2)
        + "\n</research_context>\n\n<selected_news_material>\n"
        + json.dumps(packet, ensure_ascii=False, indent=2)
        + "\n</selected_news_material>"
    )


def batch_system_prompt(*, category_keys: list[str], labels: list[str],
                        prompt_path: Path = DEFAULT_SYSTEM_PROMPT) -> str:
    return system_prompt(prompt_path, category_keys=category_keys, labels=labels) + (
        "\n本次输出范围为批量新闻：仅返回 {\"news\":[新闻对象]}，"
        "每条保留对应 source_rank，不生成 editor_note、news_section 或 summary。"
    )


def batch_user_prompt(batch: list[dict[str, Any]]) -> str:
    return "请为下列选中新闻逐条生成日报卡片。\n\n<selected_news_material>\n" + json.dumps(
        {"news": batch}, ensure_ascii=False, indent=2
    ) + "\n</selected_news_material>"


def shell_system_prompt(*, prompt_path: Path = DEFAULT_SYSTEM_PROMPT) -> str:
    return system_prompt(prompt_path, category_keys=["unused"], labels=["行业视角", "工作启发"]) + (
        "\n本次输出范围为编辑简评：只依据研究上下文（可能为空）和已完成的新闻卡片，"
        "不要重新生成 news。输出且仅输出一个 JSON object："
        "{\"editor_note\":\"100-170字今日编辑简评\","
        "\"news_section\":{\"title\":\"行业动态\",\"subtitle\":\"一句栏目说明\"},"
        "\"summary_headline\":\"8-22字今日主线\",\"summary\":\"90-180字有论证的编辑小结\"}。"
        "不要编造材料之外的数字、能力、发布时间、正式上线状态、官方回应或安全结论。"
    )


def shell_user_prompt(paper: dict[str, Any] | None, news: list[dict[str, Any]]) -> str:
    compact_news = [
        {
            "title": item.get("title"),
            "one_liner": item.get("one_liner"),
            "observation": item.get("observation"),
            "boundary": item.get("boundary"),
        }
        for item in news
    ]
    return "请生成日报的编辑简评、栏目标题和小结。\n\n<research_context>\n" + json.dumps(
        (
            {
                "included": True,
                "title": paper.get("title"),
                "conclusion": paper.get("conclusion"),
                "boundary": paper.get("boundary"),
            }
            if paper
            else {"included": False, "note": "今日没有入选研究前沿；不得提及或补造研究内容。"}
        ),
        ensure_ascii=False,
        indent=2,
    ) + "\n</research_context>\n\n<news_cards>\n" + json.dumps(
        compact_news, ensure_ascii=False, indent=2
    ) + "\n</news_cards>"


def deterministic_shell(paper: dict[str, Any] | None, news: list[dict[str, Any]], default_section: dict[str, Any]) -> dict[str, Any]:
    titles = [str(item.get("title") or "").strip() for item in news if str(item.get("title") or "").strip()]
    lead_titles = "、".join(titles[:3])[:110] or "今日 AI 行业动态"
    research_title = str((paper or {}).get("title") or "").strip()[:70]
    research_note = (
        f"研究前沿聚焦“{research_title}”，与产品、模型和开发工具的变化一起，"
        "提示我们持续区分已确认发布、行业信号与仍待验证的判断。"
        if research_title
        else "本期未设置研究前沿，重点以产品、模型、治理和开发工具的行业变化为主，"
        "并持续区分已确认事实与仍待验证的判断。"
    )
    summary_research = f"研究前沿为“{research_title}”。" if research_title else "本期不设研究前沿。"
    return {
        "editor_note": (
            f"今天共筛出 {len(news)} 条行业动态，重点关注 {lead_titles}。"
            f"{research_note}"
        ),
        "news_section": {
            "title": str(default_section.get("title") or "行业动态"),
            "subtitle": str(default_section.get("subtitle") or "今日值得关注的 AI 行业变化"),
        },
        "summary_headline": f"今日主线：{titles[0][:48] if titles else (research_title or 'AI 行业动态')}",
        "summary": (
            f"今日日报覆盖 {len(news)} 条已选行业信息，{summary_research}"
            f"行业侧以 {lead_titles} 为代表；请结合每条卡片中的来源、编辑判断和适用边界理解，"
            "避免将二手信息或尚未核实的表述当作确定结论。"
        ),
    }


def source_focus(item: dict[str, Any]) -> str:
    source_type = str(item.get("source_type") or "")
    return {
        "model_release": "模型能力、开放权重或推理生态的更新",
        "product_launch": "产品入口、试用机制或用户体验的变化",
        "tool": "工具、框架或开发工作流的实践信号",
        "benchmark": "能力、安全或评测口径相关的行业信号",
        "industry_news": "企业采用与工作方式变化的行业信号",
    }.get(source_type, "AI 产品、技术或行业动态")


def source_observation(item: dict[str, Any]) -> tuple[str, str]:
    source_type = str(item.get("source_type") or "")
    if source_type in {"tool", "product_launch"}:
        return "工作启发", "若考虑引入相关产品或工具，应先核对适配范围、使用门槛与实际工作流成本，再判断是否值得投入。"
    if source_type == "model_release":
        return "行业视角", "模型与推理生态的更新更值得从兼容性、部署选择和长期可替代性来判断，而不只看单一能力表述。"
    if source_type == "benchmark":
        return "行业视角", "涉及性能或安全的表述应区分厂商声明、测试条件和独立验证，避免把单一案例直接外推为普遍结论。"
    return "行业视角", "把这条信息作为跟踪行业方向的信号，同时结合原始来源和后续验证判断其实际影响。"


def deterministic_news(materials_news: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Produce a source-grounded high-volume fallback without model inference."""

    output: list[dict[str, Any]] = []
    for item in materials_news:
        group = str(item.get("selection_group") or "")
        raw_tags = item.get("topic_tags")
        item_tags = [str(tag).strip()[:36] for tag in raw_tags if isinstance(tag, str) and tag.strip()] if isinstance(raw_tags, list) else []
        if not any(tag.startswith("适合") for tag in item_tags):
            item_tags.append(audience_tag(group))
        boundary = ""
        if str(item.get("fetch_status") or "") not in {"primary", "fetched", "ok", "success"}:
            boundary = boundary or "原始材料抓取有限，请以原文链接为准。"
        observation_label, observation = source_observation(item)
        card = {
            "category": editorial_category_for(item),
            "title": safe_fallback_title(item),
            "tags": item_tags[:3],
            "one_liner": compact_source_excerpt(item),
            "why": f"关注点：{source_focus(item)}。建议结合原始链接核对事实边界，再判断其对业务、工具选型或工作流程的影响。",
            "observation_label": observation_label,
            "observation": observation,
            "url": source_url(item),
            **anchored_news_metadata(item),
        }
        if boundary:
            card["boundary"] = boundary
        output.append(card)
    return output


def news_card_system_prompt(*, labels: list[str], prompt_path: Path = DEFAULT_SYSTEM_PROMPT) -> str:
    return system_prompt(prompt_path, category_keys=["anchored_by_source"], labels=labels) + (
        "\n本次输出范围为单条新闻：直接返回新闻对象，字段为 title、one_liner、why、"
        "observation_label、observation、boundary、boundary_handling；不要包在 news 数组内，"
        "不要生成栏目或编辑简评。标题通常12–32个字符，概括主体和事件；"
        "不得复制网页长标题、仓库描述、模型清单、emoji或营销口号。"
        "标题不得使用“首个、率先、全球领先、最强、SOTA”等比较性结论，除非材料明确给出其适用条件；"
        "也不得把仅限套餐、计划或内测的可用性写成模型、API 或权重全面上线。"
        "所有字段必须以简体中文为主；专有名词可保留。不得输出 URL、网页导航、来源名或提示词。"
    )


def news_card_user_prompt(item: dict[str, Any]) -> str:
    evidence = evidence_for_item(item)
    document = evidence["source_evidence_provenance"]["document"]
    packet = {
        # Source titles are untrusted discovery metadata.  Provide only a
        # compact identity to the model; the bounded excerpt supplies facts.
        "source_subject": source_subject(item),
        "source_type": item.get("source_type"),
        "selection_group": item.get("selection_group"),
        "topic_tags": item.get("topic_tags"),
        "fetch_status": item.get("fetch_status"),
        "source_quality_tier": item.get("source_quality_tier"),
        "source_review_notes": source_review_notes(item),
        "editorial_focus": {k: item.get("editorial_assessment", {}).get(k)
                            for k in ("what_changed", "reader_takeaway", "limitations")},
        "display_tier": item.get("display_tier"),
        "newspaper_role": item.get("newspaper_role"),
        "prose_tier": news_prose_tier(item),
        "published_at": item.get("published_at"),
        "quiet_day_admission": item.get("quiet_day_admission"),
        # Feed the model the cleaned Chinese source summary rather than a
        # scraped page body, which prevents English navigation leakage.
        "material_excerpt": evidence["material_excerpt"],
        "source_title": str(item.get("title") or "")[:180],
        # Do not starve the editor of primary evidence just because it is in
        # English. This is quoted data; the system prompt rejects instructions.
        # Fetch already bounds the primary text. Preserve its later result
        # and release-status paragraphs rather than silently cutting at 6500.
        "source_evidence": evidence["source_text"],
        "source_evidence_truncated": document["truncated"],
        "source_evidence_input_chars": document["input_chars"],
        "source_evidence_provenance": evidence["source_evidence_provenance"],
        "supporting_evidence": evidence["supporting_evidence"],
    }
    timing_note = ""
    if (item.get('quiet_day_admission') or {}).get('label') == '近期补漏':
        timing_note = "这条是近期补漏，沿用材料原始日期，不得说成今天刚发生或刚发布。"
    return "请为这条新闻生成日报卡片文案。" + timing_note + "\n\n<news_material>\n" + json.dumps(
        packet, ensure_ascii=False, indent=2
    ) + "\n</news_material>"


def editorial_text(value: Any, field: str, *, limit: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ContentApiError(f"Content API output is missing {field}.")
    text = value.strip()
    text = re.sub(r"https?://\S+", "", text)
    text = " ".join(text.split()).strip()
    chinese_chars = sum("\u4e00" <= char <= "\u9fff" for char in text)
    latin_words = len(re.findall(r"[A-Za-z][A-Za-z0-9._-]*", text))
    # A useful caveat such as “仅限企业内测。” must not be padded to pass a
    # prose-language check. Keep the stricter minimum for the actual summary.
    minimum_chinese = 4 if field == "boundary" else 8
    if chinese_chars < minimum_chinese or latin_words > chinese_chars * 0.65:
        raise ContentApiError(f"Content API output for {field} is not usable Chinese editorial copy.")
    if len(re.sub(r"\s+", "", text)) > limit:
        raise ContentApiError(f"{field} exceeds {limit} non-whitespace characters; rewrite the main point, do not truncate.")
    return text


# These narrow classes guard only explicit evidence limits when the editor
# wants to omit a separate boundary. They do not invent caveats or require all
# source notes to be printed; unfamiliar limits remain governed by the source-
# grounded prompt and the recorded editorial reason.
BOUNDARY_CONSTRAINTS = (
    ("limited_access",
     r"内测|私测|灰度|封闭测试|仅(?:限|向|对).{0,16}(?:测试用户|企业客户|部分用户)|尚未(?:全面|正式|公开)开放",
     r"内测|私测|灰度|预览|试用|封闭测试|仅(?:限|向|对).{0,20}(?:用户|客户|企业|套餐)|尚未.{0,8}(?:开放|上线)|逐步.{0,6}(?:开放|上线)"),
    ("unconfirmed",
     r"(?:未经|尚未|尚无|没有|未获|未被).{0,8}(?:证实|确认)|待核验|传闻|传言",
     r"(?:未经|尚未|尚无|没有|未获|未被).{0,8}(?:证实|确认)|待核验|传闻|传言|仍待.{0,6}(?:确认|证实)"),
    ("self_reported",
     r"(?:厂商|公司|作者|供应商).{0,5}(?:自述|声称|宣称|自测)",
     r"(?:厂商|公司|作者|供应商).{0,5}(?:自述|声称|宣称|自测|称|表示)|(?:尚待|仍待|有待|缺少|缺乏|未).{0,8}(?:验证|测试|复现)"),
    ("not_independently_verified",
     r"(?:尚未|没有|未有|缺少|缺乏|未提供|未见|未经过).{0,10}(?:独立|第三方).{0,4}(?:测试|验证|复现)|(?:未|尚未|未经)独立验证",
     r"(?:尚待|仍待|有待|需|需要|待|尚未|未|缺少|缺乏).{0,10}(?:验证|测试|复现)|(?:厂商|作者|公司).{0,5}(?:自述|称|自测)"),
    ("secondary_reporting",
     r"二手(?:报道|材料|来源|信息)|媒体转述|未经官方回应|仅.{0,6}媒体报道",
     r"据.{1,40}(?:报道|披露)|(?:媒体|报道|报告)(?:称|指出)|转述|尚未.{0,6}(?:回应|确认)"),
)


def validate_omitted_boundary(handling: dict[str, Any], source: dict[str, Any], copy: dict[str, Any]) -> None:
    notes = "\n".join(source_review_notes(source))
    # Only the visible brief fields count. An observation that the renderer
    # hides cannot carry the qualifier that makes the published claim safe.
    visible = " ".join(copy.get(field, "") for field in ("one_liner", "why"))
    if news_prose_tier(source) != "brief":
        visible += " " + copy.get("observation", "")
    for name, note_pattern, reader_pattern in BOUNDARY_CONSTRAINTS:
        if not re.search(note_pattern, notes):
            continue
        if handling["decision"] == "internal_only":
            raise ContentApiError(f"boundary_handling internal_only cannot discard the material {name} constraint; preserve it in boundary or qualify the visible summary.")
        if not re.search(reader_pattern, visible):
            raise ContentApiError(f"boundary_handling qualified_summary lacks the {name} qualifier in the visible summary; preserve the evidence limit explicitly.")


def generated_news_copy(generated: dict[str, Any], source: dict[str, Any]) -> dict[str, Any]:
    """Normalize complete model sentences, then request editorial repair if needed."""
    copy = {
        "one_liner": editorial_text(generated.get("one_liner"), "one_liner", limit=520),
        "why": editorial_text(generated.get("why"), "why", limit=520),
        "observation": "",
    }
    observation = generated.get("observation")
    if isinstance(observation, str) and observation.strip():
        copy["observation"] = editorial_text(observation, "observation", limit=560)
    elif news_prose_tier(source) != "brief":
        raise ContentApiError("Content API output is missing observation for a deep news card.")
    boundary = generated.get("boundary")
    if isinstance(boundary, str) and boundary.strip():
        copy["boundary"] = editorial_text(boundary, "boundary", limit=500)
    elif source_review_notes(source):
        handling = generated.get("boundary_handling")
        if (not isinstance(handling, dict)
                or handling.get("decision") not in {"qualified_summary", "internal_only"}
                or not isinstance(handling.get("reason"), str) or not handling["reason"].strip()):
            raise ContentApiError(
                "Source review notes contain evidence limits. Preserve material uncertainty in boundary or one_liner; "
                "when boundary is empty explain its treatment in boundary_handling (qualified_summary/internal_only, reason)."
            )
        validate_omitted_boundary(handling, source, copy)
    # Catch-up provenance is displayed text too. Include it inside the local
    # card retry budget instead of first discovering an overflow after imaging.
    from quiet_day_selection import apply_quiet_labels
    rendered = {**copy, **anchored_news_metadata(source), 'url': source_url(source)}
    apply_quiet_labels([rendered], [source])
    if rendered.get('boundary'):
        copy['boundary'] = rendered['boundary']
    issues = news_editorial_issues(rendered)
    if issues:
        raise ContentApiError("; ".join(str(issue["message"]) for issue in issues))
    return copy


def normalized_title(value: Any) -> str:
    """Normalize a title only for equality/risk checks, never for rendering."""

    return re.sub(r"[\W_]+", "", str(value or "").casefold(), flags=re.UNICODE)


def source_subject(item: dict[str, Any]) -> str:
    """Return a compact named subject without republishing a source headline."""

    parsed = urlparse(str(item.get("primary_url") or ""))
    host = parsed.netloc.lower().removeprefix("www.")
    if host in {"github.com", "gitlab.com"}:
        parts = [unquote(part).strip() for part in parsed.path.split("/") if part.strip()]
        if len(parts) >= 2:
            name = re.sub(r"[_.-]+", " ", parts[1]).strip()
            if name:
                return name.title().replace(" ", "")[:32]

    raw_title = str(item.get("title") or "")
    model_match = re.search(
        r"\b(?:glm|qwen|deepseek|gpt|claude|gemini|kimi|llama|grok)[\s_-]*\d[\w._-]*",
        raw_title,
        re.IGNORECASE,
    )
    if model_match:
        return model_match.group(0).replace("_", "-")[:32]
    return ""


def safe_fallback_title(item: dict[str, Any]) -> str:
    """Produce a conservative title when the model cannot supply one safely.

    It intentionally derives only a named product/repository identity from the
    URL or a known model identifier.  It must never use the untrusted page or
    RSS title as a publication fallback.
    """

    subject = source_subject(item)
    source_type = str(item.get("source_type") or "")
    group = str(item.get("selection_group") or "")
    if source_type == "model_release":
        label = "模型更新待核验"
    elif group in {"security_boundary", "governance_risk"}:
        label = "智能体安全动态待核验"
    elif source_type in {"tool", "methodology"} or group in {"methodology", "developer_tool"}:
        label = "开源智能体工具动态"
    else:
        label = "AI 行业动态待核验"
    return f"{subject}：{label}" if subject else label


def is_publishable_news_title(value: str, source: dict[str, Any]) -> bool:
    text = " ".join(value.split()).strip()
    chinese_chars = sum("\u4e00" <= char <= "\u9fff" for char in text)
    latin_words = len(re.findall(r"[A-Za-z][A-Za-z0-9._-]*", text))
    if not text or len(text) > MAX_NEWS_TITLE_CHARS or chinese_chars < 4:
        return False
    if latin_words > chinese_chars:
        return False
    if EMOJI_RE.search(text) or any(pattern.search(text) for pattern in RAW_TITLE_PATTERNS):
        return False
    source_title = str(source.get("title") or "")
    # A long verbatim source headline is never an editorial title.  Short
    # product/model identities are allowed, but the title still needs Chinese
    # editorial context under the checks above.
    if len(text) >= 20 and normalized_title(text) == normalized_title(source_title):
        return False
    return True


def editorial_news_title(value: Any, source: dict[str, Any]) -> tuple[str, str]:
    """Return a publishable title and its provenance for the audit record."""

    text = " ".join(str(value or "").split()).strip()
    if is_publishable_news_title(text, source):
        return text, "model"
    return safe_fallback_title(source), "safe_fallback"


def news_card_workers() -> int:
    try:
        requested = int(os.environ.get("AI_DAILY_NEWS_CARD_WORKERS", str(DEFAULT_NEWS_CARD_WORKERS)))
    except ValueError:
        requested = DEFAULT_NEWS_CARD_WORKERS
    return max(1, min(MAX_NEWS_GENERATION_WORKERS, requested))


def fallback_model_workers() -> int:
    """Bound concurrent failover calls to protect a smaller backup route.

    The primary model can be attempted concurrently for independent cards.
    When that route is down, however, all workers would otherwise switch to
    the same fallback model at once.  A gateway that has enough capacity for
    one editorial request can then return transient 5xx responses for every
    backup request.  Keep this deliberately conservative by default; it can
    be raised to two only through the job environment after the gateway has
    demonstrated that capacity.
    """

    try:
        requested = int(os.environ.get("AI_DAILY_FALLBACK_MODEL_WORKERS", str(DEFAULT_FALLBACK_MODEL_WORKERS)))
    except ValueError:
        requested = DEFAULT_FALLBACK_MODEL_WORKERS
    return max(1, min(2, requested))


def generate_individual_news_cards(
    runtimes: tuple[Any, ...] | Any,
    *,
    materials_news: list[dict[str, Any]],
    labels: list[str],
    prompt_path: Path = DEFAULT_SYSTEM_PROMPT,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Generate one editorial card per source and drop isolated failures.

    Sending all selected stories in one JSON response overloads the configured
    gateway. Per-card calls preserve AI editorial judgment while allowing an
    ordinary failed card to leave the issue without discarding successful
    cards or publishing a source/template fallback.
    """

    if not isinstance(runtimes, tuple):
        runtimes = (runtimes,)
    if not runtimes:
        raise ContentApiError("At least one text runtime is required for AI editorial generation.")
    card_system_prompt = news_card_system_prompt(labels=labels, prompt_path=prompt_path)
    materials_news = planned_news_sources(
        materials_news, categories=[editorial_category_for(item) for item in materials_news], allow_partial=True,
    )
    fallback_cards = deterministic_news(materials_news)
    card_runtimes = tuple(
        replace(runtime, timeout_seconds=min(runtime.timeout_seconds, 90), max_retries=0)
        for runtime in runtimes
    )
    fallback_slots = threading.BoundedSemaphore(fallback_model_workers())

    def generate_one(index: int) -> tuple[int, dict[str, Any] | None, dict[str, Any]]:
        source = materials_news[index]
        fallback = fallback_cards[index]
        attempts: list[dict[str, Any]] = []
        # One repair on the same route, with the exact validation feedback;
        # then move to configured failover. No unbounded editorial loop.
        repair_feedback = ""
        repair_used = False
        attempts_to_run = [(i, runtime, repair) for i, runtime in enumerate(card_runtimes)
                           for repair in (False, True)]
        for attempt_index, card_runtime, repair in attempts_to_run:
            if repair and (not repair_feedback or repair_used):
                continue
            if repair:
                repair_used = True
            prompt = news_card_user_prompt(source)
            if repair:
                prompt += ("\n上次输出没有通过文案校验。请只修复以下问题，仍仅依据上述原文："
                           + repair_feedback + "。返回完整JSON；专有名词保留，其余使用自然中文。")
            repair_feedback = ""
            try:
                # The configured primary route is safe to fan out.  Once it
                # fails, queue backup requests so a temporary 502 does not
                # turn into a thundering herd against the only working model.
                if attempt_index:
                    with fallback_slots:
                        generated, response = chat_json(
                            card_runtime,
                            system_prompt=card_system_prompt,
                            user_prompt=prompt,
                        )
                else:
                    generated, response = chat_json(
                        card_runtime,
                        system_prompt=card_system_prompt,
                        user_prompt=prompt,
                    )
                label = str(generated.get("observation_label") or "")
                if label not in labels:
                    label = fallback["observation_label"]
                title, title_mode = editorial_news_title(generated.get("title"), source)
                # A generic "待核验" fallback tells us that the model did not
                # identify an actual news event.  It is useful in diagnostics,
                # but it is not publishable editorial copy.  Retrying another
                # configured text route is safer than sending an anonymous
                # placeholder card such as "AI 行业动态待核验".
                if title_mode != "model":
                    raise ContentApiError(
                        "News card did not return a publishable Chinese editorial title; "
                        "refusing a placeholder-title fallback."
                    )
                card = {
                    **fallback,
                    "title": title,
                    "observation_label": label,
                }
                # A draft fallback must never supply publication prose. In
                # particular, source-review notes are constraints for the
                # editor, not ready-to-send reader boundaries.
                card.pop("boundary", None)
                card.update(generated_news_copy(generated, source))
                return index, card, {
                    "mode": "model",
                    "provider": provider_snapshot(card_runtime),
                    "title_mode": title_mode,
                    "fallback_model_used": attempt_index > 0,
                    "repair_used": repair_used,
                    "newspaper_role": source.get("newspaper_role"),
                    "prose_tier": news_prose_tier(source),
                    "boundary_handling": generated.get("boundary_handling"),
                    "source_evidence_provenance": evidence_for_item(source)["source_evidence_provenance"],
                    "failed_attempts": attempts,
                    "response": response,
                }
            except (GatewayError, ContentApiError, OSError) as exc:
                if isinstance(exc, ContentApiError) and not repair_used:
                    repair_feedback = str(exc)
                attempts.append({"provider": provider_snapshot(card_runtime), "reason": str(exc),
                                 "repair": repair})
        return index, None, {
            "mode": "dropped",
            "source_index": index,
            "source_title": str(source.get("title") or ""),
            "source_url": source_url(source),
            "newspaper_role": source.get("newspaper_role"),
            "scores": source.get("scores", {}),
            "attempts": attempts,
        }

    completed: list[tuple[int, dict[str, Any] | None, dict[str, Any]]] = []
    with ThreadPoolExecutor(max_workers=min(news_card_workers(), len(materials_news))) as executor:
        futures = [executor.submit(generate_one, index) for index in range(len(materials_news))]
        for future in as_completed(futures):
            completed.append(future.result())
    completed.sort(key=lambda item: item[0])
    cards = [card for _, card, _ in completed if card is not None]
    diagnostics = [diagnostic for _, card, diagnostic in completed if card is not None]
    dropped = [diagnostic for _, card, diagnostic in completed if card is None]
    return cards, {
        "mode": "per_item_ai_editorial",
        "prose_policy_version": PROSE_POLICY_VERSION,
        "system_prompt": str(prompt_path),
        "system_prompt_sha256": file_sha256(prompt_path),
        "card_count": len(cards),
        "model_card_count": len(cards),
        "fallback_card_count": 0,
        "dropped_card_count": len(dropped),
        "cards": diagnostics,
        "dropped_cards": dropped,
    }


def generate_shell_with_fallback(
    runtimes: tuple[Any, ...],
    *,
    paper: dict[str, Any] | None,
    news: list[dict[str, Any]],
    default_section: dict[str, Any],
    prompt_path: Path = DEFAULT_SYSTEM_PROMPT,
) -> tuple[dict[str, Any], dict[str, Any]]:
    attempts: list[dict[str, Any]] = []
    repair_feedback = ""
    repair_used = False
    attempts_to_run = [(index, runtime, repair) for index, runtime in enumerate(runtimes)
                       for repair in (False, True)]
    for attempt_index, runtime, repair in attempts_to_run:
        if repair and (not repair_feedback or repair_used):
            continue
        if repair:
            repair_used = True
        shell_runtime = replace(runtime, timeout_seconds=min(runtime.timeout_seconds, 90), max_retries=0)
        prompt = shell_user_prompt(paper, news)
        if repair:
            prompt += ("\n上次编辑简评未通过正文校验：" + repair_feedback
                       + "。围绕主判断重新取舍，不增加材料外信息，不截断句子；返回完整编辑简评JSON。")
        repair_feedback = ""
        try:
            shell, metadata = chat_json(
                shell_runtime,
                system_prompt=shell_system_prompt(prompt_path=prompt_path),
                user_prompt=prompt,
            )
            for field in ("editor_note", "summary_headline", "summary"):
                shell[field] = nonempty(shell.get(field), field)
            issues = shell_editorial_issues(shell)
            if issues:
                raise ContentApiError("; ".join(str(issue["message"]) for issue in issues))
            return shell, {
                "mode": "model",
                "provider": provider_snapshot(shell_runtime),
                "fallback_model_used": attempt_index > 0,
                "repair_used": repair_used,
                "failed_attempts": attempts,
                "response": metadata,
            }
        except (GatewayError, ContentApiError, OSError) as exc:
            if isinstance(exc, ContentApiError) and not repair_used:
                repair_feedback = str(exc)
            attempts.append({"provider": provider_snapshot(shell_runtime), "reason": str(exc), "repair": repair})
    return deterministic_shell(paper, news, default_section), {"mode": "deterministic_fallback", "attempts": attempts}


def anchor_batch_source_ranks(raw_news: Any, packet_batch: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Preserve explicit source identity; repair only a complete local 1..N map.

    The URL remains script-owned and normalize_news still checks every selected
    item exactly once. This only corrects the common model behavior of turning
    a batch's original source ranks into 1..N.
    """

    if not isinstance(raw_news, list) or len(raw_news) != len(packet_batch):
        raise ContentApiError("A news batch must return exactly one object for every input source.")
    input_ranks = [item.get("source_rank") for item in packet_batch]
    if any(type(rank) is not int for rank in input_ranks) or len(set(input_ranks)) != len(input_ranks):
        raise ContentApiError("news batch inputs must have unique integer source_rank values.")
    for index, item in enumerate(raw_news):
        if not isinstance(item, dict):
            raise ContentApiError(f"news batch item {index} must be an object.")
    returned_ranks = [item.get("source_rank") for item in raw_news]
    if any(type(rank) is not int for rank in returned_ranks) or len(set(returned_ranks)) != len(returned_ranks):
        raise ContentApiError("news batch output must have unique integer source_rank values.")
    if set(returned_ranks) == set(input_ranks):
        return [dict(item) for item in raw_news]
    if set(returned_ranks) == set(range(1, len(packet_batch) + 1)):
        return [{**item, "source_rank": input_ranks[item["source_rank"] - 1]} for item in raw_news]
    raise ContentApiError("news batch source ranks do not identify the selected sources.")


def generate_batched_content(
    runtime: Any,
    *,
    packet: dict[str, Any],
    materials_news: list[dict[str, Any]],
    paper: dict[str, Any] | None,
    category_keys: list[str],
    labels: list[str],
    default_section: dict[str, Any],
    prompt_path: Path = DEFAULT_SYSTEM_PROMPT,
) -> tuple[dict[str, Any], dict[str, Any]]:
    materials_news = planned_news_sources(
        materials_news, categories=[editorial_category_for(item) for item in materials_news], allow_partial=True,
    )
    packet_news = packet.get("news")
    if not isinstance(packet_news, list):
        raise ContentApiError("news packet must be a list.")
    category_set = set(category_keys)
    label_set = set(labels)
    by_rank = {item.get("rank"): item for item in materials_news if isinstance(item, dict)}
    packet_news = [
        {**item, "newspaper_role": by_rank[item.get("source_rank")].get("newspaper_role"),
         "prose_tier": news_prose_tier(by_rank[item.get("source_rank")])}
        if isinstance(item, dict) and item.get("source_rank") in by_rank else item
        for item in packet_news
    ]
    batch_specs = [
        (index, packet_news[offset : offset + MAX_NEWS_PER_TEXT_CALL])
        for index, offset in enumerate(range(0, len(packet_news), MAX_NEWS_PER_TEXT_CALL))
    ]

    def generate_one_batch(index: int, packet_batch: list[dict[str, Any]]) -> tuple[int, list[dict[str, Any]], dict[str, Any]]:
        ranks = {item.get("source_rank") for item in packet_batch if isinstance(item, dict)}
        materials_batch = [by_rank[rank] for rank in ranks if rank in by_rank]
        raw_batch, metadata = chat_json(
            runtime,
            system_prompt=batch_system_prompt(category_keys=category_keys, labels=labels, prompt_path=prompt_path),
            user_prompt=batch_user_prompt(packet_batch),
        )
        normalized = normalize_news(
            anchor_batch_source_ranks(raw_batch.get("news"), packet_batch),
            materials_batch,
            category_keys=category_set,
            allowed_labels=label_set,
        )
        return index, normalized, {"source_ranks": sorted(rank for rank in ranks if isinstance(rank, int)), "response": metadata}

    completed_batches: list[tuple[int, list[dict[str, Any]], dict[str, Any]]] = []
    with ThreadPoolExecutor(max_workers=min(MAX_NEWS_GENERATION_WORKERS, len(batch_specs))) as executor:
        futures = [executor.submit(generate_one_batch, index, batch) for index, batch in batch_specs]
        for future in as_completed(futures):
            completed_batches.append(future.result())

    completed_batches.sort(key=lambda item: item[0])
    normalized_news = [item for _, batch, _ in completed_batches for item in batch]
    batch_responses = [metadata for _, _, metadata in completed_batches]

    if len(normalized_news) != len(materials_news):
        raise ContentApiError("Batched content generation did not cover every selected news item.")
    normalized_news.sort(key=lambda item: next(index for index, source in enumerate(materials_news) if source_url(source) == item["url"]))

    shell, shell_metadata = generate_shell_with_fallback(
        (runtime,), paper=paper, news=normalized_news, default_section=default_section,
        prompt_path=prompt_path,
    )

    return {**shell, "news": normalized_news}, {"mode": "batched", "news_batches": batch_responses, "shell": shell_metadata}


def normalize_news(
    raw_news: Any,
    materials: list[dict[str, Any]],
    *,
    category_keys: set[str],
    allowed_labels: set[str],
) -> list[dict[str, Any]]:
    if not isinstance(raw_news, list):
        raise ContentApiError("Content API output is missing news array.")
    by_rank = {item.get("rank"): item for item in materials if isinstance(item, dict)}
    output: list[dict[str, Any]] = []
    used: set[int] = set()
    for index, item in enumerate(raw_news):
        if not isinstance(item, dict):
            raise ContentApiError(f"news[{index}] must be an object.")
        rank = item.get("source_rank")
        if not isinstance(rank, int) or rank not in by_rank or rank in used:
            raise ContentApiError(f"news[{index}].source_rank must map to one unique selected source.")
        used.add(rank)
        source = by_rank[rank]
        group = str(source.get("selection_group") or "")
        # The editorial model writes the card copy, but category is a
        # source-grounded deterministic decision.  Never let a syntactically
        # valid yet semantically wrong model category reach the email.
        category = editorial_category_for(source)
        if category not in category_keys:
            raise ContentApiError(f"Derived category {category!r} is missing from content_sections.")
        label = str(item.get("observation_label") or "")
        if label not in allowed_labels:
            label = "行业视角" if "行业视角" in allowed_labels else sorted(allowed_labels)[0]
        title, title_mode = editorial_news_title(item.get("title"), source)
        if title_mode != "model":
            raise ContentApiError("News batch did not return a publishable Chinese editorial title; refusing a placeholder-title fallback.")
        normalized = {
            "category": category,
            "title": title,
            "tags": tags(item.get("tags"), audience_tag(group)),
            "observation_label": label,
            **generated_news_copy(item, source),
            **anchored_news_metadata(source),
            "url": source_url(source),
        }
        output.append(normalized)
    expected = set(by_rank)
    if used != expected:
        missing = sorted(expected - used)
        raise ContentApiError(f"Content API output omitted selected news source ranks: {missing}.")
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--materials", required=True, type=Path)
    parser.add_argument("--brief-json", type=Path, help="Optional parsed research brief for a research-feature issue.")
    parser.add_argument("--date", required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--raw-output", type=Path)
    parser.add_argument("--runtime-config", type=Path)
    parser.add_argument("--system-prompt", type=Path, default=DEFAULT_SYSTEM_PROMPT)
    parser.add_argument("--content-sections", type=Path)
    parser.add_argument("--editorial-policy", type=Path)
    parser.add_argument(
        "--allow-deterministic-fallback",
        action="store_true",
        help="Permit a template-only draft if every configured text model fails. Never use for scheduled production preview.",
    )
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    outputs = [args.output, *([args.raw_output] if args.raw_output else [])]
    if any(path.exists() for path in outputs) and not args.force:
        existing = next(path for path in outputs if path.exists())
        raise SystemExit(f"Content API artifact already exists: {existing}. Use --force to overwrite.")

    try:
        materials = load_json_object(args.materials)
        paper: dict[str, Any] | None = None
        if args.brief_json:
            brief = load_json_object(args.brief_json)
            candidate = brief.get("paper")
            if not isinstance(candidate, dict):
                raise ContentApiError("brief-json.paper must be an object.")
            paper = candidate
        content_sections = load_content_sections(args.content_sections)
        editorial_policy = load_editorial_policy(args.editorial_policy)
        category_keys = [
            str(item.get("key"))
            for item in content_sections.get("news_categories", [])
            if isinstance(item, dict) and str(item.get("key") or "").strip()
        ]
        if not category_keys:
            raise ContentApiError("content_sections has no news category keys.")
        observation_config = editorial_policy.get("observation", {})
        labels = [str(item) for item in observation_config.get("allowed_labels", []) if str(item).strip()]
        if not labels:
            labels = ["行业视角", "工作启发"]
        packet = source_packet(materials)
        runtimes = resolve_provider_fallbacks("text", load_runtime_config(args.runtime_config))
        runtime = runtimes[0]
        default_section = editorial_policy.get("email_shell", {}).get("news_section", {})
        source_news = materials.get("news", [])
        if not isinstance(source_news, list):
            raise ContentApiError("materials.news must be a list.")
        source_news, duplicate_drops = deduplicate_material_news(source_news)
        # Every selected item receives an independent editorial pass. Earlier
        # versions only used this path for 7+ stories and sent a six-story
        # edition through the terse batch prompt, making the concise edition
        # less informative precisely when selection had done its job.
        if packet["news"]:
            news, card_metadata = generate_individual_news_cards(
                runtimes,
                materials_news=source_news,
                labels=labels,
                prompt_path=args.system_prompt,
            )
            from quiet_day_selection import apply_quiet_labels
            news = apply_quiet_labels(news, source_news)
            raw_output, shell_metadata = generate_shell_with_fallback(
                runtimes,
                paper=paper,
                news=news,
                default_section=default_section,
                prompt_path=args.system_prompt,
            )
            raw_output["news"] = news
            metadata = {
                **card_metadata,
                "shell": shell_metadata,
            }
            if duplicate_drops:
                all_drops = [*duplicate_drops, *metadata.get("dropped_cards", [])]
                metadata["dropped_cards"] = all_drops
                metadata["dropped_card_count"] = len(all_drops)
            if not args.allow_deterministic_fallback and (
                not news
                or card_metadata["model_card_count"] != len(news)
                or card_metadata["fallback_card_count"] != 0
                or shell_metadata.get("mode") != "model"
            ):
                raise ContentApiError(
                    "No publishable all-model news set and shell were produced; "
                    "refusing to write a template-only daily."
                )
        else:
            if not paper:
                raise ContentApiError('No news or research brief; stop without filler.')
            news = []
            raw_output, metadata = generate_shell_with_fallback(
                runtimes, paper=paper, news=news, default_section=default_section,
                prompt_path=args.system_prompt)
            if not args.allow_deterministic_fallback and metadata.get('mode') != 'model':
                raise ContentApiError('Research-only issue requires a model-written shell.')
            raw_output['news'] = []
        raw_section = raw_output.get("news_section") if isinstance(raw_output.get("news_section"), dict) else {}
        content = {
            "date": args.date,
            "editor_note": nonempty(raw_output.get("editor_note"), "editor_note"),
            "news_section": {
                "title": nonempty(raw_section.get("title") or default_section.get("title"), "news_section.title", limit=60),
                "subtitle": nonempty(
                    raw_section.get("subtitle") or default_section.get("subtitle"),
                    "news_section.subtitle",
                    limit=120,
                ),
            },
            "news": news,
            "summary_headline": nonempty(raw_output.get("summary_headline"), "summary_headline", limit=80),
            "summary": nonempty(raw_output.get("summary"), "summary", limit=1800),
            "generation": {
                "provider": provider_snapshot(runtime),
                "provider_chain": [provider_snapshot(item) for item in runtimes],
                "response": metadata,
                "source_material": str(args.materials),
                "source_material_sha256": file_sha256(args.materials),
                "selection_sha256": str(materials.get("selection_sha256") or ""),
                "research_brief": str(args.brief_json) if args.brief_json else None,
                "generated_at": utc_now_iso(),
                "system_prompt": str(args.system_prompt),
                "system_prompt_sha256": file_sha256(args.system_prompt),
                "prose_policy_version": PROSE_POLICY_VERSION,
            },
        }
        if paper:
            content["paper"] = paper
        write_json_object(args.output, content)
        if args.output.parent.name == args.date:
            ledger = sync_backlog(args.output.parent.parent, args.date)
            pending = [x["id"] for x in ledger["items"].values()
                       if x["status"] == "generation_failed"]
            print(f"Editorial backlog: {len(pending)} generation failures retained for recovery.")
        if args.raw_output:
            write_json_object(args.raw_output, raw_output)
    except (OSError, json.JSONDecodeError, DailyConfigError, GatewayError, ContentApiError) as exc:
        raise SystemExit(f"Content API generation failed: {exc}") from exc

    print(f"API daily content JSON: {args.output}")


if __name__ == "__main__":
    main()
