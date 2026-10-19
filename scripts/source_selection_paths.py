"""Source selection paths."""
from __future__ import annotations
from typing import Any
from pathlib import Path
import json
import re
from source_identity import (
    SourceSelectionError,
)
from source_selection_constants import (
    SOURCE_QUALITY_TIERS,
)


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
        quality_tier = source.get("quality_tier")
        if quality_tier and quality_tier not in SOURCE_QUALITY_TIERS:
            raise SourceSelectionError(
                f"Source {source['id']} has unsupported quality_tier: {quality_tier}"
            )
    official_social_accounts = data.get("official_social_accounts", [])
    if not isinstance(official_social_accounts, list):
        raise SourceSelectionError("official_social_accounts must be a list when configured.")
    for account in official_social_accounts:
        if (
            not isinstance(account, dict)
            or str(account.get("platform", "")).lower() != "x"
            or not str(account.get("handle", "")).strip()
        ):
            raise SourceSelectionError(
                "Every official_social_accounts item needs platform=x and a non-empty handle."
            )
    return data


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

