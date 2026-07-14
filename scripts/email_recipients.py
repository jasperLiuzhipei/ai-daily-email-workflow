#!/usr/bin/env python3
"""Shared recipient-list helpers for AI daily email scripts."""

from __future__ import annotations

import json
import re
from email.utils import formataddr, getaddresses, parseaddr
from pathlib import Path


DEFAULT_RECIPIENT_CONFIG = Path(__file__).resolve().parents[1] / "config" / "recipients.json"
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class RecipientError(Exception):
    pass


def normalize_email(email: str) -> str:
    email = email.strip()
    if not EMAIL_RE.match(email):
        raise RecipientError(f"Invalid email address: {email}")
    return email


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


def load_recipient_config(config_path: Path | None = None) -> dict[str, list[dict[str, str]]]:
    path = (config_path or DEFAULT_RECIPIENT_CONFIG).resolve()
    if not path.exists():
        raise RecipientError(f"Missing recipient config: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise RecipientError("Recipient config must be a JSON object.")
    normalized: dict[str, list[dict[str, str]]] = {}
    for list_name, entries in data.items():
        if not isinstance(entries, list):
            raise RecipientError(f"Recipient list must be an array: {list_name}")
        normalized_entries: list[dict[str, str]] = []
        for entry in entries:
            if not isinstance(entry, dict):
                raise RecipientError(f"Recipient entry must be an object in list: {list_name}")
            normalized_entries.append(
                {
                    "name": str(entry.get("name") or "").strip(),
                    "email": normalize_email(str(entry.get("email") or "")),
                }
            )
        normalized[list_name] = normalized_entries
    return normalized


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


def resolve_recipients(
    list_name: str | None,
    manual_values: list[str],
    config_path: Path | None = None,
) -> list[dict[str, str]]:
    recipients: list[dict[str, str]] = []
    if list_name:
        config = load_recipient_config(config_path)
        if list_name not in config:
            choices = ", ".join(sorted(config))
            raise RecipientError(f"Unknown recipient list: {list_name}. Available: {choices}")
        recipients.extend(config[list_name])
    recipients.extend(parse_manual_addresses(manual_values))
    return dedupe_recipients(recipients)


def formatted_addresses(recipients: list[dict[str, str]]) -> list[str]:
    return [format_address(recipient.get("name", ""), recipient["email"]) for recipient in recipients]


def email_only(recipients: list[dict[str, str]]) -> list[str]:
    return [recipient["email"] for recipient in recipients]
