#!/usr/bin/env python3
"""Generate a research-image candidate through a configured portable provider.

Codex built-in image generation remains an external tool and is intentionally
not invoked here. This script handles API-backed providers such as gpt-image-2
or a company OpenAI-compatible image gateway.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import struct
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ai_gateway_client import (
    GatewayError,
    generate_image_bytes,
    load_runtime_config,
    provider_snapshot as runtime_provider_snapshot,
    resolve_provider as resolve_runtime_provider,
)
from image_provider_registry import ImageProviderError, load_provider_registry, provider_snapshot, resolve_provider


class GenerateError(Exception):
    pass


def inspect_image_payload(raw: bytes, *, expected_format: str | None) -> dict[str, Any]:
    """Reject gateway error pages and record basic image properties.

    The production default is PNG, so dimensions can be verified without a
    Pillow dependency. JPEG/WebP payloads still receive strict magic-byte
    checks; their dimensions are left for the later staging validator.
    """

    image_format = ""
    width: int | None = None
    height: int | None = None
    if raw.startswith(b"\x89PNG\r\n\x1a\n") and len(raw) >= 24:
        image_format = "png"
        width, height = struct.unpack(">II", raw[16:24])
    elif raw.startswith(b"\xff\xd8\xff"):
        image_format = "jpeg"
    elif len(raw) >= 12 and raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        image_format = "webp"
    else:
        raise GenerateError("Image gateway returned bytes that are not PNG, JPEG, or WebP.")
    expected = (expected_format or "").strip().lower().replace("jpg", "jpeg")
    if expected and expected != image_format:
        raise GenerateError(f"Image gateway returned {image_format}, expected {expected}.")
    if width is not None and height is not None and (width < 512 or height < 512):
        raise GenerateError(f"Image gateway returned an unexpectedly small image: {width}x{height}.")
    return {"format": image_format, "width": width, "height": height, "bytes": len(raw)}


def validate_requested_aspect(image_info: dict[str, Any], requested_size: str | None) -> None:
    """Ensure the gateway honored the requested orientation and aspect ratio.

    Providers may return a different pixel density within a billing tier, so
    exact dimensions are not required. A material aspect-ratio change is a
    hard failure because it breaks the email composition.
    """

    if not requested_size:
        return
    match = re.fullmatch(r"(\d+)x(\d+)", requested_size.strip().lower())
    width = image_info.get("width")
    height = image_info.get("height")
    if not match or not isinstance(width, int) or not isinstance(height, int) or height <= 0:
        return
    requested_ratio = int(match.group(1)) / int(match.group(2))
    actual_ratio = width / height
    if abs(actual_ratio / requested_ratio - 1) > 0.03:
        raise GenerateError(
            f"Image gateway returned aspect ratio {width}x{height}, expected approximately {requested_size}."
        )


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
    image_info: dict[str, Any],
    fallback_allowed: bool,
) -> None:
    payload = {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "provider": provider_snapshot(provider_id, provider),
        "request_id": request_id,
        "prompt_file": str(prompt_file),
        "prompt_sha256": sha256_text(prompt),
        "request": request,
        "route_policy": {
            "fallback_allowed": fallback_allowed,
            "fallback_used": bool(response_metadata.get("fallback", {}).get("used")),
        },
        "response": response_metadata,
        "output": {
            "path": str(output),
            "sha256": sha256_bytes(output.read_bytes()),
            **image_info,
        },
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def json_value(value: Any) -> Any:
    """Serialize gateway metadata without persisting image payloads."""

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


def generate_api_image(
    provider_id: str,
    provider: dict,
    prompt: str,
    output: Path,
    *,
    size: str | None,
    quality: str | None,
    runtime_config_path: Path | None,
    fallback_runtime_config_path: Path | None,
    dry_run: bool,
    allow_fallback: bool = True,
) -> tuple[dict, str | None, dict[str, Any], dict[str, Any] | None]:
    configured_model = str(provider.get("model") or "gpt-image-2")
    fallback_models = {
        str(model).strip()
        for model in provider.get("fallback_models", [])
        if str(model).strip()
    }
    request_overrides: dict[str, Any] = {}
    if size:
        request_overrides["size"] = size
    if quality:
        request_overrides["quality"] = quality
    if provider.get("response_format"):
        request_overrides["response_format"] = provider["response_format"]
    def generate_from_runtime(
        config_path: Path | None,
        *,
        allow_fallback_model: bool = False,
    ) -> tuple[bytes, dict[str, Any], dict[str, Any]]:
        runtime = resolve_runtime_provider("image", load_runtime_config(config_path))
        allowed_models = {configured_model}
        if allow_fallback_model:
            allowed_models.update(fallback_models)
        if runtime.model not in allowed_models:
            raise GenerateError(
                f"Image runtime model {runtime.model!r} is not compatible with image provider "
                f"{provider_id!r}; allowed models: {sorted(allowed_models)!r}."
            )
        request = {"model": runtime.model, **runtime.request_defaults, **request_overrides}
        raw, request, response_metadata = generate_image_bytes(
            runtime,
            prompt=prompt,
            request_overrides=request_overrides,
        )
        response_metadata = {
            **response_metadata,
            "runtime": runtime_provider_snapshot(runtime),
        }
        return raw, request, response_metadata

    primary_runtime = resolve_runtime_provider("image", load_runtime_config(runtime_config_path))
    if primary_runtime.model != configured_model:
        raise GenerateError(
            f"Image runtime model {primary_runtime.model!r} does not match image provider registry model {configured_model!r}."
        )
    request = {"model": primary_runtime.model, **primary_runtime.request_defaults, **request_overrides}
    if dry_run:
        return request, None, {"dry_run": True, "provider": primary_runtime.model}, None

    try:
        raw, request, response_metadata = generate_from_runtime(runtime_config_path)
    except GatewayError as primary_error:
        if not allow_fallback or fallback_runtime_config_path is None:
            raise GenerateError(f"{provider_id} image request failed: {primary_error}") from primary_error
        try:
            raw, request, response_metadata = generate_from_runtime(
                fallback_runtime_config_path,
                allow_fallback_model=True,
            )
        except (GatewayError, GenerateError) as fallback_error:
            raise GenerateError(
                f"{provider_id} primary image request failed: {primary_error}; fallback also failed: {fallback_error}"
            ) from fallback_error
        response_metadata["fallback"] = {
            "used": True,
            "primary_error": str(primary_error)[:400],
        }
    image_info = inspect_image_payload(raw, expected_format=str(request.get("output_format") or ""))
    validate_requested_aspect(image_info, str(request.get("size") or ""))
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(raw)
    return request, response_metadata.get("request_id"), json_value(response_metadata), image_info


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--provider",
        help="Provider id or alias from image_providers.json. Defaults to the registry default provider.",
    )
    parser.add_argument("--provider-registry", type=Path)
    parser.add_argument(
        "--runtime-config",
        type=Path,
        help="AI runtime config. Defaults to config/ai_runtime.json; secrets remain in environment variables.",
    )
    parser.add_argument(
        "--fallback-runtime-config",
        type=Path,
        help="Optional image-only runtime config used after a primary gateway request failure.",
    )
    parser.add_argument(
        "--forbid-fallback",
        action="store_true",
        help=(
            "Require the configured primary Image2 route. A primary failure stops generation instead of "
            "silently using an alternate gateway."
        ),
    )
    parser.add_argument("--prompt-file", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--generation-manifest", required=True, type=Path)
    parser.add_argument("--size", help="Override image runtime default size.")
    parser.add_argument("--quality", help="Override image runtime default quality.")
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
        prompt = load_prompt(args.prompt_file)
        request, request_id, response_metadata, image_info = generate_api_image(
            provider_id,
            provider,
            prompt,
            args.out,
            size=args.size,
            quality=args.quality,
            runtime_config_path=args.runtime_config,
            fallback_runtime_config_path=args.fallback_runtime_config,
            allow_fallback=not args.forbid_fallback,
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
        if image_info is None:
            raise GenerateError("Image metadata validation did not run.")
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
            image_info=image_info,
            fallback_allowed=not args.forbid_fallback,
        )
    except (OSError, ImageProviderError, GatewayError, GenerateError) as exc:
        raise SystemExit(f"Image generation failed: {exc}") from exc

    print(f"Generated image: {args.out}")
    print(f"Generation manifest: {args.generation_manifest}")


if __name__ == "__main__":
    main()
