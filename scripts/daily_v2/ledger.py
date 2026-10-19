"""SQLite authority for immutable shadow revisions and crash-safe delivery intents.

No SMTP implementation lives here. A future publisher must provide its transport
only after credential isolation and V1 delivery-fact migration are verified.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import date, datetime, timezone
from email.message import EmailMessage
from email import policy
import json
from pathlib import Path
import sqlite3
from uuid import uuid4

from .contracts import GateError, canonical, digest, preflight, recipients


def now():
    return datetime.now(timezone.utc).isoformat()


SCHEMA = """
CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
INSERT OR IGNORE INTO metadata VALUES ('schema_version','2');
INSERT OR IGNORE INTO metadata VALUES ('mode','shadow');
CREATE TABLE IF NOT EXISTS revisions (
 id TEXT PRIMARY KEY, issue_date TEXT NOT NULL, revision INTEGER NOT NULL,
 document TEXT NOT NULL, document_hash TEXT NOT NULL, created_at TEXT NOT NULL,
 UNIQUE(issue_date,revision));
CREATE TRIGGER IF NOT EXISTS immutable_revision BEFORE UPDATE ON revisions
 BEGIN SELECT RAISE(ABORT,'immutable revision'); END;
CREATE TRIGGER IF NOT EXISTS no_revision_delete BEFORE DELETE ON revisions
 BEGIN SELECT RAISE(ABORT,'immutable revision'); END;
CREATE TABLE IF NOT EXISTS reviews (
 id TEXT PRIMARY KEY, revision_id TEXT NOT NULL REFERENCES revisions(id),
 target_hash TEXT NOT NULL, record TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TRIGGER IF NOT EXISTS immutable_review BEFORE UPDATE ON reviews
 BEGIN SELECT RAISE(ABORT,'immutable review'); END;
CREATE TABLE IF NOT EXISTS releases (
 id TEXT PRIMARY KEY, revision_id TEXT NOT NULL UNIQUE REFERENCES revisions(id),
 issue_date TEXT NOT NULL, manifest TEXT NOT NULL, manifest_hash TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('APPROVED','SUPERSEDED','SENDING','PARTIAL','SEND_UNCERTAIN','SENT')));
CREATE UNIQUE INDEX IF NOT EXISTS one_approved ON releases(issue_date) WHERE status='APPROVED';
CREATE TRIGGER IF NOT EXISTS immutable_manifest BEFORE UPDATE OF manifest,manifest_hash,revision_id,issue_date ON releases
 BEGIN SELECT RAISE(ABORT,'immutable release manifest'); END;
CREATE TABLE IF NOT EXISTS messages (
 release_id TEXT NOT NULL REFERENCES releases(id), recipient_id TEXT NOT NULL,
 address TEXT NOT NULL, message_id TEXT NOT NULL, mime BLOB NOT NULL, mime_hash TEXT NOT NULL,
 PRIMARY KEY(release_id,recipient_id));
CREATE TRIGGER IF NOT EXISTS immutable_message BEFORE UPDATE ON messages
 BEGIN SELECT RAISE(ABORT,'immutable MIME'); END;
CREATE TABLE IF NOT EXISTS deliveries (
 release_id TEXT NOT NULL, recipient_id TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('READY','IN_FLIGHT','ACCEPTED','REFUSED','UNKNOWN')),
 attempt_id TEXT, detail TEXT, updated_at TEXT NOT NULL,
 PRIMARY KEY(release_id,recipient_id),
 FOREIGN KEY(release_id,recipient_id) REFERENCES messages(release_id,recipient_id));
CREATE TABLE IF NOT EXISTS events (
 seq INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, action TEXT NOT NULL, object_id TEXT NOT NULL, detail TEXT NOT NULL);
CREATE TRIGGER IF NOT EXISTS immutable_event BEFORE UPDATE ON events
 BEGIN SELECT RAISE(ABORT,'immutable audit event'); END;
CREATE TRIGGER IF NOT EXISTS no_event_delete BEFORE DELETE ON events
 BEGIN SELECT RAISE(ABORT,'immutable audit event'); END;
CREATE TABLE IF NOT EXISTS feedback_outbox (
 release_id TEXT NOT NULL, recipient_id TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'PENDING',
 attempts INTEGER NOT NULL DEFAULT 0, last_error TEXT,
 PRIMARY KEY(release_id,recipient_id), FOREIGN KEY(release_id,recipient_id) REFERENCES messages(release_id,recipient_id));
CREATE TABLE IF NOT EXISTS weekly_roots (
 iso_year INTEGER NOT NULL, iso_week INTEGER NOT NULL, recipient_id TEXT NOT NULL,
 message_id TEXT NOT NULL, PRIMARY KEY(iso_year,iso_week,recipient_id));
CREATE TABLE IF NOT EXISTS checkpoints (
 cache_key TEXT PRIMARY KEY, result TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS attempts (
 id TEXT PRIMARY KEY, cache_key TEXT NOT NULL, ordinal INTEGER NOT NULL,
 status TEXT NOT NULL, record TEXT NOT NULL, created_at TEXT NOT NULL);
"""


class Ledger:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path, timeout=15, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.executescript(SCHEMA)
        meta = dict(self.db.execute("SELECT key,value FROM metadata"))
        if meta != {"schema_version": "2", "mode": "shadow"}:
            raise GateError("This build only accepts a V2 shadow ledger")

    def close(self):
        self.db.close()

    @contextmanager
    def transaction(self):
        self.db.execute("BEGIN IMMEDIATE")
        try:
            yield
            self.db.execute("COMMIT")
        except BaseException:
            self.db.execute("ROLLBACK")
            raise

    def event(self, action, object_id, detail):
        self.db.execute("INSERT INTO events(at,action,object_id,detail) VALUES (?,?,?,?)",
                        (now(), action, object_id, canonical(detail)))

    def freeze(self, document):
        if document.get('mode') != 'shadow' or document.get('production_eligible') is not False:
            raise GateError('Only shadow documents can enter this ledger')
        body = canonical(document)
        signature = digest(document)
        day = document["issue_date"]
        with self.transaction():
            last = self.db.execute("SELECT * FROM revisions WHERE issue_date=? ORDER BY revision DESC LIMIT 1", (day,)).fetchone()
            if last and last["document_hash"] == signature:
                return last["id"]
            revision = last["revision"] + 1 if last else 1
            rid = f"rev_{day}_{revision}_{signature[:12]}"
            self.db.execute("UPDATE releases SET status='SUPERSEDED' WHERE issue_date=? AND status='APPROVED'", (day,))
            self.db.execute("INSERT INTO revisions VALUES (?,?,?,?,?,?)", (rid, day, revision, body, signature, now()))
            self.event("preview_frozen", rid, {"previous": last["id"] if last else None})
            return rid

    def revision(self, rid):
        row = self.db.execute("SELECT * FROM revisions WHERE id=?", (rid,)).fetchone()
        if not row:
            raise GateError("Unknown revision")
        data = json.loads(row["document"])
        if digest(data) != row["document_hash"]:
            raise GateError("Revision integrity mismatch")
        return {**dict(row), "document": data}

    def record_review(self, rid, record):
        rev = self.revision(rid)
        if record.get("target_hash") != rev["document_hash"]:
            raise GateError("Review target does not match frozen revision")
        if record.get("kind") not in {"human", "ai"} or record.get("result") not in {"pass", "revise", "needs_human", "unavailable"}:
            raise GateError("Invalid review type or result")
        if not record.get("actor") or not record.get("notes") or not isinstance(record.get("issues"), list):
            raise GateError("Review requires actor, notes and an explicit issue list")
        if record["kind"] == "ai" and not record.get("execution"):
            raise GateError("AI review requires independent call provenance")
        if any(not isinstance(issue, dict) or issue.get('severity') not in {'hard', 'soft'} for issue in record['issues']):
            raise GateError('Review issues require explicit severity')
        review_id = "review_" + uuid4().hex
        with self.transaction():
            self.db.execute("INSERT INTO reviews VALUES (?,?,?,?,?)", (review_id, rid, rev["document_hash"], canonical(record), now()))
            self.event("review_recorded", rid, {"review_id": review_id, "kind": record["kind"], "result": record["result"]})
        return review_id

    def approve(self, rid, actor, *, acknowledge=(), reissue_of=None, reason=None):
        if not actor.strip():
            raise GateError("Approval actor is required")
        with self.transaction():
            rev = self.revision(rid)
            doc = rev["document"]
            latest = self.db.execute("SELECT id FROM revisions WHERE issue_date=? ORDER BY revision DESC LIMIT 1", (rev["issue_date"],)).fetchone()
            if latest["id"] != rid:
                raise GateError("Only the latest revision can be approved")
            from .workbench import check_document
            check = check_document(doc)
            if check != doc["preflight"] or check["errors"]:
                raise GateError("Preflight failed or changed: " + "; ".join(check["errors"]))
            if set(check["warnings"]) - set(acknowledge):
                raise GateError("Unacknowledged preflight warnings")
            review = self.db.execute("SELECT * FROM reviews WHERE revision_id=? ORDER BY rowid DESC LIMIT 1", (rid,)).fetchone()
            if not review:
                raise GateError("A hash-bound editorial review is required")
            record = json.loads(review["record"])
            if record["result"] != "pass" or any(x.get("severity") == "hard" for x in record["issues"]):
                raise GateError("Editorial review has unresolved issues")
            if self.db.execute("SELECT 1 FROM releases WHERE revision_id=?", (rid,)).fetchone():
                raise GateError("Revision already has a release")
            previous = self.db.execute("SELECT DISTINCT r.* FROM releases r JOIN deliveries d ON d.release_id=r.id WHERE r.issue_date=? AND d.attempt_id IS NOT NULL", (rev["issue_date"],)).fetchall()
            if previous:
                if not reissue_of or not reason or reissue_of not in {p["id"] for p in previous}:
                    raise GateError("An earlier release has delivery facts; explicit reissue reference and reason required")
                unresolved = self.db.execute("SELECT 1 FROM deliveries d JOIN releases r ON r.id=d.release_id WHERE r.issue_date=? AND d.status IN ('IN_FLIGHT','UNKNOWN')", (rev["issue_date"],)).fetchone()
                if unresolved:
                    raise GateError("Resolve uncertain deliveries before creating a reissue")
            elif reissue_of:
                raise GateError("Invalid reissue reference")
            if previous:
                self.db.execute("UPDATE releases SET status='SUPERSEDED' WHERE issue_date=? AND status!='SENT'", (rev['issue_date'],))
                self.event('reissue_predecessors_retired', rid, {'predecessors': [p['id'] for p in previous], 'reason': reason})
            release_id = "rel_" + uuid4().hex
            messages = []
            iso = date.fromisoformat(rev["issue_date"]).isocalendar()
            for address in recipients(doc["recipients"]):
                recipient_id = digest(address)
                mid = f"<{release_id}.{recipient_id[:24]}@shadow.ai-daily.invalid>"
                root = self.db.execute("SELECT message_id FROM weekly_roots WHERE iso_year=? AND iso_week=? AND recipient_id=?", (iso.year, iso.week, recipient_id)).fetchone()
                msg = EmailMessage(policy=policy.SMTP)
                msg["From"] = "AI DAILY <preview@ai-daily.invalid>"
                msg["To"] = address
                msg["Subject"] = doc["subject"]
                msg["Date"] = datetime.now(timezone.utc)
                msg["Message-ID"] = mid
                msg["X-AI-Daily-Mode"] = "shadow"
                if root:
                    msg["In-Reply-To"] = root["message_id"]
                    msg["References"] = root["message_id"]
                from bs4 import BeautifulSoup
                msg.set_content(BeautifulSoup(doc["html"], "html.parser").get_text("\n", strip=True))
                msg.add_alternative(doc["html"], subtype="html")
                mime = msg.as_bytes()
                messages.append((release_id, recipient_id, address, mid, mime, digest(mime)))
            manifest = {"schema_version": 2, "mode": "shadow", "release_id": release_id,
                        "revision_id": rid, "revision_hash": rev["document_hash"], "issue_date": rev["issue_date"],
                        "approved_by": actor, "approved_at": now(), "review_id": review["id"],
                        "acknowledged_warnings": sorted(acknowledge), "reissue_of": reissue_of, "reissue_reason": reason,
                        "recipient_snapshot_hash": digest(doc["recipients"]),
                        "messages": [{"recipient_id": m[1], "message_id": m[3], "mime_sha256": m[5]} for m in messages],
                        "feedback": {"mode": "disabled_in_shadow", "source_clicks": "not_collected"}}
            self.db.execute("UPDATE releases SET status='SUPERSEDED' WHERE issue_date=? AND status='APPROVED'", (rev["issue_date"],))
            self.db.execute("INSERT INTO releases VALUES (?,?,?,?,?,?)", (release_id, rid, rev["issue_date"], canonical(manifest), digest(manifest), "APPROVED"))
            self.db.executemany("INSERT INTO messages VALUES (?,?,?,?,?,?)", messages)
            self.db.executemany("INSERT INTO deliveries VALUES (?,?, 'READY',NULL,NULL,?)", [(release_id, m[1], now()) for m in messages])
            self.event("shadow_approved", release_id, {"actor": actor, "revision_id": rid})
            return release_id

    def release(self, release_id):
        row = self.db.execute("SELECT * FROM releases WHERE id=?", (release_id,)).fetchone()
        if not row:
            raise GateError("Unknown release")
        manifest = json.loads(row["manifest"])
        if digest(manifest) != row["manifest_hash"]:
            raise GateError("Manifest integrity mismatch")
        rev = self.revision(row["revision_id"])
        if rev["document_hash"] != manifest["revision_hash"]:
            raise GateError("Release revision mismatch")
        if digest(rev['document']['recipients']) != manifest['recipient_snapshot_hash']:
            raise GateError('Recipient snapshot mismatch')
        actual = self.db.execute("SELECT * FROM messages WHERE release_id=? ORDER BY recipient_id", (release_id,)).fetchall()
        expected = {m["recipient_id"]: m for m in manifest["messages"]}
        if set(expected) != {m["recipient_id"] for m in actual}:
            raise GateError("MIME recipient set mismatch")
        for m in actual:
            if digest(m["mime"]) != expected[m["recipient_id"]]["mime_sha256"] or digest(m["address"]) != m["recipient_id"] or m["message_id"] != expected[m["recipient_id"]]["message_id"]:
                raise GateError("MIME integrity mismatch")
        return {**dict(row), "manifest": manifest}

    def claim(self, release_id):
        with self.transaction():
            release = self.release(release_id)
            if release["status"] not in {"APPROVED", "PARTIAL", "SENDING"}:
                raise GateError("Release cannot be claimed: " + release["status"])
            if self.db.execute("SELECT 1 FROM deliveries WHERE release_id=? AND status IN ('IN_FLIGHT','UNKNOWN')", (release_id,)).fetchone():
                raise GateError("Another worker owns this release or delivery outcome is uncertain")
            row = self.db.execute("SELECT * FROM deliveries WHERE release_id=? AND status IN ('READY','REFUSED') ORDER BY recipient_id LIMIT 1", (release_id,)).fetchone()
            if not row:
                return None
            attempt = uuid4().hex
            self.db.execute("UPDATE deliveries SET status='IN_FLIGHT',attempt_id=?,updated_at=? WHERE release_id=? AND recipient_id=?", (attempt, now(), release_id, row["recipient_id"]))
            self.db.execute("UPDATE releases SET status='SENDING' WHERE id=?", (release_id,))
            self.event("intent_claimed", release_id, {"recipient_id": row["recipient_id"], "attempt_id": attempt})
            return {"release_id": release_id, "recipient_id": row["recipient_id"], "attempt_id": attempt}

    def _outcome(self, release_id, recipient_id, status):
        if status == "ACCEPTED":
            self.db.execute("INSERT OR IGNORE INTO feedback_outbox(release_id,recipient_id) VALUES (?,?)", (release_id, recipient_id))
            row = self.db.execute("SELECT r.issue_date,m.message_id FROM releases r JOIN messages m ON m.release_id=r.id WHERE r.id=? AND m.recipient_id=?", (release_id, recipient_id)).fetchone()
            iso = date.fromisoformat(row["issue_date"]).isocalendar()
            self.db.execute("INSERT OR IGNORE INTO weekly_roots VALUES (?,?,?,?)", (iso.year, iso.week, recipient_id, row["message_id"]))
        statuses = [r[0] for r in self.db.execute("SELECT status FROM deliveries WHERE release_id=?", (release_id,))]
        state = "SEND_UNCERTAIN" if "UNKNOWN" in statuses else "SENDING" if "IN_FLIGHT" in statuses else "SENT" if all(s == "ACCEPTED" for s in statuses) else "PARTIAL"
        self.db.execute("UPDATE releases SET status=? WHERE id=?", (state, release_id))

    def finish(self, claim, status, detail):
        if status not in {"ACCEPTED", "REFUSED", "UNKNOWN"}:
            raise GateError("Invalid delivery outcome")
        with self.transaction():
            row = self.db.execute("SELECT * FROM deliveries WHERE release_id=? AND recipient_id=?", (claim["release_id"], claim["recipient_id"])).fetchone()
            if not row or row["status"] != "IN_FLIGHT" or row["attempt_id"] != claim["attempt_id"]:
                raise GateError("Stale or foreign claim")
            self.db.execute("UPDATE deliveries SET status=?,detail=?,updated_at=? WHERE release_id=? AND recipient_id=?", (status, detail, now(), claim["release_id"], claim["recipient_id"]))
            self._outcome(claim["release_id"], claim["recipient_id"], status)
            self.event("simulated_delivery_" + status.lower(), claim["release_id"], {**claim, "detail": detail})

    def reconcile(self, release_id, recipient_id, outcome, actor, evidence):
        if outcome not in {"ACCEPTED", "REFUSED", "UNKNOWN"} or not actor.strip() or not evidence.strip():
            raise GateError("Reconciliation requires actor, evidence and explicit outcome")
        with self.transaction():
            row = self.db.execute("SELECT status FROM deliveries WHERE release_id=? AND recipient_id=?", (release_id, recipient_id)).fetchone()
            if not row or row["status"] not in {"UNKNOWN", "IN_FLIGHT"}:
                raise GateError("Only uncertain or abandoned claims can be reconciled")
            self.db.execute("UPDATE deliveries SET status=?,detail=?,updated_at=? WHERE release_id=? AND recipient_id=?", (outcome, evidence, now(), release_id, recipient_id))
            self._outcome(release_id, recipient_id, outcome)
            self.event("delivery_reconciled", release_id, {"recipient_id": recipient_id, "actor": actor, "outcome": outcome, "evidence": evidence})

    def summary(self, release_id):
        release = self.release(release_id)
        counts = dict(self.db.execute("SELECT status,count(*) FROM deliveries WHERE release_id=? GROUP BY status", (release_id,)))
        return {"release_id": release_id, "mode": "shadow", "status": release["status"], "counts": counts,
                "real_emails_sent": 0, "feedback_pending": self.db.execute("SELECT count(*) FROM feedback_outbox WHERE release_id=? AND status='PENDING'", (release_id,)).fetchone()[0]}

    def sync_feedback(self, sink):
        """Sink must upsert on (release_id,recipient_id); retry is at-least-once."""
        for row in self.db.execute("SELECT * FROM feedback_outbox WHERE status='PENDING'").fetchall():
            key = (row["release_id"], row["recipient_id"])
            try:
                sink({"release_id": key[0], "recipient_id": key[1], "accepted": True, "mode": "shadow"})
            except Exception as exc:
                self.db.execute("UPDATE feedback_outbox SET attempts=attempts+1,last_error=? WHERE release_id=? AND recipient_id=?", (type(exc).__name__, *key))
            else:
                self.db.execute("UPDATE feedback_outbox SET status='DONE',attempts=attempts+1,last_error=NULL WHERE release_id=? AND recipient_id=?", key)

    def published_identities(self):
        """Shadow-local publication facts only. Never export these as V1 sent facts."""
        result = set()
        for row in self.db.execute("SELECT DISTINCT r.revision_id FROM releases r JOIN deliveries d ON r.id=d.release_id WHERE d.status='ACCEPTED'"):
            doc = self.revision(row[0])["document"]
            for card in doc["content"]["news"] + doc["content"]["deep_features"]:
                result.update(card["source_identities"])
        return result

    def backup(self, destination):
        destination = Path(destination)
        if destination.exists():
            raise GateError("Backup destination already exists")
        target = sqlite3.connect(destination)
        try:
            self.db.backup(target)
            if target.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise GateError("Backup integrity check failed")
        finally:
            target.close()
