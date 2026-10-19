#!/usr/bin/env python3
"""Create and validate an editorial quality gate for an AI Daily research image.

Hashes bind the review to files; they do not prove who viewed the image.
Keyword and text-density heuristics are warnings for a reviewer, not automatic
rejection rules. The script checks image decoding but cannot judge aesthetics
or facts from pixels. Three publication checks must be recorded after inspection;
the approve command never converts pending checks into passes.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from PIL import Image

from ai_daily_common import file_sha256, load_json_object, write_json_object


class EditorialReviewError(Exception):
    pass


SCHEMA_VERSION = 1
REQUIRED_MANUAL_CHECKS = (
    "main_story",
    "source_specificity",
    "evidence_boundary",
)
GENERIC_CORE_PHRASES = (
    "通用推理核心",
    "中央推理核心",
    "通用智能核心",
    "ai核心",
    "ai core",
)
LIFE_SCIENCE_CASE_GROUPS = (
    ("蛋白", "结合物", "结合位点"),
    ("nmr", "lc-ms", "谱图", "氢计数", "纯度", "化学分析"),
)


def as_text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def handoff_from_brief(path: Path) -> dict[str, Any]:
    data = load_json_object(path)
    handoff = data.get("visual_handoff")
    if not isinstance(handoff, dict):
        raise EditorialReviewError(f"{path} is missing visual_handoff.")
    return handoff


def text_values(value: Any) -> list[str]:
    """Return all human-facing strings from an overlay-plan section."""

    values: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            if key == "text" and isinstance(item, str):
                values.append(item.strip())
            else:
                values.extend(text_values(item))
    elif isinstance(value, list):
        for item in value:
            values.extend(text_values(item))
    return [item for item in values if item]


def overlay_text_from_derivation(path: Path | None) -> tuple[list[str], Path | None]:
    if not path:
        return [], None
    data = load_json_object(path)
    overlay = data.get("overlay_plan")
    if not isinstance(overlay, dict):
        raise EditorialReviewError("derivation manifest is missing overlay_plan.")
    raw_path = as_text(overlay.get("path"))
    if not raw_path:
        raise EditorialReviewError("derivation manifest overlay_plan.path is required.")
    plan_path = Path(raw_path)
    if not plan_path.exists():
        raise EditorialReviewError(f"Missing overlay plan: {plan_path}")
    return text_values(load_json_object(plan_path)), plan_path


def joined_handoff(handoff: dict[str, Any]) -> str:
    return "\n".join(
        "\n".join(as_text(item) for item in value) if isinstance(value, list) else as_text(value)
        for value in handoff.values()
    ).casefold()


def detected_case_groups(text: str) -> list[str]:
    names = ("protein_design", "spectral_chemistry")
    return [name for name, words in zip(names, LIFE_SCIENCE_CASE_GROUPS, strict=True) if any(word in text for word in words)]


def structural_checks(
    handoff: dict[str, Any],
    *,
    layout: str,
    render_mode: str,
    overlay_text: list[str],
) -> list[dict[str, str]]:
    text = joined_handoff(handoff)
    checks: list[dict[str, str]] = []
    case_groups = detected_case_groups(text)
    if len(case_groups) >= 2 and layout == "mechanism_hub":
        checks.append(
            {
                "id": "route_fit",
                "decision": "warn",
                "reason": "Multiple case-related terms detected. Check whether one mechanism or a comparison actually explains them; keywords alone cannot decide route fitness.",
            }
        )
    else:
        checks.append({"id": "route_fit", "decision": "pass", "reason": "No case-route heuristic warning; actual relationships still require visual review."})

    generic_terms = [term for term in GENERIC_CORE_PHRASES if term in text or any(term in item.casefold() for item in overlay_text)]
    if generic_terms:
        checks.append(
            {
                "id": "source_specificity",
                "decision": "warn",
                "reason": "Check whether these generic terms obscure the specific mechanism: " + ", ".join(generic_terms),
            }
        )
    else:
        checks.append(
            {
                "id": "source_specificity",
                "decision": "pass",
                "reason": "No forbidden generic AI-core label was detected in the handoff or overlay ledger.",
            }
        )

    if render_mode == "hybrid_overlay":
        checks.append({
            "id": "overlay_density",
            "decision": "warn",
            "reason": f"Hybrid overlay contains {len(overlay_text)} text elements. Inspect completeness and readability at email width; counts and layout names do not establish a density limit.",
        })
    return checks


def manual_template() -> list[dict[str, str]]:
    questions = {
        "main_story": "邮件宽度下能否读出主结论？标题和关键标签是否可读？",
        "source_specificity": "图中的主要机制和关键方向是否与来源一致？只把会改变读者理解的错误列为阻断项。",
        "evidence_boundary": "数字、名称和能力边界是否有来源依据？是否存在会误导读者的无关品牌或伪造细节？",
    }
    return [{"id": item, "question": questions[item], "decision": "pending", "notes": ""} for item in REQUIRED_MANUAL_CHECKS]


def check_image_file(path: Path) -> dict[str, int]:
    try:
        with Image.open(path) as candidate:
            candidate.verify()
        with Image.open(path) as candidate:
            candidate.load()
            return {"width": candidate.width, "height": candidate.height}
    except (OSError, ValueError, Image.DecompressionBombError) as exc:
        raise EditorialReviewError(f"Image cannot be decoded: {path}") from exc


def create_audit(
    *,
    brief_json: Path,
    image_source: Path,
    layout: str,
    render_mode: str,
    derivation_manifest: Path | None,
) -> dict[str, Any]:
    if not image_source.exists():
        raise EditorialReviewError(f"Missing image source: {image_source}")
    dimensions = check_image_file(image_source)
    handoff = handoff_from_brief(brief_json)
    overlay_text, overlay_plan = overlay_text_from_derivation(derivation_manifest)
    checks = structural_checks(handoff, layout=layout, render_mode=render_mode, overlay_text=overlay_text)
    has_structural_failure = any(check["decision"] == "fail" for check in checks)
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": "ai_daily_research_image_editorial_review",
        "brief_json": str(brief_json),
        "brief_sha256": file_sha256(brief_json),
        "image_source": str(image_source),
        "image_source_sha256": file_sha256(image_source),
        "dimensions": dimensions,
        "layout": layout,
        "render_mode": render_mode,
        "derivation_manifest": str(derivation_manifest) if derivation_manifest else None,
        "overlay_plan": str(overlay_plan) if overlay_plan else None,
        "overlay_label_count": len(overlay_text),
        "overlay_labels": overlay_text,
        "structural_checks": checks,
        "structural_decision": "reject" if has_structural_failure else "pass",
        "decision": "rejected" if has_structural_failure else "needs_review",
        "manual_checks": manual_template(),
        "reviewer": None,
        "review_notes": "",
    }


def manual_checks_pass(data: dict[str, Any]) -> bool:
    checks = data.get("manual_checks")
    if not isinstance(checks, list):
        return False
    decisions = {
        as_text(item.get("id")): as_text(item.get("decision"))
        for item in checks
        if isinstance(item, dict)
    }
    return all(decisions.get(check_id) == "pass" for check_id in REQUIRED_MANUAL_CHECKS)


def approve_audit(data: dict[str, Any], *, reviewer: str, notes: str) -> dict[str, Any]:
    if data.get("structural_decision") != "pass":
        raise EditorialReviewError("Cannot approve an editorial audit with structural failures.")
    if not manual_checks_pass(data):
        raise EditorialReviewError("Record a complete explicit visual pass before approval; pending checks are never auto-approved.")
    if not reviewer.strip() or not notes.strip():
        raise EditorialReviewError("Reviewer and inspection notes are required.")
    data["decision"] = "pass"
    data["reviewer"] = reviewer
    data["review_notes"] = notes
    return data


def validate_editorial_review_for_approval(
    review_path: Path,
    *,
    image_source: Path,
    brief_json: Path | None,
) -> dict[str, Any]:
    if not review_path.exists():
        raise EditorialReviewError(f"Missing editorial review: {review_path}")
    review = load_json_object(review_path)
    if review.get("schema_version") != SCHEMA_VERSION or review.get("kind") != "ai_daily_research_image_editorial_review":
        raise EditorialReviewError("Unsupported editorial review schema.")
    if review.get("image_source_sha256") != file_sha256(image_source):
        raise EditorialReviewError("Editorial review is not bound to --image-source.")
    check_image_file(image_source)
    if brief_json and review.get("brief_sha256") != file_sha256(brief_json):
        raise EditorialReviewError("Editorial review is not bound to --brief-json.")
    if review.get("structural_decision") != "pass":
        raise EditorialReviewError("Editorial review has unresolved structural failures.")
    visual_pass = manual_checks_pass(review)
    if review.get("validation_mode") == "automated_visual":
        checks = review.get("automated_checks", {})
        visual = checks.get("visual", {}).get("result", {})
        editorial = checks.get("editorial", {}).get("result", {})
        staged = Path(str(review.get("staged_image") or ""))
        visual_pass = (
            visual.get("decision") == "pass" and visual.get("issues") == []
            and all(visual.get(k) is True for k in ("main_story_readable", "mechanism_materially_correct", "claims_grounded", "critical_text_readable"))
            and editorial.get("decision") == "pass" and editorial.get("issues") == []
            and all(editorial.get(k) is True for k in ("source_grounded", "readable_chinese", "structure_complete", "no_internal_notes", "no_duplicates"))
            and staged.is_file() and review.get("staged_image_sha256") == file_sha256(staged)
        )
    if review.get("decision") != "pass" or not visual_pass:
        raise EditorialReviewError("Editorial review requires a complete explicit visual pass.")
    if not as_text(review.get("reviewer")) or not as_text(review.get("review_notes")):
        raise EditorialReviewError("Editorial review lacks reviewer or inspection notes.")
    return review


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)

    audit = sub.add_parser("audit", help="Create a structural-plus-manual review record.")
    audit.add_argument("--brief-json", required=True, type=Path)
    audit.add_argument("--image-source", required=True, type=Path)
    audit.add_argument(
        "--layout",
        required=True,
        choices=["mechanism_hub", "case_comparison", "evidence_sidebar", "benchmark_comparison"],
    )
    audit.add_argument(
        "--render-mode",
        required=True,
        choices=["model_native", "hybrid_overlay", "deterministic_infographic"],
    )
    audit.add_argument("--derivation-manifest", type=Path)
    audit.add_argument("--out", required=True, type=Path)

    approve = sub.add_parser("approve", help="Record a completed visual review after inspection.")
    approve.add_argument("--review", required=True, type=Path)
    approve.add_argument("--reviewer", required=True)
    approve.add_argument("--notes", required=True)

    args = parser.parse_args()
    try:
        if args.action == "audit":
            review = create_audit(
                brief_json=args.brief_json,
                image_source=args.image_source,
                layout=args.layout,
                render_mode=args.render_mode,
                derivation_manifest=args.derivation_manifest,
            )
            write_json_object(args.out, review)
            print(f"Editorial review: {args.out} ({review['decision']})")
        else:
            review = load_json_object(args.review)
            approved = approve_audit(review, reviewer=args.reviewer, notes=args.notes)
            write_json_object(args.review, approved)
            print(f"Editorial review approved: {args.review}")
    except (OSError, json.JSONDecodeError, EditorialReviewError) as exc:
        raise SystemExit(f"Editorial review failed: {exc}") from exc


if __name__ == "__main__":
    main()
