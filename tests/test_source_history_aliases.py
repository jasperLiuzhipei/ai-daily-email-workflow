"""Cross-issue deduplication must use explicit source aliases in either item."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from source_history import content_duplicate_check, previous_item_from_object


class SourceHistoryAliasTests(unittest.TestCase):
    def test_new_rewrite_is_duplicate_when_its_alias_matches_an_actual_published_card(self):
        previous = previous_item_from_object({"title": "厂商检索工具开放预览", "url": "https://official.test/release"})
        current = {"title": "企业知识查询的新入口", "primary_url": "https://media.test/rewrite",
                   "coverage_source_urls": ["https://official.test/release?utm_source=newsletter"]}
        result = content_duplicate_check(current, [previous])
        self.assertTrue(result["is_duplicate"])
        self.assertEqual(result["reason"], "same_url")

    def test_independent_updates_with_only_an_incidental_article_link_remain_separate(self):
        previous = previous_item_from_object({"title": "厂商检索工具开放预览", "url": "https://official.test/release"})
        current = {"title": "文档编辑权限提供分组设置", "primary_url": "https://official.test/permissions",
                   "links": [{"url": "https://official.test/release"}]}
        self.assertFalse(content_duplicate_check(current, [previous])["is_duplicate"])


if __name__ == "__main__":
    unittest.main()
