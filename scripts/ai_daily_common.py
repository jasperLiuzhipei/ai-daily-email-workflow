#!/usr/bin/env python3
"""Shared helpers for the AI daily email workflow scripts."""

from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


NEWS_FORBIDDEN_IMAGE_KEYS = ("image", "image_alt", "image_prompt", "visual_handoff", "visual_strategy")


class JsonObjectError(ValueError):
    pass


def load_json_object(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise JsonObjectError(f"{path} must contain a JSON object.")
    return data


def write_json_object(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def slugify(value: str, fallback: str = "research-cover") -> str:
    value = value.strip().lower()
    value = re.sub(r"[\u2018\u2019\u201c\u201d'\"`]", "", value)
    value = re.sub(r"[^a-z0-9]+", "-", value)
    value = re.sub(r"-{2,}", "-", value).strip("-")
    return value or fallback


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def file_mtime_iso(path: Path) -> str:
    return datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat()


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def default_research_image_manifest_path(run_date: str) -> Path:
    return Path("daily_runs") / run_date / "research_image_manifest.json"


def codex_home() -> Path:
    return Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))


def default_imagegen_output_root() -> Path:
    return codex_home() / "generated_images"


def is_external_asset(value: str) -> bool:
    return value.startswith(("http://", "https://", "cid:"))


def resolve_workspace_path(value: str | Path | None, workspace: Path) -> Path | None:
    if not value:
        return None
    path = Path(value)
    if path.is_absolute():
        return path
    return workspace / path


def same_workspace_path(a: str | Path | None, b: str | Path | None, workspace: Path) -> bool:
    pa = resolve_workspace_path(a, workspace)
    pb = resolve_workspace_path(b, workspace)
    if not pa or not pb:
        return False
    try:
        return pa.resolve() == pb.resolve()
    except OSError:
        return pa == pb


def is_within_path(path: str | Path, parent: str | Path) -> bool:
    try:
        Path(path).resolve().relative_to(Path(parent).resolve())
    except (OSError, ValueError):
        return False
    return True


def relative_to_output(path: str | Path, workspace: Path, output: Path) -> str:
    source = resolve_workspace_path(path, workspace)
    target = resolve_workspace_path(output, workspace)
    if not source or not target:
        return Path(path).as_posix()

    try:
        relative = os.path.relpath(source.resolve(), target.resolve().parent)
    except OSError:
        relative = os.path.relpath(source, target.parent)
    return Path(relative).as_posix()
