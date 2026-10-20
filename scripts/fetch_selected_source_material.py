#!/usr/bin/env python3
"""Fetch compact, prompt-safe source material for API editorial generation.

The selector deliberately works from feeds and metadata. This step adds a
bounded primary-source excerpt after selection, so the text model does not
have to invent a daily brief from titles alone. It is not an AI step and does
not require an API key.
"""

from __future__ import annotations

import argparse
import html
import ipaddress
import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from source_document import read_document
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from ai_daily_common import file_sha256, load_json_object, utc_now_iso, write_json_object


class SourceMaterialError(RuntimeError):
    pass


HUGGINGFACE_PAPER_ID_RE = re.compile(r"^https?://huggingface\.co/papers/(\d{4}\.\d{4,5})(?:[/?#].*)?$", re.I)




def normalize(value: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(value or "")).strip()


def compact_text(value: str, limit: int) -> str:
    value = normalize(value)
    if len(value) <= limit:
        return value
    return value[: max(0, limit - 1)].rstrip() + "…"


def normalize_source_url(value: str) -> str:
    """Return a feed URL with HTML entities decoded exactly once."""

    return html.unescape(str(value or "")).strip()


def safe_public_http_url(value: str) -> bool:
    # urllib's HTTP request line must be ASCII. Feed summaries occasionally
    # append full-width punctuation or prose to an otherwise valid URL; treat
    # those entries as unreadable candidates so research fallback can continue
    # instead of aborting the entire daily workflow with UnicodeEncodeError.
    try:
        value.encode("ascii")
    except UnicodeEncodeError:
        return False
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return False
    hostname = parsed.hostname.lower()
    if hostname in {"localhost", "localhost.localdomain"} or hostname.endswith(".local"):
        return False
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        return True
    return not (address.is_private or address.is_loopback or address.is_link_local or address.is_reserved)


def editorial_primary_url(value: str, source_type: str) -> str:
    """Resolve known index pages to the paper that supports research claims.

    Hugging Face's `/papers/<arXiv id>` pages are useful discovery entries but
    can be unavailable from a scheduled server. For a research card we should
    read and link the underlying arXiv paper, not silently downgrade to a
    social or index-page excerpt.
    """

    if source_type == "paper":
        match = HUGGINGFACE_PAPER_ID_RE.match(value)
        if match:
            return f"https://arxiv.org/abs/{match.group(1)}"
    return value


def fetch_html(url: str, *, timeout_seconds: int, max_bytes: int) -> tuple[str, str, int]:
    request = Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; AI-Daily-Source-Reader/1.0; +internal-editorial)",
            "Accept": "text/html,application/xhtml+xml,text/plain;q=0.8,*/*;q=0.2",
        },
    )
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            raw = response.read(max_bytes + 1)
            if len(raw) > max_bytes:
                raw = raw[:max_bytes]
            charset = response.headers.get_content_charset() or "utf-8"
            return raw.decode(charset, errors="replace"), response.geturl(), int(getattr(response, "status", 200))
    except HTTPError as exc:
        raise SourceMaterialError(f"HTTP {exc.code}") from exc
    except URLError as exc:
        raise SourceMaterialError(f"network error: {exc.reason}") from exc
    except OSError as exc:
        raise SourceMaterialError(f"request error: {exc}") from exc


def source_material(candidate: dict[str, Any], *, max_chars: int, timeout_seconds: int, max_bytes: int) -> dict[str, Any]:
    requested_url = normalize_source_url(candidate.get("primary_url") or candidate.get("source_url") or "")
    source_type = str(candidate.get("source_type") or "")
    primary_url = editorial_primary_url(requested_url, source_type)
    fallback = compact_text(str(candidate.get("source_excerpt") or ""), max_chars)
    record: dict[str, Any] = {
        "rank": candidate.get("rank"),
        "title": str(candidate.get("title") or ""),
        "primary_url": primary_url,
        "discovery_url": requested_url if requested_url != primary_url else "",
        "aihot_url": str(candidate.get("aihot_url") or ""),
        "primary_host": str(candidate.get("primary_host") or ""),
        "published_at": str(candidate.get("published_at") or ""),
        "source_type": source_type,
        "selection_group": str(candidate.get("selection_group") or ""),
        "topic_tags": candidate.get("topic_tags") if isinstance(candidate.get("topic_tags"), list) else [],
        "scores": candidate.get("scores") if isinstance(candidate.get("scores"), dict) else {},
        "source_quality_tier": str(candidate.get("source_quality_tier") or "discovery"),
        "boundary": str(candidate.get("boundary") or ""),
        "fallback_excerpt": fallback,
        "fetch_status": "fallback_only",
        "source_text": fallback,
    }
    verification = candidate.get("primary_verification", {})
    # Keep scoring and writing on the same bounded evidence snapshot. A later
    # fetch must not silently replace the material that justified selection.
    if candidate.get('editorial_assessment'):
        from event_editorial import digest, canonical_url
        packet = candidate.get('editorial_packet', {})
        assessment = candidate['editorial_assessment']
        body = candidate.get('source_text') or ''
        primary = next((s for s in packet.get('sources', [])
                        if s['source_id'] == assessment.get('primary_source_id')), {})
        if (assessment.get('input_sha256') != digest(packet)
                or candidate.get('editorial_source_text_sha256') != digest(body)
                or body != primary.get('text')
                or canonical_url(primary_url) != canonical_url(primary.get('url'))):
            raise SourceMaterialError('Editorial evidence changed after scoring; re-evaluate before composition')
        record.update(fetch_status='fetched', source_text=body,
                      event_id=candidate['event_id'], editorial_assessment=assessment,
                      coverage_source_urls=candidate.get('coverage_source_urls', []),
                      display_tier=candidate.get('display_tier'), daily_rank=candidate.get('daily_rank'),
                      resolved_url=primary_url)
        if candidate.get('quiet_day_admission'):
            record['quiet_day_admission'] = candidate['quiet_day_admission']
        record['boundary'] = assessment.get('limitations', '')
        from editorial_evidence import supporting_quotes
        record['supporting_evidence'] = supporting_quotes(candidate)
        return record
    if verification.get("status") == "verified" and candidate.get("source_text"):
        response_path = Path(verification.get("response_path", ""))
        if (response_path.is_file() and response_path.resolve().is_relative_to(Path.cwd().resolve())
                and file_sha256(response_path) == verification.get("response_sha256")):
            record.update(fetch_status="fetched", source_text=compact_text(candidate["source_text"], max_chars),
                          resolved_url=verification.get("evidence_url", primary_url), http_status=200,
                          page_title=verification.get("page_title", ""), primary_verification=verification)
            record['document_identity'] = verification.get('document_identity', {})
            if verification.get("date_conflict"):
                record["boundary"] = (record["boundary"] + " 官方页面与API日期字段不一致，不能将采集日期直接当作发布日期。 ").strip()
            return record
    if not safe_public_http_url(primary_url):
        record["fetch_status"] = "skipped_unsafe_or_missing_url"
        return record
    try:
        raw, final_url, http_status = fetch_html(primary_url, timeout_seconds=timeout_seconds, max_bytes=max_bytes)
        if http_status != 200 or not safe_public_http_url(final_url):
            raise SourceMaterialError("unsafe redirect or unsuccessful HTTP status")
        page_title, body, published_dates, identity = read_document(raw)
        combined = compact_text(body, max_chars)
        if not combined:
            raise SourceMaterialError("empty readable text")
        record.update(
            {
                "fetch_status": "fetched",
                "resolved_url": final_url,
                "http_status": http_status,
                "page_title": compact_text(page_title, 300),
                "source_text": combined,
                "document_identity": identity,
            }
        )
    except SourceMaterialError as exc:
        record["fetch_status"] = "fallback_after_fetch_error"
        record["fetch_error"] = str(exc)
    return record


def fetch_group(
    candidates: list[dict[str, Any]],
    *,
    max_chars: int,
    timeout_seconds: int,
    max_bytes: int,
    workers: int,
) -> list[dict[str, Any]]:
    if not candidates:
        return []
    results: dict[int, dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=max(1, min(workers, len(candidates)))) as pool:
        futures = {
            pool.submit(
                source_material,
                candidate,
                max_chars=max_chars,
                timeout_seconds=timeout_seconds,
                max_bytes=max_bytes,
            ): index
            for index, candidate in enumerate(candidates)
        }
        for future in as_completed(futures):
            index = futures[future]
            try:
                results[index] = future.result()
            except Exception as exc:  # Defensive: retain the selected item even if one parser fails.
                candidate = candidates[index]
                if candidate.get('editorial_assessment'):
                    raise SourceMaterialError('Evaluated evidence is invalid; re-evaluate before composition') from exc
                results[index] = {
                    "rank": candidate.get("rank"),
                    "title": str(candidate.get("title") or ""),
                    "primary_url": str(candidate.get("primary_url") or ""),
                    "fetch_status": "fallback_after_unexpected_error",
                    "fetch_error": str(exc),
                    "source_text": compact_text(str(candidate.get("source_excerpt") or ""), max_chars),
                }
    return [results[index] for index in range(len(candidates))]


def candidate_identity(candidate: dict[str, Any]) -> str:
    """Return a stable enough identity for same-run fallback de-duplication."""

    return str(
        candidate.get("primary_url")
        or candidate.get("source_url")
        or candidate.get("aihot_url")
        or candidate.get("bestblogs_url")
        or candidate.get("title")
        or ""
    ).strip()


def number(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def research_fallback_candidates(
    selection: dict[str, Any],
    *,
    selected_research: list[dict[str, Any]],
    selected_news: list[dict[str, Any]],
    limit: int,
) -> list[dict[str, Any]]:
    """Return the next eligible research candidates in normal selection order.

    This deliberately reuses the score thresholds persisted with the original
    selection, rather than broadening the research bar after a network error.
    Candidates already selected as news are excluded so a fallback cannot
    silently create a duplicate card in the same issue.
    """

    if selection.get('event_editorial'):
        return []  # V2 cannot replace an evaluated selection with v1 fallback scores.
    config_snapshot = selection.get("config_snapshot")
    policy_snapshot = (
        config_snapshot.get("selection_policy") if isinstance(config_snapshot, dict) else {}
    )
    policy = policy_snapshot.get("selection") if isinstance(policy_snapshot, dict) else {}
    research_min_score = number(
        policy.get("research_min_score") if isinstance(policy, dict) else None,
        70.0,
    )
    research_min_relevance = number(
        policy.get("research_min_internal_relevance") if isinstance(policy, dict) else None,
        15.0,
    )
    unavailable = {
        candidate_identity(item)
        for item in [*selected_research, *selected_news]
        if candidate_identity(item)
    }
    pool: list[dict[str, Any]] = []
    raw_candidates = selection.get("all_candidates_scored")
    if not isinstance(raw_candidates, list):
        return []
    for candidate in raw_candidates:
        if not isinstance(candidate, dict):
            continue
        identity = candidate_identity(candidate)
        scores = candidate.get("scores") if isinstance(candidate.get("scores"), dict) else {}
        if (
            not identity
            or identity in unavailable
            or candidate.get("research_eligible") is not True
            or number(scores.get("final_research")) < research_min_score
            or number(scores.get("internal_relevance")) < research_min_relevance
        ):
            continue
        unavailable.add(identity)
        pool.append(candidate)
    pool.sort(
        key=lambda item: (
            number((item.get("scores") or {}).get("final_research")),
            number((item.get("score_breakdown") or {}).get("source_reliability")),
            number((item.get("score_breakdown") or {}).get("industry_impact")),
            number((item.get("scores") or {}).get("final_news")),
            number((item.get("scores") or {}).get("total")),
        ),
        reverse=True,
    )
    return pool[:limit]


def attempt_summary(candidate: dict[str, Any], material: dict[str, Any]) -> dict[str, str]:
    """Keep an auditable, non-content copy of each research fetch attempt."""

    return {
        "title": str(candidate.get("title") or ""),
        "primary_url": str(candidate.get("primary_url") or candidate.get("source_url") or ""),
        "fetch_status": str(material.get("fetch_status") or ""),
        "fetch_error": str(material.get("fetch_error") or ""),
    }


def apply_research_fetch_fallback(
    selection: dict[str, Any],
    *,
    chosen: list[dict[str, Any]],
    original_count: int,
    attempts: list[dict[str, str]],
) -> None:
    """Persist the final research route so selection and materials stay aligned."""

    selection["selected_research"] = [
        {"rank": index, **{key: value for key, value in candidate.items() if key != "rank"}}
        for index, candidate in enumerate(chosen, start=1)
    ]
    selection["research_fetch_fallback"] = {
        "status": "replaced" if chosen else "exhausted",
        "original_selected_count": original_count,
        "final_selected_count": len(chosen),
        "attempts": attempts,
    }
    summary = selection.get("selection_summary")
    if not isinstance(summary, dict):
        return
    summary["research_count"] = len(chosen)
    summary["research_pick"] = str(chosen[0].get("title") or "") if chosen else ""
    reading = summary.get("reading_estimate")
    if not isinstance(reading, dict):
        return
    original_minutes = number(reading.get("research_minutes"))
    retained_minutes = round(original_minutes * len(chosen) / max(1, original_count), 1)
    news_minutes = number(reading.get("news_minutes"))
    reading["research_minutes"] = retained_minutes
    reading["total_minutes"] = round(news_minutes + retained_minutes, 1)


def fetch_research_with_fallbacks(
    selection: dict[str, Any],
    research: list[dict[str, Any]],
    news: list[dict[str, Any]],
    *,
    max_chars: int,
    timeout_seconds: int,
    max_bytes: int,
    workers: int,
    fallback_limit: int,
) -> list[dict[str, Any]]:
    """Fetch selected research, replacing unreadable items with scored peers.

    A research feature is a stronger claim than a news card and must have
    readable primary-source material.  We therefore retry with the next
    already-qualified candidate, rather than falling back to its RSS excerpt.
    """

    initial_materials = fetch_group(
        research,
        max_chars=max_chars,
        timeout_seconds=timeout_seconds,
        max_bytes=max_bytes,
        workers=workers,
    )
    chosen: list[dict[str, Any]] = []
    materials: list[dict[str, Any]] = []
    attempts: list[dict[str, str]] = []
    for candidate, material in zip(research, initial_materials, strict=True):
        attempts.append(attempt_summary(candidate, material))
        if material.get("fetch_status") == "fetched":
            chosen.append(candidate)
            materials.append(material)

    target_count = len(research)
    if len(chosen) < target_count:
        for candidate in research_fallback_candidates(
            selection,
            selected_research=research,
            selected_news=news,
            limit=fallback_limit,
        ):
            material = source_material(
                candidate,
                max_chars=max_chars,
                timeout_seconds=timeout_seconds,
                max_bytes=max_bytes,
            )
            attempts.append(attempt_summary(candidate, material))
            if material.get("fetch_status") != "fetched":
                continue
            chosen.append(candidate)
            materials.append(material)
            if len(chosen) >= target_count:
                break

    if len(chosen) != target_count:
        # Never publish a partial or fallback-excerpt research claim. A quiet
        # pure-news issue is safer and more useful than weak evidence.
        chosen = []
        materials = []

    if any(item.get("fetch_status") != "fetched" for item in initial_materials):
        apply_research_fetch_fallback(
            selection,
            chosen=chosen,
            original_count=target_count,
            attempts=attempts,
        )
    return materials


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--selection", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--research-max-chars", type=int, default=12000)
    parser.add_argument("--news-max-chars", type=int, default=4500)
    parser.add_argument("--timeout-seconds", type=int, default=20)
    parser.add_argument("--max-response-bytes", type=int, default=700_000)
    parser.add_argument("--max-workers", type=int, default=4)
    parser.add_argument(
        "--research-fallback-limit",
        type=int,
        default=3,
        help="Maximum next-best research candidates to fetch after a selected primary source fails.",
    )
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    if args.output.exists() and not args.force:
        raise SystemExit(f"Source material already exists: {args.output}. Use --force to refresh.")
    if min(
        args.research_max_chars,
        args.news_max_chars,
        args.timeout_seconds,
        args.max_response_bytes,
        args.max_workers,
        args.research_fallback_limit,
    ) <= 0:
        raise SystemExit("Material-fetch limits must be positive.")
    try:
        selection = load_json_object(args.selection)
        research = selection.get("selected_research", [])
        news = selection.get("selected_news", [])
        if not isinstance(research, list) or not isinstance(news, list):
            raise SourceMaterialError("selection must contain selected_research and selected_news arrays.")
        research_items = [item for item in research if isinstance(item, dict)]
        news_items = [item for item in news if isinstance(item, dict)]
        research_materials = fetch_research_with_fallbacks(
            selection,
            research_items,
            news_items,
            max_chars=args.research_max_chars,
            timeout_seconds=args.timeout_seconds,
            max_bytes=args.max_response_bytes,
            workers=args.max_workers,
            fallback_limit=args.research_fallback_limit,
        )
        if "research_fetch_fallback" in selection:
            write_json_object(args.selection, selection)
        payload = {
            "schema_version": 2,
            "created_at": utc_now_iso(),
            "selection": str(args.selection),
            "selection_sha256": file_sha256(args.selection),
            "research": research_materials,
            "news": fetch_group(
                news_items,
                max_chars=args.news_max_chars,
                timeout_seconds=args.timeout_seconds,
                max_bytes=args.max_response_bytes,
                workers=args.max_workers,
            ),
        }
        write_json_object(args.output, payload)
    except (OSError, json.JSONDecodeError, SourceMaterialError) as exc:
        raise SystemExit(f"Source material fetch failed: {exc}") from exc

    fetched = sum(1 for item in [*payload["research"], *payload["news"]] if item.get("fetch_status") == "fetched")
    total = len(payload["research"]) + len(payload["news"])
    print(f"Source material written: {args.output}")
    print(f"Primary sources fetched: {fetched}/{total}")


if __name__ == "__main__":
    main()
