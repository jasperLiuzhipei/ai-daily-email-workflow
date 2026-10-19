#!/usr/bin/env python3
"""Securely configure the local AI Daily API runtime without echoing a key."""

from __future__ import annotations

import argparse
import getpass
import os
import tempfile
from pathlib import Path


WORKFLOW_DIR = Path(__file__).resolve().parent.parent
DEFAULT_ENV_FILE = WORKFLOW_DIR / ".env"


def update_env_file(
    path: Path,
    updates: dict[str, str],
    *,
    section_comment: str = "# API-only AI Daily runtime",
) -> None:
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    remaining = dict(updates)
    output: list[str] = []
    for line in lines:
        stripped = line.strip()
        candidate = stripped[7:].lstrip() if stripped.startswith("export ") else stripped
        name = candidate.split("=", 1)[0].strip() if "=" in candidate else ""
        if name in remaining:
            output.append(f"{name}={remaining.pop(name)}")
        else:
            output.append(line)
    if remaining:
        if output and output[-1].strip():
            output.append("")
        output.append(section_comment)
        output.extend(f"{name}={value}" for name, value in remaining.items())

    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent, text=True)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write("\n".join(output).rstrip() + "\n")
        os.chmod(temporary, 0o600)
        temporary.replace(path)
    finally:
        if temporary.exists():
            temporary.unlink()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=DEFAULT_ENV_FILE)
    parser.add_argument("--base-url", help="Approved OpenAI-compatible relay base URL ending in /v1.")
    parser.add_argument(
        "--scope",
        choices=("shared", "text", "image", "image-fallback"),
        default="shared",
        help="Store a shared credential, a primary text/image credential, or an image fallback credential.",
    )
    parser.add_argument("--text-model", default="gpt-6-sol", help="Text model alias exposed by the relay.")
    parser.add_argument("--image-model", default="gpt-image-2", help="Image model alias exposed by the relay.")
    parser.add_argument(
        "--model-only",
        action="store_true",
        help="Update only the text or image model alias; does not read or print credentials.",
    )
    args = parser.parse_args()

    if args.model_only:
        if args.scope == "text":
            updates = {"AI_DAILY_TEXT_MODEL": args.text_model}
        elif args.scope == "image":
            updates = {"AI_DAILY_IMAGE_MODEL": args.image_model}
        elif args.scope == "image-fallback":
            updates = {"AI_DAILY_IMAGE_FALLBACK_MODEL": args.image_model}
        else:
            raise SystemExit("Configuration failed: --model-only supports --scope text, image, or image-fallback only.")
        update_env_file(args.env_file, updates)
        print(f"AI Daily model updated: {args.env_file}")
        print(f"Scope: {args.scope}. No API key or base URL was read or printed.")
        return

    base_url = (args.base_url or "").strip().rstrip("/")
    if not base_url.startswith("https://"):
        raise SystemExit("Configuration failed: --base-url must use https://")
    api_key = getpass.getpass("AI Daily relay API Key: ").strip()
    if not api_key:
        raise SystemExit("Configuration failed: API Key cannot be empty.")

    if args.scope == "shared":
        updates = {
            "AI_DAILY_API_KEY": api_key,
            "AI_DAILY_API_BASE_URL": base_url,
            "AI_DAILY_TEXT_MODEL": args.text_model,
            "AI_DAILY_IMAGE_MODEL": args.image_model,
        }
    elif args.scope == "text":
        updates = {
            "AI_DAILY_TEXT_API_KEY": api_key,
            "AI_DAILY_TEXT_BASE_URL": base_url,
            "AI_DAILY_TEXT_MODEL": args.text_model,
        }
    elif args.scope == "image":
        updates = {
            "AI_DAILY_IMAGE_API_KEY": api_key,
            "AI_DAILY_IMAGE_BASE_URL": base_url,
            "AI_DAILY_IMAGE_MODEL": args.image_model,
        }
    else:
        updates = {
            "AI_DAILY_IMAGE_FALLBACK_API_KEY": api_key,
            "AI_DAILY_IMAGE_FALLBACK_BASE_URL": base_url,
            "AI_DAILY_IMAGE_FALLBACK_MODEL": args.image_model,
        }
    update_env_file(args.env_file, updates)
    print(f"AI Daily runtime configured: {args.env_file}")
    print(f"Scope: {args.scope}. API Key stored with file mode 600 and was not printed.")


if __name__ == "__main__":
    main()
