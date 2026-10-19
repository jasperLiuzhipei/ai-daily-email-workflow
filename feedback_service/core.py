from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import sqlite3
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse


ISSUE_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
LINK_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,95}$")


class FeedbackError(ValueError):
    """Raised for invalid or expired feedback input."""


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _b64decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass(frozen=True)
class FeedbackIdentity:
    issue_id: str
    recipient_id: str
    expires_at: int


def normalize_click_title(value: object) -> str:
    title = " ".join(str(value or "").split())
    if len(title) > 240:
        raise FeedbackError("Link title is too long.")
    return title


def validate_click_destination(value: object) -> str:
    destination = str(value or "").strip()
    parsed = urlparse(destination)
    if (
        not destination
        or len(destination) > 4096
        or parsed.scheme not in {"https", "http"}
        or not parsed.netloc
    ):
        raise FeedbackError("Invalid click destination.")
    return destination


def _click_payload(token: str, link_id: str, destination_url: str, link_title: str) -> bytes:
    return "\0".join(
        ("ai-daily-click-v1", token, link_id, destination_url, normalize_click_title(link_title))
    ).encode("utf-8")


def sign_click_link(
    token: str,
    link_id: str,
    destination_url: str,
    link_title: str,
    secret: str,
) -> str:
    if not LINK_ID_RE.fullmatch(link_id):
        raise FeedbackError("Invalid link ID.")
    validate_click_destination(destination_url)
    digest = hmac.new(
        secret.encode("utf-8"),
        _click_payload(token, link_id, destination_url, link_title),
        hashlib.sha256,
    ).digest()
    return _b64encode(digest)


def verify_click_signature(
    token: str,
    link_id: str,
    destination_url: str,
    link_title: str,
    signature: str,
    secret: str,
) -> None:
    if not LINK_ID_RE.fullmatch(link_id):
        raise FeedbackError("Invalid link ID.")
    validate_click_destination(destination_url)
    try:
        supplied = _b64decode(signature)
    except ValueError as exc:
        raise FeedbackError("Invalid click signature.") from exc
    expected = hmac.new(
        secret.encode("utf-8"),
        _click_payload(token, link_id, destination_url, link_title),
        hashlib.sha256,
    ).digest()
    if not hmac.compare_digest(supplied, expected):
        raise FeedbackError("Invalid click signature.")


def create_feedback_token(
    issue_id: str,
    recipient_key: str,
    secret: str,
    *,
    ttl_days: int = 90,
    now: int | None = None,
) -> str:
    if not ISSUE_RE.fullmatch(issue_id):
        raise FeedbackError("Invalid issue ID.")
    if not recipient_key.strip():
        raise FeedbackError("Recipient key is required.")
    if len(secret) < 16:
        raise FeedbackError("Feedback signing secret must be at least 16 characters.")

    issued_at = int(time.time() if now is None else now)
    recipient_id = hmac.new(
        secret.encode("utf-8"),
        f"recipient\0{issue_id}\0{recipient_key.strip().lower()}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()[:32]
    payload = json.dumps(
        {"i": issue_id, "r": recipient_id, "e": issued_at + ttl_days * 86400},
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    signature = hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).digest()
    return f"{_b64encode(payload)}.{_b64encode(signature)}"


def verify_feedback_token(
    token: str,
    secret: str,
    *,
    now: int | None = None,
) -> FeedbackIdentity:
    try:
        encoded_payload, encoded_signature = token.split(".", 1)
        payload = _b64decode(encoded_payload)
        signature = _b64decode(encoded_signature)
        expected = hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).digest()
        if not hmac.compare_digest(signature, expected):
            raise FeedbackError("Invalid feedback token.")
        data = json.loads(payload)
        identity = FeedbackIdentity(
            issue_id=str(data["i"]),
            recipient_id=str(data["r"]),
            expires_at=int(data["e"]),
        )
    except FeedbackError:
        raise
    except (ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise FeedbackError("Invalid feedback token.") from exc

    if not ISSUE_RE.fullmatch(identity.issue_id):
        raise FeedbackError("Invalid feedback token.")
    if int(time.time() if now is None else now) > identity.expires_at:
        raise FeedbackError("Feedback token has expired.")
    return identity


SCHEMA = """
CREATE TABLE IF NOT EXISTS ratings (
    issue_id TEXT NOT NULL,
    recipient_id TEXT NOT NULL,
    score INTEGER NOT NULL CHECK(score BETWEEN 1 AND 5),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (issue_id, recipient_id)
);

CREATE TABLE IF NOT EXISTS comments (
    issue_id TEXT NOT NULL,
    recipient_id TEXT NOT NULL,
    comment_text TEXT NOT NULL CHECK(length(comment_text) BETWEEN 1 AND 300),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (issue_id, recipient_id)
);

CREATE TABLE IF NOT EXISTS feedback_events (
    event_id TEXT PRIMARY KEY,
    issue_id TEXT NOT NULL,
    recipient_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    event_value TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS open_events (
    issue_id TEXT NOT NULL,
    recipient_id TEXT NOT NULL,
    first_opened_at TEXT NOT NULL,
    last_opened_at TEXT NOT NULL,
    open_count INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (issue_id, recipient_id)
);

CREATE TABLE IF NOT EXISTS click_events (
    issue_id TEXT NOT NULL,
    recipient_id TEXT NOT NULL,
    link_id TEXT NOT NULL,
    link_title TEXT NOT NULL DEFAULT '',
    destination_url TEXT NOT NULL,
    first_clicked_at TEXT NOT NULL,
    last_clicked_at TEXT NOT NULL,
    request_count INTEGER NOT NULL DEFAULT 1 CHECK(request_count > 0),
    qualified_first_clicked_at TEXT,
    qualified_last_clicked_at TEXT,
    qualified_click_count INTEGER NOT NULL DEFAULT 0
        CHECK(qualified_click_count >= 0 AND qualified_click_count <= request_count),
    PRIMARY KEY (issue_id, recipient_id, link_id)
);

CREATE TABLE IF NOT EXISTS deliveries (
    issue_id TEXT PRIMARY KEY,
    issue_number INTEGER,
    series TEXT,
    sent_at TEXT,
    recipient_count INTEGER NOT NULL DEFAULT 0,
    recipient_group TEXT,
    recipient_fingerprint TEXT,
    snapshot_path TEXT,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_feedback_events_issue_created
    ON feedback_events(issue_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_comments_issue_updated
    ON comments(issue_id, updated_at DESC);

CREATE INDEX IF NOT EXISTS idx_deliveries_sent_at
    ON deliveries(sent_at DESC);

CREATE INDEX IF NOT EXISTS idx_click_events_issue_qualified
    ON click_events(issue_id, qualified_click_count DESC, last_clicked_at DESC);
"""


class FeedbackStore:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 10000")
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def initialize(self) -> None:
        with self.connect() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.executescript(SCHEMA)

    def upsert_delivery(
        self,
        issue_id: str,
        *,
        issue_number: int | None = None,
        series: str | None = None,
        sent_at: str | None = None,
        recipient_count: int = 0,
        recipient_group: str | None = None,
        recipient_fingerprint: str | None = None,
        snapshot_path: str | None = None,
        timestamp: str | None = None,
    ) -> None:
        if not ISSUE_RE.fullmatch(issue_id):
            raise FeedbackError("Invalid issue ID.")
        if recipient_count < 0:
            raise FeedbackError("Recipient count cannot be negative.")
        if issue_number is not None and issue_number <= 0:
            raise FeedbackError("Issue number must be positive.")
        updated_at = timestamp or utc_now()
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO deliveries
                    (issue_id, issue_number, series, sent_at, recipient_count,
                     recipient_group, recipient_fingerprint, snapshot_path, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(issue_id) DO UPDATE SET
                    issue_number = excluded.issue_number,
                    series = excluded.series,
                    sent_at = excluded.sent_at,
                    recipient_count = excluded.recipient_count,
                    recipient_group = excluded.recipient_group,
                    recipient_fingerprint = excluded.recipient_fingerprint,
                    snapshot_path = excluded.snapshot_path,
                    updated_at = excluded.updated_at
                """,
                (
                    issue_id,
                    issue_number,
                    series,
                    sent_at,
                    recipient_count,
                    recipient_group,
                    recipient_fingerprint,
                    snapshot_path,
                    updated_at,
                ),
            )

    @staticmethod
    def _snapshot_recipient_count(path: Path) -> int | None:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(payload, dict):
            return None
        value = payload.get("recipient_count")
        if isinstance(value, int) and value >= 0:
            return value
        recipients = payload.get("recipients")
        if isinstance(recipients, list):
            return len(recipients)
        return None

    def sync_send_ledger(self, path: Path | str) -> int:
        """Import production delivery metadata without storing email addresses."""

        ledger_path = Path(path)
        if not ledger_path.exists():
            return 0
        try:
            payload = json.loads(ledger_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise FeedbackError(f"Unable to read send ledger: {ledger_path}") from exc
        production = payload.get("prod") if isinstance(payload, dict) else None
        if not isinstance(production, dict):
            raise FeedbackError("Send ledger must contain a prod object.")

        project_root = ledger_path.resolve().parent.parent
        imported = 0
        for issue_id, raw in production.items():
            if not isinstance(raw, dict) or not ISSUE_RE.fullmatch(str(issue_id)):
                continue
            delivery = raw.get("recipient_delivery")
            delivery = delivery if isinstance(delivery, dict) else {}
            group = delivery.get("recipient_group")
            group = group if isinstance(group, dict) else {}
            recipient_count = delivery.get("recipient_count")
            snapshot = delivery.get("snapshot")
            if not isinstance(snapshot, str) or not snapshot.strip():
                eml = raw.get("eml")
                if isinstance(eml, str) and eml.strip():
                    snapshot = str(Path(eml).parent / "recipient_snapshot.json")
            snapshot_path: Path | None = None
            if isinstance(snapshot, str) and snapshot.strip():
                candidate = Path(snapshot)
                snapshot_path = candidate if candidate.is_absolute() else project_root / candidate
            if not isinstance(recipient_count, int) or recipient_count < 0:
                recipient_count = (
                    self._snapshot_recipient_count(snapshot_path)
                    if snapshot_path is not None
                    else None
                )
            if recipient_count is None:
                recipient_count = 0

            issue_number = raw.get("issue")
            if not isinstance(issue_number, int) or issue_number <= 0:
                issue_number = None
            self.upsert_delivery(
                str(issue_id),
                issue_number=issue_number,
                series=str(raw.get("series") or "") or None,
                sent_at=str(raw.get("sent_at") or "") or None,
                recipient_count=recipient_count,
                recipient_group=str(group.get("name") or raw.get("recipient_list") or "") or None,
                recipient_fingerprint=str(delivery.get("fingerprint") or "") or None,
                snapshot_path=str(snapshot or "") or None,
            )
            imported += 1
        return imported

    @staticmethod
    def _validate_event_id(event_id: str) -> str:
        value = event_id.strip()
        if not value or len(value) > 128:
            raise FeedbackError("Invalid event ID.")
        return value

    def record_rating(
        self,
        identity: FeedbackIdentity,
        score: int,
        event_id: str,
        *,
        timestamp: str | None = None,
    ) -> dict[str, object]:
        if score not in range(1, 6):
            raise FeedbackError("Score must be between 1 and 5.")
        event_id = self._validate_event_id(event_id)
        recorded_at = timestamp or utc_now()

        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO feedback_events
                    (event_id, issue_id, recipient_id, event_type, event_value, created_at)
                VALUES (?, ?, ?, 'rating', ?, ?)
                """,
                (event_id, identity.issue_id, identity.recipient_id, str(score), recorded_at),
            )
            duplicate_event = cursor.rowcount == 0
            if not duplicate_event:
                connection.execute(
                    """
                    INSERT INTO ratings
                        (issue_id, recipient_id, score, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(issue_id, recipient_id) DO UPDATE SET
                        score = excluded.score,
                        updated_at = excluded.updated_at
                    """,
                    (
                        identity.issue_id,
                        identity.recipient_id,
                        score,
                        recorded_at,
                        recorded_at,
                    ),
                )
            row = connection.execute(
                "SELECT score, created_at, updated_at FROM ratings WHERE issue_id = ? AND recipient_id = ?",
                (identity.issue_id, identity.recipient_id),
            ).fetchone()
        if row is None:
            raise FeedbackError("Rating could not be recorded.")
        return {
            "issue_id": identity.issue_id,
            "score": int(row["score"]),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "duplicate_event": duplicate_event,
        }

    def record_comment(
        self,
        identity: FeedbackIdentity,
        comment: str,
        event_id: str,
        *,
        timestamp: str | None = None,
    ) -> dict[str, object]:
        text = " ".join(comment.strip().split())
        if not 1 <= len(text) <= 300:
            raise FeedbackError("Comment must contain 1 to 300 characters.")
        event_id = self._validate_event_id(event_id)
        recorded_at = timestamp or utc_now()

        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO feedback_events
                    (event_id, issue_id, recipient_id, event_type, event_value, created_at)
                VALUES (?, ?, ?, 'comment', ?, ?)
                """,
                (event_id, identity.issue_id, identity.recipient_id, text, recorded_at),
            )
            duplicate_event = cursor.rowcount == 0
            if not duplicate_event:
                connection.execute(
                    """
                    INSERT INTO comments
                        (issue_id, recipient_id, comment_text, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(issue_id, recipient_id) DO UPDATE SET
                        comment_text = excluded.comment_text,
                        updated_at = excluded.updated_at
                    """,
                    (
                        identity.issue_id,
                        identity.recipient_id,
                        text,
                        recorded_at,
                        recorded_at,
                    ),
                )
            row = connection.execute(
                "SELECT comment_text, created_at, updated_at FROM comments WHERE issue_id = ? AND recipient_id = ?",
                (identity.issue_id, identity.recipient_id),
            ).fetchone()
        if row is None:
            raise FeedbackError("Comment could not be recorded.")
        return {
            "issue_id": identity.issue_id,
            "comment": row["comment_text"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "duplicate_event": duplicate_event,
        }

    def record_open(
        self,
        identity: FeedbackIdentity,
        *,
        timestamp: str | None = None,
    ) -> None:
        opened_at = timestamp or utc_now()
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO open_events
                    (issue_id, recipient_id, first_opened_at, last_opened_at, open_count)
                VALUES (?, ?, ?, ?, 1)
                ON CONFLICT(issue_id, recipient_id) DO UPDATE SET
                    last_opened_at = excluded.last_opened_at,
                    open_count = open_events.open_count + 1
                """,
                (identity.issue_id, identity.recipient_id, opened_at, opened_at),
            )

    def record_click(
        self,
        identity: FeedbackIdentity,
        link_id: str,
        link_title: str,
        destination_url: str,
        *,
        qualified: bool,
        timestamp: str | None = None,
    ) -> dict[str, object]:
        if not LINK_ID_RE.fullmatch(link_id):
            raise FeedbackError("Invalid link ID.")
        title = normalize_click_title(link_title)
        destination = validate_click_destination(destination_url)
        clicked_at = timestamp or utc_now()
        qualified_at = clicked_at if qualified else None
        qualified_count = 1 if qualified else 0
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO click_events
                    (issue_id, recipient_id, link_id, link_title, destination_url,
                     first_clicked_at, last_clicked_at, request_count,
                     qualified_first_clicked_at, qualified_last_clicked_at,
                     qualified_click_count)
                VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?)
                ON CONFLICT(issue_id, recipient_id, link_id) DO UPDATE SET
                    link_title = CASE
                        WHEN excluded.link_title <> '' THEN excluded.link_title
                        ELSE click_events.link_title
                    END,
                    destination_url = excluded.destination_url,
                    last_clicked_at = excluded.last_clicked_at,
                    request_count = click_events.request_count + 1,
                    qualified_first_clicked_at = CASE
                        WHEN excluded.qualified_click_count > 0
                            THEN COALESCE(click_events.qualified_first_clicked_at, excluded.qualified_first_clicked_at)
                        ELSE click_events.qualified_first_clicked_at
                    END,
                    qualified_last_clicked_at = CASE
                        WHEN excluded.qualified_click_count > 0 THEN excluded.qualified_last_clicked_at
                        ELSE click_events.qualified_last_clicked_at
                    END,
                    qualified_click_count = click_events.qualified_click_count + excluded.qualified_click_count
                """,
                (
                    identity.issue_id,
                    identity.recipient_id,
                    link_id,
                    title,
                    destination,
                    clicked_at,
                    clicked_at,
                    qualified_at,
                    qualified_at,
                    qualified_count,
                ),
            )
            row = connection.execute(
                """
                SELECT request_count, qualified_click_count
                FROM click_events
                WHERE issue_id = ? AND recipient_id = ? AND link_id = ?
                """,
                (identity.issue_id, identity.recipient_id, link_id),
            ).fetchone()
        if row is None:
            raise FeedbackError("Click could not be recorded.")
        return {
            "issue_id": identity.issue_id,
            "link_id": link_id,
            "request_count": int(row["request_count"]),
            "qualified_click_count": int(row["qualified_click_count"]),
        }

    def summary(self, issue_id: str) -> dict[str, object]:
        if not ISSUE_RE.fullmatch(issue_id):
            raise FeedbackError("Invalid issue ID.")
        with self.connect() as connection:
            ratings = connection.execute(
                "SELECT score, COUNT(*) AS count FROM ratings WHERE issue_id = ? GROUP BY score",
                (issue_id,),
            ).fetchall()
            average = connection.execute(
                "SELECT AVG(score) AS average, COUNT(*) AS count FROM ratings WHERE issue_id = ?",
                (issue_id,),
            ).fetchone()
            comments = connection.execute(
                "SELECT COUNT(*) AS count FROM comments WHERE issue_id = ?",
                (issue_id,),
            ).fetchone()
            opens = connection.execute(
                "SELECT COUNT(*) AS unique_count, COALESCE(SUM(open_count), 0) AS total_count FROM open_events WHERE issue_id = ?",
                (issue_id,),
            ).fetchone()
            open_window = connection.execute(
                "SELECT MIN(first_opened_at) AS first_opened_at, MAX(last_opened_at) AS last_opened_at FROM open_events WHERE issue_id = ?",
                (issue_id,),
            ).fetchone()
            clicks = connection.execute(
                """
                SELECT
                    COUNT(DISTINCT recipient_id) AS candidate_unique_count,
                    COALESCE(SUM(request_count), 0) AS candidate_total_count,
                    COUNT(DISTINCT CASE WHEN qualified_click_count > 0 THEN recipient_id END) AS effective_unique_count,
                    COALESCE(SUM(qualified_click_count), 0) AS effective_total_count
                FROM click_events
                WHERE issue_id = ?
                """,
                (issue_id,),
            ).fetchone()
            delivery = connection.execute(
                """
                SELECT issue_number, series, sent_at, recipient_count, recipient_group,
                       recipient_fingerprint, snapshot_path
                FROM deliveries WHERE issue_id = ?
                """,
                (issue_id,),
            ).fetchone()
        distribution = {str(score): 0 for score in range(1, 6)}
        for row in ratings:
            distribution[str(row["score"])] = int(row["count"])
        rating_count = int(average["count"] or 0)
        comment_count = int(comments["count"] or 0)
        unique_opens = int(opens["unique_count"] or 0)
        total_opens = int(opens["total_count"] or 0)
        candidate_unique_clicks = int(clicks["candidate_unique_count"] or 0)
        candidate_total_clicks = int(clicks["candidate_total_count"] or 0)
        effective_unique_clicks = int(clicks["effective_unique_count"] or 0)
        effective_total_clicks = int(clicks["effective_total_count"] or 0)
        recipient_count = int(delivery["recipient_count"] or 0) if delivery is not None else 0

        def rate(value: int) -> float | None:
            if recipient_count <= 0:
                return None
            return round(value * 100 / recipient_count, 1)

        return {
            "issue_id": issue_id,
            "issue_number": int(delivery["issue_number"]) if delivery is not None and delivery["issue_number"] is not None else None,
            "series": str(delivery["series"] or "") if delivery is not None else "",
            "sent_at": str(delivery["sent_at"] or "") if delivery is not None else "",
            "recipient_count": recipient_count,
            "recipient_group": str(delivery["recipient_group"] or "") if delivery is not None else "",
            "recipient_fingerprint": str(delivery["recipient_fingerprint"] or "") if delivery is not None else "",
            "snapshot_path": str(delivery["snapshot_path"] or "") if delivery is not None else "",
            "rating_count": rating_count,
            "average_score": round(float(average["average"]), 2) if average["average"] is not None else None,
            "distribution": distribution,
            "comment_count": comment_count,
            "estimated_unique_opens": unique_opens,
            "estimated_total_opens": total_opens,
            "first_opened_at": str(open_window["first_opened_at"] or ""),
            "last_opened_at": str(open_window["last_opened_at"] or ""),
            "estimated_open_rate": rate(unique_opens),
            "rating_rate": rate(rating_count),
            "comment_rate": rate(comment_count),
            "candidate_unique_clicks": candidate_unique_clicks,
            "candidate_total_clicks": candidate_total_clicks,
            "effective_unique_clicks": effective_unique_clicks,
            "effective_total_clicks": effective_total_clicks,
            "filtered_click_requests": candidate_total_clicks - effective_total_clicks,
            "effective_click_rate": rate(effective_unique_clicks),
        }

    def list_issue_summaries(self) -> list[dict[str, object]]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT issues.issue_id
                FROM (
                    SELECT issue_id FROM deliveries
                    UNION SELECT issue_id FROM ratings
                    UNION SELECT issue_id FROM comments
                    UNION SELECT issue_id FROM open_events
                    UNION SELECT issue_id FROM click_events
                ) AS issues
                LEFT JOIN deliveries ON deliveries.issue_id = issues.issue_id
                ORDER BY COALESCE(deliveries.sent_at, issues.issue_id) DESC,
                         issues.issue_id DESC
                """
            ).fetchall()
        return [self.summary(str(row["issue_id"])) for row in rows]

    def list_click_links(self, issue_id: str) -> list[dict[str, object]]:
        if not ISSUE_RE.fullmatch(issue_id):
            raise FeedbackError("Invalid issue ID.")
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT
                    link_id,
                    MAX(link_title) AS link_title,
                    MAX(destination_url) AS destination_url,
                    COUNT(*) AS candidate_unique_clicks,
                    COALESCE(SUM(request_count), 0) AS candidate_total_clicks,
                    SUM(CASE WHEN qualified_click_count > 0 THEN 1 ELSE 0 END) AS effective_unique_clicks,
                    COALESCE(SUM(qualified_click_count), 0) AS effective_total_clicks
                FROM click_events
                WHERE issue_id = ?
                GROUP BY link_id
                ORDER BY effective_unique_clicks DESC, effective_total_clicks DESC, link_id ASC
                """,
                (issue_id,),
            ).fetchall()
        denominator = int(self.summary(issue_id).get("recipient_count") or 0)
        return [
            {
                "link_id": str(row["link_id"]),
                "link_title": str(row["link_title"] or ""),
                "destination_url": str(row["destination_url"] or ""),
                "candidate_unique_clicks": int(row["candidate_unique_clicks"] or 0),
                "candidate_total_clicks": int(row["candidate_total_clicks"] or 0),
                "effective_unique_clicks": int(row["effective_unique_clicks"] or 0),
                "effective_total_clicks": int(row["effective_total_clicks"] or 0),
                "filtered_click_requests": int(row["candidate_total_clicks"] or 0)
                - int(row["effective_total_clicks"] or 0),
                "effective_click_rate": (
                    round(int(row["effective_unique_clicks"] or 0) * 100 / denominator, 1)
                    if denominator > 0
                    else None
                ),
            }
            for row in rows
        ]

    def list_comments(self, issue_id: str) -> list[dict[str, str]]:
        if not ISSUE_RE.fullmatch(issue_id):
            raise FeedbackError("Invalid issue ID.")
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT recipient_id, comment_text, created_at, updated_at
                FROM comments
                WHERE issue_id = ?
                ORDER BY updated_at DESC
                """,
                (issue_id,),
            ).fetchall()
        return [
            {
                "recipient_id": str(row["recipient_id"]),
                "comment": str(row["comment_text"]),
                "created_at": str(row["created_at"]),
                "updated_at": str(row["updated_at"]),
            }
            for row in rows
        ]
