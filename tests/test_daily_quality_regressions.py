from __future__ import annotations

import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from generate_daily_content_api import deduplicate_material_news  # noqa: E402
from validate_ai_daily_content import ValidationError, validate_unique_events, has_visual_relation_syntax  # noqa: E402


class DailyQualityRegressionTests(unittest.TestCase):
    def test_selection_event_id_deduplicates_different_urls_before_model_calls(self) -> None:
        first = {"title": "官方发布", "event_id": "same-event", "primary_url": "https://example.com/a"}
        repeat = {"title": "另一网站报道", "event_id": "same-event", "primary_url": "https://other.example/b"}
        independent = {"title": "独立更新", "event_id": "different-event", "primary_url": "https://example.com/c"}
        kept, dropped = deduplicate_material_news([first, repeat, independent])
        self.assertEqual(kept[1], independent)
        self.assertEqual({key: kept[0][key] for key in first}, first)
        self.assertEqual(kept[0]["coverage_source_urls"], ["https://example.com/a", "https://other.example/b"])
        self.assertEqual(dropped[0]["event_key"], "event:same-event")

    def test_coverage_alias_deduplicates_without_shared_event_id(self) -> None:
        first = {"title": "官方原文", "primary_url": "https://example.com/release",
                 "coverage_source_urls": ["https://news.example/story?utm_source=feed"]}
        repeat = {"title": "转述", "url": "https://news.example/story"}
        kept, _ = deduplicate_material_news([first, repeat])
        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0]["title"], first["title"])
        self.assertEqual(kept[0]["primary_url"], first["primary_url"])
        self.assertEqual(set(kept[0]["coverage_source_urls"]),
                         {"https://example.com/release", "https://news.example/story?utm_source=feed", "https://news.example/story"})

    def test_final_content_blocks_duplicate_event_across_different_urls(self) -> None:
        content = {"news": [
            {"title": "官方发布", "event_id": "same-event", "url": "https://example.com/a"},
            {"title": "另一报道", "event_id": "same-event", "url": "https://other.example/b"},
        ]}
        with self.assertRaisesRegex(ValidationError, "Repeated event"):
            validate_unique_events(content)

    def test_graph_relation_syntax_stays_out_of_reader_prose(self) -> None:
        self.assertTrue(has_visual_relation_syntax("模型绕过策略。关系：模型｜发出请求｜网关"))
        self.assertTrue(has_visual_relation_syntax("关系：模型｜向网关提交欺骗性的 DNS 响应"))
        self.assertTrue(has_visual_relation_syntax("DNS 响应，并借用共享 Fastly IP 请求被封锁 URL｜SPACE 网络网关"))
        self.assertTrue(has_visual_relation_syntax("关系: model | requests"))
        self.assertFalse(has_visual_relation_syntax("模型发出请求，网关按策略检查并返回结果。"))


if __name__ == "__main__":
    unittest.main()
