#!/usr/bin/env python3
"""Single-run state and artifact manifest for the AI Daily workflow.

The manifest is deliberately local-file based so it can be used today and
mapped to a task table/object store later. It records only operational
metadata: artifact paths and hashes, delivery-group metadata, and state
transitions. Email addresses, SMTP credentials, and article bodies stay out of
this file.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from ai_daily_common import file_mtime_iso, file_sha256, load_json_object, utc_now_iso, write_json_object


WORKFLOW_RUN_SCHEMA_VERSION = 1
WORKFLOW_RUN_FILENAME = "workflow_run.json"
PHASE_ORDER = {
    "DISCOVERED": 0,
    "SELECTED": 1,
    "CONTENT_READY": 2,
    "IMAGE_APPROVED": 3,
    "HTML_READY": 4,
    "PACKAGED": 5,
    "TESTED": 6,
    "APPROVED_TO_SEND": 7,
    "SENT": 8,
}
EMAIL_RE = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")


class WorkflowStateError(ValueError):
    pass


def default_workflow_state_path(run_dir: Path) -> Path:
    return run_dir / WORKFLOW_RUN_FILENAME


def new_workflow_state(run_date: str) -> dict[str, Any]:
    now = utc_now_iso()
    return {
        "schema_version": WORKFLOW_RUN_SCHEMA_VERSION,
        "run_id": f"ai-daily-{run_date}",
        "run_date": run_date,
        "phase": "DISCOVERED",
        "outcome": "PENDING",
        "created_at": now,
        "updated_at": now,
        "artifacts": {},
        "delivery": {
            "test": {"status": "not_run"},
            "prod": {"status": "not_sent"},
        },
        "last_error": None,
        "events": [
            {
                "at": now,
                "action": "initialize",
                "phase": "DISCOVERED",
                "message": "Run state initialized.",
            }
        ],
    }


def load_workflow_state(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        state = load_json_object(path)
    except Exception as exc:  # Keep caller errors actionable without leaking JSON internals.
        raise WorkflowStateError(f"Invalid workflow state: {path}") from exc
    if state.get("schema_version") != WORKFLOW_RUN_SCHEMA_VERSION:
        raise WorkflowStateError(
            f"Unsupported workflow state schema_version: {state.get('schema_version')!r}."
        )
    if not isinstance(state.get("events"), list) or not isinstance(state.get("artifacts"), dict):
        raise WorkflowStateError(f"Workflow state has invalid events/artifacts fields: {path}")
    if state.get("phase") not in PHASE_ORDER:
        raise WorkflowStateError(f"Workflow state has unknown phase: {state.get('phase')!r}")
    return state


def ensure_workflow_state(path: Path, run_date: str) -> dict[str, Any]:
    state = load_workflow_state(path)
    if state is None:
        state = new_workflow_state(run_date)
        write_json_object(path, state)
        return state
    if state.get("run_date") != run_date:
        raise WorkflowStateError(
            f"Workflow state date mismatch: expected {run_date}, found {state.get('run_date')!r}."
        )
    return state


def workspace_path(path: Path, workspace: Path) -> str:
    try:
        return path.resolve().relative_to(workspace.resolve()).as_posix()
    except (OSError, ValueError):
        return str(path.resolve())


def artifact_descriptor(path: Path, workspace: Path) -> dict[str, Any]:
    descriptor: dict[str, Any] = {
        "path": workspace_path(path, workspace),
        "status": "ready" if path.exists() else "missing",
    }
    if not path.exists():
        return descriptor
    if path.is_dir():
        descriptor["kind"] = "directory"
        descriptor["updated_at"] = file_mtime_iso(path)
        return descriptor
    descriptor.update(
        {
            "kind": "file",
            "bytes": path.stat().st_size,
            "sha256": file_sha256(path),
            "updated_at": file_mtime_iso(path),
        }
    )
    return descriptor


def sanitize_error(value: str, limit: int = 600) -> str:
    value = EMAIL_RE.sub("<redacted-email>", value)
    return value.replace("\n", " ").strip()[:limit]


def _advance_phase(current: str, requested: str) -> str:
    if requested not in PHASE_ORDER:
        raise WorkflowStateError(f"Unknown workflow phase: {requested}")
    if PHASE_ORDER[requested] < PHASE_ORDER[current]:
        return current
    return requested


def record_run_event(
    path: Path,
    run_date: str,
    *,
    action: str,
    phase: str | None = None,
    message: str | None = None,
    artifacts: dict[str, Path] | None = None,
    delivery_channel: str | None = None,
    delivery: dict[str, Any] | None = None,
    workspace: Path | None = None,
) -> dict[str, Any]:
    state = ensure_workflow_state(path, run_date)
    workspace = (workspace or Path.cwd()).resolve()
    now = utc_now_iso()
    effective_phase = state["phase"]
    if phase:
        effective_phase = _advance_phase(state["phase"], phase)
        state["phase"] = effective_phase
        if effective_phase == "SENT":
            state["outcome"] = "SENT"
        elif state.get("outcome") == "FAILED":
            state["outcome"] = "PENDING"
    if artifacts:
        for key, artifact_path in artifacts.items():
            state["artifacts"][key] = artifact_descriptor(artifact_path, workspace)
    if delivery_channel:
        if delivery_channel not in {"test", "prod"}:
            raise WorkflowStateError(f"Unknown delivery channel: {delivery_channel}")
        state["delivery"][delivery_channel] = {
            "updated_at": now,
            **(delivery or {}),
        }
    event: dict[str, Any] = {"at": now, "action": action, "phase": effective_phase}
    if message:
        event["message"] = message
    if artifacts:
        event["artifacts"] = sorted(artifacts)
    if delivery_channel:
        event["delivery_channel"] = delivery_channel
    state["events"].append(event)
    state["updated_at"] = now
    state["last_error"] = None
    write_json_object(path, state)
    return state


def record_run_failure(path: Path, run_date: str, *, action: str, error: Exception | str) -> dict[str, Any]:
    state = ensure_workflow_state(path, run_date)
    now = utc_now_iso()
    message = sanitize_error(str(error))
    # A rejected resend attempt must not rewrite the outcome of an already
    # successful official delivery. The event still preserves the audit trail.
    if state["phase"] != "SENT":
        state["outcome"] = "FAILED"
        state["last_error"] = {"at": now, "action": action, "message": message}
    state["events"].append(
        {
            "at": now,
            "action": action,
            "phase": state["phase"],
            "message": f"Failed: {message}",
        }
    )
    state["updated_at"] = now
    write_json_object(path, state)
    return state


def is_prod_sent(state: dict[str, Any] | None) -> bool:
    if not state:
        return False
    return state.get("phase") == "SENT" or state.get("delivery", {}).get("prod", {}).get("status") == "sent"


def summarize_workflow_state(state: dict[str, Any]) -> list[str]:
    prod = state.get("delivery", {}).get("prod", {})
    test = state.get("delivery", {}).get("test", {})
    return [
        f"run_id: {state['run_id']}",
        f"phase: {state['phase']} | outcome: {state['outcome']}",
        f"artifacts: {len(state.get('artifacts', {}))} | events: {len(state.get('events', []))}",
        f"test delivery: {test.get('status', 'not_run')} | prod delivery: {prod.get('status', 'not_sent')}",
        f"updated_at: {state['updated_at']}",
    ]
