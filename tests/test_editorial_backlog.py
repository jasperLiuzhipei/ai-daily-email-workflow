from __future__ import annotations
import json
from datetime import datetime
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from editorial_backlog import identity, publication_items, recall_candidates, sync_backlog
from select_aihot_sources import extract_aihot_id, load_previous_items, score_candidate
from select_aihot_sources import parse_datetime, parse_official_rss_feed, parse_generic_listing_page
from generate_daily_content_api import ContentApiError, editorial_text, generate_individual_news_cards, news_card_user_prompt
from ai_gateway_client import ProviderRuntime


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


class BacklogTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "daily_runs"
        self.day = self.root / "2026-09-09"
        self.candidate = {"title": "Terminal-Universe 训练终端智能体", "primary_url": "https://arxiv.org/abs/2609.04148",
                          "published_at": "2026-09-03T00:00:00+08:00", "scores": {"final_news": 98.7},
                          "score_breakdown": {}, "source_excerpt": "通过还原工作区生成可验证的新任务。"}
        write(self.day / "source_selection_auto_2026-09-09.json", {
            "all_candidates_scored": [self.candidate], "selected_news": [self.candidate], "selected_research": []})

    def tearDown(self):
        self.tmp.cleanup()

    def test_failed_card_survives_news_window_and_is_not_deduped_by_metadata(self):
        content = {"news": [], "generation": {"response": {"dropped_cards": [
            {"source_title": self.candidate["title"], "source_url": self.candidate["primary_url"],
             "attempts": [{"reason": "Chinese editorial validation failed"}]}]}}}
        path = self.day / "ai_daily_email_content.json"
        write(path, content)
        ledger = sync_backlog(self.root, "2026-09-10")
        entry = next(iter(ledger["items"].values()))
        self.assertEqual(entry["status"], "generation_failed")
        recalled = recall_candidates(ledger, "2026-09-10")
        self.assertEqual(recalled[0]["published_at"], self.candidate["published_at"])
        self.assertEqual(recalled[0]["research_window_strategy"], "unpublished_backlog")
        self.assertEqual(load_previous_items([path]), [])

    def test_freshness_filter_precedes_quota_so_stale_failures_do_not_hide_recent_news(self):
        ledger = {"policy": {"max_recalled_candidates": 1}, "items": {}}
        for index, (stamp, status, score) in enumerate([
            ("2026-09-30T09:00:00+08:00", "generation_failed", 99),
            ("2026-10-09T09:00:00+08:00", "ready", 70),
        ]):
            ledger["items"][str(index)] = {"id": str(index), "first_seen": "2026-10-09",
                "status": status, "raw_news_score": score,
                "candidate": {"title": f"News {index}", "published_at": stamp}}
        excluded = []
        recalled = recall_candidates(ledger, "2026-10-10",
            start_at=datetime.fromisoformat("2026-10-06T08:30:00+08:00"),
            end_at=datetime.fromisoformat("2026-10-10T08:30:00+08:00"),
            freshness_exclusions=excluded)
        self.assertEqual([item["backlog_id"] for item in recalled], ["1"])
        self.assertEqual([item["id"] for item in excluded], ["0"])
        self.assertEqual(ledger["items"]["0"]["status"], "generation_failed")

    def test_recall_freshness_needs_both_window_bounds(self):
        with self.assertRaisesRegex(ValueError, "both start_at and end_at"):
            recall_candidates({}, "2026-10-10", start_at=datetime.fromisoformat("2026-10-06T08:30:00+08:00"))

    def test_draft_does_not_close_item_and_actual_publication_does(self):
        card = {"title": "终端训练方法", "url": self.candidate["primary_url"]}
        write(self.day / "ai_daily_email_content.json", {"news": [card]})
        ledger = sync_backlog(self.root, "2026-09-10")
        self.assertEqual(next(iter(ledger["items"].values()))["status"], "draft_ready")
        self.assertEqual(len(recall_candidates(ledger, "2026-09-10")), 1)
        write(self.root / "send_ledger.json", {"prod": {"2026-09-09": {
            "sent_at": "2026-09-09T10:00:00+08:00", "published_items": publication_items({"news": [card]})}}})
        ledger = sync_backlog(self.root, "2026-09-10")
        self.assertEqual(next(iter(ledger["items"].values()))["status"], "published")
        self.assertEqual(recall_candidates(ledger, "2026-09-10"), [])

    def test_source_cap_stays_in_effect_and_dismissal_survives_resync(self):
        self.candidate["score_breakdown"] = {"secondary_news_score_cap": 79}
        write(self.day / "source_selection_auto_2026-09-09.json", {"all_candidates_scored": [self.candidate]})
        ledger = sync_backlog(self.root, "2026-09-10")
        entry = next(iter(ledger["items"].values()))
        self.assertEqual(entry["effective_news_score"], 79)
        self.assertEqual(entry["status"], "needs_source_review")
        entry.update(status="dismissed", reason="Duplicate roundup, original separately reviewed.")
        write(self.root / "editorial_backlog.json", ledger)
        ledger = sync_backlog(self.root, "2026-09-11")
        self.assertEqual(recall_candidates(ledger, "2026-09-11"), [])

    def test_source_aliases_unify_x_and_arxiv_versions(self):
        self.assertEqual(identity("https://x.com/OpenAI/status/123"), identity("https://x.com/i/web/status/123"))
        self.assertEqual(identity("https://arxiv.org/abs/2609.04148"), identity("https://arxiv.org/html/2609.04148v1"))
        self.assertEqual(extract_aihot_id("https://aihot.news/items/abc"), "abc")

    def test_official_images_release_and_english_security_research_are_recognized(self):
        images, _ = score_candidate({"title": "Introducing ChatGPT Images 2.5", "description": "Sharper images and editing.",
            "primary_url": "https://openai.com/index/introducing-chatgpt-images-2-5", "aihot_url": "", "categories": ["Product"],
            "published_at": "2026-09-09T00:00:00+08:00", "feed_source": "direct:openai_newsroom", "source_authority": "official_first_party"}, [])
        self.assertEqual(images["selection_group"], "model_release")
        security, _ = score_candidate({"title": "An alignment assessment of recent cybersecurity incidents",
            "description": "Assessment of unauthorized access to real third-party systems.",
            "primary_url": "https://anthropic.com/research/alignment-assessment-cybersecurity-incidents", "aihot_url": "",
            "categories": ["Official Research"], "source_type": "paper", "source_pool": "research", "feed_source": "direct:anthropic_research",
            "source_authority": "official_research", "published_at": "2026-09-09T00:00:00+08:00"}, [])
        self.assertEqual(security["selection_group"], "security_boundary")
        self.assertGreaterEqual(security["scores"]["internal_relevance"], 15)

    def test_primary_discovery_uses_submission_time_and_dated_sibling_cards(self):
        atom = '''<feed xmlns="http://www.w3.org/2005/Atom"><entry>
          <id>https://arxiv.org/abs/2609.09153v1</id><title>Procedural Graphs: Execution Structures for LLM Agents</title>
          <published>2026-09-08T17:59:41Z</published><updated>2026-09-10T09:00:00Z</updated>
          <summary>Agent tool execution and evaluation of procedural memory.</summary></entry></feed>'''
        start, end = parse_datetime('2026-09-07T08:30:00+08:00'), parse_datetime('2026-09-10T08:30:00+08:00')
        source = {'id':'arxiv_agent_research','pool':'research','authority':'paper_repository','source_type_override':'paper'}
        cards, _ = parse_official_rss_feed(atom,start,end,[],source=source,research_window_strategy='direct_research_lookback')
        self.assertEqual(len(cards),1)
        self.assertEqual(cards[0]['published_at'],'2026-09-09T01:59:41+08:00')
        self.assertTrue(cards[0]['research_eligible'])
        page = '''<div class="post-item"><div class="post-item__top">SEPT. 9, 2026</div>
          <a class="post-item__link" href="/harness/"><span>Harness Engineering for AI Coding Agents</span>
          <p>Behavioral evaluations assert on tool calls and file changes.</p></a></div>'''
        source = {'id':'google_developers','url':'https://developers.googleblog.com/','pool':'news'}
        cards, _ = parse_generic_listing_page(page,start,end,[],source=source,research_window_strategy='official_release_catchup')
        self.assertEqual(len(cards),1)
        self.assertIn('tool calls',cards[0]['description'])


class EditorialRepairTests(unittest.TestCase):
    def test_english_names_in_chinese_copy_do_not_cause_a_false_failure(self):
        text = "Terminal-Universe 使用 Qwen3.5-27B 训练，在 Terminal-Bench 和 EvoCode-Bench 上验证表现；这些提升仅限论文测试设置。"
        self.assertEqual(editorial_text(text, "one_liner", limit=520), text)
        with self.assertRaises(ContentApiError):
            editorial_text("This is entirely English navigation and article copy.", "one_liner", limit=520)

    def test_one_targeted_retry_recovers_invalid_copy_and_keeps_primary_evidence(self):
        runtime = ProviderRuntime(kind="text", base_url="https://example.test/v1", api_key="test", model="test",
                                  endpoint_path="chat/completions", timeout_seconds=5, max_retries=0, request_defaults={})
        source = {"title": "Terminal-Universe 终端训练", "primary_url": "https://arxiv.org/abs/2609.04148",
                  "fetch_status": "fetched", "source_text": "Reusable environments are reconstructed from trajectories.",
                  "source_type": "methodology", "selection_group": "methodology", "topic_tags": ["训练数据"]}
        valid = {"title": "终端训练从模仿轨迹转向重建环境", "one_liner": "该研究从智能体轨迹还原可重复使用的工作区，并生成新的可验证训练任务。",
                 "why": "训练材料由一次性演示变成可反复执行的环境，为数据扩展提供了新的选择。",
                 "observation_label": "工作启发", "observation": "应记录任务环境、验证器与运行依赖，评估重建环境能否覆盖真实任务。"}
        invalid = {**valid, "one_liner": "An English-only summary without usable Chinese editorial copy."}
        with patch("generate_daily_content_api.chat_json", side_effect=[(invalid, {}), (valid, {})]) as chat:
            cards, metadata = generate_individual_news_cards(runtime, materials_news=[source], labels=["工作启发"])
        self.assertEqual(len(cards), 1)
        self.assertEqual(chat.call_count, 2)
        self.assertTrue(metadata["cards"][0]["repair_used"])
        self.assertIn("one_liner", chat.call_args.kwargs["user_prompt"])
        self.assertIn("Reusable environments", news_card_user_prompt(source))


if __name__ == "__main__":
    unittest.main()
