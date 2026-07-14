#!/usr/bin/env python3
"""Verify that the research image matches the recorded prompt/image lineage."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from ai_daily_common import (
    JsonObjectError,
    default_imagegen_output_root,
    default_research_image_manifest_path,
    file_sha256,
    is_within_path,
    load_json_object,
    resolve_workspace_path,
    same_workspace_path,
)
from image_provider_registry import (
    ImageProviderError,
    load_provider_registry,
    provider_can_enter_production,
    resolve_provider,
)


class LineageError(Exception):
    pass


default_manifest_path = default_research_image_manifest_path


def research_item_from_manifest(content: dict[str, Any], manifest: dict[str, Any]) -> dict[str, Any]:
    items = content.get("research_items")
    research_index = manifest.get("research_index")
    if isinstance(items, list):
        if isinstance(research_index, int):
            try:
                item = items[research_index]
            except IndexError as exc:
                raise LineageError(f"research_items[{research_index}] is missing.") from exc
            if not isinstance(item, dict):
                raise LineageError(f"research_items[{research_index}] must be an object.")
            return item

        staged_value = str(manifest.get("staged_image") or "")
        paper_image_value = str(manifest.get("paper_image") or "")
        for item in items:
            if not isinstance(item, dict):
                continue
            image_value = str(item.get("image") or "")
            if image_value and (image_value == staged_value or image_value == paper_image_value):
                return item
        raise LineageError("No research item matches manifest image path.")

    paper = content.get("paper")
    if not isinstance(paper, dict):
        raise LineageError("content.paper or content.research_items must be present.")
    return paper


def verify_lineage(
    content: dict[str, Any],
    manifest_path: Path,
    workspace: Path,
    *,
    require_imagegen: bool = True,
    allow_candidate_provider: bool = False,
    provider_registry_path: Path | None = None,
) -> None:
    if not manifest_path.exists():
        raise LineageError(f"Missing research image manifest: {manifest_path}")

    manifest = load_json_object(manifest_path)
    schema_version = manifest.get("schema_version")
    if schema_version not in {1, 2}:
        raise LineageError("research image manifest schema_version must be 1 or 2.")
    if manifest.get("review_status") != "pass":
        raise LineageError("research image manifest review_status must be pass.")

    paper = research_item_from_manifest(content, manifest)

    image_value = paper.get("image")
    if not isinstance(image_value, str) or not image_value.strip():
        raise LineageError("research item image is required.")

    image_path = resolve_workspace_path(image_value, workspace)
    if not image_path or not image_path.exists():
        raise LineageError(f"Missing paper image: {image_path}")

    source_kind = manifest.get("source_kind")
    if schema_version == 1:
        if require_imagegen and source_kind != "imagegen":
            raise LineageError(f"Research image must come from imagegen, got source_kind={source_kind!r}.")
        provider_id = "codex_imagegen" if source_kind == "imagegen" else "manual_provided"
        quality_decision = "approved" if source_kind == "imagegen" else "pending"
        provider = None
    else:
        raw_provider = manifest.get("provider")
        if not isinstance(raw_provider, dict) or not isinstance(raw_provider.get("id"), str):
            raise LineageError("schema v2 image manifest must include provider.id.")
        registry = load_provider_registry(provider_registry_path)
        provider_id, provider = resolve_provider(raw_provider["id"], registry)
        quality_gate = manifest.get("quality_gate")
        if not isinstance(quality_gate, dict):
            raise LineageError("schema v2 image manifest must include quality_gate.")
        quality_decision = quality_gate.get("decision")
        if quality_decision not in {"approved", "pending", "rejected"}:
            raise LineageError("quality_gate.decision must be approved, pending, or rejected.")
        if quality_decision == "rejected":
            raise LineageError("A rejected research image cannot enter an email.")
        if quality_decision == "approved" and provider.get("requires_manual_comparison") and not quality_gate.get("comparison_notes"):
            raise LineageError("Approved candidate image is missing quality_gate.comparison_notes.")
        if require_imagegen and not provider.get("baseline") and not allow_candidate_provider:
            raise LineageError(
                f"Research image provider {provider_id!r} is a candidate. Use the native baseline or pass "
                "--allow-candidate-image-provider for a non-production comparison build."
            )
        if not allow_candidate_provider and not provider_can_enter_production(provider, quality_decision):
            raise LineageError(
                f"Provider {provider_id!r} has not passed the production quality gate. "
                "Candidate images must stay in comparison builds until manually approved."
            )

    staged_value = manifest.get("staged_image")
    paper_image_value = manifest.get("paper_image")
    if not same_workspace_path(str(staged_value or ""), image_value, workspace):
        raise LineageError("content.paper.image does not match manifest.staged_image.")
    if paper_image_value and not same_workspace_path(str(paper_image_value), image_value, workspace):
        raise LineageError("content.paper.image does not match manifest.paper_image.")

    expected_image_hash = manifest.get("staged_image_sha256")
    if not isinstance(expected_image_hash, str) or not expected_image_hash:
        raise LineageError("manifest.staged_image_sha256 is required.")
    actual_image_hash = file_sha256(image_path)
    if actual_image_hash != expected_image_hash:
        raise LineageError("paper image hash does not match manifest.staged_image_sha256.")

    prompt_value = manifest.get("prompt_file")
    prompt_hash = manifest.get("prompt_sha256")
    requires_prompt = source_kind == "imagegen" if schema_version == 1 else bool(provider and provider.get("requires_prompt"))
    requires_codex_root = source_kind == "imagegen" if schema_version == 1 else bool(
        provider and provider.get("requires_codex_output_root")
    )
    if requires_prompt:
        if not prompt_value:
            raise LineageError("Research image manifest must include prompt_file for this provider.")
        prompt_path = resolve_workspace_path(str(prompt_value), workspace)
        if not prompt_path or not prompt_path.exists():
            raise LineageError(f"Missing prompt file: {prompt_path}")
        if file_sha256(prompt_path) != prompt_hash:
            raise LineageError("prompt hash does not match manifest.prompt_sha256.")

    source_value = manifest.get("image_source")
    if not source_value:
        raise LineageError("research image manifest must include image_source.")
    source_path = resolve_workspace_path(str(source_value), workspace)
    if not source_path or not source_path.exists():
        raise LineageError(f"Missing research image source: {source_path}")
    source_hash = manifest.get("image_source_sha256")
    if not isinstance(source_hash, str) or file_sha256(source_path) != source_hash:
        raise LineageError("research image source hash does not match manifest.image_source_sha256.")

    if requires_codex_root:
        imagegen_root = Path(str(manifest.get("imagegen_output_root") or default_imagegen_output_root()))
        if manifest.get("imagegen_source_root_checked") is not True:
            raise LineageError("built-in ImageGen manifest must record imagegen_source_root_checked=true.")
        if not is_within_path(source_path, imagegen_root):
            raise LineageError(f"built-in ImageGen source image is not under {imagegen_root}.")

    if schema_version == 2 and manifest.get("generation_manifest"):
        generation = manifest["generation_manifest"]
        if not isinstance(generation, dict):
            raise LineageError("generation_manifest must be an object.")
        generation_path = resolve_workspace_path(str(generation.get("path") or ""), workspace)
        if not generation_path or not generation_path.exists():
            raise LineageError(f"Missing generation manifest: {generation_path}")
        if file_sha256(generation_path) != generation.get("sha256"):
            raise LineageError("generation manifest hash does not match.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Verify research image lineage manifest.")
    parser.add_argument("--content", required=True, type=Path, help="Daily email content JSON.")
    parser.add_argument("--date", help="Run date YYYY-MM-DD, used for default manifest path.")
    parser.add_argument("--manifest", type=Path, help="Research image manifest path.")
    parser.add_argument("--workspace", type=Path, default=Path.cwd(), help="Workspace root.")
    parser.add_argument(
        "--allow-provided",
        action="store_true",
        help="Allow source_kind=provided instead of requiring imagegen.",
    )
    parser.add_argument(
        "--allow-candidate-image-provider",
        action="store_true",
        help="Permit a non-baseline provider for a comparison build only; it remains blocked from production sends.",
    )
    parser.add_argument("--provider-registry", type=Path, help="Image provider registry path.")
    args = parser.parse_args()

    try:
        if not args.manifest and not args.date:
            raise LineageError("Either --manifest or --date is required.")
        manifest_path = args.manifest or default_manifest_path(args.date or "")
        verify_lineage(
            load_json_object(args.content),
            manifest_path,
            args.workspace,
            require_imagegen=not args.allow_provided,
            allow_candidate_provider=args.allow_candidate_image_provider or args.allow_provided,
            provider_registry_path=args.provider_registry,
        )
    except (OSError, json.JSONDecodeError, JsonObjectError, ImageProviderError, LineageError) as exc:
        raise SystemExit(f"Lineage check failed: {exc}") from exc

    print(f"Valid research image lineage: {manifest_path}")


if __name__ == "__main__":
    main()
