#!/usr/bin/env python3
"""Store an AI Daily SMTP password locally without echoing or printing it."""

from __future__ import annotations

import argparse
import getpass
from pathlib import Path

from configure_ai_runtime import DEFAULT_ENV_FILE, update_env_file


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=DEFAULT_ENV_FILE)
    args = parser.parse_args()

    password = getpass.getpass("SMTP password or authorization code (input hidden): ")
    if not password:
        raise SystemExit("Configuration failed: SMTP password cannot be empty.")
    if "\n" in password or "\r" in password:
        raise SystemExit("Configuration failed: SMTP password cannot contain a newline.")

    update_env_file(
        args.env_file,
        {"AI_DAILY_SMTP_PASSWORD": password},
        section_comment="# AI Daily SMTP credential (do not commit)",
    )
    print(f"SMTP credential saved to {args.env_file} with file mode 600.")
    print("The credential was not printed. Do not commit this file or send its contents in chat.")


if __name__ == "__main__":
    main()
