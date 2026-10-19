from __future__ import annotations

import sys
import unittest
from datetime import datetime
from pathlib import Path


WORKFLOW_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WORKFLOW_DIR / "scripts"))

from select_aihot_sources import (  # noqa: E402
    TZ,
    configure_source_quality_policy,
    is_verified_applied_science_capability_research,
    is_unverified_x_model_release,
    load_source_registry,
    news_selection_score,
    parse_dated_link_time_cards,
    parse_generic_listing_page,
    parse_official_rss_feed,
    parse_release_notes_page,
    score_candidate,
    source_mix_summary,
)


class SourceQualityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.registry = load_source_registry(WORKFLOW_DIR / "config" / "source_registry.json")
        configure_source_quality_policy(cls.registry)

    def test_configured_official_x_account_is_verified_social(self) -> None:
        candidate, duplicate = score_candidate(
            {
                "title": "Qwen 新模型正式发布",
                "description": "Qwen 官方宣布模型上线并开放 API。",
                "primary_url": "https://x.com/Alibaba_Qwen/status/2078754377473601787",
                "aihot_url": "",
                "categories": ["模型发布"],
                "feed_source": "all",
                "research_window_strategy": "exact_window",
            },
            [],
        )

        self.assertIsNone(duplicate)
        self.assertEqual(candidate["source_quality_tier"], "verified_social")
        self.assertFalse(is_unverified_x_model_release(candidate))
        self.assertEqual(candidate["score_breakdown"]["source_reliability"], 88)
        self.assertEqual(candidate["score_breakdown"]["unverified_x_model_release_penalty"], 0)

    def test_unverified_x_repost_keeps_the_penalty(self) -> None:
        candidate, _ = score_candidate(
            {
                "title": "Qwen 新模型正式发布",
                "description": "社区账号转述模型上线消息。",
                "primary_url": "https://x.com/example_reposter/status/1",
                "aihot_url": "",
                "categories": ["模型发布"],
                "feed_source": "all",
                "research_window_strategy": "exact_window",
            },
            [],
        )

        self.assertEqual(candidate["source_quality_tier"], "discovery")
        self.assertTrue(is_unverified_x_model_release(candidate))
        self.assertEqual(candidate["score_breakdown"]["source_reliability"], 58)
        self.assertEqual(candidate["score_breakdown"]["unverified_x_model_release_penalty"], 12)

    def test_secondary_media_report_cannot_auto_include_over_primary_source(self) -> None:
        candidate, _ = score_candidate(
            {
                "title": "字节跳动发布豆包工作，与飞书深度打通",
                "description": "豆包工作面向生产力场景推出，支持任务拆解、工具调用与飞书企业上下文。",
                "primary_url": "https://www.ithome.com/0/993/865.htm",
                "aihot_url": "https://aihot.virxact.com/items/doubao-work",
                "categories": ["AI"],
                "feed_source": "main",
                "research_window_strategy": "exact_window",
            },
            [],
        )

        self.assertEqual(candidate["score_breakdown"]["source_reliability"], 55)
        self.assertEqual(candidate["score_breakdown"]["secondary_news_score_cap"], 79)
        self.assertLessEqual(news_selection_score(candidate), 79)

    def test_ithome_morning_roundup_is_discovery_only(self) -> None:
        candidate, _ = score_candidate(
            {
                "title": "IT早报 0826：多项科技资讯汇总",
                "description": "今天的重要科技资讯有 27 条，分别列出产品发布并提供查看详情链接，咱们明天见。",
                "primary_url": "https://www.ithome.com/0/994/311.htm",
                "aihot_url": "https://aihot.virxact.com/items/ithome-roundup",
                "categories": ["AI"],
                "feed_source": "main",
                "research_window_strategy": "exact_window",
            },
            [],
        )

        self.assertEqual(candidate["score_breakdown"]["roundup_news_score_cap"], 0)
        self.assertEqual(news_selection_score(candidate), 0)

    def test_every_to_commentary_requires_a_resolved_primary_source(self) -> None:
        candidate, _ = score_candidate(
            {
                "title": "ChatGPT 与 OpenClaw 指南更新",
                "description": "一篇二手解读称相关指南已经改版，但未提供可核验的具体更新。",
                "primary_url": "https://every.to/example/guide-update",
                "aihot_url": "https://aihot.virxact.com/items/every-guide",
                "categories": ["AI"],
                "feed_source": "main",
                "research_window_strategy": "exact_window",
            },
            [],
        )

        self.assertEqual(candidate["source_quality_tier"], "discovery")
        self.assertEqual(news_selection_score(candidate), 0)

    def test_model_hardware_standard_is_not_a_model_release(self) -> None:
        candidate, duplicate = score_candidate(
            {
                "title": "Anthropic 推出模型硬件标准，让 AI 智能体控制物理世界",
                "description": "该标准定义智能体与设备之间的连接和控制接口。",
                "primary_url": "https://example.com/model-hardware-standard",
                "aihot_url": "",
                "categories": ["AI"],
                "feed_source": "main",
                "research_window_strategy": "exact_window",
            },
            [],
        )

        self.assertIsNone(duplicate)
        self.assertNotEqual(candidate["source_type"], "model_release")
        self.assertEqual(candidate["selection_group"], "enterprise_integration")

    def test_openai_rss_is_parsed_as_a_primary_source(self) -> None:
        source = next(item for item in self.registry["sources"] if item["id"] == "openai_newsroom")
        xml = """<?xml version=\"1.0\" encoding=\"UTF-8\"?>
        <rss version=\"2.0\"><channel><item>
          <title>Introducing a product update</title>
          <link>https://openai.com/index/product-update/</link>
          <description>Official product announcement.</description>
          <pubDate>Sun, 26 Jul 2026 09:00:00 GMT</pubDate>
        </item></channel></rss>"""

        candidates, duplicates = parse_official_rss_feed(
            xml,
            datetime(2026, 7, 25, tzinfo=TZ),
            datetime(2026, 7, 27, tzinfo=TZ),
            [],
            source=source,
            research_window_strategy="exact_window",
        )

        self.assertEqual(duplicates, [])
        self.assertEqual(len(candidates), 1)
        candidate = candidates[0]
        self.assertEqual(candidate["feed_source"], "direct:openai_newsroom")
        self.assertEqual(candidate["source_authority"], "official_first_party")
        self.assertEqual(candidate["source_quality_tier"], "primary")
        self.assertEqual(candidate["score_breakdown"]["source_reliability"], 95)

    def test_generic_listing_parser_reads_json_ld_articles(self) -> None:
        source = next(item for item in self.registry["sources"] if item["id"] == "google_research")
        html = """<html><head>
        <script type="application/ld+json">
        {
          "@graph": [
            {
              "@type": "BlogPosting",
              "headline": "A Google Research update",
              "url": "https://research.google/blog/example-update/",
              "datePublished": "2026-07-26T08:00:00Z",
              "description": "Official research update."
            },
            {
              "@type": "BlogPosting",
              "headline": "An older update",
              "url": "https://research.google/blog/older-update/",
              "datePublished": "2026-06-01T08:00:00Z"
            }
          ]
        }
        </script></head></html>"""

        candidates, duplicates = parse_generic_listing_page(
            html,
            datetime(2026, 7, 25, tzinfo=TZ),
            datetime(2026, 7, 27, tzinfo=TZ),
            [],
            source=source,
            research_window_strategy="direct_research_lookback",
        )

        self.assertEqual(duplicates, [])
        self.assertEqual(len(candidates), 1)
        candidate = candidates[0]
        self.assertEqual(candidate["feed_source"], "direct:google_research")
        self.assertEqual(candidate["source_quality_tier"], "primary")
        self.assertTrue(candidate["research_eligible"])

    def test_kimi_dated_cards_are_parsed_as_primary_news(self) -> None:
        source = next(item for item in self.registry["sources"] if item["id"] == "moonshot_kimi_blog")
        html = """<html><article>
        <h3><a href="/blog/posts/k3">Kimi K3 正式发布</a></h3>
        <time dateTime="2026-07-26T00:00:00.000Z">2026年07月26日</time>
        </article></html>"""

        candidates, duplicates = parse_dated_link_time_cards(
            html,
            datetime(2026, 7, 25, tzinfo=TZ),
            datetime(2026, 7, 27, tzinfo=TZ),
            [],
            source=source,
            research_window_strategy="exact_window",
        )

        self.assertEqual(duplicates, [])
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["title"], "Kimi K3 正式发布")
        self.assertEqual(candidates[0]["primary_url"], "https://platform.moonshot.cn/blog/posts/k3")
        self.assertEqual(candidates[0]["source_quality_tier"], "primary")

    def test_release_notes_cards_keep_their_individual_release_dates(self) -> None:
        source = next(item for item in self.registry["sources"] if item["id"] == "zai_release_notes")
        self.assertEqual(source["catchup_lookback_hours"], 72)
        html = """<html>
        <div class="update-container" id="2026-07-26">
          <button data-component-part="update-label">2026-07-26</button>
          <div data-component-part="update-description">GLM-5.3-Flash</div>
          <div data-component-part="update-content">Official model release.</div>
        </div>
        <div class="update-container" id="2026-07-20">
          <button data-component-part="update-label">2026-07-20</button>
          <div data-component-part="update-description">GLM-5.3</div>
          <div data-component-part="update-content">Older release.</div>
        </div>
        </html>"""

        candidates, duplicates = parse_release_notes_page(
            html,
            datetime(2026, 7, 25, tzinfo=TZ),
            datetime(2026, 7, 27, tzinfo=TZ),
            [],
            source=source,
            research_window_strategy="exact_window",
        )

        self.assertEqual(duplicates, [])
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["title"], "GLM-5.3-Flash")
        self.assertEqual(candidates[0]["published_at"], "2026-07-26T00:00:00+08:00")
        self.assertIn("release=2026-07-26", candidates[0]["primary_url"])
        self.assertEqual(candidates[0]["source_type"], "model_release")
        self.assertEqual(candidates[0]["selection_group"], "model_release")
        # A dated release-note card is discoverable, but a model version name
        # alone no longer establishes a major update or a 76-point floor.
        self.assertEqual(candidates[0]["model_update_priority"]["priority_bonus"], 0)
        self.assertFalse(candidates[0]["model_update_priority"]["lead_eligible"])

    def test_source_mix_summary_keeps_discovery_and_quality_separate(self) -> None:
        summary = source_mix_summary(
            [
                {
                    "feed_source": "all",
                    "source_quality_tier": "discovery",
                    "primary_host": "x.com",
                },
                {
                    "feed_source": "direct:openai_newsroom",
                    "source_quality_tier": "primary",
                    "primary_host": "openai.com",
                },
            ]
        )

        self.assertEqual(summary["item_count"], 2)
        self.assertEqual(summary["direct_item_count"], 1)
        self.assertEqual(summary["by_quality_tier"][0], {"value": "discovery", "count": 1})
        self.assertEqual(summary["by_discovery_feed"][1], {"value": "direct:openai_newsroom", "count": 1})

    def test_official_applied_science_capability_research_clears_relevance_floor(self) -> None:
        candidate, duplicate = score_candidate(
            {
                "title": "How Claude is accelerating protein design and analytical chemistry",
                "description": "Official research on Claude for protein design and analytical chemistry.",
                "primary_url": "https://www.anthropic.com/research/Claude-accelerates-protein-design",
                "aihot_url": "",
                "categories": ["Official Research"],
                "feed_source": "direct:anthropic_research",
                "source_pool": "research",
                "source_authority": "official_research",
                "source_quality_tier": "primary",
                "source_type": "paper",
                "research_window_strategy": "exact_window",
            },
            [],
        )

        self.assertIsNone(duplicate)
        self.assertTrue(
            is_verified_applied_science_capability_research(
                candidate,
                " ".join([candidate["title"], candidate["description"]]).lower(),
            )
        )
        self.assertGreaterEqual(candidate["scores"]["internal_relevance"], 15)


if __name__ == "__main__":
    unittest.main()
