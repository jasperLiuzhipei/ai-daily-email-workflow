#!/usr/bin/env python3
"""V2 shadow workbench. No real-send command, credentials or production mutation."""
from __future__ import annotations

import argparse
from datetime import date, datetime
import json
from pathlib import Path
import sys
from zoneinfo import ZoneInfo

from daily_v2.contracts import GateError
from daily_v2.ledger import Ledger
from daily_v2.workbench import build_document, edit_content, export_preview


def read(path):
    return json.loads(Path(path).read_text()) if path else None


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--db", type=Path, default=Path("output/v2-shadow/ledger.sqlite3"))
    sub = p.add_subparsers(dest="command", required=True)
    preview = sub.add_parser("preview")
    preview.add_argument("--content", type=Path, required=True)
    preview.add_argument("--date", required=True)
    preview.add_argument("--issue", type=int)
    preview.add_argument("--recipients", type=Path, help="JSON list of shadow email addresses")
    preview.add_argument("--materials", type=Path)
    preview.add_argument("--selection", type=Path)
    preview.add_argument("--editorial", type=Path)
    preview.add_argument("--out", type=Path, default=Path("output/v2-shadow/previews"))
    inspect = sub.add_parser("inspect")
    inspect.add_argument("revision_id", nargs='?')
    inspect.add_argument('--date', default=datetime.now(ZoneInfo('Asia/Shanghai')).date().isoformat())
    inspect.add_argument("--out", type=Path, default=Path("output/v2-shadow/previews"))
    review = sub.add_parser("review")
    review.add_argument("revision_id")
    review.add_argument("--record", type=Path, required=True)
    approve = sub.add_parser("approve-shadow")
    approve.add_argument("revision_id")
    approve.add_argument("--actor", required=True)
    approve.add_argument("--acknowledge", action="append", default=[])
    approve.add_argument("--reissue-of")
    approve.add_argument("--reason")
    simulate = sub.add_parser("simulate-delivery")
    simulate.add_argument("release_id")
    simulate.add_argument("--outcome", choices=["ACCEPTED", "REFUSED", "UNKNOWN", "CRASH"], default="ACCEPTED")
    status = sub.add_parser("status")
    status.add_argument("release_id")
    reconcile = sub.add_parser("reconcile")
    reconcile.add_argument("release_id")
    reconcile.add_argument("recipient_id")
    reconcile.add_argument("--outcome", choices=["ACCEPTED", "REFUSED", "UNKNOWN"], required=True)
    reconcile.add_argument("--actor", required=True)
    reconcile.add_argument("--evidence", required=True)
    backup = sub.add_parser("backup")
    backup.add_argument("destination", type=Path)
    edit = sub.add_parser("edit")
    edit.add_argument("--content", type=Path, required=True)
    edit.add_argument("--operations", type=Path, required=True)
    edit.add_argument("--actor", required=True)
    edit.add_argument("--reason", required=True)
    edit.add_argument("--out", type=Path, required=True)
    replay = sub.add_parser("replay")
    replay.add_argument("--run-root", type=Path, default=Path("daily_runs"))
    replay.add_argument("--limit", type=int, default=20)
    replay.add_argument("--out", type=Path, default=Path("output/v2-shadow/replay.json"))
    args = p.parse_args()
    ledger = Ledger(args.db)
    try:
        if args.command == "preview":
            doc = build_document(read(args.content), args.date, read(args.recipients) or ["preview@example.invalid"],
                                 issue=args.issue, materials=read(args.materials), selection=read(args.selection), editorial=read(args.editorial))
            rid = ledger.freeze(doc)
            result = {"revision_id": rid, "preview": str(export_preview(ledger, rid, args.out)), "preflight": doc["preflight"], "mode": "shadow"}
        elif args.command == "inspect":
            if not args.revision_id:
                latest = ledger.db.execute('SELECT id FROM revisions WHERE issue_date=? ORDER BY revision DESC LIMIT 1', (args.date,)).fetchone()
                if not latest:
                    raise GateError('No preview revision for ' + args.date)
                args.revision_id = latest['id']
            rev = ledger.revision(args.revision_id)
            result = {"revision_id": args.revision_id, "document_hash": rev["document_hash"], "preview": str(export_preview(ledger, args.revision_id, args.out))}
        elif args.command == "review":
            result = {"review_id": ledger.record_review(args.revision_id, read(args.record))}
        elif args.command == "approve-shadow":
            result = {"release_id": ledger.approve(args.revision_id, args.actor, acknowledge=args.acknowledge, reissue_of=args.reissue_of, reason=args.reason), "mode": "shadow"}
        elif args.command == "simulate-delivery":
            claim = ledger.claim(args.release_id)
            if claim and args.outcome != "CRASH":
                ledger.finish(claim, args.outcome, "CLI fault rehearsal; no SMTP connection")
            result = {**ledger.summary(args.release_id), "claim": claim}
        elif args.command == "status":
            result = ledger.summary(args.release_id)
        elif args.command == "reconcile":
            ledger.reconcile(args.release_id, args.recipient_id, args.outcome, args.actor, args.evidence)
            result = ledger.summary(args.release_id)
        elif args.command == "backup":
            ledger.backup(args.destination)
            result = {"backup": str(args.destination), "integrity_check": "ok"}
        elif args.command == "edit":
            content, audit = edit_content(read(args.content), read(args.operations), actor=args.actor, reason=args.reason)
            if args.out.exists():
                raise GateError("Edit output already exists; choose a new draft path")
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(json.dumps(content, ensure_ascii=False, indent=2))
            args.out.with_suffix(".audit.json").write_text(json.dumps(audit, ensure_ascii=False, indent=2))
            result = {"draft": str(args.out), "audit": audit}
        else:
            rows = []
            files = sorted(args.run_root.glob("20*/ai_daily_email_content.normalized.json"), reverse=True)
            for path in files:
                day = path.parent.name
                if date.fromisoformat(day).weekday() > 4:
                    continue
                try:
                    doc = build_document(read(path), day, ["replay@example.invalid"], materials=read(path.parent / "source_material.json") if (path.parent / "source_material.json").exists() else None)
                    rid = ledger.freeze(doc)
                    row = {"date": day, "status": "rendered", "revision_id": rid, "preflight": doc["preflight"], "html_sha256": doc["html_sha256"]}
                except (GateError, ValueError, KeyError, TypeError, AttributeError) as exc:
                    row = {"date": day, "status": "blocked", "error": str(exc)}
                rows.append(row)
                if len(rows) >= args.limit:
                    break
            result = {"mode": "shadow", "scope": "historical_content_adapter_and_rendering; no fresh collection, model calls or SMTP",
                      "count": len(rows), "rendered": sum(r["status"] == "rendered" for r in rows), "rows": rows}
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2))
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except (GateError, ValueError, KeyError) as exc:
        print(json.dumps({"error": str(exc), "mode": "shadow"}, ensure_ascii=False), file=sys.stderr)
        return 2
    finally:
        ledger.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
