#!/usr/bin/env python3
"""Migrate legacy flat recipient JSON into the v1 recipient-registry schema."""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path


DEFAULT_CONFIG = Path(__file__).resolve().parents[1] / "config" / "recipients.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Migrate legacy AI Daily recipient JSON to registry schema v1.")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG, help="Legacy recipients JSON path.")
    parser.add_argument("--write", action="store_true", help="Write migration in place and preserve a local backup.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    path = args.config.resolve()
    raw_text = path.read_text(encoding="utf-8")
    data = json.loads(raw_text)
    if not isinstance(data, dict):
        raise SystemExit("Migration failed: recipient config must be a JSON object.")
    if "groups" in data:
        raise SystemExit("Migration skipped: recipient config already uses registry schema.")

    groups: dict[str, dict] = {}
    for key, members in data.items():
        if not isinstance(members, list):
            raise SystemExit(f"Migration failed: legacy group {key!r} is not an array.")
        display_name = "AI 日报测试名单" if key == "test" else "AI 日报正式名单" if key == "prod" else key
        groups[key] = {
            "id": f"ai-daily-{key}",
            "name": display_name,
            "version": 1,
            "enabled": True,
            "members": members,
        }

    migrated = {"schema_version": 1, "groups": groups}
    output = json.dumps(migrated, ensure_ascii=False, indent=2) + "\n"
    if not args.write:
        print(output)
        print("Dry-run only. Add --write to migrate in place.")
        return

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = path.with_name(f"{path.stem}.{timestamp}.backup.json")
    backup.write_text(raw_text, encoding="utf-8")
    path.write_text(output, encoding="utf-8")
    print(f"Migrated recipient registry: {path}")
    print(f"Backup: {backup}")


if __name__ == "__main__":
    main()
