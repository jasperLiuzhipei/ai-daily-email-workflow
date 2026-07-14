#!/usr/bin/env python3
"""Recipient registry and immutable delivery-snapshot helpers.

The local JSON registry is intentionally shaped like a future database-backed
recipient service: named groups have stable IDs, explicit versions, enabled
states, and members. Existing flat ``{"test": [...], "prod": [...]}``
configuration remains supported during migration.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from email.utils import formataddr, getaddresses, parseaddr
from pathlib import Path
from typing import Any


DEFAULT_RECIPIENT_CONFIG = Path(__file__).resolve().parents[1] / "config" / "recipients.json"
RECIPIENT_SNAPSHOT_NAME = "recipient_snapshot.json"
REGISTRY_SCHEMA_VERSION = 1
SNAPSHOT_SCHEMA_VERSION = 1
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class RecipientError(Exception):
    pass


def normalize_email(email: str) -> str:
    email = email.strip()
    if not EMAIL_RE.match(email):
        raise RecipientError(f"Invalid email address: {email}")
    return email


def normalize_member(entry: Any, group_key: str) -> dict[str, str]:
    if not isinstance(entry, dict):
        raise RecipientError(f"Recipient entry must be an object in group: {group_key}")
    member = {
        "name": str(entry.get("name") or "").strip(),
        "email": normalize_email(str(entry.get("email") or "")),
    }
    employee_id = str(entry.get("employee_id") or "").strip()
    if employee_id:
        member["employee_id"] = employee_id
    return member


def dedupe_recipients(recipients: list[dict[str, str]]) -> list[dict[str, str]]:
    seen: set[str] = set()
    deduped: list[dict[str, str]] = []
    for recipient in recipients:
        email = recipient["email"].lower()
        if email in seen:
            continue
        seen.add(email)
        deduped.append(recipient)
    return deduped


def format_address(name: str, email: str) -> str:
    email = normalize_email(email)
    name = name.strip()
    return formataddr((name, email)) if name else email


def parse_manual_addresses(values: list[str]) -> list[dict[str, str]]:
    recipients: list[dict[str, str]] = []
    for value in values:
        for name, email in getaddresses([value]):
            if not email:
                name, email = parseaddr(value)
            if not email:
                raise RecipientError(f"Could not parse recipient: {value}")
            recipients.append({"name": name.strip(), "email": normalize_email(email)})
    return recipients


def legacy_group(key: str, entries: Any) -> dict[str, Any]:
    if not isinstance(entries, list):
        raise RecipientError(f"Recipient list must be an array: {key}")
    return {
        "key": key,
        "id": f"legacy-{key}",
        "name": key,
        "version": 0,
        "enabled": True,
        "source_format": "legacy_flat_json",
        "members": [normalize_member(entry, key) for entry in entries],
    }


def normalize_group(key: str, raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise RecipientError(f"Recipient group must be an object: {key}")
    group_id = str(raw.get("id") or "").strip()
    if not group_id:
        raise RecipientError(f"Recipient group {key} is missing stable id.")
    version = raw.get("version")
    if not isinstance(version, int) or version < 1:
        raise RecipientError(f"Recipient group {key} version must be a positive integer.")
    members = raw.get("members")
    if not isinstance(members, list):
        raise RecipientError(f"Recipient group {key} members must be an array.")
    return {
        "key": key,
        "id": group_id,
        "name": str(raw.get("name") or key).strip(),
        "version": version,
        "enabled": bool(raw.get("enabled", True)),
        "source_format": "registry_v1",
        "members": [normalize_member(entry, key) for entry in members],
    }


def load_recipient_registry(config_path: Path | None = None) -> dict[str, dict[str, Any]]:
    path = (config_path or DEFAULT_RECIPIENT_CONFIG).resolve()
    if not path.exists():
        raise RecipientError(f"Missing recipient config: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RecipientError(f"Invalid recipient config JSON: {path}") from exc
    if not isinstance(data, dict):
        raise RecipientError("Recipient config must be a JSON object.")

    groups = data.get("groups")
    if groups is None:
        return {key: legacy_group(key, entries) for key, entries in data.items()}
    if data.get("schema_version") != REGISTRY_SCHEMA_VERSION:
        raise RecipientError(
            f"Unsupported recipient registry schema_version: {data.get('schema_version')!r}."
        )
    if not isinstance(groups, dict):
        raise RecipientError("Recipient registry groups must be an object.")
    return {key: normalize_group(key, raw) for key, raw in groups.items()}


def load_recipient_config(config_path: Path | None = None) -> dict[str, list[dict[str, str]]]:
    """Compatibility view for callers that only need members by group key."""

    return {key: list(group["members"]) for key, group in load_recipient_registry(config_path).items()}


def delivery_fingerprint(group: dict[str, Any] | None, recipients: list[dict[str, str]]) -> str:
    payload = {
        "recipient_group": {
            key: group[key]
            for key in ("key", "id", "name", "version", "enabled", "source_format")
            if group and key in group
        }
        if group
        else None,
        "recipients": [
            {key: recipient[key] for key in ("employee_id", "name", "email") if key in recipient}
            for recipient in sorted(recipients, key=lambda item: item["email"].lower())
        ],
    }
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def resolve_recipient_delivery(
    list_name: str | None,
    manual_values: list[str],
    config_path: Path | None = None,
) -> dict[str, Any]:
    group: dict[str, Any] | None = None
    recipients: list[dict[str, str]] = []
    if list_name:
        registry = load_recipient_registry(config_path)
        if list_name not in registry:
            choices = ", ".join(sorted(registry))
            raise RecipientError(f"Unknown recipient group: {list_name}. Available: {choices}")
        group = registry[list_name]
        if not group["enabled"]:
            raise RecipientError(f"Recipient group is disabled: {list_name}")
        recipients.extend(group["members"])

    manual_recipients = parse_manual_addresses(manual_values)
    recipients.extend(manual_recipients)
    recipients = dedupe_recipients(recipients)
    group_meta = (
        {key: group[key] for key in ("key", "id", "name", "version", "enabled", "source_format")}
        if group
        else None
    )
    return {
        "recipient_group": group_meta,
        "recipients": recipients,
        "recipient_count": len(recipients),
        "manual_recipient_count": len(manual_recipients),
        "fingerprint": delivery_fingerprint(group_meta, recipients),
    }


def resolve_recipients(
    list_name: str | None,
    manual_values: list[str],
    config_path: Path | None = None,
) -> list[dict[str, str]]:
    return resolve_recipient_delivery(list_name, manual_values, config_path)["recipients"]


def write_recipient_snapshot(path: Path, delivery: dict[str, Any]) -> None:
    snapshot = {
        "schema_version": SNAPSHOT_SCHEMA_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "recipient_group": delivery["recipient_group"],
        "recipients": delivery["recipients"],
        "recipient_count": delivery["recipient_count"],
        "manual_recipient_count": delivery["manual_recipient_count"],
        "fingerprint": delivery["fingerprint"],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def load_recipient_snapshot(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise RecipientError(f"Missing recipient snapshot: {path}")
    try:
        snapshot = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RecipientError(f"Invalid recipient snapshot JSON: {path}") from exc
    if not isinstance(snapshot, dict) or snapshot.get("schema_version") != SNAPSHOT_SCHEMA_VERSION:
        raise RecipientError(f"Unsupported recipient snapshot schema: {path}")
    recipients = snapshot.get("recipients")
    if not isinstance(recipients, list):
        raise RecipientError(f"Recipient snapshot has no recipients array: {path}")
    normalized = dedupe_recipients([normalize_member(item, "snapshot") for item in recipients])
    group = snapshot.get("recipient_group")
    if group is not None and not isinstance(group, dict):
        raise RecipientError(f"Recipient snapshot group must be an object: {path}")
    expected = delivery_fingerprint(group, normalized)
    if snapshot.get("fingerprint") != expected:
        raise RecipientError(f"Recipient snapshot fingerprint mismatch: {path}")
    if snapshot.get("recipient_count") != len(normalized):
        raise RecipientError(f"Recipient snapshot count mismatch: {path}")
    return {
        **snapshot,
        "recipient_group": group,
        "recipients": normalized,
        "recipient_count": len(normalized),
        "fingerprint": expected,
    }


def formatted_addresses(recipients: list[dict[str, str]]) -> list[str]:
    return [format_address(recipient.get("name", ""), recipient["email"]) for recipient in recipients]


def email_only(recipients: list[dict[str, str]]) -> list[str]:
    return [recipient["email"] for recipient in recipients]
