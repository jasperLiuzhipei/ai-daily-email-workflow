import sys
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from select_aihot_sources import TZ, generic_listing_rows, parse_generic_listing_page


class RefreshAdapterTests(unittest.TestCase):
    def test_claude_date_outside_link_and_deduplication(self):
        card = '''<div class="card_blog_wrap"><div class="u-text-style-caption">Sep 15, 2026</div>
          <div class="card_blog_title">Salesforce in Claude</div><a href="/blog/salesforce-in-claude">Read</a></div>'''
        source = dict(id="claude_blog", url="https://claude.com/blog", pool="news")
        rows, _ = parse_generic_listing_page(card * 2,
            datetime(2026, 9, 13, tzinfo=TZ), datetime(2026, 9, 16, tzinfo=TZ), [],
            source=source, research_window_strategy="official_release_catchup")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["title"], "Salesforce in Claude")
        self.assertEqual(rows[0]["published_at"], "2026-09-15T00:00:00+08:00")
        self.assertEqual(generic_listing_rows(card.replace("Sep 15, 2026", ""), source["url"]), [])

    def test_google_groups_nested_models_as_one_release(self):
        page = '''<h2>September 15, 2026</h2><ul><li><strong>Gemini Live GA</strong>
          <ul><li><strong>Base</strong>Fast</li><li><strong>Thinking</strong>Deep</li></ul></li></ul>
          <h2>September 3, 2026</h2><ul><li><strong>Lyria GA</strong></li></ul>'''
        rows = generic_listing_rows(page, "https://ai.google.dev/gemini-api/docs/changelog")
        self.assertEqual([r["title"] for r in rows], ["Gemini Live GA", "Lyria GA"])
        self.assertIn("Base", rows[0]["description"])
        self.assertNotIn("Lyria", rows[0]["description"])
        self.assertNotEqual(rows[0]["url"], rows[1]["url"])

    def test_deepseek_iso_dates_and_no_cross_release_leak(self):
        page = '''<h2>Date: 2026-09-10 &#8203;</h2><h3>Flash Release &#8203;</h3><p>New vision support</p>
          <h2>Date: 2026-08-21</h2><h3>Old release</h3><p>Old details</p>'''
        rows = generic_listing_rows(page, "https://api-docs.deepseek.com/updates/")
        self.assertEqual(rows[0]["title"], "Flash Release")
        self.assertEqual(rows[0]["published_at"], "2026-09-10T00:00:00+08:00")
        self.assertEqual(rows[0]["description"], "New vision support")


if __name__ == "__main__":
    unittest.main()
