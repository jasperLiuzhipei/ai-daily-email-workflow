#!/usr/bin/env python3
"""Validate the API-only AI Daily runtime without exposing credentials."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from ai_gateway_client import GatewayError, load_runtime_config, provider_snapshot, resolve_provider


class RuntimeCheckError(RuntimeError):
    pass


def model_ids(payload: dict[str, Any]) -> set[str]:
    data = payload.get("data")
    if not isinstance(data, list):
        return set()
    return {
        str(item.get("id"))
        for item in data
        if isinstance(item, dict) and isinstance(item.get("id"), str) and item.get("id")
    }


def fetch_models(base_url: str, api_key: str, timeout_seconds: int) -> set[str]:
    request = Request(
        f"{base_url.rstrip('/')}/models",
        headers={"Authorization": f"Bearer {api_key}", "User-Agent": "ai-daily-workflow/1.0"},
    )
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        raise RuntimeCheckError(f"model discovery returned HTTP {exc.code}") from exc
    except (URLError, OSError) as exc:
        raise RuntimeCheckError(f"model discovery failed: {exc}") from exc
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeCheckError("model discovery returned invalid JSON") from exc
    if not isinstance(payload, dict):
        raise RuntimeCheckError("model discovery response must be an object")
    return model_ids(payload)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-config", type=Path)
    parser.add_argument(
        "--live-models",
        action="store_true",
        help="Call GET /models on each configured relay. Default only validates local config and environment.",
    )
    parser.add_argument(
        "--kind",
        choices=("all", "text", "image"),
        default="all",
        help="Validate both providers or isolate one provider during gateway diagnosis.",
    )
    args = parser.parse_args()

    try:
        config = load_runtime_config(args.runtime_config)
        kinds = ("text", "image") if args.kind == "all" else (args.kind,)
        providers = {kind: resolve_provider(kind, config) for kind in kinds}
        if "image" in providers:
            image_defaults = providers["image"].request_defaults
            response_format = image_defaults.get("response_format")
            if response_format not in {"b64_json", "url"}:
                raise RuntimeCheckError(
                    "image.request_defaults.response_format must be b64_json or url for local staging"
                )
            if response_format == "url" and not providers["image"].image_url_allowed_host_suffixes:
                raise RuntimeCheckError(
                    "image response_format=url requires an image.response_url.allowed_host_suffixes allowlist"
                )
            supported_image_models = {"gpt-image-2", "gpt-image-2-vip"}
            if providers["image"].model not in supported_image_models:
                raise RuntimeCheckError(
                    "image model must be one of "
                    f"{sorted(supported_image_models)!r}, got {providers['image'].model!r}"
                )

        print("AI Daily API runtime: local config valid")
        for kind, provider in providers.items():
            snapshot = provider_snapshot(provider)
            print(
                f"- {kind}: model={snapshot['model']} endpoint={snapshot['base_url']}/{snapshot['endpoint_path']} "
                f"timeout={snapshot['timeout_seconds']}s"
            )
        if args.live_models:
            discovered: dict[str, set[str]] = {}
            for provider in providers.values():
                if provider.base_url not in discovered:
                    discovered[provider.base_url] = fetch_models(
                        provider.base_url,
                        provider.api_key,
                        min(provider.timeout_seconds, 30),
                    )
                ids = discovered[provider.base_url]
                if ids and provider.model not in ids:
                    raise RuntimeCheckError(
                        f"{provider.model!r} is not present in GET /models for {provider.base_url}"
                    )
            print("Live model discovery: passed")
    except (GatewayError, RuntimeCheckError) as exc:
        raise SystemExit(f"AI runtime check failed: {exc}") from exc


if __name__ == "__main__":
    main()
