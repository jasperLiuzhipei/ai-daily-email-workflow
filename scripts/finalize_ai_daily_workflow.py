#!/usr/bin/env python3
"""Finalize the AI daily email after research image generation.

This script intentionally starts after image generation and visual review. It
does not generate images; it stages a reviewed provider output into a traceable
project asset and strict-builds the HTML.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from ai_daily_common import default_imagegen_output_root, default_research_image_manifest_path
from find_latest_imagegen_output import FindImagegenError, image_candidates


SCRIPT_DIR = Path(__file__).resolve().parent


class FinalizeError(Exception):
    pass


def run_step(args: list[str]) -> None:
    print("+ " + " ".join(args), flush=True)
    subprocess.run(args, check=True)


def script_path(filename: str) -> str:
    return str(SCRIPT_DIR / filename)


def default_run_path(run_date: str, filename: str) -> Path:
    return Path("daily_runs") / run_date / filename


def apply_default_paths(args: argparse.Namespace) -> None:
    run_date = args.date
    args.content = args.content or default_run_path(run_date, "ai_daily_email_content.json")
    args.out_content = args.out_content or args.content
    args.brief_json = args.brief_json or default_run_path(run_date, "research_brief.parsed.json")
    args.prompt_file = args.prompt_file or default_run_path(run_date, "research_image_prompt.txt")
    args.imagegen_start_marker = args.imagegen_start_marker or default_run_path(run_date, "imagegen_start.marker")
    args.image_source_path_file = args.image_source_path_file or default_run_path(run_date, "imagegen_source_path.txt")
    args.manifest = args.manifest or default_research_image_manifest_path(run_date)
    args.output = args.output or (Path("output") / f"ai_daily_email_{run_date}.html")
    args.normalized_output = args.normalized_output or default_run_path(
        run_date, "ai_daily_email_content.normalized.json"
    )
    if not args.provider_id:
        if args.source_kind == "imagegen":
            args.provider_id = "codex_imagegen"
        elif args.source_kind == "provided":
            args.provider_id = "manual_provided"
        else:
            raise FinalizeError("--provider-id is required when --source-kind generated.")


def select_image_source(args: argparse.Namespace) -> Path:
    if args.image_source:
        return args.image_source

    if args.source_kind != "imagegen":
        raise FinalizeError("--image-source is required for non-native image providers.")

    if not args.imagegen_start_marker.exists():
        raise FinalizeError(f"Missing imagegen start marker: {args.imagegen_start_marker}")

    try:
        candidates = image_candidates(args.imagegen_output_root, args.imagegen_start_marker.stat().st_mtime)
    except FindImagegenError as exc:
        raise FinalizeError(str(exc)) from exc
    if not candidates:
        raise FinalizeError(
            f"No image_gen output found under {args.imagegen_output_root} after {args.imagegen_start_marker}."
        )

    selected = candidates[0]
    args.image_source_path_file.parent.mkdir(parents=True, exist_ok=True)
    args.image_source_path_file.write_text(str(selected) + "\n", encoding="utf-8")
    return selected


def build_stage_cmd(args: argparse.Namespace, image_source: Path) -> list[str]:
    cmd = [
        sys.executable,
        script_path("stage_research_image.py"),
        "--content",
        str(args.content),
        "--out-content",
        str(args.out_content),
        "--brief-json",
        str(args.brief_json),
        "--prompt-file",
        str(args.prompt_file),
        "--source-kind",
        args.source_kind,
        "--provider-id",
        args.provider_id,
        "--image-source",
        str(image_source),
        "--date",
        args.date,
        "--manifest",
        str(args.manifest),
        "--review-status",
        args.review_status,
        "--review-notes",
        args.review_notes,
        "--max-bytes",
        str(args.max_bytes),
    ]
    if args.provider_registry:
        cmd.extend(["--provider-registry", str(args.provider_registry)])
    if args.generation_manifest:
        cmd.extend(["--generation-manifest", str(args.generation_manifest)])
    if args.quality_decision:
        cmd.extend(["--quality-decision", args.quality_decision])
    if args.comparison_notes:
        cmd.extend(["--comparison-notes", args.comparison_notes])
    if args.source_kind == "imagegen":
        cmd.extend(
            [
                "--imagegen-start-marker",
                str(args.imagegen_start_marker),
                "--imagegen-output-root",
                str(args.imagegen_output_root),
            ]
        )
    if args.asset_slug:
        cmd.extend(["--asset-slug", args.asset_slug])
    return cmd


def build_verify_cmd(args: argparse.Namespace) -> list[str]:
    cmd = [
        sys.executable,
        script_path("verify_research_image_lineage.py"),
        "--content",
        str(args.out_content),
        "--manifest",
        str(args.manifest),
    ]
    if args.source_kind == "provided":
        cmd.append("--allow-provided")
    if args.allow_candidate_image_provider:
        cmd.append("--allow-candidate-image-provider")
    if args.provider_registry:
        cmd.extend(["--provider-registry", str(args.provider_registry)])
    return cmd


def build_email_cmd(args: argparse.Namespace) -> list[str]:
    cmd = [
        sys.executable,
        script_path("build_ai_daily_email.py"),
        "--content",
        str(args.out_content),
        "--date",
        args.date,
        "--theme",
        args.theme,
        "--news-layout",
        args.news_layout,
        "--output",
        str(args.output),
        "--normalized-output",
        str(args.normalized_output),
        "--require-image-manifest",
        "--image-manifest",
        str(args.manifest),
        "--series",
        args.series,
        "--issue-ledger",
        str(args.issue_ledger),
    ]
    if args.issue is not None:
        cmd.extend(["--issue", str(args.issue)])
    if args.no_write_ledger:
        cmd.append("--no-write-ledger")
    if args.source_kind == "provided":
        cmd.append("--allow-provided-research-image")
    if args.allow_candidate_image_provider:
        cmd.append("--allow-candidate-image-provider")
    if args.provider_registry:
        cmd.extend(["--image-provider-registry", str(args.provider_registry)])
    return cmd


def main() -> None:
    parser = argparse.ArgumentParser(description="Finalize staged image, manifest, strict HTML, and validation.")
    parser.add_argument("--date", required=True, help="Run date YYYY-MM-DD.")
    parser.add_argument("--content", type=Path, help="Input daily email content JSON.")
    parser.add_argument("--out-content", type=Path, help="Patched content JSON output.")
    parser.add_argument("--brief-json", type=Path, help="Parsed research brief JSON.")
    parser.add_argument("--prompt-file", type=Path, help="Research image prompt file.")
    parser.add_argument("--imagegen-start-marker", type=Path, help="Marker created immediately before image_gen.")
    parser.add_argument("--image-source", type=Path, help="Reviewed image source path. Optional for imagegen.")
    parser.add_argument("--image-source-path-file", type=Path, help="Where to write the selected imagegen source path.")
    parser.add_argument("--imagegen-output-root", type=Path, default=default_imagegen_output_root())
    parser.add_argument("--manifest", type=Path, help="Research image manifest path.")
    parser.add_argument("--asset-slug", help="Override staged image slug.")
    parser.add_argument("--review-notes", default="checked against Visual Handoff and Reject If")
    parser.add_argument("--review-status", choices=["pass", "needs-review"], default="pass")
    parser.add_argument("--source-kind", choices=["imagegen", "generated", "provided"], default="imagegen")
    parser.add_argument("--provider-id", help="Image provider id from the registry.")
    parser.add_argument("--provider-registry", type=Path, help="Image provider registry path.")
    parser.add_argument("--generation-manifest", type=Path, help="API provider generation manifest.")
    parser.add_argument("--quality-decision", choices=["approved", "pending", "rejected"])
    parser.add_argument("--comparison-notes", help="Manual A/B comparison notes for non-baseline providers.")
    parser.add_argument(
        "--allow-candidate-image-provider",
        action="store_true",
        help="Build a non-production comparison HTML with a candidate provider image.",
    )
    parser.add_argument("--theme", default="clay_orange")
    parser.add_argument("--news-layout", default="paper")
    parser.add_argument("--output", type=Path, help="Output HTML path.")
    parser.add_argument("--normalized-output", type=Path, help="Normalized enriched JSON output path.")
    parser.add_argument("--series", choices=["trial", "official", "none"], default="trial")
    parser.add_argument("--issue", type=int, help="Override issue number.")
    parser.add_argument("--issue-ledger", type=Path, default=Path("daily_runs") / "issue_ledger.json")
    parser.add_argument("--no-write-ledger", action="store_true")
    parser.add_argument("--max-bytes", type=int, default=1_000_000)
    args = parser.parse_args()
    apply_default_paths(args)

    try:
        image_source = select_image_source(args)
        run_step(build_stage_cmd(args, image_source))
        run_step(build_verify_cmd(args))
        run_step(build_email_cmd(args))
        run_step([sys.executable, script_path("validate_ai_daily_content.py"), str(args.normalized_output)])
    except (OSError, subprocess.CalledProcessError, FinalizeError) as exc:
        raise SystemExit(f"Finalize failed: {exc}") from exc

    print("\nFinalized AI daily email workflow.")
    print(f"Image source: {image_source}")
    print(f"Patched content: {args.out_content}")
    print(f"Manifest: {args.manifest}")
    print(f"HTML: {args.output}")
    print(f"Normalized JSON: {args.normalized_output}")


if __name__ == "__main__":
    main()
