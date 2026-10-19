#!/usr/bin/env python3
"""Securely configure local Vercel/Supabase feedback environment values."""

from __future__ import annotations

import argparse
import getpass
import re
import secrets
import sys
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parents[1]
ENV_PATH = PROJECT_DIR / ".env.local"
WORKFLOW_ENV_PATH = PROJECT_DIR.parent / ".env"
KEY_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$")


def normalize_url(value: str) -> str:
    url = value.strip().rstrip("/")
    if not url.startswith("https://"):
        raise ValueError("Supabase Project URL must start with https://")
    return url


def prompt_secret(label: str, *, generate_if_blank: bool = False) -> tuple[str, bool]:
    value = getpass.getpass(f"{label}: ").strip()
    if value:
        return value, False
    if generate_if_blank:
        return secrets.token_hex(32), True
    raise ValueError(f"{label} cannot be empty.")


def update_env(path: Path, values: dict[str, str]) -> None:
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    remaining = dict(values)
    output: list[str] = []
    for line in lines:
        match = KEY_RE.match(line)
        if match and match.group(1) in remaining:
            key = match.group(1)
            output.append(f"{key}={remaining.pop(key)}")
        else:
            output.append(line)
    if remaining:
        if output and output[-1]:
            output.append("")
        output.append("# AI Daily feedback web runtime")
        output.extend(f"{key}={value}" for key, value in remaining.items())
    path.write_text("\n".join(output).rstrip() + "\n", encoding="utf-8")
    path.chmod(0o600)


def read_env(path: Path) -> dict[str, str]:
    if not path.exists():
        raise ValueError(f"Missing local frontend configuration: {path}")
    result: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        match = KEY_RE.match(line)
        if match:
            result[match.group(1)] = match.group(2)
    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Write feedback_web/.env.local without echoing supplied Secret values."
    )
    parser.add_argument(
        "--project-url",
        help="Supabase Project URL, for example https://xxxx.supabase.co",
    )
    parser.add_argument(
        "--sync-workflow",
        action="store_true",
        help="Copy the shared feedback values into the parent workflow .env after Vercel deployment.",
    )
    parser.add_argument(
        "--feedback-base-url",
        help="Public Vercel feedback URL. Required with --sync-workflow.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.sync_workflow:
        try:
            base_url = normalize_url(str(args.feedback_base_url or ""))
            values = read_env(ENV_PATH)
            signing_secret = values.get("AI_DAILY_FEEDBACK_SECRET", "")
            admin_secret = values.get("AI_DAILY_FEEDBACK_ADMIN_KEY", "")
            if len(signing_secret) < 16 or len(admin_secret) < 16:
                raise ValueError("Local feedback secrets are missing or invalid.")
            update_env(
                WORKFLOW_ENV_PATH,
                {
                    "AI_DAILY_FEEDBACK_BASE_URL": base_url,
                    "AI_DAILY_FEEDBACK_SECRET": signing_secret,
                    "AI_DAILY_FEEDBACK_ADMIN_KEY": admin_secret,
                },
            )
        except ValueError as exc:
            print(f"Workflow sync aborted: {exc}", file=sys.stderr)
            return 2
        print(f"Updated {WORKFLOW_ENV_PATH} with mode 600.")
        print("The email workflow can now create signed links and sync delivery denominators.")
        return 0

    try:
        project_url = normalize_url(str(args.project_url or ""))
        supabase_secret, _ = prompt_secret("Paste Supabase Secret Key (input hidden)")
        if not supabase_secret.startswith("sb_secret_"):
            raise ValueError("Expected a Supabase server Secret Key beginning with sb_secret_.")
        feedback_secret, generated_feedback = prompt_secret(
            "AI_DAILY_FEEDBACK_SECRET (press Enter to generate)", generate_if_blank=True
        )
        admin_secret, generated_admin = prompt_secret(
            "AI_DAILY_FEEDBACK_ADMIN_KEY (press Enter to generate)", generate_if_blank=True
        )
        if len(feedback_secret) < 16 or len(admin_secret) < 16:
            raise ValueError("Feedback secrets must contain at least 16 characters.")
    except ValueError as exc:
        print(f"Configuration aborted: {exc}", file=sys.stderr)
        return 2

    update_env(
        ENV_PATH,
        {
            "SUPABASE_URL": project_url,
            "SUPABASE_SECRET_KEY": supabase_secret,
            "AI_DAILY_FEEDBACK_SECRET": feedback_secret,
            "AI_DAILY_FEEDBACK_ADMIN_KEY": admin_secret,
        },
    )
    print(f"Updated {ENV_PATH} with mode 600.")
    print("The Supabase server key was accepted and is not printed.")
    if generated_feedback or generated_admin:
        print("Copy the generated values below into Vercel Environment Variables now. Do not paste them into chat.")
        if generated_feedback:
            print(f"AI_DAILY_FEEDBACK_SECRET={feedback_secret}")
        if generated_admin:
            print(f"AI_DAILY_FEEDBACK_ADMIN_KEY={admin_secret}")
    else:
        print("Use the same feedback and admin secrets in Vercel Environment Variables.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
