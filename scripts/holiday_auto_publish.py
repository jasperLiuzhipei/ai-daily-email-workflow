#!/usr/bin/env python3
"""Publish a short, explicitly approved holiday window of AI Daily issues.

The existing per-image editorial record remains required. This helper is not
a substitute for it. Within a configured date window it adds two gates:

* deterministic editorial/lineage checks plus a model editorial QA pass;
* a model visual QA pass tied to the staged research image hash.

Any failed, missing, malformed, or inconclusive check exits non-zero before
the SMTP send command is reached.  The resulting JSON audit file preserves
the decision and the reason for a skipped issue.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from ai_daily_common import write_json_object
from image_provider_registry import ImageProviderError
from verify_research_image_lineage import LineageError, verify_lineage
from workflow_context import SCRIPT_DIR, WORKFLOW_DIR, WorkflowError, assert_content_lineage, default_paths, record_phase
# Preserve historical imports while current workflow modules depend directly
# on quality_review. Only date-window handling and dispatch live here.
from quality_review import (
    AutoPublishBlocked,
    require,
    json_object,
    plain_text,
    parse_response_json,
    qa_runtime,
    call_quality_model,
    compact_item,
    compact_source_packet,
    compact_daily_for_qa,
    EDITORIAL_QA_SYSTEM,
    VISUAL_QA_SYSTEM,
    normalized_identity,
    deterministic_editorial_checks,
    editorial_review,
    research_item,
    image_mime,
    visual_review,
    require_image_editorial_record,
    approve_image_manifest,
)


def local_today() -> str:
    return datetime.now().astimezone().date().isoformat()


def parse_date(value: str, *, field: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise AutoPublishBlocked(f"{field} must be YYYY-MM-DD, got {value!r}.") from exc


def write_gate_audit(path: Path, payload: dict[str, Any]) -> None:
    payload["updated_at"] = datetime.now(timezone.utc).isoformat()
    write_json_object(path, payload)


def run_gate(args: argparse.Namespace) -> tuple[dict[str, Any], bool]:
    run_date = args.date or local_today()
    window_start = parse_date(args.window_start, field="window start")
    window_end = parse_date(args.window_end, field="window end")
    requested_date = parse_date(run_date, field="run date")
    require(window_start <= window_end, "Holiday auto-publish window is invalid.")
    if not args.allow_outside_window:
        require(
            window_start <= requested_date <= window_end,
            f"{run_date} is outside the approved holiday auto-publish window {window_start}..{window_end}.",
        )

    workspace = args.workspace.resolve()
    os.chdir(workspace)
    paths = default_paths(run_date)
    audit_path = paths.run_dir / getattr(args, "audit_filename", "holiday_auto_publish_quality_gate.json")
    audit: dict[str, Any] = {
        "schema_version": 1,
        "run_date": run_date,
        "window": {"start": window_start.isoformat(), "end": window_end.isoformat()},
        "status": "running",
        "checks": {},
    }
    try:
        require(paths.content.exists(), f"Missing generated content: {paths.content}.")
        require(paths.normalized_content.exists(), f"Missing normalized content: {paths.normalized_content}.")
        require(paths.html.exists(), f"Missing preview HTML: {paths.html}.")
        workflow = json_object(paths.workflow_state, label="workflow state")
        require(not workflow.get("last_error"), "Workflow state records an unresolved generation error.")
        prod = workflow.get("delivery", {}).get("prod", {}) if isinstance(workflow.get("delivery"), dict) else {}
        if not args.dry_run:
            require(prod.get("status") not in {"sent", "sending"}, "Production delivery has already started for this date.")
        assert_content_lineage(paths)
        audit["checks"]["artifact_lineage"] = "pass"

        content = json_object(paths.content, label="daily content")
        materials = json_object(paths.source_materials, label="source material")
        deterministic_editorial_checks(content)
        audit["checks"]["deterministic_editorial"] = "pass"

        runtime = qa_runtime(args.runtime_config)
        editorial, editorial_metadata = editorial_review(runtime, content=content, materials=materials)
        audit["checks"]["editorial_qa"] = {"result": editorial, "metadata": editorial_metadata}

        paper = research_item(content)
        if paper is not None:
            # First verify all immutable input/output lineage while candidate
            # status is still pending.  Only the explicit QA result below may
            # move it to production approval.
            verify_lineage(
                content,
                paths.image_manifest,
                workspace,
                require_imagegen=False,
                allow_candidate_provider=True,
                provider_registry_path=args.image_provider_registry,
            )
            manifest = json_object(paths.image_manifest, label="research image manifest")
            # Dry runs must enforce the same prerequisite as a real approval.
            require_image_editorial_record(manifest, workspace)
            visual, visual_metadata = visual_review(runtime, workspace=workspace, content=content, manifest=manifest)
            audit["checks"]["visual_qa"] = {"result": visual, "metadata": visual_metadata}
            if not args.dry_run:
                approve_image_manifest(
                    paths.image_manifest,
                    workspace=workspace,
                    editorial=editorial,
                    editorial_metadata=editorial_metadata,
                    visual=visual,
                    visual_metadata=visual_metadata,
                    quality_policy=getattr(args, "quality_gate_policy", "holiday_automated_visual_gate"),
                    review_note=getattr(
                        args,
                        "review_note",
                        "Holiday automated editorial + visual quality gate passed; per-image audit is recorded.",
                    ),
                )
                verify_lineage(
                    content,
                    paths.image_manifest,
                    workspace,
                    require_imagegen=False,
                    allow_candidate_provider=False,
                    provider_registry_path=args.image_provider_registry,
                )
            audit["checks"]["research_image_lineage"] = "pass"
        else:
            audit["checks"]["research_image_lineage"] = "not_applicable_news_only"

        audit["status"] = "passed"
        write_gate_audit(audit_path, audit)
        if not args.dry_run:
            record_phase(
                paths,
                action=getattr(args, "phase_action", "holiday_auto_quality_gate"),
                message=getattr(
                    args,
                    "phase_message",
                    "Holiday automated editorial and visual quality gates passed before production send.",
                ),
                artifacts={getattr(args, "audit_artifact_key", "holiday_auto_publish_quality_gate"): audit_path},
            )
        return audit, paper is not None
    except (AutoPublishBlocked, WorkflowError, LineageError, ImageProviderError, OSError, json.JSONDecodeError) as exc:
        audit["status"] = "blocked"
        audit["reason"] = str(exc)
        write_gate_audit(audit_path, audit)
        raise AutoPublishBlocked(str(exc)) from exc


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", help="Issue date YYYY-MM-DD; defaults to the server local date.")
    parser.add_argument("--workspace", type=Path, default=Path.cwd(), help="AI Daily workspace root.")
    parser.add_argument("--runtime-config", type=Path, help="AI runtime config; secrets remain in the environment.")
    parser.add_argument("--image-provider-registry", type=Path, default=WORKFLOW_DIR / "config" / "image_providers.json")
    parser.add_argument("--window-start", default="2026-08-05", help="Inclusive YYYY-MM-DD holiday auto-publish start.")
    parser.add_argument("--window-end", default="2026-08-07", help="Inclusive YYYY-MM-DD holiday auto-publish end.")
    parser.add_argument("--allow-outside-window", action="store_true", help="Diagnostic only; never use in the systemd timer.")
    parser.add_argument("--dry-run", action="store_true", help="Run gates without approving the image or sending email.")
    args = parser.parse_args()

    try:
        audit, has_research = run_gate(args)
        print(f"Holiday auto quality gate passed for {audit['run_date']} ({'research + news' if has_research else 'news only'}).")
        if args.dry_run:
            print("Dry run: no image approval or email send was performed.")
            return
        command = [
            sys.executable,
            str(SCRIPT_DIR / "run_ai_daily.py"),
            "prod",
            "--date",
            str(audit["run_date"]),
            "--skip-build",
            "--series",
            "trial",
            "--send",
            "--confirm-prod",
            "--require-feedback",
        ]
        print("Starting production send only after all holiday gates passed.")
        subprocess.run(command, check=True)
    except (AutoPublishBlocked, subprocess.CalledProcessError) as exc:
        raise SystemExit(f"Holiday auto-publish blocked; no email was sent: {exc}") from exc


if __name__ == "__main__":
    main()
