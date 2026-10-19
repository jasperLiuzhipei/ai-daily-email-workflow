import sys
import tempfile
import unittest
from email import policy
from email.parser import BytesParser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from create_email_send_package import build_personalized_messages, FeedbackRuntime


class NewRecipientThreadingTests(unittest.TestCase):
    def test_midweek_newcomer_has_no_other_recipients_thread_headers(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            rows = build_personalized_messages(package_dir=root,
                html="<html><body>Daily</body></html>", plain_text="Daily",
                subject="Weekly subject", from_addr="sender@example.com", from_name="Daily",
                recipients=[{"email":"old@example.com"}, {"email":"new@example.com"}],
                images=[], feedback=FeedbackRuntime(enabled=True),
                conversation_root_message_ids={"old@example.com":"<old-root@example.com>"})
            messages = [BytesParser(policy=policy.default).parsebytes((root/r["path"]).read_bytes()) for r in rows]
            self.assertEqual(len(messages), 2)
            self.assertEqual(messages[0]["In-Reply-To"], "<old-root@example.com>")
            self.assertIsNone(messages[1]["In-Reply-To"])
            self.assertIsNone(messages[1]["References"])
            self.assertIn("new@example.com", messages[1]["To"])
            self.assertNotIn("old@example.com", messages[1]["To"])
            self.assertTrue(messages[1]["Message-ID"])


if __name__ == "__main__":
    unittest.main()
