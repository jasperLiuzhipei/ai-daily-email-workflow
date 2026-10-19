from __future__ import annotations

import json
import tempfile
import threading
import unittest
from contextlib import contextmanager
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.request import HTTPRedirectHandler, Request, build_opener, urlopen

from feedback_service.admin_views import dashboard_page, issue_page
from feedback_service.core import (
    FeedbackError,
    FeedbackStore,
    create_feedback_token,
    sign_click_link,
    verify_click_signature,
    verify_feedback_token,
)
from feedback_service.routes import API_DELIVERY_PATH, HEALTH_PATH, METADATA_PATH, click_url
from feedback_service.server import FeedbackApplication, FeedbackHandler


SECRET = "test-secret-at-least-sixteen-characters"


@contextmanager
def feedback_http_server(database: Path, admin_key: str):
    app = FeedbackApplication(database, SECRET, admin_key, send_ledger=None)
    server = ThreadingHTTPServer(("127.0.0.1", 0), FeedbackHandler)
    server.app = app  # type: ignore[attr-defined]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address
        yield f"http://{host}:{port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


class FeedbackTokenTests(unittest.TestCase):
    def test_round_trip_and_anonymous_identity(self) -> None:
        token = create_feedback_token(
            "2026-07-17",
            "person@example.com",
            SECRET,
            now=1_700_000_000,
        )
        identity = verify_feedback_token(token, SECRET, now=1_700_000_100)
        self.assertEqual(identity.issue_id, "2026-07-17")
        self.assertNotIn("person@example.com", token)
        self.assertEqual(len(identity.recipient_id), 32)

    def test_tampered_and_expired_tokens_are_rejected(self) -> None:
        token = create_feedback_token("17", "person@example.com", SECRET, ttl_days=1, now=100)
        with self.assertRaises(FeedbackError):
            verify_feedback_token(token + "x", SECRET, now=101)
        with self.assertRaises(FeedbackError):
            verify_feedback_token(token, SECRET, now=100 + 86401)

    def test_click_signature_cannot_be_reused_for_another_destination(self) -> None:
        token = create_feedback_token("issue-17", "person@example.com", SECRET)
        signature = sign_click_link(
            token,
            "source-01",
            "https://example.com/article",
            "测试文章",
            SECRET,
        )
        verify_click_signature(
            token,
            "source-01",
            "https://example.com/article",
            "测试文章",
            signature,
            SECRET,
        )
        with self.assertRaises(FeedbackError):
            verify_click_signature(
                token,
                "source-01",
                "https://example.com/other",
                "测试文章",
                signature,
                SECRET,
            )


class FeedbackStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.store = FeedbackStore(Path(self.temp_dir.name) / "feedback.db")
        self.token = create_feedback_token("issue-17", "person@example.com", SECRET)
        self.identity = verify_feedback_token(self.token, SECRET)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_rating_upserts_and_duplicate_event_is_idempotent(self) -> None:
        first = self.store.record_rating(self.identity, 3, "rating-event-1", timestamp="2026-07-17T01:00:00+00:00")
        duplicate = self.store.record_rating(self.identity, 5, "rating-event-1", timestamp="2026-07-17T01:00:01+00:00")
        changed = self.store.record_rating(self.identity, 5, "rating-event-2", timestamp="2026-07-17T01:00:02+00:00")

        self.assertEqual(first["score"], 3)
        self.assertTrue(duplicate["duplicate_event"])
        self.assertEqual(duplicate["score"], 3)
        self.assertEqual(changed["score"], 5)
        summary = self.store.summary("issue-17")
        self.assertEqual(summary["rating_count"], 1)
        self.assertEqual(summary["distribution"]["5"], 1)

    def test_comment_is_optional_and_updates_one_record(self) -> None:
        self.store.record_comment(self.identity, "  第一次建议  ", "comment-event-1")
        self.store.record_comment(self.identity, "第二次建议", "comment-event-2")
        summary = self.store.summary("issue-17")
        self.assertEqual(summary["comment_count"], 1)
        comments = self.store.list_comments("issue-17")
        self.assertEqual(len(comments), 1)
        self.assertEqual(comments[0]["comment"], "第二次建议")

    def test_open_count_tracks_unique_and_total(self) -> None:
        self.store.record_open(self.identity)
        self.store.record_open(self.identity)
        summary = self.store.summary("issue-17")
        self.assertEqual(summary["estimated_unique_opens"], 1)
        self.assertEqual(summary["estimated_total_opens"], 2)

    def test_clicks_keep_candidates_but_only_qualified_requests_count_for_ctr(self) -> None:
        self.store.upsert_delivery("issue-17", recipient_count=20)
        self.store.record_click(
            self.identity,
            "source-01",
            "测试文章",
            "https://example.com/article",
            qualified=False,
            timestamp="2026-07-17T04:00:00+00:00",
        )
        self.store.record_click(
            self.identity,
            "source-01",
            "测试文章",
            "https://example.com/article",
            qualified=True,
            timestamp="2026-07-17T04:01:00+00:00",
        )
        summary = self.store.summary("issue-17")
        self.assertEqual(summary["candidate_unique_clicks"], 1)
        self.assertEqual(summary["candidate_total_clicks"], 2)
        self.assertEqual(summary["effective_unique_clicks"], 1)
        self.assertEqual(summary["effective_total_clicks"], 1)
        self.assertEqual(summary["filtered_click_requests"], 1)
        self.assertEqual(summary["effective_click_rate"], 5.0)
        links = self.store.list_click_links("issue-17")
        self.assertEqual(len(links), 1)
        self.assertEqual(links[0]["link_title"], "测试文章")
        self.assertEqual(links[0]["effective_click_rate"], 5.0)

    def test_delivery_denominators_calculate_rates(self) -> None:
        self.store.upsert_delivery(
            "issue-17",
            issue_number=18,
            series="trial",
            sent_at="2026-07-17T11:31:01+08:00",
            recipient_count=20,
            recipient_group="AI 日报正式名单",
        )
        self.store.record_open(self.identity, timestamp="2026-07-17T04:00:00+00:00")
        self.store.record_rating(self.identity, 4, "rating-rate-test")
        self.store.record_comment(self.identity, "希望多一些方法论", "comment-rate-test")

        summary = self.store.summary("issue-17")
        self.assertEqual(summary["recipient_count"], 20)
        self.assertEqual(summary["issue_number"], 18)
        self.assertEqual(summary["estimated_open_rate"], 5.0)
        self.assertEqual(summary["rating_rate"], 5.0)
        self.assertEqual(summary["comment_rate"], 5.0)

    def test_issue_summaries_are_sorted_by_date_descending(self) -> None:
        self.store.upsert_delivery("2026-07-16", recipient_count=30)
        self.store.upsert_delivery("2026-07-18", recipient_count=31)
        self.store.upsert_delivery("2026-07-17", recipient_count=32)
        issues = self.store.list_issue_summaries()
        dated = [item["issue_id"] for item in issues if str(item["issue_id"]).startswith("2026-")]
        self.assertEqual(
            dated[:3],
            ["2026-07-18", "2026-07-17", "2026-07-16"],
        )

    def test_send_ledger_sync_uses_delivery_metadata_and_snapshot_fallback(self) -> None:
        root = Path(self.temp_dir.name)
        package = root / "email_packages" / "2026-07-17" / "prod"
        package.mkdir(parents=True)
        snapshot = package / "recipient_snapshot.json"
        snapshot.write_text(
            json.dumps({"recipient_count": 30, "recipients": [{"email": "hidden@example.com"}]}),
            encoding="utf-8",
        )
        ledger = root / "daily_runs" / "send_ledger.json"
        ledger.parent.mkdir(parents=True)
        ledger.write_text(
            json.dumps(
                {
                    "prod": {
                        "2026-07-17": {
                            "sent_at": "2026-07-17T11:31:01+08:00",
                            "series": "trial",
                            "issue": 18,
                            "eml": "email_packages/2026-07-17/prod/ai-7-17.eml",
                            "recipient_list": "prod",
                        }
                    }
                }
            ),
            encoding="utf-8",
        )

        self.assertEqual(self.store.sync_send_ledger(ledger), 1)
        summary = self.store.summary("2026-07-17")
        self.assertEqual(summary["recipient_count"], 30)
        self.assertEqual(summary["issue_number"], 18)
        self.assertNotIn("hidden@example.com", json.dumps(summary))

    def test_admin_views_render_summary_and_comments(self) -> None:
        self.store.upsert_delivery("2026-07-17", issue_number=18, recipient_count=30)
        summary = self.store.summary("2026-07-17")
        dashboard = dashboard_page([summary])
        detail = issue_page(summary, [{
            "recipient_id": "anonymous-recipient",
            "comment": "选源很好",
            "created_at": "2026-07-17T03:00:00+00:00",
            "updated_at": "2026-07-17T03:10:00+00:00",
        }])
        self.assertIn("日报数据后台", dashboard)
        self.assertIn("2026-07-17", dashboard)
        self.assertIn("选源很好", detail)
        self.assertIn("2026.07.17 11:10", detail)


class FeedbackRouteTests(unittest.TestCase):
    def test_click_route_records_then_redirects_without_becoming_an_open_redirect(self) -> None:
        class NoRedirect(HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, newurl):
                return None

        with tempfile.TemporaryDirectory() as temp_dir:
            with feedback_http_server(Path(temp_dir) / "feedback.db", "admin-test-key") as base_url:
                token = create_feedback_token("2026-07-22", "person@example.com", SECRET)
                target = "https://example.com/article"
                signature = sign_click_link(token, "source-01", target, "测试文章", SECRET)
                url = click_url(
                    base_url,
                    token,
                    "source-01",
                    target,
                    "测试文章",
                    signature,
                )
                opener = build_opener(NoRedirect)
                with self.assertRaises(Exception) as captured:
                    opener.open(Request(url, headers={"User-Agent": "Mozilla/5.0"}), timeout=2)
                self.assertEqual(getattr(captured.exception, "code", None), 302)
                request = Request(
                    f"{base_url}/api/summary/2026-07-22",
                    headers={"X-Admin-Key": "admin-test-key"},
                )
                with urlopen(request, timeout=2) as response:
                    summary = json.loads(response.read().decode("utf-8"))
                self.assertEqual(summary["effective_unique_clicks"], 1)

    def test_deployment_routes_and_delivery_sync_share_one_contract(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            with feedback_http_server(Path(temp_dir) / "feedback.db", "admin-test-key") as base_url:
                with urlopen(f"{base_url}{HEALTH_PATH}", timeout=2) as response:
                    health = json.loads(response.read().decode("utf-8"))
                with urlopen(f"{base_url}{METADATA_PATH}", timeout=2) as response:
                    metadata = json.loads(response.read().decode("utf-8"))
                self.assertTrue(health["ok"])
                self.assertEqual(metadata["routes"]["delivery_api"], API_DELIVERY_PATH)

                request = Request(
                    f"{base_url}{API_DELIVERY_PATH}",
                    data=json.dumps({
                        "issue_id": "2026-07-22",
                        "issue_number": 21,
                        "series": "trial",
                        "sent_at": "2026-07-22T12:00:00+08:00",
                        "recipient_count": 27,
                        "recipient_group": "AI 日报正式名单",
                        "recipient_fingerprint": "test-fingerprint",
                    }).encode("utf-8"),
                    headers={
                        "Content-Type": "application/json",
                        "X-Admin-Key": "admin-test-key",
                    },
                    method="POST",
                )
                with urlopen(request, timeout=2) as response:
                    summary = json.loads(response.read().decode("utf-8"))
                self.assertEqual(summary["issue_id"], "2026-07-22")
                self.assertEqual(summary["recipient_count"], 27)


if __name__ == "__main__":
    unittest.main()
