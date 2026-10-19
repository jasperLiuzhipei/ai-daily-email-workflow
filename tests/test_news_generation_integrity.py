"""Offline coverage checks for source identity and generation planning."""
from copy import deepcopy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from generate_daily_content_api import (
    ContentApiError, anchor_batch_source_ranks, deduplicate_material_news, normalize_news,
)


class NewsGenerationIntegrityTests(unittest.TestCase):
    def test_batch_reordering_keeps_copy_associated_with_its_explicit_source_rank(self):
        packet = [{"source_rank": 10}, {"source_rank": 20}]
        raw = [{"source_rank": 20, "title": "第二条来源"}, {"source_rank": 10, "title": "第一条来源"}]
        self.assertEqual(anchor_batch_source_ranks(raw, packet), raw)

    def test_locally_renumbered_batch_uses_local_identity_even_when_reordered(self):
        packet = [{"source_rank": 10}, {"source_rank": 20}]
        raw = [{"source_rank": 2, "title": "第二条来源"}, {"source_rank": 1, "title": "第一条来源"}]
        self.assertEqual([item["source_rank"] for item in anchor_batch_source_ranks(raw, packet)], [20, 10])
        self.assertEqual([item["source_rank"] for item in raw], [2, 1])

    def test_ambiguous_or_duplicate_batch_identity_is_rejected(self):
        for ranks in ([10, 10], [10, 99], [None, 20], [True, 2]):
            with self.subTest(ranks=ranks), self.assertRaises(ContentApiError):
                anchor_batch_source_ranks([{"source_rank": rank} for rank in ranks],
                                          [{"source_rank": 10}, {"source_rank": 20}])

    def test_batch_cannot_publish_a_placeholder_title(self):
        selected = {"rank": 1, "title": "厂商发布检索工具预览", "source_type": "tool",
                    "primary_url": "https://example.test/tool"}
        with self.assertRaisesRegex(ContentApiError, "placeholder-title"):
            normalize_news([{"source_rank": 1, "title": ""}], [selected],
                           category_keys={"industry_signal"}, allowed_labels={"行业视角"})

    def test_duplicate_alias_chain_is_one_card_and_keeps_every_provenance_url(self):
        sources = [
            {"title": "首发", "primary_url": "https://example.test/a", "coverage_source_urls": ["https://example.test/b"]},
            {"title": "转述", "primary_url": "https://example.test/b", "coverage_source_urls": ["https://example.test/c"]},
            {"title": "再转述", "primary_url": "https://example.test/c"},
        ]
        before = deepcopy(sources)
        kept, dropped = deduplicate_material_news(sources)
        self.assertEqual(sources, before)
        self.assertEqual([c["title"] for c in kept], ["首发"])
        self.assertEqual(kept[0]["coverage_source_urls"], ["https://example.test/a", "https://example.test/b", "https://example.test/c"])
        self.assertEqual([d["source_index"] for d in dropped], [1, 2])

    def test_later_bridge_merges_already_seen_duplicate_components(self):
        sources = [
            {"title": "A", "primary_url": "https://example.test/a"},
            {"title": "独立事件", "primary_url": "https://example.test/independent"},
            {"title": "C", "primary_url": "https://example.test/c"},
            {"title": "报道汇合", "primary_url": "https://example.test/b", "coverage_source_urls": ["https://example.test/a", "https://example.test/c"]},
        ]
        kept, dropped = deduplicate_material_news(sources)
        self.assertEqual([c["title"] for c in kept], ["A", "独立事件"])
        self.assertEqual([d["source_index"] for d in dropped], [2, 3])

    def test_shared_background_incident_does_not_merge_independent_news(self):
        background = "相关文章：Anthropic Claude 网络安全评估出现未经授权访问真实系统的案例。"
        sources = [
            {"title": "Anthropic推出企业权限管理工具", "primary_url": "https://example.test/permissions", "source_text": background},
            {"title": "Claude新增文档协作入口", "primary_url": "https://example.test/docs", "source_text": background},
        ]
        self.assertEqual(deduplicate_material_news(sources), (sources, []))

    def test_duplicate_cannot_occupy_both_feature_sections(self):
        first = {"title": "同一事件", "primary_url": "https://example.test/a", "event_id": "same", "newspaper_role": "lead"}
        other = {**first, "primary_url": "https://example.test/b", "newspaper_role": "supplement"}
        with self.assertRaisesRegex(ContentApiError, "both newspaper features"):
            deduplicate_material_news([first, other])

    def test_feature_role_survives_merging_into_the_first_source(self):
        first = {"title": "同一事件", "primary_url": "https://example.test/a", "event_id": "same", "newspaper_role": "brief"}
        other = {**first, "primary_url": "https://example.test/b", "newspaper_role": "lead"}
        kept, _ = deduplicate_material_news([first, other])
        self.assertEqual(kept[0]["newspaper_role"], "lead")


if __name__ == "__main__":
    unittest.main()
