#!/usr/bin/env python3
"""Stage a generated research image and patch daily email content JSON."""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

from ai_daily_common import (
    JsonObjectError,
    default_imagegen_output_root,
    default_research_image_manifest_path,
    file_mtime_iso,
    file_sha256,
    is_within_path,
    load_json_object,
    slugify,
    utc_now_iso,
    write_json_object,
)
from image_provider_registry import (
    ImageProviderError,
    default_quality_decision,
    default_source_kind,
    load_provider_registry,
    provider_snapshot,
    resolve_provider,
)

class StageError(Exception):
    pass


def legacy_provider_for_source_kind(source_kind: str) -> str:
    """Keep existing commands valid while recording the new provider identity."""
    return "codex_imagegen" if source_kind == "imagegen" else "manual_provided"


def load_generation_manifest(path: Path | None) -> dict | None:
    if not path:
        return None
    if not path.exists():
        raise StageError(f"Missing generation manifest: {path}")
    data = load_json_object(path)
    if data.get("schema_version") != 1:
        raise StageError("generation manifest schema_version must be 1.")
    return data


def load_brief_defaults(path: Path | None) -> tuple[str | None, str | None]:
    if not path:
        return None, None
    data = load_json_object(path)
    slug = data.get("image_asset_slug")
    paper = data.get("paper") if isinstance(data.get("paper"), dict) else {}
    alt = paper.get("image_alt") if isinstance(paper.get("image_alt"), str) else None
    return slug if isinstance(slug, str) else None, alt


def target_research_item(data: dict, research_index: int | None) -> dict:
    if research_index is not None:
        items = data.get("research_items")
        if not isinstance(items, list):
            raise StageError("--research-index requires content.research_items.")
        try:
            item = items[research_index]
        except IndexError as exc:
            raise StageError(f"content.research_items[{research_index}] is missing.") from exc
        if not isinstance(item, dict):
            raise StageError(f"content.research_items[{research_index}] must be an object.")
        return item

    paper = data.get("paper")
    if not isinstance(paper, dict):
        raise StageError("content.paper must be an object, or pass --research-index for content.research_items.")
    return paper


def maybe_compress_image(src: Path, dst: Path, max_bytes: int) -> Path:
    if max_bytes <= 0:
        shutil.copy2(src, dst)
        return dst

    try:
        from PIL import Image
    except Exception:
        shutil.copy2(src, dst)
        return dst

    with Image.open(src) as img:
        img.load()
        if max(img.size) > 1800:
            ratio = 1800 / max(img.size)
            new_size = (int(img.size[0] * ratio), int(img.size[1] * ratio))
            img = img.resize(new_size, Image.Resampling.LANCZOS)

        has_alpha = img.mode in ("RGBA", "LA") or ("transparency" in img.info)
        if dst.suffix.lower() == ".png" or has_alpha:
            img.save(dst, optimize=True)
            if dst.stat().st_size <= max_bytes or has_alpha:
                return dst

        rgb = img.convert("RGB")
        jpg_dst = dst.with_suffix(".jpg")
        for quality in [92, 86, 80, 74, 68]:
            rgb.save(jpg_dst, format="JPEG", quality=quality, optimize=True, progressive=True)
            if jpg_dst.stat().st_size <= max_bytes:
                if dst.exists() and dst != jpg_dst:
                    dst.unlink()
                return jpg_dst

        rgb.save(jpg_dst, format="JPEG", quality=62, optimize=True, progressive=True)
        if dst.exists() and dst != jpg_dst:
            dst.unlink()
        return jpg_dst


def main() -> None:
    parser = argparse.ArgumentParser(description="Copy a generated research image into assets and patch content JSON.")
    parser.add_argument("--content", required=True, type=Path, help="Daily email content JSON to patch.")
    parser.add_argument("--image-source", required=True, type=Path, help="Generated image path.")
    parser.add_argument("--date", required=True, help="Run date, YYYY-MM-DD.")
    parser.add_argument("--brief-json", type=Path, help="Parsed brief JSON from parse_ai_frontier_brief.py.")
    parser.add_argument("--prompt-file", type=Path, help="research_image_prompt.txt used to generate the image.")
    parser.add_argument(
        "--imagegen-start-marker",
        type=Path,
        help="Marker file created immediately before calling built-in image_gen.",
    )
    parser.add_argument(
        "--source-kind",
        choices=["imagegen", "generated", "provided"],
        help="Image provenance. Defaults to the selected provider's expected source kind.",
    )
    parser.add_argument(
        "--provider-id",
        help="Provider id or alias from config/image_providers.json. Defaults from --source-kind for legacy commands.",
    )
    parser.add_argument("--provider-registry", type=Path, help="Image provider registry path.")
    parser.add_argument(
        "--generation-manifest",
        type=Path,
        help="Manifest emitted by generate_research_image.py for API-generated candidates.",
    )
    parser.add_argument(
        "--quality-decision",
        choices=["approved", "pending", "rejected"],
        help="Baseline images default to approved; all candidates default to pending.",
    )
    parser.add_argument(
        "--comparison-notes",
        help="Required when manually approving a non-baseline provider against the native ImageGen baseline.",
    )
    parser.add_argument("--manifest", type=Path, help="Output image lineage manifest path.")
    parser.add_argument(
        "--imagegen-output-root",
        type=Path,
        help="Allowed root for built-in imagegen source files. Defaults to $CODEX_HOME/generated_images.",
    )
    parser.add_argument(
        "--allow-non-imagegen-source-root",
        action="store_true",
        help="Allow --source-kind imagegen with an image source outside the imagegen output root.",
    )
    parser.add_argument(
        "--allow-image-before-prompt",
        action="store_true",
        help="Allow source images older than --prompt-file. Use only for explicit provided-image fallback.",
    )
    parser.add_argument(
        "--review-status",
        choices=["pass", "needs-review"],
        default="pass",
        help="Manual/Codex visual review status before staging the image.",
    )
    parser.add_argument("--review-notes", help="Short notes about visual review, rejection, or fallback context.")
    parser.add_argument("--asset-slug", help="Override staged image slug.")
    parser.add_argument("--alt", help="Override image alt text.")
    parser.add_argument("--research-index", type=int, help="Patch content.research_items[index] instead of content.paper.")
    parser.add_argument("--out-content", type=Path, help="Patched content JSON output. Defaults to overwrite --content.")
    parser.add_argument("--assets-root", type=Path, default=Path("assets") / "email", help="Asset root directory.")
    parser.add_argument("--max-bytes", type=int, default=1_000_000, help="Target max image size. Use 0 to disable.")
    args = parser.parse_args()

    try:
        registry = load_provider_registry(args.provider_registry)
        requested_provider = args.provider_id or legacy_provider_for_source_kind(args.source_kind or "imagegen")
        provider_id, provider = resolve_provider(requested_provider, registry)
        source_kind = args.source_kind or default_source_kind(provider)
        expected_source_kind = default_source_kind(provider)
        if source_kind != expected_source_kind:
            raise StageError(
                f"source_kind={source_kind!r} does not match provider {provider_id!r} "
                f"(expected {expected_source_kind!r})."
            )
        quality_decision = args.quality_decision or default_quality_decision(provider)
        generation_manifest = load_generation_manifest(args.generation_manifest)
        if provider.get("execution") == "openai_images_api" and not generation_manifest:
            raise StageError(f"--generation-manifest is required for API provider {provider_id}.")
        if provider.get("requires_manual_comparison") and quality_decision == "approved" and not args.comparison_notes:
            raise StageError(
                "A non-baseline provider needs --comparison-notes before it can be marked approved. "
                "Record the manual comparison against the native ImageGen baseline."
            )
        if not args.image_source.exists():
            raise StageError(f"Missing image source: {args.image_source}")
        if generation_manifest:
            generated_provider = generation_manifest.get("provider")
            output = generation_manifest.get("output")
            if not isinstance(generated_provider, dict) or generated_provider.get("id") != provider_id:
                raise StageError("generation manifest provider does not match --provider-id.")
            if not isinstance(output, dict) or output.get("sha256") != file_sha256(args.image_source):
                raise StageError("generation manifest output hash does not match --image-source.")
        if provider.get("requires_prompt") and not args.prompt_file:
            raise StageError(f"--prompt-file is required for provider {provider_id}.")
        if args.prompt_file and not args.prompt_file.exists():
            raise StageError(f"Missing prompt file: {args.prompt_file}")
        if args.imagegen_start_marker and not args.imagegen_start_marker.exists():
            raise StageError(f"Missing imagegen start marker: {args.imagegen_start_marker}")
        if (
            provider.get("requires_prompt")
            and args.prompt_file
            and args.image_source.stat().st_mtime < args.prompt_file.stat().st_mtime
            and not args.allow_image_before_prompt
        ):
            raise StageError(
                "Image source is older than the prompt file. It was not generated from this prompt."
            )
        if (
            provider.get("requires_codex_output_root")
            and args.imagegen_start_marker
            and args.image_source.stat().st_mtime < args.imagegen_start_marker.stat().st_mtime
        ):
            raise StageError("Image source is older than the imagegen start marker. It was not generated in this run.")
        imagegen_root = args.imagegen_output_root or default_imagegen_output_root()
        if (
            provider.get("requires_codex_output_root")
            and not args.allow_non_imagegen_source_root
            and not is_within_path(args.image_source, imagegen_root)
        ):
            raise StageError(
                f"Built-in ImageGen source must be under {imagegen_root}. "
                "Use the original built-in image_gen output path, or select a non-Codex provider."
            )
        brief_slug, brief_alt = load_brief_defaults(args.brief_json)
        slug = slugify(args.asset_slug or brief_slug or args.image_source.stem)
        suffix = args.image_source.suffix.lower() or ".png"
        if suffix not in [".png", ".jpg", ".jpeg", ".webp"]:
            suffix = ".png"
        asset_dir = args.assets_root / args.date
        asset_dir.mkdir(parents=True, exist_ok=True)
        staged = asset_dir / f"{slug}{suffix}"
        staged = maybe_compress_image(args.image_source, staged, args.max_bytes)

        data = load_json_object(args.content)
        paper = target_research_item(data, args.research_index)
        paper["image"] = str(staged)
        paper["image_alt"] = args.alt or brief_alt or paper.get("image_alt") or f"{paper.get('title', '研究前沿')} 图解"
        if args.research_index == 0 or (args.research_index is None and "paper" not in data):
            data["paper"] = dict(paper)

        out_content = args.out_content or args.content
        write_json_object(out_content, data)

        manifest_path = args.manifest or default_research_image_manifest_path(args.date)
        manifest = {
            "schema_version": 2,
            "date": args.date,
            "created_at": utc_now_iso(),
            "source_kind": source_kind,
            "provider": provider_snapshot(provider_id, provider),
            "quality_gate": {
                "policy": "baseline_locked",
                "decision": quality_decision,
                "requires_manual_comparison": bool(provider.get("requires_manual_comparison")),
                "comparison_notes": args.comparison_notes,
            },
            "generation_manifest": {
                "path": str(args.generation_manifest),
                "sha256": file_sha256(args.generation_manifest),
            }
            if args.generation_manifest
            else None,
            "imagegen_output_root": str(imagegen_root) if provider.get("requires_codex_output_root") else None,
            "imagegen_source_root_checked": bool(
                provider.get("requires_codex_output_root") and not args.allow_non_imagegen_source_root
            ),
            "content": str(out_content),
            "research_index": args.research_index,
            "brief_json": str(args.brief_json) if args.brief_json else None,
            "prompt_file": str(args.prompt_file) if args.prompt_file else None,
            "prompt_sha256": file_sha256(args.prompt_file) if args.prompt_file else None,
            "prompt_mtime": file_mtime_iso(args.prompt_file) if args.prompt_file else None,
            "imagegen_start_marker": str(args.imagegen_start_marker) if args.imagegen_start_marker else None,
            "imagegen_start_marker_mtime": (
                file_mtime_iso(args.imagegen_start_marker) if args.imagegen_start_marker else None
            ),
            "image_source": str(args.image_source),
            "image_source_sha256": file_sha256(args.image_source),
            "image_source_mtime": file_mtime_iso(args.image_source),
            "staged_image": str(staged),
            "staged_image_sha256": file_sha256(staged),
            "staged_image_mtime": file_mtime_iso(staged),
            "paper_image": paper["image"],
            "image_alt": paper["image_alt"],
            "review_status": args.review_status,
            "review_notes": args.review_notes,
            "max_bytes": args.max_bytes,
        }
        write_json_object(manifest_path, manifest)
    except (OSError, json.JSONDecodeError, JsonObjectError, ImageProviderError, StageError) as exc:
        raise SystemExit(f"Stage failed: {exc}") from exc

    print(f"Staged image: {staged}")
    print(f"Patched content: {out_content}")
    print(f"Image manifest: {manifest_path}")


if __name__ == "__main__":
    main()
