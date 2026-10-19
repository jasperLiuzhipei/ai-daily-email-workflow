"""Deterministic contracts shared by preview, approval and delivery rehearsal."""
from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, timedelta
from hashlib import sha256
import json
import re
from urllib.parse import urlsplit

from bs4 import BeautifulSoup
from editorial_backlog import identity, item_urls


class GateError(ValueError):
    pass


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(value):
    return sha256(value if isinstance(value, bytes) else canonical(value).encode()).hexdigest()


def subject(day):
    iso = date.fromisoformat(day).isocalendar()
    return f"AI 情报雷达｜{iso.year}年第{iso.week}周"


def safe_url(value):
    if not isinstance(value, str) or re.search(r"[\x00-\x20\x7f]", value):
        return False
    try:
        parts = urlsplit(value)
        return parts.scheme in {"https", "http"} and bool(parts.hostname) and not parts.username and not parts.password
    except ValueError:
        return False


def recipients(values):
    result = []
    for raw in values:
        if not isinstance(raw, str):
            raise GateError("Recipients must be plain email addresses")
        email = raw.strip().lower()
        if not re.fullmatch(r"[a-z0-9.!#$%&'*+/=?^_`{|}~-]+@[a-z0-9-]+(?:\.[a-z0-9-]+)+", email):
            raise GateError("Invalid recipient address")
        if email not in result:
            result.append(email)
    if not result:
        raise GateError("A frozen recipient snapshot is required")
    return sorted(result)


def normalize(content):
    """One-way legacy adapter. Never invent missing editorial content."""
    data = deepcopy(content)
    if "research_items" in data:
        depth = data.pop("research_items")
    else:
        depth = [data["paper"]] if isinstance(data.get("paper"), dict) else []
    data.pop("paper", None)
    data["deep_features"] = data.get("deep_features", depth)
    for card in data.get("news", []) + data["deep_features"]:
        urls = item_urls(card)
        keys = sorted({identity(url) for url in urls})
        card["card_id"] = card.get("card_id") or "card_" + digest(keys or card.get("title", ""))[:20]
        card["source_identities"] = keys
        card.pop("image", None)
        card.pop("image_alt", None)
    data["schema_version"] = 2
    return data


def preflight(content, html, day, recipient_list):
    """Structural gates only. A pass is explicitly not a factual AI review."""
    errors, warnings = [], []
    news, deep = content.get("news", []), content.get("deep_features", [])
    if not 1 <= len(news) <= 12:
        errors.append("news_count: requires 1–12")
    elif len(news) < 5:
        warnings.append("low_news_count")
    if len(deep) > 1:
        errors.append("deep_count: at most one")
    ids, sources = set(), set()
    soup = BeautifulSoup(html, "html.parser")
    if not soup.body or soup.body.get("data-ai-daily-date") != day:
        errors.append("html_date_mismatch")
    if not soup.title or soup.title.get_text() != subject(day):
        errors.append("weekly_subject_mismatch")
    if soup.select("img,svg,canvas,script,iframe,object,embed,form,link,base,meta[http-equiv]"):
        errors.append("text_edition_has_active_or_external_assets")
    all_ids = [node["id"] for node in soup.select("[id]")]
    if len(set(all_ids)) != len(all_ids):
        errors.append("duplicate_html_anchor")
    hrefs = set()
    for node in soup.find_all(True):
        if any(key.lower().startswith("on") for key in node.attrs):
            errors.append("html_event_handler")
        for attr in ("src", "background"):
            if node.get(attr):
                errors.append("unexpected_asset_reference")
        if re.search(r"url\s*\(|expression\s*\(|@import", str(node.get("style", "")), re.I):
            errors.append("external_or_active_css")
    for style in soup.select("style"):
        if re.search(r"url\s*\(|expression\s*\(|@import", style.get_text(), re.I):
            errors.append("external_or_active_css")
    for a in soup.select("a[href]"):
        href = a["href"]
        hrefs.add(href)
        if href.startswith("#"):
            if href[1:] not in all_ids:
                errors.append("broken_anchor")
        elif not safe_url(href) and not href.startswith("mailto:"):
            errors.append("unsafe_link")
    text = soup.get_text(" ", strip=True)
    for card in news + deep:
        card_id = card.get("card_id")
        if not card_id or card_id in ids:
            errors.append("missing_or_duplicate_card_id")
        ids.add(card_id)
        required = ("title", "one_liner", "why") if card in news else ("title", "evidence", "insight", "boundary")
        for key in required:
            if not card.get(key):
                errors.append(f"{card_id}: missing {key}")
        if card.get("title", "") not in text:
            errors.append(f"{card_id}: title_not_rendered")
        urls = item_urls(card)
        if not urls or not all(safe_url(url) for url in urls):
            errors.append(f"{card_id}: source_missing_or_invalid")
        if not any(url in hrefs for url in urls):
            errors.append(f"{card_id}: source_not_directly_linked")
        keys = set(card.get("source_identities", []))
        if keys & sources:
            errors.append(f"{card_id}: duplicate_source")
        sources |= keys
    try:
        recipients(recipient_list)
    except GateError as exc:
        errors.append(str(exc))
    if not content.get("editor_note"):
        errors.append("missing_editor_note")
    return {"schema_version": 2, "kind": "deterministic_preflight", "errors": sorted(set(errors)),
            "warnings": sorted(set(warnings)), "news_count": len(news), "deep_count": len(deep),
            "reading_minutes": max(1, round(len(text) / 550)), "link_check": "syntax_and_rendered_coverage_only",
            "client_test": "not_performed", "test_delivery": "not_performed"}


def coverage_window(now, last_cutoff=None, track="timely_news"):
    current = datetime.fromisoformat(now)
    if current.tzinfo is None:
        raise GateError("Coverage timestamp must include timezone")
    hours = {"timely_news": 48, "durable_method": 96, "research": 72}[track]
    gap = None
    if last_cutoff:
        cutoff = datetime.fromisoformat(last_cutoff)
        if cutoff.tzinfo is None or cutoff > current:
            raise GateError("Invalid previous selection cutoff")
        gap = (current - cutoff).total_seconds() / 3600
        hours = min(96, max(hours, gap))
    return {"from": (current - timedelta(hours=hours)).isoformat(), "to": current.isoformat(),
            "hours": hours, "uncovered_gap_hours": max(0, (gap or 0) - 96),
            "previous_cutoff_known": last_cutoff is not None}
