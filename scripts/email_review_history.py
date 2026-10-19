"""Immutable review checkpoints; the latest receipt remains a compatibility view."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import uuid

from send_email_package_smtp import write_json_atomic


def write_once(path: Path, value: dict) -> None:
    """Atomically create a private receipt, without replacing previous evidence."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    data = (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode()
    if path.exists():
        if path.read_bytes() != data:
            raise ValueError(f"Refusing to overwrite review evidence: {path}")
        return
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".receipt-", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def history_dir(paths, receipt: dict) -> Path:
    review_id = receipt.get("review_id", "")
    if not isinstance(review_id, str) or not re.fullmatch(r"[0-9a-f]{32}", review_id):
        raise ValueError("Invalid review history ID")
    return paths.run_dir / "email_review_history" / review_id


def start_review(paths, receipt: dict, previous: dict) -> None:
    if previous:
        # Includes legacy receipts and copy-repair diagnostics written to latest.
        digest = hashlib.sha256(json.dumps(previous, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        write_once(paths.run_dir / "email_review_history" / "previous" / f"{digest}.json", previous)
        if previous.get("status") == "reviewing" and previous.get("review_id"):
            interrupted = {**deepcopy(previous), "status": "interrupted",
                "detected_at": datetime.now(timezone.utc).isoformat()}
            dest = history_dir(paths, previous) / "interrupted.json"
            if not dest.exists():
                write_once(dest, interrupted)
    receipt["review_id"] = uuid.uuid4().hex
    receipt["previous_review_id"] = previous.get("review_id")
    receipt["history_path"] = str(history_dir(paths, receipt).relative_to(paths.run_dir))
    active = []
    for scope in ("publish", "prepare", "prod"):
        path = paths.run_dir / f"{scope}_recovery.json"
        if not path.exists():
            continue
        report = json.loads(path.read_text())
        if report.get("status") == "running" and report.get("attempts"):
            attempt = report["attempts"][-1]
            if attempt.get("status") not in {"passed", "failed"}:
                active.append({"scope": scope, "attempt": attempt.get("number"), "started_at": attempt.get("started_at", "")})
    if active:
        receipt["recovery"] = max(active, key=lambda item: item["started_at"])
    checkpoint(paths, receipt, "started")


def checkpoint(paths, receipt: dict, event: str) -> None:
    if not re.fullmatch(r"[a-z0-9_-]+", event):
        raise ValueError("Invalid review event")
    write_once(history_dir(paths, receipt) / f"{event}.json", receipt)
    write_json_atomic(paths.run_dir / "email_self_review.json", receipt)


def save_input(paths, receipt: dict, payload, system_prompt: str, kind: str = "editorial") -> None:
    if kind not in {"editorial", "visual"}:
        raise ValueError("Invalid model input kind")
    request = {"system_prompt": system_prompt, "user_content": payload}
    write_once(history_dir(paths, receipt) / f"{kind}_input.json", request)
    receipt[f"{kind}_input_sha256"] = hashlib.sha256(
        json.dumps(request, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def bind_approval(paths, receipt: dict) -> None:
    if receipt.get("review_id"):
        event = "approval_" + receipt["hashes"]["image_manifest"]
        checkpoint(paths, receipt, event)
    else:
        # Compatibility for a pre-history receipt; preserve its bound view too.
        digest = hashlib.sha256(json.dumps(receipt, sort_keys=True).encode()).hexdigest()
        write_once(paths.run_dir / "email_review_history" / "previous" / f"approval-{digest}.json", receipt)
        write_json_atomic(paths.run_dir / "email_self_review.json", receipt)
