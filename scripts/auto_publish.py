#!/usr/bin/env python3
"""Publish today's prepared AI Daily only after editorial and visual quality gates.

This is the regular workday counterpart to ``holiday_auto_publish.py``.  It
uses the same deterministic, editorial, visual, image-lineage, and delivery
deduplication protections, but its approved window is exactly the issue date
being processed.  A systemd timer may invoke it on weekdays; leaving that
timer disabled preserves the normal preview-and-human-review workflow.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from work_calendar import DEFAULT_CALENDAR, today_iso, workday_decision
from holiday_auto_publish import AutoPublishBlocked, SCRIPT_DIR, local_today, run_gate


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", help="Issue date YYYY-MM-DD; defaults to the server local date.")
    parser.add_argument("--workspace", type=Path, default=Path.cwd(), help="AI Daily workspace root.")
    parser.add_argument("--runtime-config", type=Path, help="AI runtime config; secrets remain in the environment.")
    parser.add_argument(
        "--image-provider-registry",
        type=Path,
        default=SCRIPT_DIR.parent / "config" / "image_providers.json",
    )
    parser.add_argument("--dry-run", action="store_true", help="Run all gates without approving an image or sending email.")
    parser.add_argument("--work-calendar", type=Path, default=DEFAULT_CALENDAR)
    raw_args = parser.parse_args()
    run_date = raw_args.date or today_iso()
    try:
        workday, reason = workday_decision(run_date, raw_args.work_calendar)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise SystemExit(f"Auto-publish blocked: {exc}") from exc
    if not workday:
        print(f"Skipped {run_date}: {reason}")
        return
    # The window is exactly one date, so a regular timer cannot accidentally
    # turn into an unrestricted historical or future backfill sender.
    args = argparse.Namespace(
        date=run_date,
        workspace=raw_args.workspace,
        runtime_config=raw_args.runtime_config,
        image_provider_registry=raw_args.image_provider_registry,
        window_start=run_date,
        window_end=run_date,
        allow_outside_window=False,
        dry_run=raw_args.dry_run,
        audit_filename="auto_publish_quality_gate.json",
        audit_artifact_key="auto_publish_quality_gate",
        quality_gate_policy="automated_editorial_visual_gate",
        review_note="Automated editorial + visual quality gates passed; per-image audit is recorded.",
        phase_action="auto_quality_gate",
        phase_message="Automated editorial and visual quality gates passed before production send.",
    )
    try:
        audit, has_research = run_gate(args)
        print(f"Auto-publish quality gate passed for {audit['run_date']} ({'research + news' if has_research else 'news only'}).")
        if args.dry_run:
            print("Dry run: no image approval or email send was performed.")
            return
        command = [
            sys.executable,
            str(SCRIPT_DIR / "run_ai_daily.py"),
            "prod",
            "--date",
            str(audit["run_date"]),
            "--work-calendar",
            str(raw_args.work_calendar.resolve()),
            "--skip-build",
            "--series",
            "trial",
            "--send",
            "--confirm-prod",
            "--require-feedback",
        ]
        print("Starting production send only after all automated gates passed.")
        subprocess.run(command, check=True)
    except (AutoPublishBlocked, subprocess.CalledProcessError) as exc:
        raise SystemExit(f"Auto-publish blocked; no email was sent: {exc}") from exc


if __name__ == "__main__":
    main()
