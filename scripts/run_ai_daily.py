#!/usr/bin/env python3
"""One-command runner for the AI daily workflow.

This orchestrates deterministic steps and deployable API editorial steps:
- optional AIHot source selection and bounded primary-source material fetch
- API-backed research brief, email JSON, and Image2 candidate generation
- HTML build/finalize from prepared JSON and reviewed research image
- email package generation with CID inline images
- SMTP dry-run or send for test/prod recipients

Codex is not a runtime dependency. Text and image inference use the configured
OpenAI-compatible gateways, while source selection, rendering, CID packaging,
and SMTP remain deterministic and auditable.
"""

from __future__ import annotations

import argparse
from workflow_cli import parse_args
from workflow_context import (
    SCRIPT_DIR,
    WORKFLOW_DIR,
    DEFAULT_EMAIL_SETTINGS,
    DEFAULT_IMAGE_FALLBACK_RUNTIME_CONFIG,
    WorkflowError,
    Paths,
    default_paths,
    load_json,
    source_material_matches_selection,
    assert_content_lineage,
    record_phase,
)
import newspaper_text
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from work_calendar import today_iso as shanghai_today, workday_decision, run_lock, previous_publication_cutoff
from ai_daily_common import file_sha256, write_json_object
from ai_gateway_client import load_env_file
from editorial_backlog import publication_items, sync_backlog
from build_ai_daily_email import assign_issue, format_date_cn, load_ledger, parse_run_date, verify_research_image_manifests
from daily_config import DailyConfigError, load_editorial_policy
from validate_ai_daily_content import ValidationError, validate_content
from email_quality_control import EmailQualityError, recover, review_current_email, send_failure_alert
from email_recipients import (
    RECIPIENT_SNAPSHOT_NAME,
    RecipientError,
    delivery_fingerprint,
    load_recipient_registry,
    load_recipient_snapshot,
)
from verify_research_image_lineage import LineageError
from image_provider_registry import ImageProviderError, load_provider_registry, resolve_provider
from workflow_run_state import (
    WorkflowStateError,
    ensure_workflow_state,
    is_prod_sent,
    load_workflow_state,
    record_run_event,
    record_run_failure,
    summarize_workflow_state,
)


def today_iso() -> str:
    return shanghai_today()


def script(name: str) -> str:
    return str(SCRIPT_DIR / name)


def load_email_settings(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise WorkflowError(f"Missing email settings: {path}")
    data = load_json(path)
    smtp = data.get("smtp")
    if not isinstance(smtp, dict):
        raise WorkflowError(f"{path} must contain smtp settings.")
    return data


def feedback_base_url(settings: dict[str, Any], args: argparse.Namespace) -> str:
    feedback = settings.get("feedback") if isinstance(settings.get("feedback"), dict) else {}
    env_name = str(feedback.get("base_url_env") or "AI_DAILY_FEEDBACK_BASE_URL").strip()
    return str(
        args.feedback_base_url
        or os.environ.get(env_name)
        or feedback.get("base_url")
        or ""
    ).strip().rstrip("/")


def weekly_conversation_args(settings: dict[str, Any], run_date: str) -> list[str]:
    """Return EML packaging arguments for an opt-in weekly email conversation.

    Monday normally starts a new conversation. On later weekdays the package
    builder receives the week's first available *personalized* package so it
    can select the matching recipient's Message-ID for the In-Reply-To /
    References headers. If an earlier daily send was skipped, the first
    available day simply starts that week's conversation instead of blocking
    the later send.
    """

    conversation = settings.get("conversation")
    if not isinstance(conversation, dict) or conversation.get("mode") != "weekly":
        return []

    issue_date = parse_run_date(run_date)
    iso_year, iso_week, _ = issue_date.isocalendar()
    template = str(conversation.get("subject_template") or "AI DAILY｜第{week}周")
    try:
        subject = template.format(year=iso_year, week=f"{iso_week:02d}")
    except (KeyError, ValueError) as exc:
        raise WorkflowError(
            "conversation.subject_template only supports {year} and {week}."
        ) from exc
    if not subject.strip():
        raise WorkflowError("conversation.subject_template produced an empty email subject.")

    cmd = ["--subject", subject.strip()]
    if issue_date.weekday() == 0:
        return cmd

    week_start = issue_date - timedelta(days=issue_date.weekday())
    # A missed Monday must not prevent the rest of that week's daily emails
    # from being delivered. Look for the earliest *previous* production
    # package in the current ISO week. If none exists, this package becomes
    # the late weekly root and subsequent days will reply to it.
    for day_offset in range(issue_date.weekday()):
        candidate_date = week_start + timedelta(days=day_offset)
        candidate_package = Path("email_packages") / candidate_date.isoformat() / "prod"
        # Non-personalized editions have no per-recipient Message-ID map and
        # cannot serve as a conversation root for personalized packaging.
        if (candidate_package / "messages").is_dir() and any((candidate_package / "messages").glob("*.eml")):
            cmd.extend(["--conversation-root-package", str(candidate_package)])
            break
    return cmd


def load_optional_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return load_json(path)


def default_api_image_provider_id() -> str:
    """Return the configured API image provider instead of hard-coding a gateway."""

    provider_id, provider = resolve_provider(None, load_provider_registry())
    if provider.get("execution") != "openai_images_api":
        raise WorkflowError(
            f"Configured default image provider {provider_id!r} is not an API image provider."
        )
    return provider_id


def image_provider_id_from_generation_manifest(paths: Paths) -> str:
    """Keep preview staging aligned with the candidate's actual generating provider."""

    if paths.api_image_generation_manifest.exists():
        try:
            manifest = load_json(paths.api_image_generation_manifest)
        except (OSError, json.JSONDecodeError) as exc:
            raise WorkflowError(
                f"Invalid image generation manifest: {paths.api_image_generation_manifest}"
            ) from exc
        provider = manifest.get("provider")
        if isinstance(provider, dict) and str(provider.get("id") or "").strip():
            return str(provider["id"])
    return default_api_image_provider_id()


def selected_research_exists(paths: Paths) -> bool:
    """Return whether this run selected a research item worth a full brief.

    Research is an editorially optional section.  A zero-research selection is
    a valid news-only issue, not a reason to lower the research bar or abort
    the day's delivery.  A selected research item whose primary source could
    not be fetched is treated the same way: it must not block the news cards
    or produce an unsupported research brief/image.
    """

    if not paths.source_materials.exists():
        raise WorkflowError(f"Missing source materials: {paths.source_materials}.")
    materials = load_json(paths.source_materials)
    research = materials.get("research")
    if research is None:
        return False
    if not isinstance(research, list):
        raise WorkflowError(f"{paths.source_materials}.research must be a list when present.")
    return any(
        isinstance(item, dict) and str(item.get("fetch_status") or "").strip().lower() == "fetched"
        for item in research
    )


def content_has_research(paths: Paths) -> bool:
    """Return whether final content contains a research feature and image gate."""

    if not paths.content.exists():
        return False
    content = load_json(paths.content)
    items = content.get("research_items")
    if isinstance(items, list) and items:
        return any(isinstance(item, dict) for item in items)
    return isinstance(content.get("paper"), dict)


def run_cmd(cmd: list[str], *, dry_label: str | None = None) -> None:
    if dry_label:
        print(f"\n[{dry_label}]", flush=True)
    print("+ " + " ".join(cmd), flush=True)
    log_dir = os.environ.get("AI_DAILY_QUALITY_LOG_DIR")
    if not log_dir:
        subprocess.run(cmd, check=True)
        return
    from email_quality_control import redact
    directory = Path(log_dir)
    directory.mkdir(parents=True, exist_ok=True)
    log_path = directory / (datetime.now().strftime("%Y%m%dT%H%M%S%f") + "_" + Path(cmd[1]).stem + ".log")
    print(f"Stage output: {log_path}", flush=True)
    with log_path.open("w", encoding="utf-8") as log:
        result = subprocess.run(cmd, stdout=log, stderr=subprocess.STDOUT, timeout=1800)
    if result.returncode:
        detail = redact(log_path.read_text(encoding="utf-8", errors="replace")[-6000:])
        raise WorkflowError(f"{dry_label or Path(cmd[1]).name} failed (exit {result.returncode}): {detail}")


def repair_email(paths: Paths, args: argparse.Namespace, reason: str) -> None:
    delivery_path = paths.run_dir / "smtp_prod_delivery.json"
    if delivery_path.exists() and load_json(delivery_path).get("sent"):
        raise WorkflowError("Production delivery has started; cannot regenerate a partly sent email. Resume the original package.")
    # Transport failures need a fresh QA call, not a new draft or a weaker model.
    if "Quality model request failed" in reason or "Quality model did not return valid JSON" in reason:
        return
    if "Final email editorial QA" in reason:
        from email_copy_repair import repair_reviewed_copy
        repair_reviewed_copy(paths, args)
        return
    if "Daily editorial rewrite required" in reason:
        from email_copy_repair import repair_news_prose
        if repair_news_prose(paths, args):
            return
    from copy import copy
    repair_args = copy(args)
    if "Visual QA" in reason:
        repair_args.refresh_api_content = False
        repair_args.refresh_api_image = True
        run_api_image(paths, repair_args)
        run_api_preview(paths, repair_args)
        return
    repair_args.refresh_api_content = True
    repair_args.refresh_api_image = True
    run_prepare(paths, repair_args)
    run_api_preview(paths, repair_args)
    if getattr(args, "action", None) == "prod":
        args.skip_build = True


def run_prepare_reviewed(paths: Paths, args: argparse.Namespace) -> None:
    from copy import copy
    from email_quality_control import policy as quality_policy
    if quality_policy().get("ai_review_enabled") is not True:
        raise EmailQualityError("AI email self-review is disabled pending gateway data authorization")
    saved = load_optional_json(paths.run_dir / "prepare_recovery.json")
    initial = args.rebuild or saved.get("status") != "passed"
    def operation():
        nonlocal initial
        if initial:
            initial = False
            run_prepare(paths, args)
            # Staging mutates flags; keep production and retry args intact.
            run_api_preview(paths, copy(args))
        return review_current_email(paths, args)
    recover(paths, args, scope="prepare", operation=operation,
            repair=lambda reason: repair_email(paths, args, reason), reset=args.rebuild)
    record_phase(paths, action="email_self_review", phase="HTML_READY",
                 message="Final email AI self-review passed.",
                 artifacts={"email_self_review": paths.run_dir / "email_self_review.json"})


def file_status(path: Path) -> str:
    if not path.exists():
        return "missing"
    if path.is_dir():
        return "dir"
    size = path.stat().st_size
    return f"ok {size:,} bytes"


def latest_eml(package_dir: Path) -> Path:
    emls = sorted(package_dir.glob("*.eml"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not emls:
        raise WorkflowError(f"No .eml found in {package_dir}")
    return emls[0]


def apply_overrides(paths: Paths, args: argparse.Namespace) -> Paths:
    paths.source_selection = args.source_selection or paths.source_selection
    paths.source_materials = args.source_materials or paths.source_materials
    paths.content = args.content or paths.content
    paths.normalized_content = args.normalized_content or paths.normalized_content
    paths.brief_json = args.brief_json or paths.brief_json
    paths.brief_markdown = args.brief_markdown or paths.brief_markdown
    paths.research_api_raw = args.research_api_raw or paths.research_api_raw
    paths.content_api_raw = args.content_api_raw or paths.content_api_raw
    paths.prompt_file = args.prompt_file or paths.prompt_file
    paths.image_manifest = args.image_manifest or paths.image_manifest
    paths.api_image = args.api_image or paths.api_image
    paths.api_image_generation_manifest = args.api_image_generation_manifest or paths.api_image_generation_manifest
    paths.selection_diagnostic = args.selection_diagnostic or paths.selection_diagnostic
    paths.selection_review = args.selection_review or paths.selection_review
    paths.workflow_state = args.workflow_state or paths.workflow_state
    paths.html = args.html or paths.html
    paths.package_test = args.test_package_dir or paths.package_test
    paths.package_prod = args.prod_package_dir or paths.package_prod
    return paths


def run_status(paths: Paths, args: argparse.Namespace) -> None:
    checks = [
        ("run_dir", paths.run_dir),
        ("issue_ledger", args.issue_ledger),
        ("send_ledger", args.send_ledger),
        ("source_selection", paths.source_selection),
        ("source_materials", paths.source_materials),
        ("content", paths.content),
        ("normalized_content", paths.normalized_content),
        ("brief_json", paths.brief_json),
        ("brief_markdown", paths.brief_markdown),
        ("research_api_raw", paths.research_api_raw),
        ("content_api_raw", paths.content_api_raw),
        ("prompt_file", paths.prompt_file),
        ("imagegen_start_marker", paths.imagegen_start_marker),
        ("image_manifest", paths.image_manifest),
        ("api_image", paths.api_image),
        ("api_image_generation_manifest", paths.api_image_generation_manifest),
        ("selection_diagnostic", paths.selection_diagnostic),
        ("selection_review", paths.selection_review),
        ("workflow_state", paths.workflow_state),
        ("html", paths.html),
        ("package_test", paths.package_test),
        ("package_prod", paths.package_prod),
    ]
    print(f"AI Daily status: {paths.run_date}")
    for label, path in checks:
        print(f"{label:22} {file_status(path):18} {path}")
    state = load_workflow_state(paths.workflow_state)
    if state:
        print("\nWorkflow state")
        for line in summarize_workflow_state(state):
            print(line)


def existing_run_artifacts(paths: Paths) -> dict[str, Path]:
    candidates = {
        "source_selection": paths.source_selection,
        "source_materials": paths.source_materials,
        "content": paths.content,
        "normalized_content": paths.normalized_content,
        "research_brief": paths.brief_json,
        "research_brief_markdown": paths.brief_markdown,
        "research_api_raw": paths.research_api_raw,
        "content_api_raw": paths.content_api_raw,
        "research_image_prompt": paths.prompt_file,
        "api_image_candidate": paths.api_image,
        "api_image_generation_manifest": paths.api_image_generation_manifest,
        "research_image_manifest": paths.image_manifest,
        "selection_diagnostic": paths.selection_diagnostic,
        "selection_review": paths.selection_review,
        "html": paths.html,
        "test_package": paths.package_test,
        "prod_package": paths.package_prod,
    }
    artifacts = {key: path for key, path in candidates.items() if path.exists()}
    for recipient_list, package_dir in (("test", paths.package_test), ("prod", paths.package_prod)):
        if not package_dir.exists():
            continue
        try:
            eml = latest_eml(package_dir)
        except WorkflowError:
            continue
        artifacts[f"{recipient_list}_eml"] = eml
        snapshot = eml.parent / RECIPIENT_SNAPSHOT_NAME
        if snapshot.exists():
            artifacts[f"{recipient_list}_recipient_snapshot"] = snapshot
    return artifacts


def inferred_phase(paths: Paths, args: argparse.Namespace) -> str:
    send_ledger = load_optional_json(args.send_ledger)
    prod_sends = send_ledger.get("prod")
    if isinstance(prod_sends, dict) and paths.run_date in prod_sends:
        return "SENT"
    if paths.package_test.exists() or paths.package_prod.exists():
        return "PACKAGED"
    if paths.html.exists():
        return "HTML_READY"
    if paths.image_manifest.exists():
        return "IMAGE_APPROVED"
    if paths.content.exists():
        return "CONTENT_READY"
    if paths.source_selection.exists():
        return "SELECTED"
    return "DISCOVERED"


def run_state(paths: Paths, args: argparse.Namespace) -> None:
    state = ensure_workflow_state(paths.workflow_state, paths.run_date)
    artifacts = existing_run_artifacts(paths)
    missing_artifacts = {key: path for key, path in artifacts.items() if key not in state["artifacts"]}
    target_phase = inferred_phase(paths, args)
    if missing_artifacts or (state["phase"] == "DISCOVERED" and target_phase != "DISCOVERED"):
        delivery_channel = None
        delivery = None
        if target_phase == "SENT":
            delivery_channel = "prod"
            delivery = {"status": "sent", "backfilled_from": "send_ledger"}
            snapshot = artifacts.get("prod_recipient_snapshot")
            if snapshot:
                delivery.update(snapshot_delivery_metadata(snapshot))
        record_phase(
            paths,
            action="backfill_state",
            phase=target_phase,
            message="Existing local run artifacts backfilled into the workflow manifest.",
            artifacts=missing_artifacts,
            delivery_channel=delivery_channel,
            delivery=delivery,
        )
        state = load_workflow_state(paths.workflow_state) or state
    print(f"AI Daily workflow state: {paths.run_date}")
    for line in summarize_workflow_state(state):
        print(line)


def snapshot_delivery_metadata(snapshot_path: Path) -> dict[str, Any]:
    try:
        delivery = load_recipient_snapshot(snapshot_path)
    except RecipientError as exc:
        raise WorkflowError(f"Missing valid recipient snapshot: {exc}") from exc
    group = delivery.get("recipient_group") or {}
    return {
        "recipient_group": {
            key: group[key]
            for key in ("key", "id", "name", "version")
            if key in group
        },
        "recipient_count": delivery["recipient_count"],
        "recipient_fingerprint": delivery["fingerprint"],
    }


def run_recipients(args: argparse.Namespace) -> None:
    """Inspect registry metadata without printing member email addresses."""

    try:
        registry = load_recipient_registry(args.recipient_config)
    except RecipientError as exc:
        raise WorkflowError(str(exc)) from exc
    print(f"Recipient registry: {args.recipient_config}")
    for key in sorted(registry):
        group = registry[key]
        state = "enabled" if group["enabled"] else "disabled"
        group_meta = {
            field: group[field]
            for field in ("key", "id", "name", "version", "enabled", "source_format")
        }
        fingerprint = delivery_fingerprint(group_meta, group["members"])
        print(
            f"- {key}: {group['name']} | {group['id']} | v{group['version']} | "
            f"{state} | {len(group['members'])} recipients | {fingerprint[:12]}"
        )
        if group["source_format"] == "legacy_flat_json":
            print("  migration note: legacy flat JSON; move to registry schema before middle-platform deployment.")


def run_select(paths: Paths, args: argparse.Namespace) -> None:
    cmd = [
        sys.executable,
        script("select_aihot_sources.py"),
        "--date",
        paths.run_date,
        "--output",
        str(paths.source_selection),
    ]
    if getattr(args, 'ai_runtime_config', None):
        cmd.extend(['--editorial-runtime-config', str(args.ai_runtime_config)])
    if args.start:
        cmd.extend(["--start-at", args.start])
    if args.end:
        cmd.extend(["--end-at", args.end])
    if args.previous_content:
        for path in args.previous_content:
            cmd.extend(["--previous-content", str(path)])
    if args.research_count is not None:
        cmd.extend(["--research-count", str(args.research_count)])
    if args.min_news_count is not None:
        cmd.extend(["--min-news-count", str(args.min_news_count)])
    elif args.news_count is not None:
        # Keep older shell aliases working, while the selector now interprets
        # this value as a minimum rather than a maximum item cap.
        cmd.extend(["--news-count", str(args.news_count)])
    if args.max_news_count is not None:
        cmd.extend(["--max-news-count", str(args.max_news_count)])
    if args.paper_lookback_hours is not None:
        cmd.extend(["--paper-lookback-hours", str(args.paper_lookback_hours)])
    if args.news_lookback_hours is not None:
        cmd.extend(["--news-lookback-hours", str(args.news_lookback_hours)])
    if args.allow_wide_news_window:
        cmd.append("--allow-wide-news-window")
    if args.no_aihot_daily:
        cmd.append("--no-aihot-daily")
    if args.no_bestblogs:
        cmd.append("--no-bestblogs")
    if args.bestblogs_max_items is not None:
        cmd.extend(["--bestblogs-max-items", str(args.bestblogs_max_items)])
    if args.selection_policy:
        cmd.extend(["--selection-policy", str(args.selection_policy)])
    if args.audience_profiles:
        cmd.extend(["--audience-profiles", str(args.audience_profiles)])
    if args.audience_profile:
        cmd.extend(["--audience-profile", args.audience_profile])
    run_cmd(cmd, dry_label="select sources")
    record_phase(
        paths,
        action="select",
        phase="SELECTED",
        message="Source selection completed.",
        artifacts={"source_selection": paths.source_selection},
    )


def run_source_materials(paths: Paths, args: argparse.Namespace, *, force: bool = False) -> None:
    if not paths.source_selection.exists():
        raise WorkflowError(f"Missing source selection: {paths.source_selection}.")
    cmd = [
        sys.executable,
        script("fetch_selected_source_material.py"),
        "--selection",
        str(paths.source_selection),
        "--output",
        str(paths.source_materials),
    ]
    if args.refresh_source_materials or force:
        cmd.append("--force")
    run_cmd(cmd, dry_label="fetch source material")
    record_phase(
        paths,
        action="fetch_source_material",
        phase="SELECTED",
        message="Bounded primary-source material captured for API editorial generation.",
        artifacts={"source_selection": paths.source_selection, "source_materials": paths.source_materials},
    )


def assert_research_cache_current(paths: Paths) -> None:
    """Only reuse a complete successful research reading from current inputs."""
    from generate_research_brief_api import DEFAULT_SYSTEM_PROMPT, ResearchApiError, load_system_prompt
    from research_cache import build_cache_contract, cache_manifest_path

    reason = "Research cache is missing, incomplete or stale. Use --refresh-api-content to regenerate the reviewed research and daily content."
    try:
        source = load_json(paths.source_materials)["research"][0]
        if not isinstance(source, dict) or source.get("fetch_status") != "fetched":
            raise ValueError("No fetched research source")
        expected = build_cache_contract(
            source=source, system_prompt=load_system_prompt(DEFAULT_SYSTEM_PROMPT),
            artifacts={"brief_json": paths.brief_json, "image_prompt": paths.prompt_file,
                       "raw_output": paths.research_api_raw, "brief_markdown": paths.brief_markdown},
        )
        actual = load_json(cache_manifest_path(paths.brief_json))
        if not isinstance(actual, dict) or any(actual.get(key) != value for key, value in expected.items()):
            raise ValueError("Completion record differs from current artifacts or policy")
        generator = load_json(paths.brief_json).get("generator", {})
        if not isinstance(generator, dict):
            raise ValueError("Missing reading metadata")
        review, recovery = generator.get("reading_review"), generator.get("prose_recovery")
        if (not isinstance(review, dict) or not isinstance(review.get("requirements"), list) or not review["requirements"]
                or not isinstance(recovery, dict) or recovery.get("policy_version") != expected["policy_version"]):
            raise ValueError("Missing successful current research reading")
    except (OSError, json.JSONDecodeError, KeyError, IndexError, TypeError, ValueError, ResearchApiError) as exc:
        raise WorkflowError(reason) from exc


def run_api_compose(paths: Paths, args: argparse.Namespace) -> None:
    """Generate research and daily content through the text gateway.

    Selection stays deterministic; only editorial reasoning crosses the model
    boundary. Existing artifacts are reused unless the caller asks to refresh
    them, which keeps retries idempotent and makes API spend explicit.
    """

    if not paths.source_selection.exists():
        run_select(paths, args)
    selection = load_json(paths.source_selection)
    if selection.get('event_editorial') and not (selection.get('selected_news') or selection.get('selected_research')):
        raise WorkflowError('No publishable events; retain the candidate pool and stop this issue without filler.')
    material_refreshed = False
    if (
        args.refresh_source_materials
        or not paths.source_materials.exists()
        or not source_material_matches_selection(paths)
    ):
        run_source_materials(paths, args, force=paths.source_materials.exists())
        material_refreshed = True

    refresh_content = args.refresh_api_content or material_refreshed

    has_research = selected_research_exists(paths)
    if has_research:
        research_outputs = [paths.brief_json, paths.prompt_file, paths.brief_markdown, paths.research_api_raw]
        existing_research = [path for path in research_outputs if path.exists()]
        if existing_research and len(existing_research) != len(research_outputs) and not refresh_content:
            raise WorkflowError(
                "Partial API research artifacts exist. Use --refresh-api-content to replace the incomplete run safely."
            )
        if refresh_content or not existing_research:
            research_cmd = [
                sys.executable,
                script("generate_research_brief_api.py"),
                "--materials",
                str(paths.source_materials),
                "--date",
                paths.run_date,
                "--out-json",
                str(paths.brief_json),
                "--prompt-out",
                str(paths.prompt_file),
                "--brief-markdown",
                str(paths.brief_markdown),
                "--raw-output",
                str(paths.research_api_raw),
            ]
            if args.ai_runtime_config:
                research_cmd.extend(["--runtime-config", str(args.ai_runtime_config)])
            if refresh_content:
                research_cmd.append("--force")
            run_cmd(research_cmd, dry_label="generate API research brief")
            assert_research_cache_current(paths)
        else:
            assert_research_cache_current(paths)
            print("[generate API research brief] reuse existing artifacts", flush=True)
    else:
        print("[generate API research brief] skipped: no research item met the selection bar", flush=True)

    content_outputs = [paths.content, paths.content_api_raw]
    existing_content = [path for path in content_outputs if path.exists()]
    if existing_content and len(existing_content) != len(content_outputs) and not refresh_content:
        raise WorkflowError(
            "Partial API content artifacts exist. Use --refresh-api-content to replace the incomplete run safely."
        )
    if refresh_content or not existing_content:
        content_cmd = [
            sys.executable,
            script("generate_daily_content_api.py"),
            "--materials",
            str(paths.source_materials),
            "--date",
            paths.run_date,
            "--output",
            str(paths.content),
            "--raw-output",
            str(paths.content_api_raw),
        ]
        if has_research:
            content_cmd.extend(["--brief-json", str(paths.brief_json)])
        if args.ai_runtime_config:
            content_cmd.extend(["--runtime-config", str(args.ai_runtime_config)])
        if args.content_sections:
            content_cmd.extend(["--content-sections", str(args.content_sections)])
        if args.editorial_policy:
            content_cmd.extend(["--editorial-policy", str(args.editorial_policy)])
        if refresh_content:
            content_cmd.append("--force")
        run_cmd(content_cmd, dry_label="generate API daily content")
    else:
        print("[generate API daily content] reuse existing artifacts", flush=True)
    artifacts: dict[str, Path] = {
        "source_selection": paths.source_selection,
        "source_materials": paths.source_materials,
        "content": paths.content,
        "content_api_raw": paths.content_api_raw,
    }
    if has_research:
        from research_cache import cache_manifest_path
        artifacts.update(
            {
                "research_brief": paths.brief_json,
                "research_brief_markdown": paths.brief_markdown,
                "research_api_raw": paths.research_api_raw,
                "research_image_prompt": paths.prompt_file,
                "research_completion": cache_manifest_path(paths.brief_json),
            }
        )
    record_phase(
        paths,
        action="api_compose",
        phase="CONTENT_READY",
        message=(
            "Research brief and email content generated through the configured text gateway."
            if has_research
            else "News-only email content generated; no research brief or image was required."
        ),
        artifacts=artifacts,
    )
    run_coverage_review(paths)


def run_coverage_review(paths: Paths, *, enforce: bool = False) -> None:
    from coverage_review import CoverageReviewError, assert_ready, refresh_review
    from daily_config import load_selection_policy
    policy = load_selection_policy()
    settings = policy.get("coverage_review", {})
    if not settings.get("enabled", False) or paths.run_date < settings.get("required_from", "9999-12-31"):
        return
    content = paths.normalized_content if enforce and paths.normalized_content.exists() else paths.content
    review = paths.run_dir / "high_value_coverage_review.json"
    if not paths.source_selection.exists() or not content.exists():
        if enforce:
            raise WorkflowError("Refusing prod send: missing source selection/content for coverage review.")
        print("Coverage review pending: selection/content inputs are incomplete.")
        return
    if (enforce and policy.get('editorial_engine') == 'event_v2'
            and paths.run_date >= policy.get('event_editorial_required_from', '9999-12-31')
            and not load_json(paths.source_selection).get('event_editorial')):
        raise WorkflowError('Refusing prod send: event-level evidence and scoring are required; legacy keyword scores cannot substitute.')
    try:
        report = (assert_ready if enforce else refresh_review)(paths.source_selection, content, review, policy)
    except (CoverageReviewError, OSError, ValueError) as exc:
        raise WorkflowError(f"Coverage review failed: {exc}") from exc
    print(f"Coverage review: {report['summary']['blocking_pending']} blocking, "
          f"{report['summary']['advisory_pending']} advisory; {review.with_suffix('.md')}")


def run_api_image(paths: Paths, args: argparse.Namespace) -> None:
    if not selected_research_exists(paths):
        print("[generate Image2 candidate] skipped: this is a news-only issue", flush=True)
        return
    if not paths.prompt_file.exists():
        raise WorkflowError(f"Missing research image prompt: {paths.prompt_file}. Run api-compose first.")
    image_outputs = [paths.api_image, paths.api_image_generation_manifest]
    existing_image = [path for path in image_outputs if path.exists()]
    if existing_image and len(existing_image) != len(image_outputs) and not args.refresh_api_image:
        raise WorkflowError("Partial Image2 artifacts exist. Use --refresh-api-image to replace the incomplete run safely.")
    if existing_image and not args.refresh_api_image:
        print("[generate Image2 candidate] reuse existing artifacts", flush=True)
    else:
        provider_id = default_api_image_provider_id()
        cmd = [
            sys.executable,
            script("generate_research_image.py"),
            "--provider",
            provider_id,
            "--prompt-file",
            str(paths.prompt_file),
            "--out",
            str(paths.api_image),
            "--generation-manifest",
            str(paths.api_image_generation_manifest),
        ]
        if args.ai_runtime_config:
            cmd.extend(["--runtime-config", str(args.ai_runtime_config)])
        fallback_runtime_config = args.image_fallback_runtime_config or DEFAULT_IMAGE_FALLBACK_RUNTIME_CONFIG
        if args.strict_image_route:
            cmd.append("--forbid-fallback")
        elif os.environ.get("AI_DAILY_IMAGE_FALLBACK_API_KEY", "").strip() and fallback_runtime_config.exists():
            cmd.extend(["--fallback-runtime-config", str(fallback_runtime_config)])
        if args.image_size:
            cmd.extend(["--size", args.image_size])
        if args.refresh_api_image:
            cmd.append("--force")
        run_cmd(cmd, dry_label="generate Image2 candidate")
    record_phase(
        paths,
        action="api_image_candidate",
        phase="CONTENT_READY",
        message="Image2 candidate generated; visual review and A/B approval remain required before production use.",
        artifacts={
            "research_image_candidate": paths.api_image,
            "research_image_generation_manifest": paths.api_image_generation_manifest,
        },
    )


def run_api_check(args: argparse.Namespace) -> None:
    cmd = [sys.executable, script("check_ai_runtime.py")]
    if args.ai_runtime_config:
        cmd.extend(["--runtime-config", str(args.ai_runtime_config)])
    if args.live_models:
        cmd.append("--live-models")
    if args.api_kind != "all":
        cmd.extend(["--kind", args.api_kind])
    run_cmd(cmd, dry_label="check API runtime")


def run_prepare(paths: Paths, args: argparse.Namespace) -> None:
    """Create all model-backed artifacts, leaving image promotion explicit."""

    run_api_compose(paths, args)
    if newspaper_text.text_only(paths.run_date):
        print("Text newspaper prepared; image generation is disabled by presentation policy.")
        return
    run_api_image(paths, args)
    print("\nAPI preparation complete.")
    print(f"Content JSON: {paths.content}")
    if selected_research_exists(paths):
        print(f"Image2 candidate: {paths.api_image}")
        print("Next: review the image, then run build with the candidate quality decision.")
    else:
        print("News-only issue: research brief and image generation were intentionally skipped.")
        print("Next: run api-preview or build to render the HTML preview.")


def run_api_preview(paths: Paths, args: argparse.Namespace) -> None:
    """Build a comparison HTML from the API candidate without approving it."""

    if newspaper_text.text_only(paths.run_date) or not content_has_research(paths):
        run_build(paths, args, write_ledger=False)
        return
    if not paths.api_image.exists() or not paths.api_image_generation_manifest.exists():
        raise WorkflowError("Missing Image2 candidate artifacts. Run prepare or api-image first.")
    args.image_source = paths.api_image
    args.source_kind = "generated"
    args.image_provider = image_provider_id_from_generation_manifest(paths)
    args.image_generation_manifest = paths.api_image_generation_manifest
    args.image_quality_decision = "pending"
    args.allow_candidate_image_provider = True
    run_build(paths, args, write_ledger=False)


def run_diagnose(paths: Paths, args: argparse.Namespace) -> None:
    if not paths.source_selection.exists():
        raise WorkflowError(
            f"Missing source selection: {paths.source_selection}. Run select first or pass --source-selection."
        )
    if not paths.content.exists():
        raise WorkflowError(f"Missing final content JSON: {paths.content}.")
    cmd = [
        sys.executable,
        script("selection_diagnostics.py"),
        "--date",
        paths.run_date,
        "--selection",
        str(paths.source_selection),
        "--content",
        str(paths.content),
        "--output",
        str(paths.selection_diagnostic),
        "--review",
        str(paths.selection_review),
    ]
    if args.overwrite_selection_review:
        cmd.append("--overwrite-review")
    run_cmd(cmd, dry_label="diagnose source selection")
    record_phase(
        paths,
        action="diagnose",
        phase="CONTENT_READY",
        message="Final content and source-selection diagnostic recorded.",
        artifacts={
            "source_selection": paths.source_selection,
            "content": paths.content,
            "selection_diagnostic": paths.selection_diagnostic,
            "selection_review": paths.selection_review,
        },
    )


def run_replay(paths: Paths, args: argparse.Namespace) -> None:
    cmd = [
        sys.executable,
        script("build_selection_replay.py"),
        "--runs-root",
        str(args.runs_root),
        "--output",
        str(args.replay_output),
    ]
    if args.include_pending_reviews:
        cmd.append("--include-pending")
    run_cmd(cmd, dry_label="build selection replay")


def record_built_html(paths: Paths, *, has_research: bool, text_only: bool) -> None:
    """A successful candidate build is not an image approval."""
    if has_research and not text_only:
        manifest = load_optional_json(paths.image_manifest)
        if manifest.get("quality_gate", {}).get("decision") != "approved":
            # Keep snapshots beside HTML so relative asset paths still resolve.
            digest = file_sha256(paths.html)
            snapshot = paths.html.with_name(f"{paths.html.stem}.comparison-{digest}{paths.html.suffix}")
            if snapshot.exists():
                if file_sha256(snapshot) != digest:
                    raise WorkflowError("Comparison HTML snapshot changed unexpectedly")
            else:
                with snapshot.open("xb") as stream:
                    stream.write(paths.html.read_bytes())
            record_phase(paths, action="stage_image_candidate", phase="CONTENT_READY",
                message="Candidate image staged; final image approval is pending.",
                artifacts={"research_image_manifest": paths.image_manifest})
            record_phase(paths, action="build_comparison_html",
                message="Candidate HTML snapshot saved before final review.",
                artifacts={"comparison_html": snapshot, "html": paths.html})
            return
        record_phase(paths, action="image_approved", phase="IMAGE_APPROVED",
            message="Approved research image manifest passed into the HTML build.",
            artifacts={"research_image_manifest": paths.image_manifest})
    record_phase(paths, action="build_html", phase="HTML_READY",
        message="HTML rendering completed." if has_research else "News-only HTML rendering completed.",
        artifacts={"html": paths.html})


def run_build(paths: Paths, args: argparse.Namespace, *, write_ledger: bool = False) -> None:
    if not paths.content.exists():
        raise WorkflowError(
            f"Missing content JSON: {paths.content}. Generate it from source selection before build."
        )

    assert_content_lineage(paths)
    from publication_dedup import reconcile
    reconcile(paths)
    if not content_has_research(paths) and args.image_source:
        from copy import copy
        args = copy(args)
        args.image_source = None
    run_coverage_review(paths)

    has_research = content_has_research(paths)
    text_only = newspaper_text.text_only(paths.run_date)
    if text_only and args.image_source:
        raise WorkflowError("Text newspaper does not accept a research image.")

    if args.image_source and not has_research:
        raise WorkflowError("This is a news-only issue; do not attach a research image.")

    if args.image_source:
        cmd = [
            sys.executable,
            script("finalize_ai_daily_workflow.py"),
            "--date",
            paths.run_date,
            "--content",
            str(paths.content),
            "--out-content",
            str(paths.content),
            "--brief-json",
            str(paths.brief_json),
            "--prompt-file",
            str(paths.prompt_file),
            "--image-source",
            str(args.image_source),
            "--source-kind",
            args.source_kind,
            "--provider-id",
            args.image_provider,
            "--manifest",
            str(paths.image_manifest),
            "--output",
            str(paths.html),
            "--normalized-output",
            str(paths.normalized_content),
            "--theme",
            args.theme,
            "--news-layout",
            args.news_layout,
            "--series",
            args.series,
            "--issue-ledger",
            str(args.issue_ledger),
        ]
        if args.image_provider_registry:
            cmd.extend(["--provider-registry", str(args.image_provider_registry)])
        if args.image_generation_manifest:
            cmd.extend(["--generation-manifest", str(args.image_generation_manifest)])
        if args.image_quality_decision:
            cmd.extend(["--quality-decision", args.image_quality_decision])
        if args.image_comparison_notes:
            cmd.extend(["--comparison-notes", args.image_comparison_notes])
        if args.image_editorial_review:
            cmd.extend(["--editorial-review", str(args.image_editorial_review)])
        if args.allow_candidate_image_provider:
            cmd.append("--allow-candidate-image-provider")
        if args.content_sections:
            cmd.extend(["--content-sections", str(args.content_sections)])
        if args.editorial_policy:
            cmd.extend(["--editorial-policy", str(args.editorial_policy)])
        if args.issue is not None:
            cmd.extend(["--issue", str(args.issue)])
        if not write_ledger:
            cmd.append("--no-write-ledger")
        if args.disable_feedback:
            cmd.append("--disable-feedback")
        run_cmd(cmd, dry_label="finalize image and build html")
        record_phase(
            paths,
            action="content_ready",
            phase="CONTENT_READY",
            message="Editorial content normalized for HTML rendering.",
            artifacts={
                "content": paths.content,
                "normalized_content": paths.normalized_content,
                "research_brief": paths.brief_json,
                "research_image_prompt": paths.prompt_file,
            },
        )
        record_built_html(paths, has_research=has_research, text_only=text_only)
        return

    if has_research and not text_only and not paths.image_manifest.exists():
        raise WorkflowError(
            f"Missing image manifest: {paths.image_manifest}. Pass --image-source after reviewing imagegen output."
        )

    cmd = [
        sys.executable,
        script("build_ai_daily_email.py"),
        "--content",
        str(paths.content),
        "--date",
        paths.run_date,
        "--theme",
        args.theme,
        "--news-layout",
        args.news_layout,
        "--output",
        str(paths.html),
        "--normalized-output",
        str(paths.normalized_content),
        "--series",
        args.series,
        "--issue-ledger",
        str(args.issue_ledger),
    ]
    if has_research and not text_only:
        cmd.extend(["--require-image-manifest", "--image-manifest", str(paths.image_manifest)])
    if args.issue is not None:
        cmd.extend(["--issue", str(args.issue)])
    if not write_ledger:
        cmd.append("--no-write-ledger")
    if args.disable_feedback:
        cmd.append("--disable-feedback")
    if args.allow_provided_research_image:
        cmd.append("--allow-provided-research-image")
    if args.allow_candidate_image_provider:
        cmd.append("--allow-candidate-image-provider")
    if args.image_provider_registry:
        cmd.extend(["--image-provider-registry", str(args.image_provider_registry)])
    if args.content_sections:
        cmd.extend(["--content-sections", str(args.content_sections)])
    if args.editorial_policy:
        cmd.extend(["--editorial-policy", str(args.editorial_policy)])
    run_cmd(cmd, dry_label="build html")
    record_phase(
        paths,
        action="content_ready",
        phase="CONTENT_READY",
        message="Editorial content normalized for HTML rendering.",
        artifacts={
            "content": paths.content,
            "normalized_content": paths.normalized_content,
        },
    )
    record_built_html(paths, has_research=has_research, text_only=text_only)


def run_package(paths: Paths, recipient_list: str, package_dir: Path, args: argparse.Namespace) -> Path:
    if not paths.html.exists():
        raise WorkflowError(f"Missing HTML: {paths.html}. Run build first.")
    cmd = [
        sys.executable,
        script("create_email_send_package.py"),
        str(paths.html),
        "--recipient-list",
        recipient_list,
        "--recipient-config",
        str(args.recipient_config),
        "--out-dir",
        str(package_dir),
        "--overwrite",
    ]
    settings: dict[str, Any] = {}
    try:
        settings = load_email_settings(args.email_settings)
        from_addr = args.from_addr or settings.get("from_addr")
        from_name = args.from_name or settings.get("from_name")
    except WorkflowError:
        from_addr = args.from_addr
        from_name = args.from_name
    if from_addr:
        cmd.extend(["--from-addr", str(from_addr)])
    if from_name:
        cmd.extend(["--from-name", str(from_name)])
    cmd.extend(weekly_conversation_args(settings, paths.run_date))
    feedback_settings = settings.get("feedback") if isinstance(settings.get("feedback"), dict) else {}
    resolved_feedback_base_url = feedback_base_url(settings, args)
    feedback_enabled = bool(feedback_settings.get("enabled", False) or resolved_feedback_base_url)
    if args.disable_feedback:
        cmd.append("--disable-feedback")
    elif feedback_enabled:
        if not resolved_feedback_base_url:
            raise WorkflowError("Feedback is enabled but no reachable feedback base URL is configured.")
        cmd.extend(["--feedback-base-url", resolved_feedback_base_url, "--feedback-issue", paths.run_date])
    if args.require_feedback:
        cmd.append("--require-feedback")
    run_cmd(cmd, dry_label=f"package {recipient_list}")
    eml = latest_eml(package_dir)
    snapshot_path = eml.parent / RECIPIENT_SNAPSHOT_NAME
    delivery = {
        "status": "packaged",
        **snapshot_delivery_metadata(snapshot_path),
    }
    record_phase(
        paths,
        action=f"package_{recipient_list}",
        phase="PACKAGED",
        message=f"{recipient_list} email package created with frozen recipient snapshot.",
        artifacts={
            f"{recipient_list}_package": package_dir,
            f"{recipient_list}_eml": eml,
            f"{recipient_list}_recipient_snapshot": snapshot_path,
        },
        delivery_channel=recipient_list,
        delivery=delivery,
    )
    return eml


def smtp_args(args: argparse.Namespace) -> list[str]:
    settings = load_email_settings(args.email_settings)
    smtp = settings["smtp"]
    host = args.smtp_host or smtp.get("host")
    port = args.smtp_port or smtp.get("port")
    security = args.smtp_security or smtp.get("security")
    user = args.smtp_user or smtp.get("user")
    from_addr = args.from_addr or settings.get("from_addr")
    from_name = args.from_name or settings.get("from_name")
    if not host or not port or not security:
        raise WorkflowError("SMTP host/port/security are required.")

    cmd = [
        "--smtp-host",
        str(host),
        "--smtp-port",
        str(port),
        "--smtp-security",
        str(security),
    ]
    if user:
        cmd.extend(["--smtp-user", str(user)])
    if from_addr:
        cmd.extend(["--from-addr", str(from_addr)])
    if from_name:
        cmd.extend(["--from-name", str(from_name)])
    if args.ask_password:
        cmd.append("--ask-password")
    if args.send:
        cmd.append("--send")
    return cmd


def run_send(paths: Paths, eml: Path, recipient_list: str, args: argparse.Namespace) -> None:
    delivery_state_path = paths.run_dir / f"smtp_{recipient_list}_delivery.json"
    cmd = [
        sys.executable,
        script("send_email_package_smtp.py"),
        str(eml),
        "--recipient-list",
        recipient_list,
        "--delivery-state",
        str(delivery_state_path),
        *smtp_args(args),
    ]
    snapshot_path = eml.parent / RECIPIENT_SNAPSHOT_NAME
    if snapshot_path.exists():
        cmd.extend(["--recipient-snapshot", str(snapshot_path)])
    run_cmd(cmd, dry_label=f"send {recipient_list}")
    delivery = {
        "status": "sent" if args.send else "dry_run",
        **snapshot_delivery_metadata(snapshot_path),
    }
    phase = None
    if args.send:
        phase = "TESTED" if recipient_list == "test" else "SENT"
    record_phase(
        paths,
        action=f"smtp_{recipient_list}",
        phase=phase,
        message=("SMTP send completed." if args.send else "SMTP dry-run completed."),
        artifacts={f"{recipient_list}_eml": eml, f"{recipient_list}_recipient_snapshot": snapshot_path},
        delivery_channel=recipient_list,
        delivery=delivery,
    )


def run_test(paths: Paths, args: argparse.Namespace) -> None:
    if not args.skip_build:
        run_build(paths, args, write_ledger=False)
    eml = run_package(paths, "test", paths.package_test, args)
    run_send(paths, eml, "test", args)


def commit_issue_ledger(paths: Paths, args: argparse.Namespace) -> int:
    if args.series == "none":
        print("Issue ledger unchanged because --series none.")
        return 0
    ledger = load_ledger(args.issue_ledger)
    issue = assign_issue(ledger, parse_run_date(paths.run_date), args.series, args.issue)
    write_json_object(args.issue_ledger, ledger)
    print(f"Committed {args.series} issue {issue} for {paths.run_date}: {args.issue_ledger}")
    return issue


def commit_send_ledger(paths: Paths, args: argparse.Namespace, eml: Path, issue: int) -> dict[str, Any]:
    ledger = load_optional_json(args.send_ledger)
    prod = ledger.setdefault("prod", {})
    if not isinstance(prod, dict):
        raise WorkflowError(f"{args.send_ledger} prod section must be an object.")
    snapshot_path = eml.parent / RECIPIENT_SNAPSHOT_NAME
    try:
        delivery = load_recipient_snapshot(snapshot_path)
    except RecipientError as exc:
        raise WorkflowError(f"Missing valid recipient snapshot for send record: {exc}") from exc
    record = {
        "sent_at": datetime.now().astimezone().isoformat(),
        "published_items": publication_items(load_json(paths.normalized_content)),
        "published_content_sha256": file_sha256(paths.normalized_content),
        "series": args.series,
        "issue": issue,
        "content": str(paths.content),
        "normalized_content": str(paths.normalized_content),
        "html": str(paths.html),
        "eml": str(eml),
        "recipient_list": "prod",
        "recipient_delivery": {
            "recipient_group": delivery.get("recipient_group"),
            "recipient_count": delivery["recipient_count"],
            "fingerprint": delivery["fingerprint"],
            "snapshot": str(snapshot_path),
        },
    }
    prod[paths.run_date] = record
    write_json_object(args.send_ledger, ledger)
    try:
        sync_backlog(paths.run_dir.parent, paths.run_date, send_ledger=args.send_ledger)
    except (OSError, ValueError) as exc:
        # SMTP has succeeded and the immutable publication snapshot is saved.
        # A derived backlog failure must never turn this into a send retry.
        print(f"Warning: backlog sync deferred until next selection: {exc}")
    print(f"Committed prod send record for {paths.run_date}: {args.send_ledger}")
    return record


def sync_feedback_delivery(
    paths: Paths,
    args: argparse.Namespace,
    record: dict[str, Any],
) -> None:
    if args.disable_feedback:
        return
    settings = load_email_settings(args.email_settings)
    base_url = feedback_base_url(settings, args)
    if not base_url:
        return
    if not os.environ.get("AI_DAILY_FEEDBACK_ADMIN_KEY"):
        print(
            "Warning: feedback links are enabled, but delivery totals were not synced because "
            "AI_DAILY_FEEDBACK_ADMIN_KEY is missing."
        )
        return
    delivery = record.get("recipient_delivery")
    delivery = delivery if isinstance(delivery, dict) else {}
    group = delivery.get("recipient_group")
    group = group if isinstance(group, dict) else {}
    group_label = str(group.get("name") or group.get("key") or "prod")
    cmd = [
        sys.executable,
        script("sync_feedback_delivery.py"),
        "--issue-id",
        paths.run_date,
        "--series",
        str(record.get("series") or ""),
        "--sent-at",
        str(record.get("sent_at") or ""),
        "--recipient-count",
        str(delivery.get("recipient_count") or 0),
        "--recipient-group",
        group_label,
        "--recipient-fingerprint",
        str(delivery.get("fingerprint") or ""),
        "--base-url",
        base_url,
    ]
    if int(record.get("issue") or 0) > 0:
        cmd.extend(["--issue-number", str(record["issue"])])
    result = subprocess.run(cmd, text=True)
    if result.returncode != 0:
        print(
            "Warning: the email was sent and recorded locally, but feedback delivery metadata "
            "could not be synced. Re-run sync_feedback_delivery.py after the service recovers."
        )
        return
    record_phase(
        paths,
        action="sync_feedback_delivery",
        message="Production delivery denominator synced to feedback analytics.",
    )


def has_existing_prod_send(paths: Paths, args: argparse.Namespace) -> bool:
    if is_prod_sent(load_workflow_state(paths.workflow_state)):
        return True
    ledger = load_optional_json(args.send_ledger)
    return isinstance(ledger.get("prod"), dict) and paths.run_date in ledger["prod"]


def check_production_email(paths: Paths, args: argparse.Namespace) -> None:
    if args.send:
        from publication_dedup import reconcile
        if reconcile(paths):
            from copy import copy
            build_args = copy(args)
            build_args.image_source = None
            build_args.allow_candidate_image_provider = True
            run_build(paths, build_args, write_ledger=False)
    if not args.skip_build:
        run_build(paths, args, write_ledger=False)
    if args.send:
        # Cached HTML is a supported send path, so build-time validation alone
        # cannot protect production. Check the exact normalized build input
        # again before package creation or any SMTP activity.
        try:
            normalized = load_json(paths.normalized_content)
            if not isinstance(normalized, dict):
                raise ValidationError("Normalized content must be an object; rebuild first.")
            if normalized.get("date") != format_date_cn(parse_run_date(paths.run_date)):
                raise ValidationError("Normalized content date differs from the requested issue; rebuild first.")
            validate_content(
                normalized,
                editorial_policy=load_editorial_policy(getattr(args, "editorial_policy", None)),
            )
            source_signature = normalized.get("presentation", {}).get("source_content_sha256")
            if not source_signature or source_signature != file_sha256(paths.content):
                raise ValidationError("Source content changed or lacks build signature; rebuild first.")
        except (OSError, json.JSONDecodeError, DailyConfigError, ValidationError) as exc:
            raise WorkflowError(f"Refusing prod send: content validation failed: {exc}") from exc
        assert_content_lineage(paths)
    if args.send and newspaper_text.enabled(paths.run_date):
        try:
            newspaper_text.assert_text_send(paths.html, normalized, paths.run_date)
        except (ValueError, OSError) as exc:
            raise WorkflowError(f"Refusing prod send: text edition check failed: {exc}") from exc
    if args.send and newspaper_text.research(normalized) and not newspaper_text.text_only(paths.run_date):
        try:
            verify_research_image_manifests(
                normalized,
                [paths.image_manifest],
                Path.cwd(),
                require_imagegen=False,
                allow_candidate_provider=False,
                provider_registry_path=args.image_provider_registry,
            )
        except (OSError, json.JSONDecodeError, ImageProviderError, LineageError) as exc:
            raise WorkflowError(f"Refusing prod send: research image quality gate failed: {exc}") from exc
    if args.send:
        review_current_email(paths, args)


def run_prod(paths: Paths, args: argparse.Namespace, *, prechecked: bool = False) -> None:
    if args.send and not args.confirm_prod:
        raise WorkflowError("Refusing prod send without --confirm-prod.")
    if args.send and args.allow_candidate_image_provider:
        raise WorkflowError("Candidate image providers are comparison-only and cannot be sent with prod --send.")
    if args.send and not args.allow_resend_prod and has_existing_prod_send(paths, args):
        raise WorkflowError(
            f"Refusing duplicate prod send for {paths.run_date}. Use --allow-resend-prod only for an intentional resend."
        )
    if args.send:
        from email_quality_control import policy as quality_policy
        if quality_policy().get("ai_review_enabled") is not True:
            raise EmailQualityError("AI email self-review is disabled pending gateway data authorization")
        if not prechecked:
            recover(paths, args, scope="prod", operation=lambda: check_production_email(paths, args),
                    repair=lambda reason: repair_email(paths, args, reason), reset=getattr(args, "rebuild", False))
    else:
        check_production_email(paths, args)
    eml = run_package(paths, "prod", paths.package_prod, args)
    if args.send:
        record_phase(
            paths,
            action="approve_prod_send",
            phase="APPROVED_TO_SEND",
            message="Prod send explicitly approved by --confirm-prod.",
            artifacts={"prod_eml": eml, "prod_recipient_snapshot": eml.parent / RECIPIENT_SNAPSHOT_NAME},
            delivery_channel="prod",
            delivery={"status": "approved", **snapshot_delivery_metadata(eml.parent / RECIPIENT_SNAPSHOT_NAME)},
        )
    try:
        if args.send:
            from email_quality_control import artifact_hashes, validate_packaged_feedback
            receipt = load_json(paths.run_dir / "email_self_review.json")
            if receipt.get("status") != "passed" or receipt.get("hashes") != artifact_hashes(paths):
                raise WorkflowError("Email changed after self-review; refusing SMTP delivery.")
            validate_packaged_feedback(eml)
        run_send(paths, eml, "prod", args)
    except (OSError, ValueError, WorkflowError, EmailQualityError, subprocess.CalledProcessError) as exc:
        if args.send:
            from email_quality_control import redact
            report = {"scope": "delivery", "attempts": [{"number": 1, "stage": "smtp", "error": redact(exc)}]}
            write_json_object(paths.run_dir / "delivery_recovery.json", report)
            try:
                alert = send_failure_alert(paths, args, report)
                print(f"Delivery failure alert: {alert['status']}")
            except Exception as alert_error:
                print(f"Delivery failure alert failed: {redact(alert_error)}")
        raise
    if args.send:
        issue = commit_issue_ledger(paths, args)
        record = commit_send_ledger(paths, args, eml, issue)
        sync_feedback_delivery(paths, args, record)


def execute_workflow(args) -> None:
    # Packaging and SMTP-only runs still need the shared local configuration.
    load_env_file()
    if not args.image_provider:
        if args.source_kind == "imagegen":
            args.image_provider = "codex_imagegen"
        elif args.source_kind == "provided":
            args.image_provider = "manual_provided"
        else:
            raise SystemExit("Workflow failed: --image-provider is required when --source-kind generated.")
    paths = apply_overrides(default_paths(args.date), args)
    if args.action in {"select", "prepare", "prepare-reviewed", "publish-reviewed", "preview", "api-compose"}:
        from ai_gateway_client import load_runtime_config
        from managed_text_model import pin_route, state_dir
        config = load_runtime_config(getattr(args, "ai_runtime_config", None))
        if state_dir(config) is not None:
            route = pin_route(config)
            print("Text model route: " + json.dumps(route, ensure_ascii=False), flush=True)
    try:
        if args.action == "status":
            run_status(paths, args)
        elif args.action == "state":
            run_state(paths, args)
        elif args.action == "recipients":
            run_recipients(args)
        elif args.action == "check-api":
            run_api_check(args)
        elif args.action == "select":
            ensure_workflow_state(paths.workflow_state, paths.run_date)
            run_select(paths, args)
        elif args.action == "prepare":
            ensure_workflow_state(paths.workflow_state, paths.run_date)
            run_prepare(paths, args)
        elif args.action == "prepare-reviewed":
            ensure_workflow_state(paths.workflow_state, paths.run_date)
            run_prepare_reviewed(paths, args)
        elif args.action == "publish-reviewed":
            from automatic_delivery import run_publish_reviewed
            ensure_workflow_state(paths.workflow_state, paths.run_date)
            run_publish_reviewed(paths, args)
        elif args.action == "preview":
            ensure_workflow_state(paths.workflow_state, paths.run_date)
            run_api_preview(paths, args)
        elif args.action == "api-compose":
            ensure_workflow_state(paths.workflow_state, paths.run_date)
            run_api_compose(paths, args)
        elif args.action == "api-image":
            ensure_workflow_state(paths.workflow_state, paths.run_date)
            run_api_image(paths, args)
        elif args.action == "diagnose":
            ensure_workflow_state(paths.workflow_state, paths.run_date)
            run_diagnose(paths, args)
        elif args.action == "replay":
            run_replay(paths, args)
        elif args.action == "build":
            ensure_workflow_state(paths.workflow_state, paths.run_date)
            run_build(paths, args, write_ledger=False)
        elif args.action == "package-test":
            ensure_workflow_state(paths.workflow_state, paths.run_date)
            run_package(paths, "test", paths.package_test, args)
        elif args.action == "package-prod":
            ensure_workflow_state(paths.workflow_state, paths.run_date)
            run_package(paths, "prod", paths.package_prod, args)
        elif args.action == "test":
            ensure_workflow_state(paths.workflow_state, paths.run_date)
            run_test(paths, args)
        elif args.action == "prod":
            ensure_workflow_state(paths.workflow_state, paths.run_date)
            run_prod(paths, args)
    except (OSError, subprocess.CalledProcessError, WorkflowError, WorkflowStateError, EmailQualityError) as exc:
        if args.action not in {"status", "recipients", "check-api", "replay"}:
            try:
                record_run_failure(paths.workflow_state, paths.run_date, action=args.action, error=exc)
            except (OSError, WorkflowStateError):
                pass
        raise SystemExit(f"Workflow failed: {exc}") from exc


def main() -> None:
    args = parse_args()
    # Existing installations may not allow the service user to replace the
    # systemd unit. Upgrade its established command at the application boundary.
    if args.action in {"prepare", "prepare-reviewed"} and getattr(args, "scheduled", False):
        from email_quality_control import policy as quality_policy
        if quality_policy().get("automatic_send_enabled") is True:
            args.action = "publish-reviewed"
        elif quality_policy().get("ai_review_enabled") is True:
            args.action = "prepare-reviewed"
    guarded = args.action in {"prepare", "prepare-reviewed", "publish-reviewed", "preview"} or (args.action == "prod" and args.send)
    if not guarded:
        execute_workflow(args)
        return
    try:
        workday, reason = workday_decision(args.date, args.work_calendar)
        print(f"Work calendar {args.date}: {reason}")
        if not workday:
            print("Skipped: non-working day.")
            return
        if args.scheduled:
            from datetime import time
            from work_calendar import SHANGHAI
            now = datetime.now(SHANGHAI)
            if args.date != now.date().isoformat() or now.time() >= time(12):
                print("Skipped: scheduled preparation must finish starting before 12:00 on the current day.")
                return
        paths = apply_overrides(default_paths(args.date), args)
        with run_lock(paths.run_dir):
            if args.action in {"prepare", "prepare-reviewed", "publish-reviewed", "preview"} and has_existing_prod_send(paths, args):
                print("Skipped: this issue has already been published.")
                return
            if args.action == "preview" and args.scheduled and not args.rebuild:
                from email_quality_control import artifact_hashes
                try:
                    reviewed = load_optional_json(paths.run_dir / "email_self_review.json")
                    if reviewed.get("status") == "passed" and reviewed.get("hashes") == artifact_hashes(paths):
                        print("Skipped: scheduled preview already rendered and AI-reviewed.")
                        return
                except (OSError, ValueError):
                    pass
            marker = paths.run_dir / "preparation_completed.json"
            state = load_workflow_state(paths.workflow_state) or {}
            if args.action in {"prepare", "prepare-reviewed", "publish-reviewed"}:
                if args.action == "prepare" and not args.rebuild and (marker.exists() or (paths.html.exists() and state.get('outcome') != 'FAILED' and state.get('phase') in {'HTML_READY', 'PACKAGED', 'TESTED', 'APPROVED_TO_SEND'})):
                    print("Skipped: preparation is complete; use --rebuild for an intentional revision.")
                    return
                if marker.exists():
                    marker.unlink()
                if not args.start:
                    cutoff = previous_publication_cutoff(paths.run_dir.parent, args.date)
                    if cutoff:
                        args.start = cutoff
                        args.allow_wide_news_window = True
                        print(f"Source window resumes from last publication cutoff: {cutoff}")
            execute_workflow(args)
            if args.action in {"prepare", "prepare-reviewed", "publish-reviewed"}:
                write_json_object(marker, {"run_date": args.date, "completed_at": datetime.now().isoformat()})
    except (OSError, ValueError, KeyError, TypeError, WorkflowStateError) as exc:
        raise SystemExit(f"Workflow blocked: {exc}") from exc


if __name__ == "__main__":
    main()
