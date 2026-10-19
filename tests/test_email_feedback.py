from __future__ import annotations

import sys
import os
import json
import subprocess
import tempfile
import unittest
from email import policy
from email.parser import BytesParser
from email.utils import getaddresses
from pathlib import Path

from bs4 import BeautifulSoup


WORKFLOW_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WORKFLOW_DIR / "scripts"))
sys.path.insert(0, str(WORKFLOW_DIR))

from email_feedback import personalize_feedback, remove_feedback, render_feedback_block  # noqa: E402
from email_recipients import load_recipient_snapshot  # noqa: E402
from feedback_service.core import verify_feedback_token  # noqa: E402
from send_email_package_smtp import load_delivery_messages  # noqa: E402


SECRET = "feedback-test-secret-at-least-sixteen"


class EmailFeedbackTests(unittest.TestCase):
    def source_html(self) -> str:
        return (
            "<html><body>"
            '<a href="https://example.com/article?id=7" data-ai-daily-track-link="1" '
            'data-ai-daily-track-title="测试文章">阅读原文</a>'
            f"<table>{render_feedback_block()}</table>"
            "</body></html>"
        )

    def test_personalization_activates_five_scores_comment_and_pixel(self) -> None:
        html, token = personalize_feedback(
            self.source_html(),
            base_url="https://feedback.internal.example/",
            issue_id="2026-07-21",
            recipient_key="person@example.com",
            secret=SECRET,
        )
        identity = verify_feedback_token(token, SECRET)
        self.assertEqual(identity.issue_id, "2026-07-21")
        self.assertNotIn("person@example.com", token)

        soup = BeautifulSoup(html, "html.parser")
        rating_links = [link.get("href") for link in soup.select("a.rating-star")]
        self.assertEqual(len(rating_links), 5)
        self.assertTrue(rating_links[0].endswith("#score=1"))
        self.assertTrue(rating_links[-1].endswith("#score=5"))
        comment_link = next(link for link in soup.find_all("a") if "写一句建议" in link.get_text(" ", strip=True))
        self.assertIn("/comment/", comment_link.get("href"))
        pixel = soup.find("img", attrs={"data-ai-daily-open": "1"})
        self.assertIsNotNone(pixel)
        self.assertIn("/open/", pixel.get("src"))
        article = soup.find("a", string="阅读原文")
        self.assertIsNotNone(article)
        self.assertEqual(article.get("href"), "https://example.com/article?id=7")
        self.assertEqual(article.get("target"), "_blank")
        self.assertIn("noopener", article.get("rel"))
        self.assertNotIn("data-ai-daily-track", str(article))

    def test_preview_cleanup_removes_feedback_without_tracking(self) -> None:
        cleaned = remove_feedback(self.source_html())
        self.assertNotIn("今天这期怎么样", cleaned)
        self.assertNotIn("data-ai-daily-feedback", cleaned)
        self.assertNotIn("/open/", cleaned)

    def test_package_keeps_preview_clean_and_builds_one_direct_link_eml_per_recipient(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            html_path = root / "email.html"
            html_path.write_text(
                "<html><head><title>反馈测试</title></head><body>"
                '<a href="https://example.com/article" data-ai-daily-track-link="1" '
                'data-ai-daily-track-title="测试文章">阅读原文</a>'
                f"<table>{render_feedback_block()}</table></body></html>",
                encoding="utf-8",
            )
            package_dir = root / "package"
            env = {
                **os.environ,
                "AI_DAILY_FEEDBACK_BASE_URL": "https://feedback.internal.example",
                "AI_DAILY_FEEDBACK_SECRET": SECRET,
            }
            root_result = subprocess.run(
                [
                    sys.executable,
                    str(WORKFLOW_DIR / "scripts" / "create_email_send_package.py"),
                    str(html_path),
                    "--out-dir",
                    str(package_dir),
                    "--to",
                    "甲 <a@example.com>",
                    "--to",
                    "乙 <b@example.com>",
                    "--feedback-issue",
                    "2026-07-21",
                    "--skip-feedback-health-check",
                ],
                env=env,
                capture_output=True,
                text=True,
            )
            self.assertEqual(root_result.returncode, 0, root_result.stderr)

            manifest = json.loads((package_dir / "manifest.json").read_text(encoding="utf-8"))
            self.assertTrue(manifest["personalized_delivery"]["enabled"])
            self.assertEqual(
                manifest["personalized_delivery"]["feedback_base_url"],
                "https://feedback.internal.example",
            )
            self.assertEqual(manifest["personalized_delivery"]["message_count"], 2)
            preview_path = next(package_dir.glob("*.eml"))
            preview = BytesParser(policy=policy.default).parsebytes(preview_path.read_bytes())
            preview_html = next(part.get_content() for part in preview.walk() if part.get_content_type() == "text/html")
            self.assertNotIn("今天这期怎么样", preview_html)
            self.assertNotIn("/open/", preview_html)
            self.assertNotIn("/click/", preview_html)

            delivery = load_recipient_snapshot(package_dir / "recipient_snapshot.json")
            messages, personalized = load_delivery_messages(preview_path, delivery)
            self.assertTrue(personalized)
            self.assertEqual(len(messages), 2)
            for message, recipient in messages:
                self.assertEqual(len(recipient), 1)
                html = next(part.get_content() for part in message.walk() if part.get_content_type() == "text/html")
                self.assertIn("今天这期怎么样", html)
                self.assertIn("/open/", html)
                self.assertNotIn("/click/", html)
                self.assertIn('href="https://example.com/article"', html)
                self.assertEqual(sum(f"#score={score}" in html for score in range(1, 6)), 5)

    def test_unavailable_feedback_degrades_without_losing_personalized_delivery(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            html_path = root / "email.html"
            html_path.write_text(
                "<html><head><title>降级测试</title></head><body>"
                '<a href="https://example.com/direct" data-ai-daily-track-link="1" '
                'data-ai-daily-track-title="测试文章">阅读原文</a>'
                f"<table>{render_feedback_block()}</table></body></html>",
                encoding="utf-8",
            )
            package_dir = root / "package"
            env = {
                **os.environ,
                "AI_DAILY_FEEDBACK_BASE_URL": "http://127.0.0.1:9",
                "AI_DAILY_FEEDBACK_SECRET": SECRET,
            }
            result = subprocess.run(
                [
                    sys.executable,
                    str(WORKFLOW_DIR / "scripts" / "create_email_send_package.py"),
                    str(html_path),
                    "--out-dir",
                    str(package_dir),
                    "--to",
                    "甲 <a@example.com>",
                    "--feedback-issue",
                    "2026-07-21",
                    "--feedback-health-timeout",
                    "0.2",
                ],
                env=env,
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            manifest = json.loads((package_dir / "manifest.json").read_text(encoding="utf-8"))
            delivery = manifest["personalized_delivery"]
            self.assertTrue(delivery["enabled"])
            self.assertFalse(delivery["feedback_features_enabled"])
            self.assertEqual(delivery["feedback_health_status"], "unavailable")
            self.assertEqual(delivery["source_links"], "direct")
            self.assertEqual(delivery["message_count"], 1)
            message_path = package_dir / delivery["messages"][0]["path"]
            message = BytesParser(policy=policy.default).parsebytes(message_path.read_bytes())
            html = next(part.get_content() for part in message.walk() if part.get_content_type() == "text/html")
            self.assertIn('href="https://example.com/direct"', html)
            self.assertNotIn("/click/", html)
            self.assertNotIn("今天这期怎么样", html)
            self.assertNotIn("data-ai-daily-open", html)

    def test_weekly_conversation_uses_each_recipient_root_message_id(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            root_html = root / "monday.html"
            root_html.write_text(
                "<html><head><title>周报</title></head><body>周一根邮件"
                '<a href="https://example.com/root" data-ai-daily-track-link="1">阅读原文</a>'
                f"<table>{render_feedback_block()}</table></body></html>",
                encoding="utf-8",
            )
            followup_html = root / "tuesday.html"
            followup_html.write_text(
                "<html><head><title>周报</title></head><body>周二跟帖"
                '<a href="https://example.com/followup" data-ai-daily-track-link="1">阅读原文</a>'
                f"<table>{render_feedback_block()}</table></body></html>",
                encoding="utf-8",
            )
            root_package = root / "monday-package"
            followup_package = root / "tuesday-package"
            env = {
                **os.environ,
                "AI_DAILY_FEEDBACK_BASE_URL": "https://feedback.internal.example",
                "AI_DAILY_FEEDBACK_SECRET": SECRET,
            }
            common = [
                "--to",
                "甲 <a@example.com>",
                "--to",
                "乙 <b@example.com>",
                "--feedback-issue",
                "2026-08-10",
                "--skip-feedback-health-check",
            ]
            root_result = subprocess.run(
                [
                    sys.executable,
                    str(WORKFLOW_DIR / "scripts" / "create_email_send_package.py"),
                    str(root_html),
                    "--out-dir",
                    str(root_package),
                    *common,
                ],
                env=env,
                capture_output=True,
                text=True,
            )
            self.assertEqual(root_result.returncode, 0, root_result.stderr)
            root_ids = {}
            for path in sorted((root_package / "messages").glob("*.eml")):
                message = BytesParser(policy=policy.default).parsebytes(path.read_bytes())
                recipient = next(email for _, email in getaddresses(message.get_all("To", [])))
                root_ids[recipient] = message["Message-ID"]

            followup_result = subprocess.run(
                [
                    sys.executable,
                    str(WORKFLOW_DIR / "scripts" / "create_email_send_package.py"),
                    str(followup_html),
                    "--out-dir",
                    str(followup_package),
                    "--subject",
                    "AI DAILY｜第33周",
                    "--conversation-root-package",
                    str(root_package),
                    *common,
                ],
                env=env,
                capture_output=True,
                text=True,
            )
            self.assertEqual(followup_result.returncode, 0, followup_result.stderr)
            for path in sorted((followup_package / "messages").glob("*.eml")):
                message = BytesParser(policy=policy.default).parsebytes(path.read_bytes())
                recipient = next(email for _, email in getaddresses(message.get_all("To", [])))
                self.assertEqual(message["Subject"], "AI DAILY｜第33周")
                self.assertEqual(message["In-Reply-To"], root_ids[recipient])
                self.assertEqual(message["References"], root_ids[recipient])


if __name__ == "__main__":
    unittest.main()
