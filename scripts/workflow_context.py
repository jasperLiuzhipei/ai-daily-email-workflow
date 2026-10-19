"""Shared workflow paths, artifact lineage and phase recording.

This module cannot dispatch generation, review or delivery. CLI adapters and
quality checks use the same paths and evidence checks without importing the runner.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any

from ai_daily_common import file_sha256
from workflow_run_state import record_run_event


SCRIPT_DIR = Path(__file__).resolve().parent
WORKFLOW_DIR = SCRIPT_DIR.parent
DEFAULT_EMAIL_SETTINGS = WORKFLOW_DIR / "config" / "email_settings.json"
DEFAULT_IMAGE_FALLBACK_RUNTIME_CONFIG = WORKFLOW_DIR / "config" / "ai_runtime.image_fallback.json"


class WorkflowError(Exception):
    pass


@dataclass
class Paths:
    run_date: str
    run_dir: Path
    source_selection: Path
    source_materials: Path
    content: Path
    normalized_content: Path
    brief_json: Path
    brief_markdown: Path
    research_api_raw: Path
    content_api_raw: Path
    prompt_file: Path
    imagegen_start_marker: Path
    image_manifest: Path
    api_image: Path
    api_image_generation_manifest: Path
    selection_diagnostic: Path
    selection_review: Path
    workflow_state: Path
    html: Path
    package_test: Path
    package_prod: Path


def default_paths(run_date: str) -> Paths:
    run_dir = Path("daily_runs") / run_date
    return Paths(
        run_date=run_date,
        run_dir=run_dir,
        source_selection=run_dir / f"source_selection_auto_{run_date}.json",
        source_materials=run_dir / "source_material.json",
        content=run_dir / "ai_daily_email_content.json",
        normalized_content=run_dir / "ai_daily_email_content.normalized.json",
        brief_json=run_dir / "research_brief.parsed.json",
        brief_markdown=run_dir / "research_brief.md",
        research_api_raw=run_dir / "research_brief.api.raw.json",
        content_api_raw=run_dir / "ai_daily_content.api.raw.json",
        prompt_file=run_dir / "research_image_prompt.txt",
        imagegen_start_marker=run_dir / "imagegen_start.marker",
        image_manifest=run_dir / "research_image_manifest.json",
        api_image=run_dir / "research_image_image2_candidate.png",
        api_image_generation_manifest=run_dir / "research_image_image2_generation.json",
        selection_diagnostic=run_dir / "selection_diagnostic.json",
        selection_review=run_dir / "selection_review.json",
        workflow_state=run_dir / "workflow_run.json",
        html=Path("output") / f"ai_daily_email_{run_date}.html",
        package_test=Path("email_packages") / run_date / "test",
        package_prod=Path("email_packages") / run_date / "prod",
    )


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def source_material_matches_selection(paths: Paths) -> bool:
    """Return whether fetched material was built from the current selection file."""

    if not paths.source_selection.exists() or not paths.source_materials.exists():
        return False
    try:
        materials = load_json(paths.source_materials)
    except (OSError, json.JSONDecodeError):
        return False
    recorded = str(materials.get("selection_sha256") or "").strip()
    return bool(recorded) and recorded == file_sha256(paths.source_selection)


def assert_content_lineage(paths: Paths) -> None:
    """Refuse to render or send a daily whose inputs no longer agree.

    Selection can be refreshed by a retry or an editor while source fetching is
    underway.  Without this check, a newer selection JSON can be displayed as
    if it produced an older material/content snapshot.
    """

    if not paths.source_selection.exists() or not paths.source_materials.exists():
        raise WorkflowError("Missing selection or source material required to verify editorial lineage.")
    try:
        materials = load_json(paths.source_materials)
        content = load_json(paths.content)
    except (OSError, json.JSONDecodeError) as exc:
        raise WorkflowError("Unable to read artifacts required to verify editorial lineage.") from exc

    selection_sha = file_sha256(paths.source_selection)
    material_sha = file_sha256(paths.source_materials)
    material_selection_sha = str(materials.get("selection_sha256") or "").strip()
    generation = content.get("generation") if isinstance(content.get("generation"), dict) else {}
    content_material_sha = str(generation.get("source_material_sha256") or "").strip()
    content_selection_sha = str(generation.get("selection_sha256") or "").strip()

    if material_selection_sha != selection_sha:
        raise WorkflowError(
            "Source selection changed after material fetch. Re-run api-compose to rebuild material and content from one snapshot."
        )
    if content_material_sha != material_sha or content_selection_sha != selection_sha:
        raise WorkflowError(
            "Content was not generated from the current source-material/selection snapshot. Re-run api-compose before build."
        )


def record_phase(
    paths: Paths,
    *,
    action: str,
    phase: str | None = None,
    message: str | None = None,
    artifacts: dict[str, Path] | None = None,
    delivery_channel: str | None = None,
    delivery: dict[str, Any] | None = None,
) -> None:
    record_run_event(
        paths.workflow_state,
        paths.run_date,
        action=action,
        phase=phase,
        message=message,
        artifacts=artifacts,
        delivery_channel=delivery_channel,
        delivery=delivery,
    )
