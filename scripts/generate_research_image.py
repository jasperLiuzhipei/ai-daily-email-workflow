#!/usr/bin/env python3
"""Generate a research-image candidate through a configured portable provider.

Codex built-in image generation remains an external tool and is intentionally
not invoked here. This script handles API-backed providers such as gpt-image-2
or a company OpenAI-compatible image gateway.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from image_provider_registry import ImageProviderError, load_provider_registry, provider_snapshot, resolve_provider


class GenerateError(Exception):
    pass


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def load_prompt(path: Path) -> str:
    if not path.exists():
        raise GenerateError(f"Missing prompt file: {path}")
    prompt = path.read_text(encoding="utf-8").strip()
    if not prompt:
        raise GenerateError(f"Prompt file is empty: {path}")
    return prompt


def output_manifest(
    path: Path,
    *,
    provider_id: str,
    provider: dict,
    prompt_file: Path,
    prompt: str,
    output: Path,
    request: dict,
    request_id: str | None,
    response_metadata: dict[str, Any],
) -> None:
    payload = {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "provider": provider_snapshot(provider_id, provider),
        "request_id": request_id,
        "prompt_file": str(prompt_file),
        "prompt_sha256": sha256_text(prompt),
        "request": request,
        "response": response_metadata,
        "output": {
            "path": str(output),
            "sha256": sha256_bytes(output.read_bytes()),
            "bytes": output.stat().st_size,
        },
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def json_value(value: Any) -> Any:
    """Serialize SDK response metadata without persisting image payloads."""

    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (list, tuple)):
        return [json_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): json_value(item) for key, item in value.items()}
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        return json_value(model_dump())
    return str(value)


def generate_openai_image(
    provider_id: str,
    provider: dict,
    prompt: str,
    output: Path,
    *,
    size: str,
    quality: str,
    timeout_seconds: int,
    max_retries: int,
    dry_run: bool,
) -> tuple[dict, str | None, dict[str, Any]]:
    api_key_env = str(provider.get("api_key_env") or "OPENAI_API_KEY")
    base_url_env = str(provider.get("base_url_env") or "OPENAI_BASE_URL")
    request = {
        "model": str(provider.get("model") or "gpt-image-2"),
        "size": size,
        "quality": quality,
        "output_format": "png",
    }
    if dry_run:
        return request, None, {"dry_run": True}

    api_key = os.environ.get(api_key_env)
    if not api_key:
        raise GenerateError(f"Missing {api_key_env}; configure it in the job secret store.")

    try:
        from openai import OpenAI
    except Exception as exc:
        raise GenerateError(f"Missing openai package: {exc}") from exc

    client_kwargs = {
        "api_key": api_key,
        "timeout": timeout_seconds,
        "max_retries": max_retries,
    }
    if base_url := os.environ.get(base_url_env):
        client_kwargs["base_url"] = base_url.rstrip("/")
    client = OpenAI(**client_kwargs)
    started = time.perf_counter()
    try:
        result = client.images.generate(prompt=prompt, n=1, **request)
    except Exception as exc:
        raise GenerateError(f"{provider_id} image request failed: {exc}") from exc
    elapsed_ms = round((time.perf_counter() - started) * 1000)
    if not result.data or not result.data[0].b64_json:
        raise GenerateError(f"{provider_id} did not return a base64 image payload.")

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(base64.b64decode(result.data[0].b64_json))
    return request, getattr(result, "_request_id", None), {
        "elapsed_ms": elapsed_ms,
        "usage": json_value(getattr(result, "usage", None)),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", default="gpt_image_2", help="Provider id or alias from image_providers.json.")
    parser.add_argument("--provider-registry", type=Path)
    parser.add_argument("--prompt-file", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--generation-manifest", required=True, type=Path)
    parser.add_argument("--size", default="auto")
    parser.add_argument("--quality", default="medium")
    parser.add_argument("--timeout-seconds", type=int, default=120, help="Per-request API timeout.")
    parser.add_argument("--max-retries", type=int, default=1, help="OpenAI SDK retry count for transient API errors.")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    try:
        registry = load_provider_registry(args.provider_registry)
        provider_id, provider = resolve_provider(args.provider, registry)
        if provider.get("execution") == "codex_builtin":
            raise GenerateError(
                "codex_imagegen is an external Codex tool. Generate there, then stage the reviewed file with --provider-id codex_imagegen."
            )
        if provider.get("execution") != "openai_images_api":
            raise GenerateError(f"No generator adapter is implemented for provider {provider_id!r}.")
        if args.out.exists() and not args.force:
            raise GenerateError(f"Output already exists: {args.out}. Use --force to overwrite.")
        if args.timeout_seconds <= 0:
            raise GenerateError("--timeout-seconds must be positive.")
        if args.max_retries < 0:
            raise GenerateError("--max-retries must be zero or greater.")

        prompt = load_prompt(args.prompt_file)
        request, request_id, response_metadata = generate_openai_image(
            provider_id,
            provider,
            prompt,
            args.out,
            size=args.size,
            quality=args.quality,
            timeout_seconds=args.timeout_seconds,
            max_retries=args.max_retries,
            dry_run=args.dry_run,
        )
        if args.dry_run:
            print(
                json.dumps(
                    {"provider": provider_id, "request": request, "response": response_metadata, "out": str(args.out)},
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return
        output_manifest(
            args.generation_manifest,
            provider_id=provider_id,
            provider=provider,
            prompt_file=args.prompt_file,
            prompt=prompt,
            output=args.out,
            request=request,
            request_id=request_id,
            response_metadata=response_metadata,
        )
    except (OSError, ImageProviderError, GenerateError) as exc:
        raise SystemExit(f"Image generation failed: {exc}") from exc

    print(f"Generated image: {args.out}")
    print(f"Generation manifest: {args.generation_manifest}")


if __name__ == "__main__":
    main()
