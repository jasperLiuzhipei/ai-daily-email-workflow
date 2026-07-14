#!/usr/bin/env python3
"""Provider registry and policy checks for AI Daily research images."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any


class ImageProviderError(Exception):
    pass


SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_REGISTRY_PATH = SCRIPT_DIR.parent / "config" / "image_providers.json"


def load_provider_registry(path: Path | None = None) -> dict[str, Any]:
    registry_path = path or DEFAULT_REGISTRY_PATH
    try:
        data = json.loads(registry_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ImageProviderError(f"Unable to load image provider registry: {registry_path}") from exc

    if data.get("schema_version") != 1:
        raise ImageProviderError("image provider registry schema_version must be 1.")
    if not isinstance(data.get("policy"), dict) or not isinstance(data.get("providers"), dict):
        raise ImageProviderError("image provider registry must contain policy and providers objects.")
    return data


def resolve_provider(
    provider_id: str | None,
    registry: dict[str, Any],
) -> tuple[str, dict[str, Any]]:
    providers = registry.get("providers")
    if not isinstance(providers, dict):
        raise ImageProviderError("image provider registry providers must be an object.")

    requested = (provider_id or registry["policy"].get("default_provider") or "").strip().lower()
    for canonical_id, raw_provider in providers.items():
        if not isinstance(raw_provider, dict):
            continue
        aliases = [canonical_id, *raw_provider.get("aliases", [])]
        if requested in {str(alias).strip().lower() for alias in aliases}:
            provider = deepcopy(raw_provider)
            if provider.get("enabled") is not True:
                raise ImageProviderError(f"Image provider {canonical_id!r} is disabled.")
            return canonical_id, provider
    raise ImageProviderError(f"Unknown image provider: {provider_id!r}.")


def default_source_kind(provider: dict[str, Any]) -> str:
    if provider.get("execution") == "codex_builtin":
        return "imagegen"
    if provider.get("execution") == "manual":
        return "provided"
    return "generated"


def provider_snapshot(provider_id: str, provider: dict[str, Any]) -> dict[str, Any]:
    keys = [
        "execution",
        "model",
        "baseline",
        "production_allowed",
        "requires_prompt",
        "requires_codex_output_root",
        "requires_manual_comparison",
    ]
    return {"id": provider_id, **{key: provider.get(key) for key in keys}}


def default_quality_decision(provider: dict[str, Any]) -> str:
    return "approved" if provider.get("baseline") is True else "pending"


def provider_can_enter_production(
    provider: dict[str, Any],
    quality_decision: str,
) -> bool:
    if provider.get("baseline") is True:
        return quality_decision == "approved"
    return provider.get("production_allowed") is True and quality_decision == "approved"
