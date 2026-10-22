"""Source identity."""
from __future__ import annotations
from typing import Any
from dataclasses import dataclass
from datetime import datetime
from dataclasses import field
import html
import re
from source_urls import clean_source_url
from source_selection_constants import (
    AIHOT_ITEM_RE,
    ARXIV_RE,
    CANONICAL_WWW_HOSTS,
    DOI_RE,
    HTML_TAG_RE,
    TZ,
    URL_RE,
    X_STATUS_ID_RE,
)


class SourceSelectionError(Exception):
    pass


@dataclass
class PreviousItem:
    title: str = ""
    urls: set[str] = field(default_factory=set)
    text: str = ""
    identities: set[str] = field(default_factory=set)


def clean_text(value: str | None) -> str:
    value = html.unescape(value or "")
    value = HTML_TAG_RE.sub("", value)
    return re.sub(r"\s+", " ", value).strip()


def normalize_text(value: str | None) -> str:
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", (value or "").lower())


def normalize_url(value: str | None) -> str:
    if not value:
        return ""
    value = clean_source_url(value).rstrip("，。).,")
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
    canonical_hosts = "|".join(re.escape(host) for host in sorted(CANONICAL_WWW_HOSTS))
    value = re.sub(
        rf"^(https?://)www\.({canonical_hosts})(?=[:/?#]|$)",
        r"\1\2",
        value,
        flags=re.I,
    )
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
        if host_of(url) not in {"aihot.virxact.com", "aihot.news"}:
            return url
    return normalize_url(aihot_url)


def normalize_x_handle(value: str | None) -> str:
    return str(value or "").strip().lstrip("@").lower()


def x_handle_from_url(url: str | None) -> str:
    match = re.match(r"https?://(?:www\.)?(?:x\.com|twitter\.com)/([^/?#]+)", url or "", re.I)
    return normalize_x_handle(match.group(1)) if match else ""


def extract_aihot_id(url: str | None) -> str:
    match = AIHOT_ITEM_RE.search(url or "")
    return match.group(1) if match else ""


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
    if "codex micro" in text and has_any(
        text,
        ["work louder", "work-louder", "keyboard", "键盘", "控制器", "co-lab"],
    ):
        keys.add("openai-codex-micro-keyboard")
    if has_any(text, ["apple 智能", "apple intelligence", "国行 iphone"]) and has_any(
        text,
        ["千问", "qwen", "备案", "集成"],
    ):
        keys.add("apple-intelligence-china-qwen-integration")
    if "grok build" in text and has_any(text, ["开源", "open source", "open-source", "全部代码"]):
        keys.add("xai-grok-build-open-source")
    if has_any(text, ["微软", "microsoft"]) and has_any(text, ["销售团队", "salespeople", "销售人员"]) and has_any(
        text,
        ["openai", "anthropic", "谷歌", "google", "贬低", "talk down"],
    ):
        keys.add("microsoft-ai-sales-positioning")
    if has_any(text, ["gpt-red", "gpt red"]) and has_any(
        text,
        ["红队", "red team", "red-team", "提示注入", "prompt injection", "鲁棒性"],
    ):
        keys.add("openai-gpt-red-automated-red-teaming")

    return keys


def has_any(text: str, words: list[str]) -> bool:
    return any(word.lower() in text for word in words)


def parse_datetime(value: str | None, default: datetime | None = None) -> datetime:
    if not value:
        if default is None:
            raise SourceSelectionError("Missing datetime value.")
        return default
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=TZ)
    return parsed.astimezone(TZ)


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

