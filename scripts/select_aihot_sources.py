#!/usr/bin/env python3
"""Select layered discovery and first-party sources for the AI daily workflow.

This script only handles source selection:

- fetch AIHot and BestBlogs discovery feeds
- fetch configured first-party research and news sources
- filter by a time window
- remove exact/content duplicates from previous issues
- score and rank one research item plus several news items
- write a structured JSON handoff for the downstream daily workflow

Important editorial rule: the same company or product may appear on consecutive
days if the news content is genuinely new. Only the same message, same source
URL, same AIHot item, or same paper/report identity is hard-excluded.
"""

from __future__ import annotations

import argparse
import difflib
import html
import json
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any


TZ = timezone(timedelta(hours=8))
DEFAULT_FEED_URL = "https://aihot.virxact.com/feed.xml"
DEFAULT_PAPER_FEED_URL = "https://aihot.virxact.com/feed/category/paper.xml"
DEFAULT_ALL_FEED_URL = "https://aihot.virxact.com/feed/all.xml"
DEFAULT_DAILY_URL = "https://aihot.virxact.com/daily"
DEFAULT_BESTBLOGS_AI_FEED_URL = "https://www.bestblogs.dev/en/feeds/rss?category=ai&minScore=90"
DEFAULT_BESTBLOGS_MAX_ITEMS = 30
DEFAULT_SEND_LEDGER = Path("daily_runs") / "send_ledger.json"
DEFAULT_NEWS_LOOKBACK_HOURS = 48
DEFAULT_MIN_NEWS_COUNT = 5
DEFAULT_DAILY_READING_BUDGET_MINUTES = 10.0
NEWS_EXPANSION_SCORE_THRESHOLD = 82.0
NEWS_OVER_BUDGET_SCORE_THRESHOLD = 90.0
NEWS_EXCEPTIONAL_SCORE_THRESHOLD = 94.0
AIHOT_DAILY_EDITORIAL_BONUS = 4.0
WORKFLOW_DIR = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE_REGISTRY = WORKFLOW_DIR / "config" / "source_registry.json"
NEWS_HIGH_SCORE_THRESHOLD = 76
TECHNIQUE_SPARSE_BONUS = 5
RESEARCH_SOURCE_HOSTS = {
    "anthropic.com",
    "arxiv.org",
    "blog.google",
    "deepmind.google",
    "machinelearning.apple.com",
    "microsoft.com",
    "openai.com",
    "research.google",
    "www.anthropic.com",
    "www.lmsys.org",
}
HTML_TAG_RE = re.compile(r"<[^>]+>")
URL_RE = re.compile(r"https?://[^\s<>()\"']+")
AIHOT_ITEM_RE = re.compile(r"aihot\.virxact\.com/items/([^/?#]+)")
AIHOT_DAILY_ITEM_RE = re.compile(r'<a href="/items/([^\"]+)"[^>]*>(.*?)</a>', re.S)
ARXIV_RE = re.compile(r"arxiv\.org/(?:abs|pdf)/([0-9]{4}\.[0-9]{4,5})(?:v\d+)?", re.I)
DOI_RE = re.compile(r"\b10\.\d{4,9}/[-._;()/:A-Z0-9]+\b", re.I)
BESTBLOGS_RESOURCE_ID_RE = re.compile(r"/(?:en/)?(?:article|status|video)/([^/?#]+)", re.I)
BESTBLOGS_PRIMARY_URL_RE = re.compile(r'\\"url\\":\\"(https?://[^"\\]+)', re.I)
X_STATUS_ID_RE = re.compile(r"(?:x\.com/(?:[^/]+/)?status/|/(?:en/)?status/)([0-9]{8,})", re.I)
TECHNIQUE_KEYWORDS = [
    "writing-great-skills",
    "great skills",
    "prompt engineering",
    "提示词工程",
    "prompt 技巧",
    "prompt技巧",
    "调试生产提示词",
    "评估是唯一严谨方式",
    "方法论",
    "工作方法",
    "agentic loop",
    "agentic loops",
    "loops",
    "智能体循环",
    "eval",
    "评估技巧",
    "调试",
    "completion criterion",
    "完成标准",
    "leading word",
    "no-op",
    "progressive disclosure",
    "渐进式披露",
    "可预测工作流",
    "过程可预测",
    "skillgrade",
    "skill 测试",
    "skills 测试",
    "单元测试",
    "回归测试",
    "regression",
    "ci 原生",
]
FOUNDATION_MODEL_KEYWORDS = [
    "openai",
    "chatgpt",
    "gpt-",
    "gpt ",
    "claude",
    "anthropic",
    "gemini",
    "google ai",
    "通义",
    "通义千问",
    "千问",
    "qwen",
    "qwen3",
    "qwen-max",
    "qwen-agent",
    "qwen agent",
    "百炼",
    "dashscope",
    "deepseek",
    "kimi",
    "moonshot",
    "豆包",
    "doubao",
    "seed",
    "混元",
    "hunyuan",
    "llama",
    "meta ai",
    "grok",
    "xai",
]
FOUNDATION_MODEL_MAJOR_SIGNAL_KEYWORDS = [
    "大版本",
    "重大升级",
    "发布",
    "推出",
    "上线",
    "开源",
    "权重",
    "api",
    "大模型",
    "基础模型",
    "agent 能力",
    "智能体能力",
    "managed agents",
    "安全",
    "漏洞",
    "隐私",
    "权限",
    "用户数据",
    "数据泄露",
    "数据争议",
    "攻击",
    "后门",
    "争议",
    "风险",
]
SECURITY_RISK_KEYWORDS = [
    "安全风险",
    "漏洞",
    "隐私",
    "权限",
    "用户数据",
    "数据泄露",
    "数据争议",
    "攻击",
    "恶意",
    "后门",
    "供应链",
    "隐藏代码",
    "完全控制",
    "审计",
]
CONTROVERSY_SIGNAL_KEYWORDS = [
    "争议",
    "爆料",
    "泄露",
    "丑闻",
    "封禁",
    "抵制",
    "质疑",
    "后门",
    "隐写",
    "隐写术",
    "steganography",
    "暗藏",
    "植入",
    "监控",
    "检测",
    "识别中国",
    "中国用户",
    "地域识别",
    "隐藏逻辑",
    "隐藏代码",
    "安全风险",
    "供应链",
    "信任危机",
]
TRUST_BOUNDARY_KEYWORDS = [
    "claude code",
    "cursor",
    "codex",
    "ai coding",
    "编程工具",
    "开发者工具",
    "agent",
    "智能体",
    "权限",
    "脚本",
    "仓库",
    "用户识别",
    "隐私",
    "安全",
    "审计",
    "可审计",
]
MILITARY_GEO_RISK_KEYWORDS = [
    "美军",
    "五角大楼",
    "国防部",
    "军方",
    "军事",
    "军用",
    "战争",
    "战场",
    "目标选择",
    "误炸",
    "伊朗",
    "以色列",
    "乌克兰",
    "palantir",
    "pentagon",
    "defense",
    "military",
    "battlefield",
    "targeting",
    "warfare",
    "terrorist",
    "terrorism",
    "weapon",
    "weapons",
    "博科圣地",
    "恐怖",
    "袭击",
    "武器",
]
RESEARCH_CATEGORY_LABELS = [
    "Alignment",
    "Economic Research",
    "Frontier Red Team",
    "Interpretability",
    "Science",
    "Societal Impacts",
]
ANTHROPIC_NEWS_CATEGORY_LABELS = [
    "Announcements",
    "Case Study",
    "Company",
    "Engineering",
    "Features",
    "Policy",
    "Product",
    "Safety",
]
DATE_LABEL_RE = re.compile(
    r"\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{1,2},\s+\d{4}\b"
)


class SourceSelectionError(Exception):
    pass


@dataclass
class PreviousItem:
    title: str = ""
    urls: set[str] = field(default_factory=set)
    text: str = ""
    identities: set[str] = field(default_factory=set)


class LinkCollector(HTMLParser):
    """Collect anchor text from simple listing pages without a browser runtime."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[dict[str, str]] = []
        self._stack: list[dict[str, Any]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a":
            return
        attr_map = {key: value or "" for key, value in attrs}
        href = attr_map.get("href", "")
        self._stack.append({"href": href, "parts": []})

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() != "a" or not self._stack:
            return
        item = self._stack.pop()
        text = clean_text(" ".join(item["parts"]))
        if item["href"] and text:
            self.links.append({"href": item["href"], "text": text})

    def handle_data(self, data: str) -> None:
        if self._stack:
            self._stack[-1]["parts"].append(data)


def clean_text(value: str | None) -> str:
    value = html.unescape(value or "")
    value = HTML_TAG_RE.sub("", value)
    return re.sub(r"\s+", " ", value).strip()


def normalize_text(value: str | None) -> str:
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", (value or "").lower())


def normalize_url(value: str | None) -> str:
    if not value:
        return ""
    value = value.strip().rstrip("，。).,")
    value = re.sub(r"#.*$", "", value)
    # Remove common tracking query params while preserving meaningful query
    # strings from sites that use them for content.
    if "?" in value:
        base, query = value.split("?", 1)
        kept = []
        for part in query.split("&"):
            key = part.split("=", 1)[0].lower()
            if key.startswith("utm_") or key in {"spm", "from", "share"}:
                continue
            kept.append(part)
        value = base + (("?" + "&".join(kept)) if kept else "")
    return value.rstrip("/")


def absolute_url(base_url: str, href: str) -> str:
    if href.startswith("http://") or href.startswith("https://"):
        return href
    if href.startswith("/"):
        match = re.match(r"(https?://[^/]+)", base_url)
        return f"{match.group(1)}{href}" if match else href
    return href


def host_of(url: str | None) -> str:
    match = re.match(r"https?://([^/]+)", url or "")
    return match.group(1).lower().replace("www.", "") if match else ""


def first_primary_url(description_html: str, aihot_url: str) -> str:
    urls = [normalize_url(url) for url in URL_RE.findall(description_html or "")]
    for url in urls:
        if "aihot.virxact.com" not in url:
            return url
    return normalize_url(aihot_url)


def parse_display_date(value: str) -> datetime | None:
    match = DATE_LABEL_RE.search(value)
    if not match:
        return None
    try:
        parsed = datetime.strptime(match.group(0), "%b %d, %Y")
    except ValueError:
        return None
    return parsed.replace(tzinfo=TZ)


def strip_known_category(value: str, labels: list[str]) -> tuple[str, str]:
    cleaned = clean_text(value)
    for label in sorted(labels, key=len, reverse=True):
        if cleaned == label:
            return "", label
        suffix = f" {label}"
        prefix = f"{label} "
        if cleaned.endswith(suffix):
            return cleaned[: -len(suffix)].strip(), label
        if cleaned.startswith(prefix):
            return cleaned[len(prefix) :].strip(), label
    return cleaned, ""


def strip_research_category(value: str) -> tuple[str, str]:
    return strip_known_category(value, RESEARCH_CATEGORY_LABELS)


def parse_anthropic_listing_link(
    link_text: str,
    category_labels: list[str],
) -> tuple[str, str, str, datetime | None]:
    published_at = parse_display_date(link_text)
    if not published_at:
        return "", "", "", None

    date_match = DATE_LABEL_RE.search(link_text)
    if not date_match:
        return "", "", "", None

    before = clean_text(link_text[: date_match.start()])
    after = clean_text(link_text[date_match.end() :])
    title = ""
    category = ""
    description = ""

    if before:
        title, category = strip_known_category(before, category_labels)
        description = after
    else:
        title, category = strip_known_category(after, category_labels)

    # Some Anthropic cards put only the category before the date and the title
    # after it, while others put title + category before the date.
    if not title and after:
        title, after_category = strip_known_category(after, category_labels)
        category = category or after_category
        description = ""

    return title, category, description, published_at


def parse_anthropic_research_link(link_text: str) -> tuple[str, str, str, datetime | None]:
    return parse_anthropic_listing_link(link_text, RESEARCH_CATEGORY_LABELS)


def load_source_registry(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise SourceSelectionError(f"Source registry does not exist: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SourceSelectionError(f"Cannot read source registry {path}: {exc}") from exc
    sources = data.get("sources")
    if not isinstance(sources, list):
        raise SourceSelectionError("Source registry must contain a sources list.")
    for source in sources:
        if not isinstance(source, dict) or not source.get("id") or not source.get("url"):
            raise SourceSelectionError("Every source registry item needs id and url fields.")
    return data


def extract_aihot_id(url: str | None) -> str:
    match = AIHOT_ITEM_RE.search(url or "")
    return match.group(1) if match else ""


def parse_aihot_daily_entries(html_text: str) -> list[dict[str, Any]]:
    """Extract the editorially featured AIHot Daily entries in page order.

    The Daily page is a recall and prioritization aid, not a source of truth.
    We only retain its item identity and display title, then match it against
    candidates already fetched from the normal feeds and resolved to originals.
    """

    entries: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item_id, raw_title in AIHOT_DAILY_ITEM_RE.findall(html_text):
        if item_id in seen:
            continue
        title = clean_text(raw_title)
        if not title:
            continue
        seen.add(item_id)
        entries.append(
            {
                "item_id": item_id,
                "title": title,
                "rank": len(entries) + 1,
            }
        )
    return entries


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
            min(100, before + AIHOT_DAILY_EDITORIAL_BONUS),
            1,
        )
        breakdown = candidate.setdefault("score_breakdown", {})
        breakdown["final_news_before_aihot_daily_bonus"] = before
        breakdown["aihot_daily_editorial_bonus"] = AIHOT_DAILY_EDITORIAL_BONUS
        matched.append(
            {
                "rank": entry["rank"],
                "title": candidate["title"],
                "aihot_url": candidate.get("aihot_url", ""),
                "primary_url": candidate.get("primary_url", ""),
            }
        )
    return matched


def extract_identities(*values: str) -> set[str]:
    joined = " ".join(v or "" for v in values)
    identities: set[str] = set()
    for match in ARXIV_RE.findall(joined):
        identities.add(f"arxiv:{match.lower()}")
    for match in DOI_RE.findall(joined):
        identities.add(f"doi:{match.lower().rstrip('.')}")
    for match in X_STATUS_ID_RE.findall(joined):
        identities.add(f"x:{match}")
    for key in message_keys(joined):
        identities.add(f"msg:{key}")
    return identities


def message_keys(value: str) -> set[str]:
    """Return content-level message keys.

    These keys intentionally describe a specific news event, not only a company
    or product. For example, "Claude Code hidden malware" and "Claude Code
    permission visibility" should be different keys even though both mention
    Claude Code.
    """

    text = value.lower()
    keys: set[str] = set()

    if "ceo-bench" in text or ("500天" in text and "创业" in text):
        keys.add("ceo-bench-500-day-startup")
    if "wayfinder" in text and ("router" in text or "路由" in text):
        keys.add("wayfinder-router-deterministic-routing")
    if "runway" in text and ("广告" in text or "localization" in text or "recipe" in text):
        keys.add("runway-ad-localization-recipe")
    if "dspark" in text or ("deepseek-v4" in text and "投机解码" in text):
        keys.add("deepseek-dspark-speculative-decoding")
    if "账单失控" in text or (
        "deepseek" in text
        and (
            "100%流量" in text
            or "100% 流量" in text
            or "100%切换" in text
            or "100% 切换" in text
            or "token 最小化" in text
            or "账单" in text
            or "成本" in text
        )
    ):
        keys.add("ai-bill-deepseek-model-routing")
    if "千问输入法" in text or ("qwen" in text and "input" in text):
        if "macos" in text or "mac os" in text or "mac" in text:
            keys.add("qwen-input-method-macos-launch")
        else:
            keys.add("qwen-input-method-launch")
    if "anthropic economic index" in text or ("economic index" in text and "使用节奏" in text):
        keys.add("anthropic-economic-index-usage-cadence")
    if "claude code" in text and ("恶意" in text or "malware" in text or "完全控制" in text):
        keys.add("claude-code-hidden-malware-attack")
    if "claude code" in text and has_any(
        text,
        ["隐写", "steganography", "中国用户", "识别中国", "检测中国", "植入"],
    ):
        keys.add("claude-code-china-user-steganography")
    if "claude code" in text and (
        "automode" in text
        or "自动模式" in text
        or (
            "权限" in text
            and not has_any(text, ["enterprise", "用量", "成本", "支出", "analytics", "admin api", "管理分析"])
        )
    ):
        keys.add("claude-code-permission-observability")
    if "openclaw" in text and ("ios" in text or "android" in text or "移动" in text or "mobile" in text):
        keys.add("openclaw-native-mobile-app")
    if "cursor" in text and ("ios" in text or "mobile" in text or "手机" in text):
        keys.add("cursor-ios-mobile-app")
    if ("margot" in text or "van laar" in text) and ("提示词" in text or "prompt" in text):
        keys.add("anthropic-margot-prompt-engineering")
    if "redknot" in text or ("kv cache" in text and "长文本" in text):
        keys.add("redknot-kv-cache-long-context")
    if "everos" in text and ("记忆" in text or "memory" in text):
        keys.add("everos-agent-memory-runtime")
    if "weave" in text and ("router" in text or "路由" in text):
        keys.add("weave-model-router")
    if "cursor" in text and "swe-bench" in text and ("奖励" in text or "reward" in text):
        keys.add("cursor-swe-bench-reward-attack")
    if "vibethinker" in text:
        keys.add("vibethinker-reasoning-compression")
    if has_any(text, ["gpt-5.6", "gpt 5.6", "codex", "chatgpt work"]) and has_any(
        text,
        ["5小时", "5 小时", "5-hour", "usage reset", "重置用量", "banked reset"],
    ):
        keys.add("openai-codex-work-usage-reset")
    if "gpt-live" in text or "gpt live" in text:
        keys.add("openai-gpt-live-voice-launch")

    return keys


def has_any(text: str, words: list[str]) -> bool:
    return any(word.lower() in text for word in words)


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


def looks_like_controversy_item(text: str) -> bool:
    """High-discussion risk signal, not automatically verified truth."""

    if not has_any(text, CONTROVERSY_SIGNAL_KEYWORDS):
        return False
    if has_any(text, TRUST_BOUNDARY_KEYWORDS):
        return True
    if has_any(text, ["openai", "anthropic", "google", "claude", "chatgpt", "gemini", "cursor"]):
        return True
    return False


def military_geo_risk_penalty(candidate: dict[str, Any]) -> float:
    """Downgrade military/geopolitical controversy for this product/ops daily."""

    text = candidate_text(candidate)
    if not has_any(text, MILITARY_GEO_RISK_KEYWORDS):
        return 0.0
    return 22.0


def collect_string_text(obj: Any) -> str:
    values: list[str] = []

    def walk(value: Any) -> None:
        if isinstance(value, str):
            if value.startswith(("http://", "https://")):
                return
            values.append(value)
        elif isinstance(value, dict):
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(obj)
    return clean_text(" ".join(values))


def collect_urls(obj: Any) -> set[str]:
    urls: set[str] = set()

    def walk(value: Any) -> None:
        if isinstance(value, str):
            if value.startswith(("http://", "https://")):
                urls.add(normalize_url(value))
            else:
                for url in URL_RE.findall(value):
                    urls.add(normalize_url(url))
        elif isinstance(value, dict):
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(obj)
    return {url for url in urls if url}


def previous_item_from_object(obj: dict[str, Any]) -> PreviousItem | None:
    title = clean_text(obj.get("title") if isinstance(obj.get("title"), str) else "")
    urls = collect_urls(obj)
    text = collect_string_text(obj)
    if not title and not urls:
        return None
    identities = extract_identities(title, text, " ".join(urls))
    for url in urls:
        item_id = extract_aihot_id(url)
        if item_id:
            identities.add(f"aihot:{item_id}")
    return PreviousItem(title=title, urls=urls, text=text, identities=identities)


def load_previous_items(paths: list[Path]) -> list[PreviousItem]:
    items: list[PreviousItem] = []

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            item = previous_item_from_object(value)
            if item:
                items.append(item)
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    for path in paths:
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        walk(data)
    return items


def auto_previous_content(run_date: str) -> list[Path]:
    official_previous = auto_previous_official_content(run_date)
    current = datetime.strptime(run_date, "%Y-%m-%d").date()
    candidates: list[tuple[str, int, Path]] = []
    names = [
        ("ai_daily_email_content.normalized.json", 0),
        ("ai_daily_email_content.json", 1),
    ]
    for filename, priority in names:
        for path in Path("daily_runs").glob(f"*/{filename}"):
            try:
                day = datetime.strptime(path.parent.name, "%Y-%m-%d").date()
            except ValueError:
                continue
            if day < current and (current - day).days <= 7:
                candidates.append((path.parent.name, priority, path))

    # Use the last week of generated/sent daily content by default. This is a
    # stronger guard for widened selection windows, while message keys still
    # allow the same company/product to appear again when the news is genuinely
    # new. Official prod-send ledger paths are kept first when present.
    paths: list[Path] = []
    seen: set[Path] = set()
    for path in official_previous:
        resolved = path.resolve()
        if resolved not in seen:
            paths.append(path)
            seen.add(resolved)
    for _, _, path in sorted(candidates, reverse=True):
        resolved = path.resolve()
        if resolved not in seen:
            paths.append(path)
            seen.add(resolved)
    return paths


def auto_previous_official_content(run_date: str) -> list[Path]:
    """Return the latest normalized content from a successful prod send.

    Test sends and local previews can create normalized JSON files. Those files
    should not affect tomorrow's source dedupe unless the corresponding issue
    was actually sent to the prod list.
    """

    if not DEFAULT_SEND_LEDGER.exists():
        return []

    current = datetime.strptime(run_date, "%Y-%m-%d").date()
    try:
        data = json.loads(DEFAULT_SEND_LEDGER.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []

    prod = data.get("prod")
    if not isinstance(prod, dict):
        return []

    candidates: list[tuple[str, Path]] = []
    for date_key, entry in prod.items():
        try:
            day = datetime.strptime(date_key, "%Y-%m-%d").date()
        except ValueError:
            continue
        if day >= current or not isinstance(entry, dict):
            continue

        content_value = entry.get("normalized_content") or entry.get("content")
        if not isinstance(content_value, str) or not content_value.strip():
            continue
        path = Path(content_value)
        if path.exists():
            candidates.append((date_key, path))

    return [sorted(candidates, reverse=True)[0][1]] if candidates else []


def latest_previous_prod_send_at(run_date: str) -> datetime | None:
    """Return the last prod-send timestamp before run_date, if recorded."""

    if not DEFAULT_SEND_LEDGER.exists():
        return None

    current = datetime.strptime(run_date, "%Y-%m-%d").date()
    try:
        data = json.loads(DEFAULT_SEND_LEDGER.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None

    prod = data.get("prod")
    if not isinstance(prod, dict):
        return None

    candidates: list[datetime] = []
    for date_key, entry in prod.items():
        try:
            day = datetime.strptime(date_key, "%Y-%m-%d").date()
        except ValueError:
            continue
        if day >= current or not isinstance(entry, dict):
            continue

        sent_at = entry.get("sent_at")
        if not isinstance(sent_at, str) or not sent_at.strip():
            continue
        try:
            parsed = parse_datetime(sent_at)
        except (ValueError, SourceSelectionError):
            continue
        candidates.append(parsed)

    return max(candidates) if candidates else None


def parse_datetime(value: str | None, default: datetime | None = None) -> datetime:
    if not value:
        if default is None:
            raise SourceSelectionError("Missing datetime value.")
        return default
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=TZ)
    return parsed.astimezone(TZ)


def run_curl(url: str, output: Path) -> str:
    output.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "curl",
        "-L",
        "--http1.1",
        "--max-time",
        "25",
        "-A",
        "Mozilla/5.0 ai-daily-source-selector/0.1",
        "-o",
        str(output),
        url,
    ]
    result = subprocess.run(cmd, text=True, capture_output=True)
    if result.returncode != 0:
        raise SourceSelectionError(result.stderr.strip() or f"curl failed for {url}")
    return output.read_text(encoding="utf-8", errors="replace")


def categories_for(item: ET.Element) -> list[str]:
    return [clean_text(c.text) for c in item.findall("category") if c.text and clean_text(c.text)]


def source_type_for(candidate: dict[str, Any]) -> str:
    text = candidate_text(candidate)
    title = candidate.get("title", "").lower()
    if looks_like_technique_item(text):
        return "methodology"
    if has_any(text, ["arxiv.org", "论文", "paper"]):
        return "paper"
    if has_any(text, ["ceo-bench", "swe-bench", "vibethinker", "benchmark", "bench", "基准", "评测"]):
        return "benchmark"
    if has_any(text, ["economic index", "报告", "report"]):
        return "report"
    if has_any(title, ["longcat", "owl alpha", "qwen 3.6", "模型"]):
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


def is_unverified_x_model_release(candidate: dict[str, Any]) -> bool:
    """Detect third-party X reposts presented as a fresh model launch.

    A model announcement carried by an official company account can be a valid
    primary source. A repost from an analyst account is still a useful lead,
    but it must not receive the full headline-model boost until an official
    release, model card, repository, or official account is resolved.
    """

    if candidate.get("primary_host") != "x.com" or candidate.get("selection_group") != "model_release":
        return False
    path = candidate.get("primary_url", "").lower()
    official_handles = [
        "/openai/",
        "/anthropicai/",
        "/googledeepmind/",
        "/googleai/",
        "/xai/",
        "/tencent/",
        "/alibaba_cloud/",
        "/qwen/",
        "/deepseek_ai/",
        "/moonshotai/",
        "/bytedance/",
    ]
    return not any(handle in path for handle in official_handles)


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
    # Classify distinct editorial subjects before applying the broad
    # foundation-model bucket. Otherwise a security incident, a Codex update,
    # and a real model release all collapse into one group and diversity rules
    # hide useful daily signals.
    if has_any(title, ["医疗评估", "medical evaluation", "医学评估"]):
        return "research_frontier"
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
    if has_any(text, MILITARY_GEO_RISK_KEYWORDS):
        return "governance_risk"
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
    if looks_like_foundation_model_priority_item(text, title):
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
    if source_type in {"benchmark", "paper", "report"}:
        industry += 14
    if source_type in {"tool", "product_launch"}:
        industry += 11
    if has_any(text, ["agent", "智能体", "模型路由", "router", "成本", "广告", "输入法", "投机解码", "推理引擎", "安全", "攻击", "使用节奏", "kv cache"]):
        industry += 9
    if looks_like_foundation_model_priority_item(text, title):
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
    if has_any(text, ["安全", "攻击", "恶意", "权限", "隐私", "评测", "benchmark", "基准"]):
        relevance += 7
    if looks_like_foundation_model_priority_item(text, title):
        relevance += 5
    if looks_like_controversy_item(text):
        relevance += 8
    relevance = max(0, min(40, relevance))

    penalty = 0
    if candidate["primary_host"] in {"ithome.com", "marktechpost.com", "the-decoder.com"}:
        penalty += 3
    if has_any(text, ["芯片", "内存", "数据中心", "商标", "报纸起诉", "自动驾驶"]) and not has_any(text, ["agent", "模型", "工具", "广告"]):
        penalty += 12
    if has_any(text, ["artifacts 22", "生态系统广度", "融资"]):
        penalty += 8
    if len(candidate["description"]) < 60:
        penalty += 5

    return industry, relevance, penalty, industry + relevance - penalty


def candidate_identities(candidate: dict[str, Any]) -> set[str]:
    identities = extract_identities(
        candidate.get("title", ""),
        candidate.get("description", ""),
        candidate.get("primary_url", ""),
        candidate.get("aihot_url", ""),
    )
    item_id = extract_aihot_id(candidate.get("aihot_url", ""))
    if item_id:
        identities.add(f"aihot:{item_id}")
    return identities


def content_duplicate_check(candidate: dict[str, Any], previous_items: list[PreviousItem]) -> dict[str, Any]:
    current_urls = {normalize_url(candidate.get("aihot_url")), normalize_url(candidate.get("primary_url"))}
    current_urls = {url for url in current_urls if url}
    current_title = normalize_text(candidate.get("title", ""))
    current_text = normalize_text(f"{candidate.get('title', '')} {candidate.get('description', '')}")
    identities = candidate_identities(candidate)
    source_type = candidate.get("source_type", "")

    for previous in previous_items:
        url_overlap = current_urls & previous.urls
        if url_overlap:
            return {
                "is_duplicate": True,
                "reason": "same_url",
                "matched_previous": previous.title or sorted(url_overlap)[0],
                "matched_value": sorted(url_overlap)[0],
            }

        identity_overlap = identities & previous.identities
        if identity_overlap:
            match = sorted(identity_overlap)[0]
            if match.startswith("msg:"):
                reason = "same_message_key"
            elif match.startswith(("arxiv:", "doi:")):
                reason = "same_research_identity"
            else:
                reason = "same_item_identity"
            return {
                "is_duplicate": True,
                "reason": reason,
                "matched_previous": previous.title,
                "matched_value": match,
            }

        if current_title and previous.title:
            title_ratio = difflib.SequenceMatcher(None, current_title, normalize_text(previous.title)).ratio()
            if title_ratio >= 0.9:
                return {
                    "is_duplicate": True,
                    "reason": "near_same_title",
                    "matched_previous": previous.title,
                    "matched_value": round(title_ratio, 3),
                }

        # Only use body similarity as a hard duplicate when the title is also
        # close. This avoids killing a new story from the same company/product.
        if current_text and previous.text:
            title_ratio = difflib.SequenceMatcher(None, current_title, normalize_text(previous.title)).ratio()
            body_ratio = difflib.SequenceMatcher(None, current_text, normalize_text(previous.text)).ratio()
            if title_ratio >= 0.78 and body_ratio >= 0.82:
                return {
                    "is_duplicate": True,
                    "reason": "same_message_content",
                    "matched_previous": previous.title,
                    "matched_value": {"title_similarity": round(title_ratio, 3), "body_similarity": round(body_ratio, 3)},
                }

        # For papers/reports, a highly similar title usually means the same
        # publication even if the source URL changed from AIHot to a media page.
        if source_type in {"paper", "benchmark", "report"} and current_title and previous.title:
            title_ratio = difflib.SequenceMatcher(None, current_title, normalize_text(previous.title)).ratio()
            if title_ratio >= 0.84:
                return {
                    "is_duplicate": True,
                    "reason": "same_research_publication",
                    "matched_previous": previous.title,
                    "matched_value": round(title_ratio, 3),
                }

    return {
        "is_duplicate": False,
        "reason": "",
        "matched_previous": "",
        "matched_value": "",
    }


def continuity_note(candidate: dict[str, Any], previous_items: list[PreviousItem]) -> str:
    title = candidate.get("title", "").lower()
    entities = [
        "Claude Code",
        "OpenRouter",
        "Codex",
        "Cursor",
        "GPT-5.6",
        "DeepSeek",
        "Runway",
        "Qwen",
        "通义",
        "千问",
        "OpenAI",
        "Google",
        "Anthropic",
        "Gemini",
    ]
    previous_text = " ".join(item.title for item in previous_items).lower()
    for entity in entities:
        if entity.lower() in title and entity.lower() in previous_text:
            return f"同主体连续出现：{entity}。允许入选，但正文必须讲清本次新增信息点。"
    return ""


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

    reader_preference = reader_preference_score(candidate)
    industry = round(candidate["industry_impact_score"] / 60 * 100, 1)
    translation = translation_value_score(candidate)
    reliability = source_reliability_score(candidate)
    freshness = freshness_diversity_score(candidate)
    controversy_heat = controversy_heat_score(candidate)
    controversy_bonus = controversy_heat_bonus(controversy_heat)
    military_penalty = military_geo_risk_penalty(candidate)
    foundation_model_bonus = 6 if looks_like_foundation_model_priority_item(text, title) else 0
    news = (
        reader_preference * 0.30
        + industry * 0.30
        + translation * 0.25
        + reliability * 0.10
        + freshness * 0.05
        + controversy_bonus
        + foundation_model_bonus
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

    candidate["score_breakdown"] = {
        "reader_preference": round(reader_preference, 1),
        "industry_impact": industry,
        "translation_value": round(translation, 1),
        "source_reliability": round(reliability, 1),
        "freshness_diversity": round(freshness, 1),
        "controversy_heat": round(controversy_heat, 1),
        "controversy_heat_bonus": round(controversy_bonus, 1),
        "foundation_model_priority_bonus": round(foundation_model_bonus, 1),
        "military_geo_risk_penalty": round(military_penalty, 1),
        "unverified_x_model_release_penalty": 12 if is_unverified_x_model_release(candidate) else 0,
        "weights": {
            "reader_preference": 30,
            "industry_impact": 30,
            "translation_value": 25,
            "source_reliability": 10,
            "freshness_diversity": 5,
        },
    }

    return round(max(0, min(100, research)), 1), round(max(0, min(100, news)), 1)


def is_research_eligible(candidate: dict[str, Any]) -> bool:
    """Keep the research slot for real research/methodology material.

    AIHot's main feed can contain tools, X posts, and product updates that
    score high on keywords like "benchmark" or "API". Those items can still be
    useful news, but they should not block paper-feed research lookback.
    """

    if candidate.get("research_eligible") is False:
        return False

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

    if source_type in {"paper", "benchmark", "report"} and host in RESEARCH_SOURCE_HOSTS:
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
        and candidate.get("research_window_strategy") != "paper_lookback"
    ]
    if not technique_candidates:
        return 0

    has_high_score_technique = any(
        candidate["scores"]["final_news"] >= NEWS_HIGH_SCORE_THRESHOLD
        for candidate in technique_candidates
    )
    if has_high_score_technique:
        for candidate in technique_candidates:
            candidate.setdefault("score_breakdown", {})["technique_sparse_bonus"] = 0
        return 0

    for candidate in technique_candidates:
        old_score = candidate["scores"]["final_news"]
        candidate["scores"]["final_news"] = round(
            min(100, old_score + TECHNIQUE_SPARSE_BONUS),
            1,
        )
        candidate.setdefault("score_breakdown", {})["final_news_before_technique_bonus"] = old_score
        candidate["score_breakdown"]["technique_sparse_bonus"] = TECHNIQUE_SPARSE_BONUS
    return len(technique_candidates)


def reader_preference_score(candidate: dict[str, Any]) -> float:
    """Score against the latest reader feedback."""

    title = candidate.get("title", "").lower()
    group = candidate["selection_group"]
    base = {
        "methodology": 100,
        "enterprise_integration": 90,
        "developer_tool": 88,
        "consumer_entry": 86,
        "security_boundary": 84,
        "cost_governance": 82,
        "creative_ops": 80,
        "model_release": 70,
        "usage_research": 68,
        "governance_risk": 64,
        "inference_speed": 56,
        "agent_memory": 50,
        "research_frontier": 48,
        "media_product": 42,
        "general_router": 55,
        "other": 45,
    }.get(group, 45)

    if "prompt" in title or "提示词" in title or "评估" in title:
        base += 12
    if "openclaw" in title:
        # The user's team has real usage, so this is more valuable than raw
        # external heat suggests.
        base += 14
    if "cursor" in title:
        base += 8
    if "redknot" in title or "小红书" in title:
        base -= 8
    if "everos" in title:
        base -= 10

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
    if host in {"ithome.com", "the-decoder.com", "marktechpost.com"}:
        return 62
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
    if score >= 85:
        return 8
    if score >= 72:
        return 6
    if score >= 60:
        return 3
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
    if looks_like_controversy_item(candidate_text(candidate)):
        return "该信息属于高讨论度争议/风险信号，当前应以社区或媒体披露口径理解，需等待更多一手证据或官方回应。"
    if candidate.get("selection_group") == "security_boundary":
        return "该信息涉及安全或数据处理风险；应区分已复现行为、受影响版本和官方后续处置，避免把单一测试结果外推为通用结论。"
    if candidate["source_type"] in {"paper", "benchmark", "report"}:
        return "评测和报告结论需要看数据集、任务设定和模型范围，不能直接外推。"
    return ""


def pack_candidate(candidate: dict[str, Any], rank: int | None = None) -> dict[str, Any]:
    packed = {
        "title": candidate["title"],
        "published_at": candidate["published_at"],
        "aihot_url": candidate["aihot_url"],
        "bestblogs_url": candidate.get("bestblogs_url", ""),
        "bestblogs_score": candidate.get("bestblogs_score"),
        "source_url": candidate["primary_url"],
        "primary_url": candidate["primary_url"],
        "primary_host": candidate["primary_host"],
        "source_type": candidate["source_type"],
        "feed_source": candidate.get("feed_source", ""),
        "source_name": candidate.get("source_name", ""),
        "source_pool": candidate.get("source_pool", ""),
        "source_authority": candidate.get("source_authority", ""),
        "research_window_strategy": candidate.get("research_window_strategy", ""),
        "aihot_daily_featured": bool(candidate.get("aihot_daily_featured")),
        "aihot_daily_rank": candidate.get("aihot_daily_rank"),
        "topic_tags": candidate["topic_tags"],
        "selection_group": candidate["selection_group"],
        "scores": candidate["scores"],
        "score_breakdown": candidate.get("score_breakdown", {}),
        "content_duplicate_check": candidate["content_duplicate_check"],
        "continuity_note": candidate["continuity_note"],
        "why_selected": (
            f"主题组 {candidate['selection_group']}；"
            f"最终新闻分 {candidate['scores']['final_news']} / "
            f"研究分 {candidate['scores']['final_research']}。"
        ),
        "business_value": business_value_for(candidate["selection_group"]),
    }
    boundary = boundary_for(candidate)
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


def parse_feed(
    xml_text: str,
    start_at: datetime,
    end_at: datetime,
    previous_items: list[PreviousItem],
    *,
    feed_source: str,
    research_window_strategy: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    root = ET.fromstring(xml_text)
    channel = root.find("channel")
    if channel is None:
        raise SourceSelectionError("Feed does not contain an RSS channel.")

    candidates: list[dict[str, Any]] = []
    duplicates: list[dict[str, Any]] = []

    for item in channel.findall("item"):
        title = clean_text(item.findtext("title"))
        aihot_url = normalize_url(clean_text(item.findtext("link")))
        description_html = item.findtext("description") or ""
        description = clean_text(description_html)
        pub_date = item.findtext("pubDate") or ""
        try:
            published_at = parsedate_to_datetime(pub_date).astimezone(TZ)
        except (TypeError, ValueError):
            continue
        if not start_at <= published_at <= end_at:
            continue

        candidate: dict[str, Any] = {
            "title": title,
            "published_at": published_at.isoformat(),
            "aihot_url": aihot_url,
            "primary_url": first_primary_url(description_html, aihot_url),
            "description": description,
            "categories": categories_for(item),
            "feed_source": feed_source,
            "research_window_strategy": research_window_strategy,
        }
        candidate, duplicate = score_candidate(candidate, previous_items)

        if duplicate:
            duplicates.append(duplicate)
        else:
            candidates.append(candidate)

    return candidates, duplicates


def parse_feed_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=TZ)
    return parsed.astimezone(TZ)


def parse_official_rss_feed(
    xml_text: str,
    start_at: datetime,
    end_at: datetime,
    previous_items: list[PreviousItem],
    *,
    source: dict[str, Any],
    research_window_strategy: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Parse an official RSS feed without treating it as an aggregator."""

    root = ET.fromstring(xml_text)
    channel = root.find("channel")
    if channel is None:
        raise SourceSelectionError(f"Official feed {source['id']} does not contain an RSS channel.")

    candidates: list[dict[str, Any]] = []
    duplicates: list[dict[str, Any]] = []
    for item in channel.findall("item"):
        title = clean_text(item.findtext("title"))
        primary_url = normalize_url(clean_text(item.findtext("link") or item.findtext("guid")))
        description_html = item.findtext("description") or ""
        published_at = parse_feed_datetime(item.findtext("pubDate") or item.findtext("date"))
        if not title or not primary_url or not published_at:
            continue
        if not start_at <= published_at <= end_at:
            continue

        pool = str(source.get("pool", "news"))
        candidate: dict[str, Any] = {
            "title": title,
            "published_at": published_at.isoformat(),
            "aihot_url": "",
            "primary_url": primary_url,
            "description": clean_text(description_html),
            "categories": [
                f"Official {pool.title()}",
                *categories_for(item),
            ],
            "feed_source": f"direct:{source['id']}",
            "source_name": source.get("name", source["id"]),
            "source_pool": pool,
            "source_authority": source.get("authority", "official_first_party"),
            "research_window_strategy": research_window_strategy,
            "research_eligible": pool == "research",
        }
        if source.get("source_type_override"):
            candidate["source_type"] = source["source_type_override"]
        candidate, duplicate = score_candidate(candidate, previous_items)
        if duplicate:
            duplicates.append(duplicate)
        else:
            candidates.append(candidate)

    return candidates, duplicates


def bestblogs_resource_id(url: str) -> str:
    match = BESTBLOGS_RESOURCE_ID_RE.search(url)
    if match:
        return match.group(1)
    return re.sub(r"[^a-zA-Z0-9_-]+", "_", url).strip("_") or "resource"


def bestblogs_primary_url(detail_html: str, resource_url: str) -> str:
    """Resolve the original source URL embedded in a BestBlogs detail page.

    The public RSS exposes a BestBlogs reading URL. Its detail page includes a
    structured `initialMeta.url` field pointing at the original publisher.
    We do not let a BestBlogs-only URL enter the final source pool: that would
    weaken the daily workflow's first-party-source policy.
    """

    for match in BESTBLOGS_PRIMARY_URL_RE.finditer(detail_html):
        url = normalize_url(html.unescape(match.group(1)).replace("\\/", "/"))
        host = host_of(url)
        if url and host not in {"bestblogs.dev", "image.jido.dev", "media.ginonotes.com"}:
            return url

    # BestBlogs exposes X posts under `/status/<post-id>`. A canonical account
    # URL is not always present in the SSR payload, but the post ID still gives
    # us the original X resource for provenance and deduplication.
    status_match = X_STATUS_ID_RE.search(resource_url)
    if status_match:
        return f"https://x.com/i/web/status/{status_match.group(1)}"
    return ""


def parse_bestblogs_feed(
    xml_text: str,
    start_at: datetime,
    end_at: datetime,
    previous_items: list[PreviousItem],
    *,
    run_date: str,
    max_items: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, str]]]:
    """Parse BestBlogs' high-score AI feed as a news-only supplement.

    BestBlogs is deliberately used for candidate recall rather than as a final
    authority. Each selected RSS item must resolve to its original URL before
    it can be scored, deduplicated, and compete with AIHot candidates.
    """

    root = ET.fromstring(xml_text)
    channel = root.find("channel")
    if channel is None:
        raise SourceSelectionError("BestBlogs feed does not contain an RSS channel.")

    stubs: list[dict[str, Any]] = []
    for item in channel.findall("item"):
        title = clean_text(item.findtext("title"))
        bestblogs_url = normalize_url(clean_text(item.findtext("link")))
        description_html = item.findtext("description") or ""
        pub_date = item.findtext("pubDate") or ""
        score_text = clean_text(item.findtext("score"))
        try:
            published_at = parsedate_to_datetime(pub_date).astimezone(TZ)
        except (TypeError, ValueError):
            continue
        if not title or not bestblogs_url or not start_at <= published_at <= end_at:
            continue
        try:
            bestblogs_score = int(score_text)
        except ValueError:
            bestblogs_score = 0
        stubs.append(
            {
                "title": title,
                "published_at": published_at.isoformat(),
                "aihot_url": "",
                "bestblogs_url": bestblogs_url,
                "bestblogs_score": bestblogs_score,
                "description": clean_text(description_html),
                "categories": ["BestBlogs", "AI"],
                "feed_source": "bestblogs",
                "research_window_strategy": "exact_window",
                "research_eligible": False,
            }
        )

    stubs.sort(key=lambda item: (item["published_at"], item["bestblogs_score"]), reverse=True)
    stubs = stubs[: max(0, max_items)]

    resolved: list[dict[str, Any]] = []
    resolution_errors: list[dict[str, str]] = []

    def resolve_stub(stub: dict[str, Any]) -> tuple[dict[str, Any], str]:
        resource_id = bestblogs_resource_id(stub["bestblogs_url"])
        detail_path = default_bestblogs_detail_path(run_date, resource_id)
        detail_html = run_curl(stub["bestblogs_url"], detail_path)
        return stub, bestblogs_primary_url(detail_html, stub["bestblogs_url"])

    # Detail resolution is network-bound. A small pool keeps the daily run
    # quick without creating an aggressive fetch pattern against BestBlogs.
    with ThreadPoolExecutor(max_workers=6) as executor:
        future_map = {executor.submit(resolve_stub, stub): stub for stub in stubs}
        for future in as_completed(future_map):
            stub = future_map[future]
            try:
                candidate, primary_url = future.result()
            except SourceSelectionError as exc:
                resolution_errors.append(
                    {
                        "title": stub["title"],
                        "url": stub["bestblogs_url"],
                        "reason": f"detail_fetch_failed: {exc}",
                    }
                )
                continue
            except OSError as exc:
                resolution_errors.append(
                    {
                        "title": stub["title"],
                        "url": stub["bestblogs_url"],
                        "reason": f"detail_read_failed: {exc}",
                    }
                )
                continue
            if not primary_url:
                resolution_errors.append(
                    {
                        "title": candidate["title"],
                        "url": candidate["bestblogs_url"],
                        "reason": "primary_url_unresolved",
                    }
                )
                continue
            candidate["primary_url"] = primary_url
            resolved.append(candidate)

    candidates: list[dict[str, Any]] = []
    duplicates: list[dict[str, Any]] = []
    for candidate in resolved:
        candidate, duplicate = score_candidate(candidate, previous_items)
        if duplicate:
            duplicates.append(duplicate)
        else:
            candidates.append(candidate)
    return candidates, duplicates, resolution_errors


def parse_anthropic_listing_page(
    html_text: str,
    start_at: datetime,
    end_at: datetime,
    previous_items: list[PreviousItem],
    *,
    source: dict[str, Any],
    research_window_strategy: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    collector = LinkCollector()
    collector.feed(html_text)

    candidates: list[dict[str, Any]] = []
    duplicates: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    source_url = str(source["url"])
    source_host = host_of(source_url)
    pool = str(source.get("pool", "research"))
    allowed_prefixes = tuple(source.get("path_prefixes", []))
    excluded_prefixes = tuple(source.get("exclude_prefixes", []))
    category_labels = RESEARCH_CATEGORY_LABELS if pool == "research" else ANTHROPIC_NEWS_CATEGORY_LABELS

    for link in collector.links:
        url = normalize_url(absolute_url(source_url, link["href"]))
        if not url or url in seen_urls:
            continue
        if host_of(url) != source_host:
            continue
        path = re.sub(r"^https?://[^/]+", "", url)
        if allowed_prefixes and not path.startswith(allowed_prefixes):
            continue
        if excluded_prefixes and path.startswith(excluded_prefixes):
            continue

        title, category, description, published_at = parse_anthropic_listing_link(
            link["text"],
            category_labels,
        )
        if not title or not published_at:
            continue
        if not start_at <= published_at <= end_at:
            continue

        seen_urls.add(url)
        candidate: dict[str, Any] = {
            "title": title,
            "published_at": published_at.isoformat(),
            "aihot_url": "",
            "primary_url": url,
            "description": description or link["text"],
            "categories": [value for value in [f"Official {pool.title()}", category] if value],
            "feed_source": f"direct:{source['id']}",
            "source_name": source.get("name", source["id"]),
            "source_pool": pool,
            "source_authority": source.get("authority", "official_first_party"),
            "research_window_strategy": research_window_strategy,
            "research_eligible": pool == "research",
        }
        if source.get("source_type_override"):
            candidate["source_type"] = source["source_type_override"]
        candidate, duplicate = score_candidate(candidate, previous_items)
        if duplicate:
            duplicates.append(duplicate)
        else:
            candidates.append(candidate)

    return candidates, duplicates


def parse_direct_research_page(
    html_text: str,
    start_at: datetime,
    end_at: datetime,
    previous_items: list[PreviousItem],
    *,
    source_name: str,
    source_url: str,
    research_window_strategy: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Backward-compatible wrapper for custom Anthropic-style research URLs."""

    source = {
        "id": source_name,
        "name": source_name,
        "url": source_url,
        "pool": "research",
        "authority": "official_research",
        "path_prefixes": ["/research/"],
        "exclude_prefixes": ["/research/team/"],
        "source_type_override": "paper",
    }
    return parse_anthropic_listing_page(
        html_text,
        start_at,
        end_at,
        previous_items,
        source=source,
        research_window_strategy=research_window_strategy,
    )


def candidate_merge_key(candidate: dict[str, Any]) -> str:
    """Merge same-message rewrites before topic diversity is applied."""

    identities = candidate_identities(candidate)
    message_identities = sorted(identity for identity in identities if identity.startswith("msg:"))
    if message_identities:
        return message_identities[0]
    return candidate.get("primary_url") or candidate.get("aihot_url") or normalize_text(candidate.get("title", ""))


def merge_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for candidate in candidates:
        key = candidate_merge_key(candidate)
        if not key:
            continue
        existing = merged.get(key)
        if existing is None:
            merged[key] = candidate
            continue

        existing_sources = set(str(existing.get("feed_source", "")).split("+"))
        candidate_sources = set(str(candidate.get("feed_source", "")).split("+"))
        sources = existing_sources | candidate_sources
        preferred = candidate if "paper" in candidate_sources and "paper" not in existing_sources else existing
        if preferred is not existing:
            merged[key] = preferred
            existing = preferred
        existing["feed_source"] = "+".join(sorted(source for source in sources if source))
        source_names = [
            value
            for value in [existing.get("source_name", ""), candidate.get("source_name", "")]
            if value
        ]
        if source_names:
            existing["source_name"] = " + ".join(dict.fromkeys(source_names))
        categories = list(dict.fromkeys(existing.get("categories", []) + candidate.get("categories", [])))
        existing["categories"] = categories
        if candidate.get("bestblogs_url") and not existing.get("bestblogs_url"):
            existing["bestblogs_url"] = candidate["bestblogs_url"]
        if candidate.get("bestblogs_score"):
            existing["bestblogs_score"] = max(
                int(existing.get("bestblogs_score") or 0),
                int(candidate["bestblogs_score"]),
            )
        existing["research_eligible"] = bool(existing.get("research_eligible", True)) or bool(
            candidate.get("research_eligible", True)
        )
        if candidate.get("research_window_strategy") == "exact_window":
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


def estimated_reading_minutes(candidate: dict[str, Any], *, research: bool = False) -> float:
    """Estimate scan-and-understand time for the detailed email treatment.

    These are deliberately transparent editorial estimates rather than measured
    analytics. They make the "more items only when worth it" tradeoff visible
    without imposing a hard item cap.
    """

    if research:
        return 3.0
    group = candidate.get("selection_group", "")
    if group in {"methodology", "security_boundary", "enterprise_integration", "infrastructure"}:
        return 1.2
    if group in {"model_release", "consumer_entry", "developer_tool", "creative_ops"}:
        return 1.0
    return 0.9


def selected_reading_estimate(selected_research: list[dict[str, Any]], selected_news: list[dict[str, Any]]) -> dict[str, Any]:
    research_minutes = round(sum(estimated_reading_minutes(item, research=True) for item in selected_research), 1)
    news_minutes = round(sum(estimated_reading_minutes(item) for item in selected_news), 1)
    daily_minutes = round(research_minutes + news_minutes, 1)
    return {
        "estimate_kind": "editorial_scan_and_understand_estimate",
        "research_minutes": research_minutes,
        "news_minutes": news_minutes,
        "total_minutes": daily_minutes,
        "soft_budget_minutes": DEFAULT_DAILY_READING_BUDGET_MINUTES,
        "over_soft_budget": daily_minutes > DEFAULT_DAILY_READING_BUDGET_MINUTES,
    }


def select_items(
    candidates: list[dict[str, Any]],
    research_count: int,
    min_news_count: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    research_candidates = sorted(
        [c for c in candidates if c["scores"]["final_research"] >= 70 and is_research_eligible(c)],
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
    used = {candidate_selection_key(item) for item in selected_research}

    news_pool = sorted(
        [
            c
            for c in candidates
            if candidate_selection_key(c) not in used and c["scores"]["final_news"] >= 76
            and c.get("research_window_strategy") not in {"paper_lookback", "direct_research_lookback"}
        ],
        key=lambda c: (c["scores"]["final_news"], c["scores"]["total"]),
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
    def can_add(item: dict[str, Any], *, exceptional: bool = False) -> bool:
        group = item["selection_group"]
        if group == "general_router":
            return False
        group_limit = group_limits.get(group, 1)
        return exceptional or group == "other" or group_counts.get(group, 0) < group_limit

    # First, guarantee a compact but useful baseline. If the strict 76-point
    # pool cannot reach the requested minimum, use the next best non-duplicate
    # items rather than producing a too-thin daily.
    for item in news_pool:
        group = item["selection_group"]
        if not can_add(item):
            continue
        selected_news.append(item)
        used.add(candidate_selection_key(item))
        if group != "other":
            group_counts[group] = group_counts.get(group, 0) + 1
        if len(selected_news) >= min_news_count:
            break

    if len(selected_news) < min_news_count:
        fallback_pool = sorted(
            [
                c
                for c in candidates
                if candidate_selection_key(c) not in used
                and c.get("research_window_strategy") not in {"paper_lookback", "direct_research_lookback"}
                and c["scores"]["final_news"] >= 70
            ],
            key=lambda c: (c["scores"]["final_news"], c["scores"]["total"]),
            reverse=True,
        )
        for item in fallback_pool:
            # Topic diversity is preferred, but the explicit minimum takes
            # precedence when a quiet day has too few distinct groups.
            if item["selection_group"] == "general_router":
                continue
            selected_news.append(item)
            used.add(candidate_selection_key(item))
            group = item["selection_group"]
            if group != "other":
                group_counts[group] = group_counts.get(group, 0) + 1
            if len(selected_news) >= min_news_count:
                break

    # There is intentionally no numeric upper cap. Extra items must clear a
    # stronger quality bar; once the reader's soft daily budget is crossed,
    # they must be exceptional. This preserves room for genuinely eventful
    # days while exposing their attention cost in the output JSON.
    for item in news_pool:
        if candidate_selection_key(item) in used:
            continue
        score = item["scores"]["final_news"]
        current_cost = selected_reading_estimate(selected_research, selected_news)
        required_score = (
            NEWS_OVER_BUDGET_SCORE_THRESHOLD
            if current_cost["total_minutes"] >= DEFAULT_DAILY_READING_BUDGET_MINUTES
            else NEWS_EXPANSION_SCORE_THRESHOLD
        )
        exceptional = score >= NEWS_EXCEPTIONAL_SCORE_THRESHOLD
        if score < required_score or not can_add(item, exceptional=exceptional):
            continue
        selected_news.append(item)
        used.add(candidate_selection_key(item))
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
        key=lambda c: (editorial_order.get(c["selection_group"], 99), -c["scores"]["final_news"])
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
            item["selection_group"] != "other"
            and selected_group_counts.get(item["selection_group"], 0) >= group_limits.get(item["selection_group"], 1)
        ):
            reason = "同主题已有更高优先级条目，避免当天内部重复。"
        elif item["scores"]["final_research"] >= 70:
            reason = "研究候选不错，但今日研究前沿名额有限。"
        elif item["scores"]["final_news"] >= 76:
            reason = "新闻价值尚可，但综合优先级略低。"
        else:
            reason = "更偏工具猎奇、硬件/政策/泛生态或来源强度不足。"
        packed = pack_candidate(item)
        packed["reason"] = reason
        rejected.append(packed)
        if len(rejected) >= 18:
            break

    return selected_research, selected_news, rejected


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
    paper_lookback_hours: int,
    paper_lookback_start_at: datetime,
    start_at: datetime,
    news_start_at: datetime,
    news_lookback_hours: int,
    allow_wide_news_window: bool,
    end_at: datetime,
    previous_paths: list[Path],
    previous_items: list[PreviousItem],
    candidates: list[dict[str, Any]],
    duplicates: list[dict[str, Any]],
    selected_research: list[dict[str, Any]],
    selected_news: list[dict[str, Any]],
    rejected: list[dict[str, Any]],
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
            "official_source_error_count": len(official_source_errors),
            "paper_lookback_hours": paper_lookback_hours,
            "news_lookback_hours": news_lookback_hours,
            "previous_content_paths": [str(path) for path in previous_paths],
            "previous_item_count": len(previous_items),
        },
        "window": {
            "start_at": start_at.isoformat(),
            "news_start_at": news_start_at.isoformat(),
            "end_at": end_at.isoformat(),
            "timezone": "Asia/Shanghai",
            "paper_lookback_start_at": paper_lookback_start_at.isoformat(),
            "news_window_policy": (
                "wide_window_allowed"
                if allow_wide_news_window
                else f"news is clamped to at most {news_lookback_hours} hours; paper lookback is research-only"
            ),
        },
        "scoring_policy": {
            "reader_preference_weight": 30,
            "industry_impact_weight": 30,
            "translation_value_weight": 25,
            "source_reliability_weight": 10,
            "freshness_diversity_weight": 5,
            "news_high_score_threshold": NEWS_HIGH_SCORE_THRESHOLD,
            "technique_sparse_bonus": (
                f"若当天没有技巧/方法类资讯达到 {NEWS_HIGH_SCORE_THRESHOLD} 分，"
                f"则给技巧/方法类候选 final_news +{TECHNIQUE_SPARSE_BONUS}，"
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
                f"AIHot Daily 命中的已验证候选可获得 +{AIHOT_DAILY_EDITORIAL_BONUS} 分编辑加权；"
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
                if item.get("score_breakdown", {}).get("technique_sparse_bonus") == TECHNIQUE_SPARSE_BONUS
            ),
            "duplicate_policy": (
                "只剔除内容级重复：同 URL、同 AIHot item、标题高度相似、"
                "同一篇论文/报告或同一条消息复述。"
                "同公司/同产品连续出现不视为重复，但必须讲清新增信息点。"
            ),
            "research_rule": "研究前沿优先论文、benchmark、方法论和能说明 Agent/模型能力边界的条目。",
            "news_rule": (
                f"行业动态至少 {DEFAULT_MIN_NEWS_COUNT} 条，无固定上限；超过基础数量后，"
                f"新增条目需达到 {NEWS_EXPANSION_SCORE_THRESHOLD:.0f} 分，"
                f"当预估阅读时长超过 {DEFAULT_DAILY_READING_BUDGET_MINUTES:.0f} 分钟时需达到 "
                f"{NEWS_OVER_BUDGET_SCORE_THRESHOLD:.0f} 分。"
                "普通新闻不使用 72 小时兜底，默认只看上一期正式发送后且最多 48 小时内的新内容；"
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
            "supplemental_news_sources": [
                {
                    "name": "AIHot Daily",
                    "url": daily_url,
                    "enabled": daily_enabled,
                    "role": "editorial_prefilter_and_priority_signal",
                    "policy": "Only boosts matching candidates already discovered from normal feeds. It never replaces primary-source resolution, freshness checks, or duplicate filtering.",
                    "entry_count": len(daily_entries),
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
        "selected_research": [pack_candidate(item, idx + 1) for idx, item in enumerate(selected_research)],
        "selected_news": [pack_candidate(item, idx + 1) for idx, item in enumerate(selected_news)],
        "rejected_items": rejected,
        "excluded_duplicates": duplicates,
        "all_candidates_scored": [pack_candidate(item) for item in candidates],
    }


def default_output_path(run_date: str) -> Path:
    return Path("daily_runs") / run_date / "source_selection.json"


def default_feed_path(run_date: str) -> Path:
    return Path("daily_runs") / run_date / "source_selection_raw" / "aihot_feed.xml"


def default_paper_feed_path(run_date: str) -> Path:
    return Path("daily_runs") / run_date / "source_selection_raw" / "aihot_paper_feed.xml"


def default_all_feed_path(run_date: str) -> Path:
    return Path("daily_runs") / run_date / "source_selection_raw" / "aihot_all_feed.xml"


def default_daily_path(run_date: str) -> Path:
    return Path("daily_runs") / run_date / "source_selection_raw" / "aihot_daily.html"


def default_bestblogs_feed_path(run_date: str) -> Path:
    return Path("daily_runs") / run_date / "source_selection_raw" / "bestblogs_ai_high_score.xml"


def default_bestblogs_detail_path(run_date: str, resource_id: str) -> Path:
    safe_id = re.sub(r"[^a-zA-Z0-9_-]+", "_", resource_id).strip("_") or "resource"
    return Path("daily_runs") / run_date / "source_selection_raw" / "bestblogs" / f"{safe_id}.html"


def default_direct_research_path(run_date: str, source_name: str) -> Path:
    safe_name = re.sub(r"[^a-zA-Z0-9_-]+", "_", source_name).strip("_") or "direct_research"
    return Path("daily_runs") / run_date / "source_selection_raw" / f"{safe_name}.html"


def default_official_source_path(run_date: str, source: dict[str, Any]) -> Path:
    safe_name = re.sub(r"[^a-zA-Z0-9_-]+", "_", str(source["id"])).strip("_") or "official_source"
    suffix = ".xml" if source.get("transport") == "rss" else ".html"
    return Path("daily_runs") / run_date / "source_selection_raw" / "official" / f"{safe_name}{suffix}"


def main() -> None:
    parser = argparse.ArgumentParser(description="Select layered sources for the AI daily workflow.")
    parser.add_argument("--date", default=datetime.now(TZ).strftime("%Y-%m-%d"))
    parser.add_argument("--start-at", help="Inclusive start time, ISO format. Naive values are Asia/Shanghai.")
    parser.add_argument("--end-at", help="Inclusive end time, ISO format. Defaults to now in Asia/Shanghai.")
    parser.add_argument("--feed-url", default=DEFAULT_FEED_URL)
    parser.add_argument("--paper-feed-url", default=DEFAULT_PAPER_FEED_URL)
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
        "--no-official-sources",
        action="store_true",
        help="Disable all optional first-party sources registered as official_direct for this run.",
    )
    parser.add_argument("--paper-lookback-hours", type=int, default=72)
    parser.add_argument(
        "--news-lookback-hours",
        type=int,
        default=DEFAULT_NEWS_LOOKBACK_HOURS,
        help=(
            "Maximum lookback for ordinary news. Paper/research lookback is controlled "
            "separately by --paper-lookback-hours."
        ),
    )
    parser.add_argument(
        "--allow-wide-news-window",
        action="store_true",
        help="Disable the ordinary-news lookback clamp. Use only for explicit backfills.",
    )
    parser.add_argument("--previous-content", type=Path, action="append", default=[])
    parser.add_argument("--no-auto-previous", action="store_true")
    parser.add_argument("--research-count", type=int, default=1)
    parser.add_argument(
        "--min-news-count",
        type=int,
        default=DEFAULT_MIN_NEWS_COUNT,
        help="Minimum selected industry-news count. There is no maximum cap.",
    )
    parser.add_argument(
        "--news-count",
        type=int,
        dest="legacy_min_news_count",
        help="Compatibility alias for --min-news-count. It is no longer a maximum cap.",
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    run_date = args.date
    midnight_start = datetime.strptime(run_date, "%Y-%m-%d").replace(tzinfo=TZ)
    default_start = latest_previous_prod_send_at(run_date) or midnight_start
    start_at = parse_datetime(args.start_at, default_start)
    end_at = parse_datetime(args.end_at, datetime.now(TZ))
    news_start_at = start_at
    if not args.allow_wide_news_window and args.news_lookback_hours >= 0:
        news_start_at = max(start_at, end_at - timedelta(hours=args.news_lookback_hours))
    feed_path = args.feed_path or default_feed_path(run_date)
    paper_feed_path = args.paper_feed_path or default_paper_feed_path(run_date)
    all_feed_path = args.all_feed_path or default_all_feed_path(run_date)
    daily_path = args.daily_path or default_daily_path(run_date)
    bestblogs_feed_path = args.bestblogs_feed_path or default_bestblogs_feed_path(run_date)
    output_path = args.output or default_output_path(run_date)
    paper_lookback_start_at = min(start_at, end_at - timedelta(hours=max(args.paper_lookback_hours, 0)))

    previous_paths = list(args.previous_content)
    if not args.no_auto_previous and not previous_paths:
        previous_paths = auto_previous_content(run_date)
    previous_items = load_previous_items(previous_paths)

    source_registry = load_source_registry(args.source_registry)
    daily_source = next(
        (
            source
            for source in source_registry["sources"]
            if source.get("enabled") and source.get("integration") == "daily_editorial"
        ),
        None,
    )
    daily_url = args.daily_url or str((daily_source or {}).get("url") or DEFAULT_DAILY_URL)
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
        news_start_at,
        end_at,
        previous_items,
        feed_source="main",
        research_window_strategy="exact_window",
    )

    all_candidates: list[dict[str, Any]] = []
    all_duplicates: list[dict[str, Any]] = []
    if not args.no_aihot_all:
        all_xml_text = run_curl(args.all_feed_url, all_feed_path)
        all_candidates, all_duplicates = parse_feed(
            all_xml_text,
            news_start_at,
            end_at,
            previous_items,
            feed_source="all",
            research_window_strategy="exact_window",
        )

    daily_entries: list[dict[str, Any]] = []
    daily_matches: list[dict[str, Any]] = []
    daily_fetch_error = ""
    if not args.no_aihot_daily:
        try:
            daily_entries = parse_aihot_daily_entries(run_curl(daily_url, daily_path))
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

    def fetch_official_source(source: dict[str, Any]) -> tuple[dict[str, Any], str]:
        source_path = default_official_source_path(run_date, source)
        return source, run_curl(str(source["url"]), source_path)

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
                else:
                    raise SourceSelectionError(f"Unsupported official source adapter: {source.get('id')}")
            except (SourceSelectionError, ET.ParseError, OSError) as exc:
                official_source_errors.append(
                    {
                        "source_id": str(source.get("id", "")),
                        "url": str(source.get("url", "")),
                        "reason": str(exc),
                    }
                )
                continue
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
        exact_merged_candidates + paper_lookback_candidates + direct_candidates + bestblogs_candidates
    )
    duplicates = merge_packed_candidates(
        exact_duplicates
        + all_duplicates
        + paper_exact_duplicates
        + paper_lookback_duplicates
        + direct_duplicates
        + bestblogs_duplicates
    )
    apply_sparse_technique_bonus(candidates)
    if not args.no_aihot_daily and daily_entries:
        daily_matches = apply_aihot_daily_signal(candidates, daily_entries)
    else:
        for candidate in candidates:
            candidate["aihot_daily_featured"] = False
            candidate["aihot_daily_rank"] = None
            candidate.setdefault("score_breakdown", {})["aihot_daily_editorial_bonus"] = 0
    min_news_count = args.legacy_min_news_count or args.min_news_count
    selected_research, selected_news, rejected = select_items(
        candidates,
        research_count=args.research_count,
        min_news_count=min_news_count,
    )

    output = build_output(
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
        paper_lookback_hours=args.paper_lookback_hours,
        paper_lookback_start_at=paper_lookback_start_at,
        start_at=start_at,
        news_start_at=news_start_at,
        news_lookback_hours=args.news_lookback_hours,
        allow_wide_news_window=args.allow_wide_news_window,
        end_at=end_at,
        previous_paths=previous_paths,
        previous_items=previous_items,
        candidates=candidates,
        duplicates=duplicates,
        selected_research=selected_research,
        selected_news=selected_news,
        rejected=rejected,
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(f"wrote {output_path}")
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


if __name__ == "__main__":
    try:
        main()
    except SourceSelectionError as exc:
        raise SystemExit(f"source selection failed: {exc}") from exc
