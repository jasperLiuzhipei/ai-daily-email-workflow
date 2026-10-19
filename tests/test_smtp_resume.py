from __future__ import annotations

import json
import smtplib
import sys
import tempfile
import unittest
from email.message import EmailMessage
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


WORKFLOW_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WORKFLOW_DIR / "scripts"))

from send_email_package_smtp import formatted_from_header, send_messages  # noqa: E402


def message_for(recipient: str) -> EmailMessage:
    message = EmailMessage()
    message["Subject"] = "续发测试"
    message["From"] = "sender@example.com"
    message["To"] = recipient
    message.set_content("test")
    return message


class FakeSMTP:
    def __init__(self, *args, **kwargs) -> None:
        self.attempted: list[str] = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        return None

    def ehlo(self) -> None:
        return None

    def login(self, user: str, password: str) -> None:
        return None

    def send_message(self, message, from_addr: str, to_addrs: list[str]):
        recipient = to_addrs[0].lower()
        self.attempted.append(recipient)
        if recipient == "bad@example.com":
            raise smtplib.SMTPRecipientsRefused(
                {recipient: (550, b"5.1.1 user unknown")}
            )
        return {}


class SMTPResumeTests(unittest.TestCase):
    def test_display_name_does_not_change_envelope_address(self) -> None:
        header = formatted_from_header("sender@example.com", "AI Daily｜AI 情报雷达")
        self.assertIn("sender@example.com", header)
        self.assertNotEqual(header, "sender@example.com")
        self.assertEqual(formatted_from_header("sender@example.com", ""), "sender@example.com")

    def test_skips_sent_recipient_continues_after_refusal_and_persists_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            state_path = Path(temp) / "smtp_state.json"
            state_path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "subject": "续发测试",
                        "created_at": "2026-07-24T00:00:00Z",
                        "updated_at": "2026-07-24T00:00:00Z",
                        "sent": {
                            "done@example.com": {
                                "sent_at": "2026-07-24T00:00:00Z"
                            }
                        },
                        "refused": {},
                    }
                ),
                encoding="utf-8",
            )
            args = SimpleNamespace(
                smtp_host="smtp.example.com",
                smtp_port=465,
                smtp_security="ssl",
                smtp_user=None,
                smtp_password=None,
                ask_password=False,
                from_addr="sender@example.com",
                delivery_state=state_path,
            )
            messages = [
                (message_for("done@example.com"), ["done@example.com"]),
                (message_for("bad@example.com"), ["bad@example.com"]),
                (message_for("next@example.com"), ["next@example.com"]),
            ]

            with patch("send_email_package_smtp.smtplib.SMTP_SSL", FakeSMTP):
                report = send_messages(args, messages, subject="续发测试")

            self.assertEqual(report["skipped"], 1)
            self.assertEqual(report["sent_now"], 1)
            self.assertEqual(report["unresolved"], ["bad@example.com"])
            state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertIn("done@example.com", state["sent"])
            self.assertIn("next@example.com", state["sent"])
            self.assertEqual(state["refused"]["bad@example.com"]["code"], 550)


if __name__ == "__main__":
    unittest.main()
