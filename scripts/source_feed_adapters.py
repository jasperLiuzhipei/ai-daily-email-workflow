"""Source feed adapters."""
from __future__ import annotations
from typing import Any
import xml.etree.ElementTree as ET
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import as_completed
from datetime import datetime, timedelta
import html
import gzip
import io
import zlib
from email.utils import parsedate_to_datetime
import re
import subprocess
import source_selection_context as context
from source_candidates import (
    direct_candidate_from_row,
)
from source_identity import (
    PreviousItem,
    SourceSelectionError,
    clean_text,
    first_primary_url,
    host_of,
    normalize_url,
)
from source_candidates import prepare_candidate
from source_selection_constants import (
    AIHOT_DAILY_ITEM_RE,
    BESTBLOGS_PRIMARY_URL_RE,
    BESTBLOGS_RESOURCE_ID_RE,
    TZ,
    X_STATUS_ID_RE,
)
from source_selection_paths import (
    default_bestblogs_detail_path,
)
from source_taxonomy import (
    is_research_eligible,
    major_gap_audit_reason,
)


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


def run_curl(
    url: str,
    output: Path,
    *,
    max_time_seconds: int = 60,
    attempts: int = 3,
) -> str:
    output.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "curl",
        "-L",
        "--fail",
        "--compressed",
        "--http1.1",
        "--connect-timeout",
        "8",
        "--max-time",
        str(max_time_seconds),
        "-A",
        "Mozilla/5.0 ai-daily-source-selector/0.1",
        "-o",
        str(output),
        url,
    ]
    errors: list[str] = []
    for attempt in range(1, max(1, attempts) + 1):
        result = subprocess.run(cmd, text=True, capture_output=True)
        if result.returncode == 0:
            try:
                data = output.read_bytes()
                # Some responses omit Content-Encoding despite carrying gzip.
                # Decode before UTF-8; replacement decoding destroys gzip bytes.
                if data.startswith(b"\x1f\x8b"):
                    with gzip.GzipFile(fileobj=io.BytesIO(data)) as stream:
                        data = stream.read(32 * 1024 * 1024 + 1)
                    if len(data) > 32 * 1024 * 1024:
                        raise SourceSelectionError("Decompressed source exceeds 32 MiB")
                    output.write_bytes(data)
                return data.decode("utf-8-sig", errors="replace")
            except (OSError, EOFError, zlib.error, SourceSelectionError) as exc:
                errors.append(f"attempt {attempt}: source response decode failed ({type(exc).__name__}): {exc}")
                continue
        detail = result.stderr.strip() or f"curl failed for {url}"
        errors.append(f"attempt {attempt}: {detail}")
    raise SourceSelectionError("\n".join(errors))


def categories_for(item: ET.Element) -> list[str]:
    return [clean_text(c.text) for c in item.findall("category") if c.text and clean_text(c.text)]


def parse_feed(
    xml_text: str,
    start_at: datetime,
    end_at: datetime,
    previous_items: list[PreviousItem],
    *,
    feed_source: str,
    research_window_strategy: str,
    news_start_at: datetime | None = None,
    gap_audit_start_at: datetime | None = None,
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
            feed_datetime = parsedate_to_datetime(pub_date)
            published_at = feed_datetime.astimezone(TZ)
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
        candidate, duplicate = prepare_candidate(candidate, previous_items)

        if news_start_at is not None and published_at < news_start_at:
            if is_research_eligible(candidate):
                candidate["research_window_strategy"] = "content_reclassified_research_lookback"
            elif (
                # Aggregators often serialize a date-only release as 00:00
                # UTC (08:00 Shanghai). Keep a narrow, explicitly flagged
                # boundary lead for review; never invent a publication hour.
                feed_datetime.utcoffset() == timedelta(0)
                and feed_datetime.hour == feed_datetime.minute == feed_datetime.second == 0
                and published_at.date() == news_start_at.date()
                and timedelta(0) < news_start_at - published_at <= timedelta(hours=1)
            ):
                candidate["research_window_strategy"] = "date_only_boundary_review"
                candidate["timing_review_required"] = True
                candidate["timing_note"] = "RSS reports 00:00 UTC near the news-window boundary; verify the original publication date and prior coverage."
            else:
                gap_reason = major_gap_audit_reason(candidate)
                gap_score = candidate.get("scores", {}).get("final_news", 0)
                if (
                    gap_audit_start_at is None
                    or published_at < gap_audit_start_at
                    or not gap_reason
                    or (not context.DISCOVERY_ONLY and gap_score < context.GAP_AUDIT_MIN_SCORE)
                ):
                    continue
                candidate["research_window_strategy"] = "major_event_gap_audit"
                candidate["gap_audit_reason"] = gap_reason

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
    if root.tag == "{http://www.w3.org/2005/Atom}feed":
        ns = {"a": "http://www.w3.org/2005/Atom"}
        candidates, duplicates = [], []
        for entry in root.findall("a:entry", ns):
            title = clean_text(entry.findtext("a:title", "", ns))
            published_at = parse_feed_datetime(entry.findtext("a:published", "", ns))
            url = normalize_url(entry.findtext("a:id", "", ns))
            if not title or not published_at or not url or not start_at <= published_at <= end_at:
                continue
            candidate, duplicate = prepare_candidate(direct_candidate_from_row({
                "title": title, "url": url, "published_at": published_at.isoformat(),
                "description": clean_text(entry.findtext("a:summary", "", ns)),
            }, source=source, research_window_strategy=research_window_strategy), previous_items)
            if duplicate:
                duplicates.append(duplicate)
            else:
                candidates.append(candidate)
        return candidates, duplicates
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
        candidate, duplicate = prepare_candidate(candidate, previous_items)
        if duplicate:
            duplicates.append(duplicate)
        else:
            candidates.append(candidate)
    return candidates, duplicates, resolution_errors
