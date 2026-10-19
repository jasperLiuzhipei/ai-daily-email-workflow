from __future__ import annotations

import sys
import unittest
from pathlib import Path


WORKFLOW_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WORKFLOW_DIR / "scripts"))

from fetch_selected_source_material import (  # noqa: E402
    apply_research_fetch_fallback,
    normalize_source_url,
    research_fallback_candidates,
    source_material,
)


def candidate(title: str, url: str, score: float, relevance: float = 20) -> dict:
    return {
        "title": title,
        "primary_url": url,
        "research_eligible": True,
        "scores": {"final_research": score, "internal_relevance": relevance, "final_news": score - 5, "total": score},
        "score_breakdown": {"source_reliability": 95, "industry_impact": 20},
    }


class ResearchFetchFallbackTests(unittest.TestCase):
    def test_source_url_decodes_feed_html_entities_once(self) -> None:
        encoded = "https://example.com/post?a=1&amp;b=2"

        self.assertEqual(normalize_source_url(encoded), "https://example.com/post?a=1&b=2")

    def test_non_ascii_candidate_url_is_skipped_without_crashing(self) -> None:
        malformed = candidate("bad-feed-url", "https://arxiv.org/abs/2609.00549）", 90)

        material = source_material(
            malformed,
            max_chars=1000,
            timeout_seconds=1,
            max_bytes=1000,
        )

        self.assertEqual(material["fetch_status"], "skipped_unsafe_or_missing_url")

    def test_fallback_uses_next_qualified_research_without_duplicate_news(self) -> None:
        initial = candidate("initial", "https://example.com/initial", 93)
        next_best = candidate("next-best", "https://example.com/next", 90)
        selected_as_news = candidate("already-news", "https://example.com/news", 99)
        below_floor = candidate("below-floor", "https://example.com/low", 69)
        selection = {
            "config_snapshot": {"selection_policy": {"selection": {"research_min_score": 70, "research_min_internal_relevance": 15}}},
            "all_candidates_scored": [selected_as_news, below_floor, initial, next_best],
        }

        result = research_fallback_candidates(
            selection,
            selected_research=[initial],
            selected_news=[selected_as_news],
            limit=3,
        )

        self.assertEqual([item["title"] for item in result], ["next-best"])

    def test_exhausted_fallback_clears_research_slot_and_keeps_audit(self) -> None:
        selection = {
            "selected_research": [candidate("failed", "https://example.com/failed", 93)],
            "selection_summary": {
                "research_count": 1,
                "research_pick": "failed",
                "reading_estimate": {"research_minutes": 3.0, "news_minutes": 6.0, "total_minutes": 9.0},
            },
        }

        apply_research_fetch_fallback(
            selection,
            chosen=[],
            original_count=1,
            attempts=[{"title": "failed", "fetch_status": "fallback_after_fetch_error", "primary_url": "https://example.com/failed", "fetch_error": "TLS"}],
        )

        self.assertEqual(selection["selected_research"], [])
        self.assertEqual(selection["research_fetch_fallback"]["status"], "exhausted")
        self.assertEqual(selection["selection_summary"]["research_count"], 0)
        self.assertEqual(selection["selection_summary"]["reading_estimate"]["total_minutes"], 6.0)


if __name__ == "__main__":
    unittest.main()
