"""Regression coverage for holiday gaps and every route around freshness bounds."""
from datetime import datetime
from email.utils import format_datetime
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from daily_config import load_selection_policy
from source_freshness import candidate_in_window, lookback_floor
import source_selection_cli as cli
import source_selection_context as context


class SourceFreshnessTests(unittest.TestCase):
    def setUp(self):
        self.context = {key: value for key, value in vars(context).items() if key.isupper()}
        self.end = datetime.fromisoformat("2026-10-10T08:30:00+08:00")
        self.floor = datetime.fromisoformat("2026-10-06T08:30:00+08:00")

    def tearDown(self):
        for key, value in self.context.items():
            setattr(context, key, value)

    def test_exact_96_hour_boundary_uses_original_publication_time(self):
        self.assertEqual(lookback_floor(self.end), self.floor)
        for stamp, expected in [
            ("2026-10-06T08:30:00+08:00", True),
            ("2026-10-06T00:30:00Z", True),
            ("2026-10-06T08:29:59+08:00", False),
            ("2026-10-10T08:30:01+08:00", False),
            ("invalid", False),
            ("", False),
        ]:
            with self.subTest(stamp=stamp):
                self.assertEqual(candidate_in_window({"published_at": stamp,
                    "backlog_first_seen": "2026-10-09"}, self.floor, self.end), expected)

    def test_long_gap_wide_window_research_and_official_catchup_cannot_exceed_four_days(self):
        result, evaluated, official_starts = self.run_selector(wide=True)
        window = result["window"]
        for key in ["start_at", "news_start_at", "paper_lookback_start_at", "lookback_floor_at"]:
            self.assertEqual(window[key], self.floor.isoformat())
        self.assertEqual(window["requested_start_at"], "2026-09-30T08:30:00+08:00")
        self.assertEqual(window["max_lookback_hours"], 96)
        self.assertEqual(official_starts, [self.floor, self.floor])
        self.assertEqual({item["title"] for item in evaluated}, {"At boundary", "Recent", "Fresh recall"})
        self.assertEqual([item["title"] for item in result["backlog"]["recalled"]], ["Fresh recall"])
        self.assertEqual([item["title"] for item in result["backlog"]["excluded_by_freshness"]], ["Old recall"])

    def test_short_news_window_and_gap_audit_stay_inside_four_day_maximum(self):
        result, evaluated, _ = self.run_selector(wide=False)
        self.assertEqual(result["window"]["news_start_at"], "2026-10-08T08:30:00+08:00")
        self.assertEqual(result["window"]["gap_audit_start_at"], self.floor.isoformat())
        self.assertTrue(all(candidate_in_window(item, self.floor, self.end) for item in evaluated))

    def test_final_gate_excludes_old_future_and_undated_adapter_results_before_editorial(self):
        leaked = [{"title": title, "published_at": stamp, "primary_url": f"https://official.test/{index}",
                   "source_type": "paper", "source_pool": "research", "research_eligible": True}
                  for index, (title, stamp) in enumerate([
                      ("Leaked old research", "2026-09-30T08:30:00+08:00"),
                      ("Leaked future release", "2026-10-11T08:30:00+08:00"),
                      ("Undated release", ""),
                  ])]
        result, evaluated, _ = self.run_selector(wide=True, official_candidates=leaked)
        self.assertFalse({item["title"] for item in leaked} & {item["title"] for item in evaluated})
        self.assertEqual({item["title"] for item in result["source"]["freshness_exclusions"]},
                         {item["title"] for item in leaked})

    def test_policy_cannot_widen_user_four_day_maximum(self):
        result, _, starts = self.run_selector(wide=True, max_lookback_hours=720)
        self.assertEqual(result["window"]["max_lookback_hours"], 96)
        self.assertEqual(result["window"]["requested_max_lookback_hours"], 720)
        self.assertEqual(starts, [self.floor, self.floor])

    def run_selector(self, *, wide, official_candidates=None, max_lookback_hours=96):
        stamps = [("Too old", "2026-10-06T08:29:59+08:00"),
                  ("At boundary", self.floor.isoformat()),
                  ("Recent", "2026-10-09T09:00:00+08:00")]
        items = []
        for index, (title, stamp) in enumerate(stamps):
            items.append(f'<item><title>{title}</title><link>https://aihot.news/items/{index}</link>'
                         f'<description>New agent release</description>'
                         f'<pubDate>{format_datetime(datetime.fromisoformat(stamp))}</pubDate></item>')
        xml = '<rss><channel>' + ''.join(items) + '</channel></rss>'
        recalls = [{"title": title, "published_at": stamp,
                    "primary_url": f"https://example.org/{index}",
                    "backlog_id": str(index), "backlog_first_seen": "2026-10-09",
                    "research_window_strategy": "unpublished_backlog"}
                   for index, (title, stamp) in enumerate([
                       ("Old recall", "2026-09-30T12:00:00+08:00"),
                       ("Fresh recall", "2026-10-09T09:00:00+08:00")])]
        registry = {"sources": [
            {"id": "official_news", "enabled": True, "integration": "official_direct",
             "pool": "news", "transport": "rss", "url": "https://example.org/news.xml",
             "catchup_lookback_hours": 720},
            {"id": "official_research", "enabled": True, "integration": "official_direct",
             "pool": "research", "transport": "rss", "url": "https://example.org/research.xml"},
        ]}
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            policy = load_selection_policy()
            policy["source_verification"]["enabled"] = False
            policy["freshness"]["max_lookback_hours"] = max_lookback_hours
            (root / "policy.json").write_text(json.dumps(policy))
            output = root / "selection.json"
            argv = ["select", "--date", "2026-10-10", "--start-at", "2026-09-30T08:30:00+08:00",
                    "--end-at", self.end.isoformat(), "--paper-lookback-hours", "720",
                    "--no-auto-previous", "--no-aihot-daily", "--no-bestblogs",
                    "--selection-policy", str(root / "policy.json"), "--output", str(output)]
            if wide:
                argv.append("--allow-wide-news-window")
            with patch.object(sys, "argv", argv), \
                 patch.object(cli, "run_curl", return_value=xml), \
                 patch.object(cli, "load_source_registry", return_value=registry), \
                 patch.object(cli, "parse_official_rss_feed", return_value=(official_candidates or [], [])) as official, \
                 patch.object(cli, "sync_backlog", return_value={}), \
                 patch.object(cli, "recall_candidates", return_value=recalls), \
                 patch("event_editorial_runtime.evaluate_candidates", return_value=([], {})) as evaluate, \
                 patch("event_editorial_runtime.apply_selection", side_effect=lambda output, *args, **kwargs: output):
                cli.main()
            return (json.loads(output.read_text()), evaluate.call_args.args[0],
                    sorted(call.args[1] for call in official.call_args_list))


if __name__ == "__main__":
    unittest.main()
