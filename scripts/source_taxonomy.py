"""Source taxonomy."""
from __future__ import annotations
from typing import Any
import re
import source_selection_context as context
from source_identity import (
    clean_text,
    has_any,
    host_of,
    x_handle_from_url,
)
from source_selection_constants import (
    APPLIED_AI_SECURITY_CONTEXT_KEYWORDS,
    CONTROVERSY_SIGNAL_KEYWORDS,
    CRYPTOGRAPHY_RESEARCH_KEYWORDS,
    FOUNDATION_MODEL_KEYWORDS,
    FOUNDATION_MODEL_MAJOR_SIGNAL_KEYWORDS,
    MARKET_ADOPTION_SIGNAL_KEYWORDS,
    MILITARY_GEO_RISK_KEYWORDS,
    NARROW_CRYPTOGRAPHY_MARKERS,
    PAPER_PRIMARY_HOSTS,
    RESEARCH_ARTIFACT_ACTION_KEYWORDS,
    RESEARCH_ARTIFACT_KEYWORDS,
    RESEARCH_EVALUATION_KEYWORDS,
    RESEARCH_ONLY_WINDOW_STRATEGIES,
    RESEARCH_SOURCE_HOSTS,
    SECONDARY_NEWS_HOSTS,
    SECURITY_RISK_KEYWORDS,
    SOCIAL_OR_AGGREGATOR_HOSTS,
    SOURCE_QUALITY_TIERS,
    TECHNIQUE_KEYWORDS,
    TRUST_BOUNDARY_KEYWORDS,
)


def news_window_eligible(candidate: dict[str, Any]) -> bool:
    if candidate.get("research_window_strategy") in RESEARCH_ONLY_WINDOW_STRATEGIES:
        return False
    # Backlog recall has historically overwritten the original window label.
    # Old research remains available for the research slot, not as fresh news.
    return not (
        candidate.get("research_window_strategy") == "unpublished_backlog"
        and (candidate.get("source_pool") == "research" or candidate.get("source_type") in {"paper", "benchmark"})
    )


def is_configured_official_x_account(url: str | None) -> bool:
    return x_handle_from_url(url) in context.ACTIVE_OFFICIAL_X_HANDLES


def source_quality_tier_for(candidate: dict[str, Any]) -> str:
    """Classify provenance without treating an aggregator as a primary source."""

    configured = str(candidate.get("source_quality_tier", "")).strip()
    if configured in SOURCE_QUALITY_TIERS:
        return configured
    if is_configured_official_x_account(candidate.get("primary_url")):
        return "verified_social"
    if str(candidate.get("source_authority", "")).startswith("official_"):
        return "primary"
    return "discovery"


def is_secondary_news_source(candidate: dict[str, Any]) -> bool:
    """Whether a media/aggregation source is discovery evidence, not primary proof."""

    return str(candidate.get("primary_host") or host_of(candidate.get("primary_url"))) in SECONDARY_NEWS_HOSTS


def is_roundup_source(candidate: dict[str, Any]) -> bool:
    """Reject multi-story digests as a single news candidate.

    A page such as IT 早报 can mention dozens of unrelated launches. Scoring
    its combined prose rewards keyword volume rather than one verifiable event,
    which is exactly the opposite of the daily's source-resolution policy.
    The individual linked report may still arrive through the discovery feeds.
    """

    title_text = clean_text(str(candidate.get("title") or "")).lower()
    if re.search(r"早报|晚报|新闻汇总|资讯汇总|daily.{0,12}roundup", title_text):
        return True
    if not is_secondary_news_source(candidate):
        return False
    title = clean_text(str(candidate.get("title") or "")).lower()
    description = clean_text(str(candidate.get("description") or "")).lower()
    if "it早报" in title or "it 早报" in title:
        return True
    return bool(
        re.search(r"(?:科技|ai|it)?(?:早报|晚报|日报|快报|新闻汇总|资讯汇总)", title)
        and any(marker in description for marker in ("重要科技资讯", "今天的重要", "查看详情", "咱们明天见"))
    )


def looks_like_technique_item(text: str) -> bool:
    if has_any(text, TECHNIQUE_KEYWORDS):
        return True
    if has_any(text, ["prompt", "提示词"]) and has_any(
        text,
        ["工程", "技巧", "调试", "debug", "eval", "评估", "生产", "可控", "稳定"],
    ):
        return True
    if has_any(text, ["skill", "skills"]) and has_any(
        text,
        [
            "编写",
            "写成",
            "可预测",
            "触发器",
            "description",
            "completion",
            "no-op",
            "leading word",
            "渐进式",
            "模型注意力",
        ],
    ):
        return True
    return False


def looks_like_agent_engineering_item(text: str, title: str = "") -> bool:
    """Recognize implementation practice rather than a foundation-model release.

    A story can mention Claude, Gemini or GPT while its actual subject is a
    Skill/MCP/agent workflow.  Those are useful daily items, but are not model
    releases and must not consume a model-release slot.
    """

    combined = f"{title} {text}".lower()
    has_agent_subject = has_any(combined, ["agent", "智能体", "skill", "skills", "mcp", "harness", "编排"])
    has_engineering_signal = has_any(
        combined,
        [
            "python",
            "教程",
            "tutorial",
            "构建",
            "开发",
            "工作流",
            "workflow",
            "connector",
            "工具调用",
            "tool calling",
            "注册表",
            "registry",
            "评估",
            "evaluation",
            "可复用",
            "orchestrator",
            "environment",
            "环境",
            "framework",
            "框架",
            "分布式",
            "distributed",
            "开源平台",
        ],
    )
    return has_agent_subject and has_engineering_signal


def looks_like_search_distribution_item(text: str, title: str = "") -> bool:
    """Detect product-entry or distribution signals, not model capabilities."""

    combined = f"{title} {text}".lower()
    return has_any(combined, ["ai overviews", "ai overview", "搜索结果", "search results", "搜索入口"]) and has_any(
        combined,
        ["出现率", "覆盖", "曝光", "流量", "点击", "访问", "查询", "关键词", "对话式", "入口", "search"],
    )


def looks_like_controversy_item(text: str) -> bool:
    """High-discussion risk signal, not automatically verified truth."""

    if not has_any(text, CONTROVERSY_SIGNAL_KEYWORDS):
        return False
    if has_any(text, TRUST_BOUNDARY_KEYWORDS):
        return True
    if has_any(text, ["openai", "anthropic", "google", "claude", "chatgpt", "gemini", "cursor"]):
        return True
    return False


def looks_like_market_adoption_item(text: str, title: str = "") -> bool:
    """Recognize market/adoption reporting that only mentions a past release.

    A post-release revenue, spend, or usage-mix article often says a model was
    launched on a prior date. That historical clause must not make the item a
    new model-release event. A concrete release action in the headline still
    wins, because that is normally the item readers are being asked to assess.
    """

    title = title.lower()
    combined = f"{title} {text}".lower()
    has_foundation_subject = has_any(combined, FOUNDATION_MODEL_KEYWORDS) or has_any(
        combined, ["基础模型", "大模型"]
    )
    headline_announces_release = has_any(
        title,
        ["发布", "推出", "上线", "开放", "开源", "升级", "更新", "release", "launch", "introducing", "available"],
    )
    return (
        has_foundation_subject
        and has_any(combined, MARKET_ADOPTION_SIGNAL_KEYWORDS)
        and not headline_announces_release
    )


def source_type_for(candidate: dict[str, Any]) -> str:
    text = candidate_text(candidate)
    title = candidate.get("title", "").lower()
    if looks_like_agent_engineering_item(text, title):
        return "methodology"
    if looks_like_search_distribution_item(text, title):
        return "industry_news"
    if looks_like_market_adoption_item(text, title):
        return "industry_news"
    if looks_like_strict_model_release(text, title):
        return "model_release"
    if has_any(text, ["arxiv.org", "论文", "paper"]):
        return "paper"
    if has_any(
        text,
        [
            "ceo-bench",
            "swe-bench",
            "exploitbench",
            "vibethinker",
            "benchmark",
            "bench",
            "基准",
            "评测",
            "联合评估",
            "技术评估",
            "漏洞利用测试",
        ],
    ):
        return "benchmark"
    if has_any(text, ["economic index", "报告", "report"]):
        return "report"
    if looks_like_technique_item(text):
        return "methodology"
    if has_any(title, ["longcat", "owl alpha", "qwen 3.6"]) and looks_like_strict_model_release(text, title):
        return "model_release"
    if has_any(text, ["github.com", "开源", "router", "路由", "runtime", "运行时", "框架", "api", "recipe", "工具", "推理引擎"]):
        return "tool"
    if has_any(text, ["上线", "发布", "推出", "宣布", "预览"]):
        return "product_launch"
    return "industry_news"


def candidate_text(candidate: dict[str, Any]) -> str:
    return (
        f"{candidate.get('title', '')} {candidate.get('description', '')} "
        f"{' '.join(candidate.get('categories', []))} {candidate.get('primary_url', '')}"
    ).lower()


def is_scope_limited_cryptography_research(candidate: dict[str, Any]) -> bool:
    """Identify mathematical-cryptography studies without applied AI-security impact.

    A candidate signature scheme or reduced-round cipher can be important
    science, but it should not be promoted as an internal Agent or production
    security incident merely because its title contains "attack". Applied
    AI-security work remains eligible when it concerns permissions, code
    execution, user data, deployed systems, or an active vulnerability.
    """

    text = candidate_text(candidate)
    return (
        has_any(text, CRYPTOGRAPHY_RESEARCH_KEYWORDS)
        and has_any(text, NARROW_CRYPTOGRAPHY_MARKERS)
        and not has_any(text, APPLIED_AI_SECURITY_CONTEXT_KEYWORDS)
    )


def has_primary_research_provenance(candidate: dict[str, Any]) -> bool:
    """Whether the selected URL can carry a research claim on its own.

    Feed labels and a social post can be useful discovery signals, but neither
    is enough to make an item the daily research feature.  The research slot
    has an image and a longer explanatory card, so it needs a resolvable
    paper, technical report, research page, or first-party research source.
    """

    host = str(candidate.get("primary_host") or "")
    if host in SOCIAL_OR_AGGREGATOR_HOSTS:
        return False
    if host in PAPER_PRIMARY_HOSTS:
        return True
    return (
        candidate.get("source_pool") == "research"
        or candidate.get("source_authority") == "official_research"
    )


def content_research_route_reason(candidate: dict[str, Any]) -> str:
    """Return a high-confidence reason for routing content into research.

    Aggregator categories are useful hints, but they are not authoritative.
    A result reported from an existing benchmark is industry news, not a
    research contribution. Content routing requires a new reusable artifact
    such as a method, benchmark, dataset, or evaluation framework.
    """

    source_type = str(candidate.get("source_type") or "")
    text = candidate_text(candidate)
    feed_sources = set(str(candidate.get("feed_source", "")).split("+"))
    categories = set(candidate.get("categories", []))
    host = str(candidate.get("primary_host") or "")

    if (
        source_type == "paper"
        or "论文" in categories
        or "paper" in feed_sources
    ) and has_primary_research_provenance(candidate):
        return "paper_identity"
    introduces_research_artifact = has_any(
        text,
        RESEARCH_ARTIFACT_ACTION_KEYWORDS,
    ) and has_any(text, RESEARCH_ARTIFACT_KEYWORDS)
    has_research_provenance = (
        host in RESEARCH_SOURCE_HOSTS
        or has_any(text, RESEARCH_EVALUATION_KEYWORDS)
    )
    if source_type == "benchmark" and introduces_research_artifact and has_research_provenance:
        return "new_benchmark_or_evaluation_artifact"
    if (
        source_type == "report"
        and has_research_provenance
        and has_any(
            text,
            [
                "发布技术报告",
                "发布研究报告",
                "提出方法",
                "提出框架",
                "technical report introduces",
                "research report introduces",
            ],
        )
    ):
        return "new_technical_or_research_report"
    return ""


def apply_content_research_routing(candidate: dict[str, Any]) -> None:
    """Promote high-confidence research by content, without trusting feed labels."""

    if candidate.get("research_eligible") is False:
        candidate["content_research_route_reason"] = ""
        return
    reason = content_research_route_reason(candidate)
    candidate["content_research_route_reason"] = reason
    if reason:
        candidate["research_eligible"] = True


def looks_like_foundation_model_priority_item(text: str, title: str = "") -> bool:
    title = title.lower()
    if not has_any(title, FOUNDATION_MODEL_KEYWORDS):
        return False
    if has_any(text, ["输入法", "插件", "教程", "prompt", "提示词"]) and not has_any(
        text,
        ["大版本", "重大升级", "安全", "隐私", "权限", "数据", "风险", "漏洞", "开源", "权重", "api"],
    ):
        return False
    return has_any(text, FOUNDATION_MODEL_MAJOR_SIGNAL_KEYWORDS)


def looks_like_strict_model_release(text: str, title: str = "") -> bool:
    """Require a concrete model artifact and a release/change action.

    Mentioning a model vendor, "model" or an agent is insufficient.  This
    narrow predicate is shared by source routing and later editorial grouping
    so that a Claude tutorial or Google search-usage report cannot be labelled
    as a model update merely because it references a foundation-model company.
    """

    combined = f"{title} {text}".lower()
    if looks_like_agent_engineering_item(text, title) or looks_like_search_distribution_item(text, title):
        return False
    if has_any(
        title,
        [
            "模型硬件标准",
            "model hardware standard",
            "hardware standard",
            "设备连接标准",
        ],
    ):
        return False
    if has_any(combined, ["公开信", "立场", "澄清", "声明", "禁止", "诉讼", "lawsuit", "policy"]):
        return False
    has_release_action = has_any(
        combined,
        ["发布", "推出", "上线", "开放", "开源", "升级", "更新", "release", "launch", "introducing", "available"],
    )
    has_model_artifact = has_any(
        combined,
        ["模型", "model", "权重", "weights", "checkpoint", "model card", "模型卡", "版本", "version", "api"],
    )
    # Named image-model versions are artifacts even when a short RSS abstract
    # says "images" rather than repeating the word "model".
    has_model_artifact = has_model_artifact or bool(
        re.search(r"\b(?:chatgpt images|gpt[ -]image)[ -]*\d", combined)
    )
    has_model_subject = has_any(combined, FOUNDATION_MODEL_KEYWORDS) or has_any(combined, ["基础模型", "大模型"])
    return has_release_action and has_model_artifact and has_model_subject


def major_gap_audit_reason(candidate: dict[str, Any]) -> str:
    """Return a conservative reason for reviewing news in a send-gap blind spot."""

    text = candidate_text(candidate)
    title = str(candidate.get("title") or "").lower()
    source_type = str(candidate.get("source_type") or "")
    group = str(candidate.get("selection_group") or "")

    if source_type == "model_release" and looks_like_foundation_model_priority_item(text, title):
        return "major_foundation_model_release"
    if (
        source_type == "benchmark"
        and has_any(text, FOUNDATION_MODEL_KEYWORDS)
        and has_any(text, RESEARCH_EVALUATION_KEYWORDS)
        and has_any(
            text,
            [
                "安全",
                "漏洞",
                "cyber",
                "显著落后",
                "大幅落后",
                "能力边界",
            ],
        )
    ):
        return "major_foundation_model_capability_evaluation"
    if group == "security_boundary" and has_any(text, FOUNDATION_MODEL_KEYWORDS):
        return "major_foundation_model_security_incident"
    if looks_like_controversy_item(text) and has_any(text, SECURITY_RISK_KEYWORDS):
        return "major_ai_security_or_trust_incident"
    return ""


def is_unverified_x_model_release(candidate: dict[str, Any]) -> bool:
    """Detect third-party X reposts presented as a fresh model launch.

    A model announcement carried by an official company account can be a valid
    primary source. A repost from an analyst account is still a useful lead,
    but it must not receive the full headline-model boost until an official
    release, model card, repository, or official account is resolved.
    """

    if candidate.get("primary_host") != "x.com" or candidate.get("selection_group") != "model_release":
        return False
    return not is_configured_official_x_account(candidate.get("primary_url"))


def is_high_impact_unverified_model_signal(candidate: dict[str, Any]) -> bool:
    """Keep exceptional capability reports visible without presenting them as releases.

    Third-party social posts remain insufficient evidence for a model launch or
    a research-frontier conclusion.  A narrowly defined class of concrete,
    high-impact capability claims is still worth a clearly labelled daily
    watch item when the normal edition has spare capacity.  This prevents a
    valuable lead such as a named model reportedly solving multiple maths
    problems from being dropped solely because no official post has appeared
    yet, while retaining the normal penalty and a mandatory verification note.
    """

    if not is_unverified_x_model_release(candidate):
        return False
    text = candidate_text(candidate).lower()
    title = str(candidate.get("title") or "").lower()
    has_named_model = has_any(title, ["astra", "model", "模型", "gpt", "claude", "gemini", "deepseek", "qwen"])
    has_capability_milestone = has_any(
        text,
        [
            "数学难题",
            "数学问题",
            "理论计算机科学",
            "证明",
            "proof",
            "mathematics",
            "theoretical computer science",
            "major breakthrough",
            "重大突破",
        ],
    )
    return has_named_model and has_capability_milestone


def topic_tags_for(candidate: dict[str, Any]) -> list[str]:
    text = candidate_text(candidate)
    rules = [
        ("通用大模型", FOUNDATION_MODEL_KEYWORDS),
        ("技巧方法", TECHNIQUE_KEYWORDS),
        ("Agent 评测", ["ceo-bench", "智能体", "agent", "长期策略", "500天"]),
        ("模型路由", ["wayfinder", "router", "路由", "模型选择"]),
        ("成本治理", ["成本", "账单", "deepseek", "token", "路由"]),
        ("内容生产", ["runway", "广告", "素材", "本地化", "recipe", "营销"]),
        ("C 端入口", ["输入法", "app", "用户入口", "macos", "用户"]),
        ("推理加速", ["dspark", "投机解码", "加速", "kv cache", "长文本加速", "推理引擎"]),
        ("安全边界", ["claude code", "攻击", "恶意", "隐藏代码", "漏洞", "完全控制", "隐写", "中国用户", "供应链", "密钥", "静默上传", "代码库"]),
        ("争议信号", CONTROVERSY_SIGNAL_KEYWORDS),
        ("使用研究", ["economic index", "使用节奏", "工作日", "周末"]),
        ("开源工具", ["github", "开源", "runtime", "运行时", "everos"]),
        ("AI Coding", ["cursor", "swe-bench", "coding", "代码"]),
    ]
    tags = [name for name, words in rules if has_any(text, words)]
    return tags[:3] or ["AI 行业"]


def selection_group_for(candidate: dict[str, Any]) -> str:
    text = candidate_text(candidate)
    title = candidate.get("title", "").lower()
    if has_any(text, ["cybersecurity incidents", "alignment assessment", "unauthorized access"]):
        return "security_boundary"
    # Classify distinct editorial subjects before applying the broad
    # foundation-model bucket. Otherwise a security incident, a Codex update,
    # and a real model release all collapse into one group and diversity rules
    # hide useful daily signals.
    if has_any(title, ["医疗评估", "medical evaluation", "医学评估"]):
        return "research_frontier"
    if has_any(title, ["起诉", "诉讼", "窃密案", "trade secret", "lawsuit"]):
        return "governance_risk"
    if has_any(title, ["纳德拉", "satya nadella"]) and has_any(
        text,
        ["反向信息悖论", "企业知识", "组织记忆", "评估", "知识产权"],
    ):
        return "enterprise_integration"
    if has_any(title, ["altman", "奥特曼"]) and has_any(
        text,
        ["知识", "信息悖论", "就业", "jobs", "job-creating", "创造就业"],
    ):
        return "governance_risk"
    if looks_like_search_distribution_item(text, title):
        return "consumer_entry"
    if looks_like_agent_engineering_item(text, title):
        return "methodology"
    if looks_like_market_adoption_item(text, title):
        return "cost_governance"
    if has_any(
        title,
        [
            "模型硬件标准",
            "model hardware standard",
            "hardware standard",
            "设备连接标准",
        ],
    ):
        return "enterprise_integration"
    if candidate.get("source_type") == "model_release" and looks_like_strict_model_release(text, title):
        return "model_release"
    if has_any(text, MILITARY_GEO_RISK_KEYWORDS):
        return "governance_risk"
    if is_scope_limited_cryptography_research(candidate):
        return "research_frontier"
    if has_any(text, SECURITY_RISK_KEYWORDS + ["密钥", "静默上传", "代码库", "git 历史"]):
        return "security_boundary"
    if has_any(title, ["skillgrade", "skill 测试", "skills 测试"]):
        return "methodology"
    if has_any(title, ["gpt 5.6 sol 保留", "gpt-5.6 sol 保留"]):
        return "developer_tool"
    if has_any(title, ["oran ge"]) and has_any(text, ["定价", "竞争", "pricing"]):
        return "media_product"
    if has_any(title, ["cliproxyapi", "后端模型切换", "模型切换方法"]):
        return "developer_tool"
    if has_any(title, ["codex", "chatgpt work"]) and has_any(text, ["更新", "限制", "额度", "work", "上线"]):
        return "developer_tool"
    if has_any(text, ["apple 智能", "apple intelligence", "国行 iphone"]) and has_any(
        text,
        ["千问", "qwen", "备案", "集成"],
    ):
        return "consumer_entry"
    if looks_like_strict_model_release(text, title):
        return "model_release"
    if "wayfinder" in title:
        return "deterministic_router"
    if has_any(title, ["claude enterprise"]) and has_any(text, ["用量", "成本", "支出", "analytics", "spend"]):
        return "cost_governance"
    if has_any(title, ["claude apps gateway", "bedrock", "google cloud", "microsoft foundry", "foundry 正式可用"]):
        return "enterprise_integration"
    if has_any(text, ["advisor", "orchestrator", "多智能体模式", "顾问", "编排者", "worker"]):
        return "methodology"
    if has_any(text, ["审计代理", "circl", "hackerone", "真实漏洞", "漏洞已修复"]):
        return "security_boundary"
    if has_any(title, ["muse image", "muse video"]) or (
        has_any(title, ["meta", "muse"]) and has_any(text, ["图像", "视频", "image", "video", "instagram", "whatsapp"])
    ):
        return "creative_ops"
    if has_any(title, ["longcat", "owl alpha", "qwen 3.6", "本地开发的理想选择"]):
        return "model_release"
    if looks_like_technique_item(text):
        return "methodology"
    if has_any(title, ["herdr", "终端", "多路复用器"]):
        return "developer_tool"
    if has_any(title, ["美军", "误炸", "ai选目标", "ai 选目标", "palantir"]):
        return "governance_risk"
    if has_any(text, ["j-space", "全局工作空间", "可解释性", "内部神经", "隐藏想法", "未说出口", "对齐"]):
        return "research_frontier"
    if has_any(title, ["brain2qwerty", "非侵入式", "句子解码"]):
        return "research_frontier"
    if has_any(title, ["osworld", "agents-a1", "cybergym", "last exam", "ceo-bench"]):
        return "agent_benchmark"
    if has_any(title, ["openclaw", "ios", "android"]):
        return "consumer_entry"
    if has_any(title, ["ai news radar", "自媒体板块", "订阅多平台账号"]):
        return "media_product"
    if "weave" in title and has_any(title, ["路由", "router"]):
        return "general_router"
    if "runway" in title or has_any(title, ["广告本地化", "recipe"]):
        return "creative_ops"
    if "dspark" in title or has_any(title, ["投机解码"]):
        return "inference_speed"
    if "redknot" in title or has_any(title, ["kv cache", "长文本加速", "推理引擎"]):
        return "inference_speed"
    if "千问输入法" in title or "输入法" in title:
        return "consumer_entry"
    if "economic index" in title or "使用节奏" in title:
        return "usage_research"
    if "账单失控" in title or ("deepseek" in title and "香饽饽" in title):
        return "cost_governance"
    if "claude code" in title and has_any(title, ["攻击", "恶意", "控制"]):
        return "security_boundary"
    if looks_like_controversy_item(text):
        if has_any(text, ["权限", "脚本", "仓库", "隐写", "中国用户", "供应链", "安全", "隐私", "开发者工具"]):
            return "security_boundary"
        return "governance_risk"
    if "everos" in title:
        return "agent_memory"
    if "ceo-bench" in title or has_any(title, ["500天创业测试"]):
        return "agent_benchmark"
    if has_any(text, ["账单失控", "token 最小化", "100% 流量切换"]):
        return "cost_governance"
    if has_any(text, ["广告", "素材", "本地化"]):
        return "creative_ops"
    if has_any(text, ["输入法", "用户入口", "语音输入"]):
        return "consumer_entry"
    if has_any(text, ["隐藏恶意代码", "完全控制"]):
        return "security_boundary"
    return "other"


def is_verified_applied_science_capability_research(candidate: dict[str, Any], text: str) -> bool:
    """Recognize primary research that tests general AI capability in science.

    A protein-design or analytical-chemistry experiment can look remote from
    an internet company when a source listing exposes only its title.  It is
    still relevant when an official research lab is testing a general model's
    ability to orchestrate specialist tools and validate results externally.
    This narrow route prevents ordinary life-science news or partnerships from
    receiving the same boost.
    """

    if candidate.get("source_authority") != "official_research":
        return False
    if candidate.get("source_type") not in {"paper", "report", "benchmark"}:
        return False
    applied_science_markers = [
        "protein design",
        "protein binder",
        "蛋白设计",
        "蛋白质设计",
        "analytical chemistry",
        "化学分析",
        "nmr",
        "lc-ms",
    ]
    frontier_model_markers = ["claude", "anthropic", "gemini", "google deepmind", "openai", "chatgpt"]
    return has_any(text, applied_science_markers) and has_any(text, frontier_model_markers)


def is_search_augmented_content_integrity_research(candidate: dict[str, Any], text: str) -> bool:
    """Recognize research on whether retrieved web content can mislead LLM output.

    This is a concrete product and operations concern for search, RAG, content
    ranking, and recommendation surfaces.  It is deliberately narrower than
    a generic security or recommendation keyword match: the source must be
    research-like, mention a retrieval/search/recommendation surface, and
    assess poisoning, manipulation, or content credibility.
    """

    if candidate.get("source_type") not in {"paper", "benchmark", "report", "methodology"}:
        return False
    retrieval_or_recommendation = has_any(
        text,
        [
            "检索增强",
            "检索增强型",
            "search-augmented",
            "retrieval-augmented",
            "retrieval augmented",
            "rag",
            "搜索推荐",
            "推荐系统",
            "推荐器",
            "recommender",
            "recommendation",
        ],
    )
    content_integrity_risk = has_any(
        text,
        [
            "内容污染",
            "网页污染",
            "web content pollution",
            "polluted page",
            "content integrity",
            "geo",
            "虚假产品",
            "虚假推荐",
            "误导推荐",
            "可信度重排",
            "credibility re-ranking",
        ],
    )
    return retrieval_or_recommendation and content_integrity_risk


def is_research_eligible(candidate: dict[str, Any]) -> bool:
    """Keep the research slot for real research/methodology material.

    AIHot's main feed can contain tools, X posts, and product updates that
    score high on keywords like "benchmark" or "API". Those items can still be
    useful news, but they should not block paper-feed research lookback.
    """

    if candidate.get("research_eligible") is False:
        return False
    if candidate.get("selection_group") == "usage_research":
        return False
    if not has_primary_research_provenance(candidate):
        return False

    if candidate.get("research_eligible") is True and candidate.get("content_research_route_reason"):
        return True

    source_type = candidate.get("source_type", "")
    feed_sources = set(str(candidate.get("feed_source", "")).split("+"))
    categories = set(candidate.get("categories", []))
    host = candidate.get("primary_host", "")
    text = candidate_text(candidate)

    if "paper" in feed_sources and (
        source_type in {"paper", "benchmark", "report", "methodology"}
        or "论文" in categories
    ):
        return True

    if source_type == "paper" and host in PAPER_PRIMARY_HOSTS:
        return True

    if source_type in {"benchmark", "report"} and candidate.get("content_research_route_reason"):
        return True

    if source_type == "methodology" and (
        "paper" in feed_sources
        or candidate.get("source_pool") == "research"
        or host in RESEARCH_SOURCE_HOSTS
    ):
        return True

    return False


def is_technique_candidate(candidate: dict[str, Any]) -> bool:
    if candidate.get("selection_group") == "methodology":
        return True
    if candidate.get("source_type") == "methodology":
        return True
    return looks_like_technique_item(candidate_text(candidate))

