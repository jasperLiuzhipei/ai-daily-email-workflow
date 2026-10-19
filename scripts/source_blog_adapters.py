"""Source blog adapters."""
from __future__ import annotations
from typing import Any
from bs4 import BeautifulSoup
from html.parser import HTMLParser
from datetime import datetime
from dataclasses import field
import html
import json
import re
from source_candidates import (
    direct_candidate_from_row,
)
from source_identity import (
    PreviousItem,
    absolute_url,
    clean_text,
    host_of,
    normalize_text,
    normalize_url,
    parse_datetime,
)
from source_candidates import prepare_candidate
from source_selection_constants import (
    ANTHROPIC_NEWS_CATEGORY_LABELS,
    DATED_LINK_TIME_CARD_RE,
    DATETIME_ATTR_RE,
    DATE_LABEL_RE,
    HREF_ATTR_RE,
    JSON_LD_SCRIPT_RE,
    NON_TECHNICAL_RESEARCH_CATEGORIES,
    RESEARCH_CATEGORY_LABELS,
    TZ,
)


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


class ReleaseNotesCollector(HTMLParser):
    """Collect dated cards from documentation-style release note pages.

    Z.ai and MiniMax publish first-party release notes as a page containing
    several dated cards rather than a feed of individual URLs. The layout uses
    the same useful semantic attributes (``update-label``, ``card-title`` and
    ``card-content``), so a small HTML parser is safer than relying on a
    browser-only rendering implementation.
    """

    _DATE_COMPONENTS = {"update-label"}
    _TITLE_COMPONENTS = {"update-description", "card-title"}
    _DESCRIPTION_COMPONENTS = {"update-content", "card-content"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[dict[str, str]] = []
        self._current_date = ""
        self._current_row: dict[str, str] | None = None
        self._captures: list[dict[str, Any]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_map = {key.lower(): value or "" for key, value in attrs}
        component = attr_map.get("data-component-part", "")
        field = ""
        if component in self._DATE_COMPONENTS:
            field = "date"
        elif component in self._TITLE_COMPONENTS:
            field = "title"
        elif component in self._DESCRIPTION_COMPONENTS:
            field = "description"
        # MiniMax puts its release date in an h4 heading before a card. Its
        # heading ID is date-like, which keeps generic page headings out.
        elif tag.lower() == "h4" and re.search(r"(?:19|20)\d{2}", attr_map.get("id", "")):
            field = "date"
        if field:
            self._captures.append({"field": field, "tag": tag.lower(), "parts": []})

    def handle_data(self, data: str) -> None:
        for capture in self._captures:
            capture["parts"].append(data)

    def handle_endtag(self, tag: str) -> None:
        if not self._captures or self._captures[-1]["tag"] != tag.lower():
            return
        capture = self._captures.pop()
        value = clean_text(" ".join(capture["parts"]))
        if not value:
            return
        if capture["field"] == "date":
            self._current_date = value
            self._current_row = None
            return
        if not self._current_date:
            return
        if capture["field"] == "title":
            self._current_row = {
                "title": value,
                "published_at": self._current_date,
                "description": "",
            }
            self.rows.append(self._current_row)
            return
        if capture["field"] == "description" and self._current_row is not None:
            existing = self._current_row["description"]
            self._current_row["description"] = clean_text(f"{existing} {value}")


def parse_display_date(value: str) -> datetime | None:
    match = DATE_LABEL_RE.search(value)
    if match:
        for pattern in ("%b %d, %Y", "%B %d, %Y"):
            try:
                return datetime.strptime(match.group(0), pattern).replace(tzinfo=TZ)
            except ValueError:
                continue
    try:
        parsed = datetime.fromisoformat((value or "").replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=TZ)
    return parsed.astimezone(TZ)


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
            # Anthropic's research index also contains economic and societal
            # reports. They remain useful signals, but do not automatically
            # compete with technical papers, methods, and evaluations.
            "research_eligible": pool == "research" and category not in NON_TECHNICAL_RESEARCH_CATEGORIES,
        }
        if source.get("source_type_override"):
            candidate["source_type"] = source["source_type_override"]
        if category in NON_TECHNICAL_RESEARCH_CATEGORIES:
            candidate["source_type"] = "report"
        candidate, duplicate = prepare_candidate(candidate, previous_items)
        if duplicate:
            duplicates.append(duplicate)
        else:
            candidates.append(candidate)

    return candidates, duplicates


def json_ld_nodes(value: Any) -> list[dict[str, Any]]:
    """Flatten a JSON-LD block enough to locate article-like entries."""

    if isinstance(value, list):
        return [node for item in value for node in json_ld_nodes(item)]
    if not isinstance(value, dict):
        return []
    nodes = [value]
    for key in ("@graph", "itemListElement", "mainEntity", "mainEntityOfPage"):
        nested = value.get(key)
        if isinstance(nested, (list, dict)):
            nodes.extend(json_ld_nodes(nested))
    item = value.get("item")
    if isinstance(item, (list, dict)):
        nodes.extend(json_ld_nodes(item))
    return nodes


def json_ld_url(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        for key in ("@id", "url"):
            if isinstance(value.get(key), str):
                return str(value[key])
    return ""


def generic_listing_rows(html_text: str, source_url: str) -> list[dict[str, str]]:
    """Extract dated article cards from JSON-LD, then fall back to dated links."""

    rows: list[dict[str, str]] = []
    # These first-party pages keep dates outside their article links. Restrict
    # adapters to observed host/structure pairs; never infer a date from fetch time.
    source_host = host_of(source_url)
    if source_host == "claude.com":
        soup = BeautifulSoup(html_text, "html.parser")
        for card in soup.select(".card_blog_wrap"):
            link = card.select_one('a[href^="/blog/"]')
            title = card.select_one(".card_blog_title")
            stamp = card.select_one(".u-text-style-caption")
            published_at = parse_display_date(stamp.get_text(" ", strip=True)) if stamp else None
            if not link or not title or not published_at:
                continue
            rows.append({"title": title.get_text(" ", strip=True),
                "url": normalize_url(absolute_url(source_url, link["href"])),
                "published_at": published_at.isoformat(), "description": ""})
        return rows

    if source_host in {"ai.google.dev", "api-docs.deepseek.com"}:
        soup = BeautifulSoup(html_text, "html.parser")
        for heading in soup.find_all("h2"):
            date_text = heading.get_text(" ", strip=True)
            published_at = parse_release_note_date(date_text) or parse_display_date(date_text)
            if not published_at:
                continue
            section = []
            for sibling in heading.find_next_siblings():
                if sibling.name in {"h1", "h2"}:
                    break
                section.append(sibling)
            if source_host == "ai.google.dev":
                entries = [li for node in section if node.name == "ul"
                    for li in node.find_all("li", recursive=False)]
            else:
                entries = [node for node in section if node.name == "h3"]
            for index, entry in enumerate(entries):
                title_node = entry.find("strong") if source_host == "ai.google.dev" else entry
                if not title_node:
                    continue
                title = clean_text(title_node.get_text(" ", strip=True)).replace("\u200b", "").strip()
                description = entry.get_text(" ", strip=True)
                if source_host == "api-docs.deepseek.com":
                    parts = []
                    for sibling in entry.find_next_siblings():
                        if sibling.name in {"h1", "h2", "h3"}:
                            break
                        parts.append(sibling.get_text(" ", strip=True))
                    description = " ".join(parts)
                # A date/entry query retains identity after fragment stripping.
                url = f"{normalize_url(source_url)}?release={published_at.date().isoformat()}&item={index}"
                rows.append({"title": title, "url": url,
                    "published_at": published_at.isoformat(), "description": description})
        return rows

    for raw_json in JSON_LD_SCRIPT_RE.findall(html_text):
        try:
            payload = json.loads(html.unescape(raw_json.strip()))
        except json.JSONDecodeError:
            continue
        for node in json_ld_nodes(payload):
            title = clean_text(str(node.get("headline") or node.get("name") or ""))
            url = normalize_url(absolute_url(source_url, json_ld_url(node.get("url") or node.get("mainEntityOfPage"))))
            published_at = parse_display_date(str(node.get("datePublished") or node.get("dateCreated") or ""))
            if not title or not url or not published_at:
                continue
            rows.append(
                {
                    "title": title,
                    "url": url,
                    "published_at": published_at.isoformat(),
                    "description": clean_text(str(node.get("description") or "")),
                }
            )

    if rows:
        return rows

    # Google Developers uses a date sibling outside the title link, not a
    # time element or JSON-LD. Parse only its observed article-card structure.
    if host_of(source_url) == "developers.googleblog.com":
        soup = BeautifulSoup(html_text, "html.parser")
        for card in soup.select(".post-item"):
            link = card.select_one("a.post-item__link[href]")
            stamp = card.select_one(".post-item__top")
            if not link or not stamp:
                continue
            date_text = stamp.get_text(" ", strip=True).title().replace("Sept.", "Sep").replace(".", "")
            published_at = parse_display_date(date_text)
            title_node = link.select_one("span, h2, h3, h4")
            if not published_at or not title_node:
                continue
            description = link.select_one("p")
            rows.append({"title": title_node.get_text(" ", strip=True),
                "url": normalize_url(absolute_url(source_url, link["href"])),
                "published_at": published_at.isoformat(),
                "description": description.get_text(" ", strip=True) if description else ""})
        return rows

    collector = LinkCollector()
    collector.feed(html_text)
    for link in collector.links:
        published_at = parse_display_date(link["text"])
        if not published_at:
            continue
        date_match = DATE_LABEL_RE.search(link["text"])
        title = clean_text(link["text"][: date_match.start()] if date_match else "")
        if not title:
            continue
        rows.append(
            {
                "title": title,
                "url": normalize_url(absolute_url(source_url, link["href"])),
                "published_at": published_at.isoformat(),
                "description": clean_text(link["text"]),
            }
        )
    return rows


def parse_release_note_date(value: str) -> datetime | None:
    """Parse the date labels used in first-party documentation release notes."""

    cleaned = (
        clean_text(value)
        .replace("\u200b", "")
        .replace("Sept.", "Sep")
        .replace("Sept", "Sep")
        .strip()
    )
    cleaned = re.sub(r"\b([A-Z][a-z]{2})\.", r"\1", cleaned)
    iso_match = re.search(r"\b(20\d{2}-\d{2}-\d{2})\b", cleaned)
    if iso_match:
        return datetime.strptime(iso_match.group(1), "%Y-%m-%d").replace(tzinfo=TZ)
    for pattern in ("%b %d, %Y", "%B %d, %Y"):
        try:
            return datetime.strptime(cleaned, pattern).replace(tzinfo=TZ)
        except ValueError:
            continue
    return None


def parse_dated_link_time_cards(
    html_text: str,
    start_at: datetime,
    end_at: datetime,
    previous_items: list[PreviousItem],
    *,
    source: dict[str, Any],
    research_window_strategy: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Parse a blog index whose title link and publication time are siblings.

    Moonshot's Kimi blog exposes clean, server-rendered article cards, but the
    article title and the ``<time>`` tag are separate elements.  The generic
    link parser cannot associate those siblings, so it would silently return
    no candidates even though this is a valid primary source.
    """

    candidates: list[dict[str, Any]] = []
    duplicates: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    source_url = str(source["url"])
    source_host = host_of(source_url)
    allowed_prefixes = tuple(source.get("path_prefixes", []))

    for match in DATED_LINK_TIME_CARD_RE.finditer(html_text):
        href_match = HREF_ATTR_RE.search(match.group("link_attrs"))
        datetime_match = DATETIME_ATTR_RE.search(match.group("time_attrs"))
        if not href_match:
            continue
        published_at = parse_display_date(
            datetime_match.group("value") if datetime_match else match.group("date_text")
        )
        title = clean_text(match.group("title"))
        url = normalize_url(absolute_url(source_url, href_match.group("value")))
        if not title or not url or not published_at or url in seen_urls:
            continue
        if host_of(url) != source_host:
            continue
        path = re.sub(r"^https?://[^/]+", "", url)
        if allowed_prefixes and not path.startswith(allowed_prefixes):
            continue
        if not start_at <= published_at <= end_at:
            continue

        seen_urls.add(url)
        candidate, duplicate = prepare_candidate(
            direct_candidate_from_row(
                {
                    "title": title,
                    "url": url,
                    "published_at": published_at.isoformat(),
                    "description": clean_text(match.group("date_text")),
                },
                source=source,
                research_window_strategy=research_window_strategy,
            ),
            previous_items,
        )
        if duplicate:
            duplicates.append(duplicate)
        else:
            candidates.append(candidate)
    return candidates, duplicates


def parse_release_notes_page(
    html_text: str,
    start_at: datetime,
    end_at: datetime,
    previous_items: list[PreviousItem],
    *,
    source: dict[str, Any],
    research_window_strategy: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Parse first-party model release note cards from a single official page."""

    collector = ReleaseNotesCollector()
    collector.feed(html_text)
    candidates: list[dict[str, Any]] = []
    duplicates: list[dict[str, Any]] = []
    seen_titles: set[str] = set()
    source_url = normalize_url(str(source["url"]))

    for row in collector.rows:
        published_at = parse_release_note_date(row["published_at"])
        title = clean_text(row["title"])
        title_key = normalize_text(title)
        if not title or not published_at or title_key in seen_titles:
            continue
        if not start_at <= published_at <= end_at:
            continue

        # These documentations use fragment links for individual releases.
        # Preserve an innocuous query key so records retain unique identities
        # after URL normalization intentionally removes fragments.
        row_url = f"{source_url}?release={published_at.date().isoformat()}&item={title_key[:48]}"
        seen_titles.add(title_key)
        candidate, duplicate = prepare_candidate(
            direct_candidate_from_row(
                {
                    "title": title,
                    "url": row_url,
                    "published_at": published_at.isoformat(),
                    "description": clean_text(row.get("description") or title),
                },
                source=source,
                research_window_strategy=research_window_strategy,
            ),
            previous_items,
        )
        if duplicate:
            duplicates.append(duplicate)
        else:
            candidates.append(candidate)
    return candidates, duplicates


def parse_generic_listing_page(
    html_text: str,
    start_at: datetime,
    end_at: datetime,
    previous_items: list[PreviousItem],
    *,
    source: dict[str, Any],
    research_window_strategy: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Parse a first-party listing page with standard JSON-LD or dated links."""

    candidates: list[dict[str, Any]] = []
    duplicates: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    source_url = str(source["url"])
    source_host = host_of(source_url)
    pool = str(source.get("pool", "news"))
    allowed_prefixes = tuple(source.get("path_prefixes", []))
    excluded_prefixes = tuple(source.get("exclude_prefixes", []))

    for row in generic_listing_rows(html_text, source_url):
        url = row["url"]
        published_at = parse_datetime(row["published_at"])
        if not url or url in seen_urls or host_of(url) != source_host:
            continue
        path = re.sub(r"^https?://[^/]+", "", url)
        if allowed_prefixes and not path.startswith(allowed_prefixes):
            continue
        if excluded_prefixes and path.startswith(excluded_prefixes):
            continue
        if not start_at <= published_at <= end_at:
            continue

        seen_urls.add(url)
        candidate: dict[str, Any] = {
            "title": row["title"],
            "published_at": published_at.isoformat(),
            "aihot_url": "",
            "primary_url": url,
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
        candidate, duplicate = prepare_candidate(candidate, previous_items)
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

