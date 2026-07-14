#!/usr/bin/env python3
"""One-command runner for the AI daily workflow.

This orchestrates deterministic steps:
- optional AIHot source selection
- HTML build/finalize from prepared JSON and reviewed research image
- email package generation with CID inline images
- SMTP dry-run or send for test/prod recipients

The LLM/editor and image_gen steps remain explicit Codex steps because they are
not callable from a local Python process. This runner makes every deterministic
step around them repeatable and guarded.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from ai_daily_common import write_json_object
from build_ai_daily_email import assign_issue, load_ledger, parse_run_date
from email_recipients import (
    DEFAULT_RECIPIENT_CONFIG,
    RECIPIENT_SNAPSHOT_NAME,
    RecipientError,
    delivery_fingerprint,
    load_recipient_registry,
    load_recipient_snapshot,
)
from verify_research_image_lineage import LineageError, verify_lineage
from image_provider_registry import ImageProviderError


SCRIPT_DIR = Path(__file__).resolve().parent
WORKFLOW_DIR = SCRIPT_DIR.parent
DEFAULT_EMAIL_SETTINGS = WORKFLOW_DIR / "config" / "email_settings.json"


class WorkflowError(Exception):
    pass


@dataclass
class Paths:
    run_date: str
    run_dir: Path
    source_selection: Path
    content: Path
    normalized_content: Path
    brief_json: Path
    prompt_file: Path
    imagegen_start_marker: Path
    image_manifest: Path
    html: Path
    package_test: Path
    package_prod: Path


def today_iso() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def script(name: str) -> str:
    return str(SCRIPT_DIR / name)


def default_paths(run_date: str) -> Paths:
    run_dir = Path("daily_runs") / run_date
    return Paths(
        run_date=run_date,
        run_dir=run_dir,
        source_selection=run_dir / f"source_selection_auto_{run_date}.json",
        content=run_dir / "ai_daily_email_content.json",
        normalized_content=run_dir / "ai_daily_email_content.normalized.json",
        brief_json=run_dir / "research_brief.parsed.json",
        prompt_file=run_dir / "research_image_prompt.txt",
        imagegen_start_marker=run_dir / "imagegen_start.marker",
        image_manifest=run_dir / "research_image_manifest.json",
        html=Path("output") / f"ai_daily_email_{run_date}.html",
        package_test=Path("email_packages") / run_date / "test",
        package_prod=Path("email_packages") / run_date / "prod",
    )


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def load_email_settings(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise WorkflowError(f"Missing email settings: {path}")
    data = load_json(path)
    smtp = data.get("smtp")
    if not isinstance(smtp, dict):
        raise WorkflowError(f"{path} must contain smtp settings.")
    return data


def load_optional_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return load_json(path)


def run_cmd(cmd: list[str], *, dry_label: str | None = None) -> None:
    if dry_label:
        print(f"\n[{dry_label}]", flush=True)
    print("+ " + " ".join(cmd), flush=True)
    subprocess.run(cmd, check=True)


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
    paths.content = args.content or paths.content
    paths.normalized_content = args.normalized_content or paths.normalized_content
    paths.brief_json = args.brief_json or paths.brief_json
    paths.prompt_file = args.prompt_file or paths.prompt_file
    paths.image_manifest = args.image_manifest or paths.image_manifest
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
        ("content", paths.content),
        ("normalized_content", paths.normalized_content),
        ("brief_json", paths.brief_json),
        ("prompt_file", paths.prompt_file),
        ("imagegen_start_marker", paths.imagegen_start_marker),
        ("image_manifest", paths.image_manifest),
        ("html", paths.html),
        ("package_test", paths.package_test),
        ("package_prod", paths.package_prod),
    ]
    print(f"AI Daily status: {paths.run_date}")
    for label, path in checks:
        print(f"{label:22} {file_status(path):18} {path}")


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
    run_cmd(cmd, dry_label="select sources")


def run_build(paths: Paths, args: argparse.Namespace, *, write_ledger: bool = False) -> None:
    if not paths.content.exists():
        raise WorkflowError(
            f"Missing content JSON: {paths.content}. Generate it from source selection before build."
        )

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
        if args.allow_candidate_image_provider:
            cmd.append("--allow-candidate-image-provider")
        if args.issue is not None:
            cmd.extend(["--issue", str(args.issue)])
        if not write_ledger:
            cmd.append("--no-write-ledger")
        run_cmd(cmd, dry_label="finalize image and build html")
        return

    if not paths.image_manifest.exists():
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
        "--require-image-manifest",
        "--image-manifest",
        str(paths.image_manifest),
        "--series",
        args.series,
        "--issue-ledger",
        str(args.issue_ledger),
    ]
    if args.issue is not None:
        cmd.extend(["--issue", str(args.issue)])
    if not write_ledger:
        cmd.append("--no-write-ledger")
    if args.allow_provided_research_image:
        cmd.append("--allow-provided-research-image")
    if args.allow_candidate_image_provider:
        cmd.append("--allow-candidate-image-provider")
    if args.image_provider_registry:
        cmd.extend(["--image-provider-registry", str(args.image_provider_registry)])
    run_cmd(cmd, dry_label="build html")


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
    try:
        settings = load_email_settings(args.email_settings)
        from_addr = args.from_addr or settings.get("from_addr")
    except WorkflowError:
        from_addr = args.from_addr
    if from_addr:
        cmd.extend(["--from-addr", str(from_addr)])
    run_cmd(cmd, dry_label=f"package {recipient_list}")
    return latest_eml(package_dir)


def smtp_args(args: argparse.Namespace) -> list[str]:
    settings = load_email_settings(args.email_settings)
    smtp = settings["smtp"]
    host = args.smtp_host or smtp.get("host")
    port = args.smtp_port or smtp.get("port")
    security = args.smtp_security or smtp.get("security")
    user = args.smtp_user or smtp.get("user")
    from_addr = args.from_addr or settings.get("from_addr")
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
    if args.ask_password:
        cmd.append("--ask-password")
    if args.send:
        cmd.append("--send")
    return cmd


def run_send(eml: Path, recipient_list: str, args: argparse.Namespace) -> None:
    cmd = [
        sys.executable,
        script("send_email_package_smtp.py"),
        str(eml),
        "--recipient-list",
        recipient_list,
        *smtp_args(args),
    ]
    snapshot_path = eml.parent / RECIPIENT_SNAPSHOT_NAME
    if snapshot_path.exists():
        cmd.extend(["--recipient-snapshot", str(snapshot_path)])
    run_cmd(cmd, dry_label=f"send {recipient_list}")


def run_test(paths: Paths, args: argparse.Namespace) -> None:
    if not args.skip_build:
        run_build(paths, args, write_ledger=False)
    eml = run_package(paths, "test", paths.package_test, args)
    run_send(eml, "test", args)


def commit_issue_ledger(paths: Paths, args: argparse.Namespace) -> int:
    if args.series == "none":
        print("Issue ledger unchanged because --series none.")
        return 0
    ledger = load_ledger(args.issue_ledger)
    issue = assign_issue(ledger, parse_run_date(paths.run_date), args.series, args.issue)
    write_json_object(args.issue_ledger, ledger)
    print(f"Committed {args.series} issue {issue} for {paths.run_date}: {args.issue_ledger}")
    return issue


def commit_send_ledger(paths: Paths, args: argparse.Namespace, eml: Path, issue: int) -> None:
    ledger = load_optional_json(args.send_ledger)
    prod = ledger.setdefault("prod", {})
    if not isinstance(prod, dict):
        raise WorkflowError(f"{args.send_ledger} prod section must be an object.")
    snapshot_path = eml.parent / RECIPIENT_SNAPSHOT_NAME
    try:
        delivery = load_recipient_snapshot(snapshot_path)
    except RecipientError as exc:
        raise WorkflowError(f"Missing valid recipient snapshot for send record: {exc}") from exc
    prod[paths.run_date] = {
        "sent_at": datetime.now().astimezone().isoformat(),
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
    write_json_object(args.send_ledger, ledger)
    print(f"Committed prod send record for {paths.run_date}: {args.send_ledger}")


def run_prod(paths: Paths, args: argparse.Namespace) -> None:
    if args.send and not args.confirm_prod:
        raise WorkflowError("Refusing prod send without --confirm-prod.")
    if args.send and args.allow_candidate_image_provider:
        raise WorkflowError("Candidate image providers are comparison-only and cannot be sent with prod --send.")
    if not args.skip_build:
        run_build(paths, args, write_ledger=False)
    if args.send:
        content_path = paths.normalized_content if paths.normalized_content.exists() else paths.content
        try:
            verify_lineage(
                load_json(content_path),
                paths.image_manifest,
                Path.cwd(),
                require_imagegen=False,
                allow_candidate_provider=False,
                provider_registry_path=args.image_provider_registry,
            )
        except (OSError, json.JSONDecodeError, ImageProviderError, LineageError) as exc:
            raise WorkflowError(f"Refusing prod send: research image quality gate failed: {exc}") from exc
    eml = run_package(paths, "prod", paths.package_prod, args)
    run_send(eml, "prod", args)
    if args.send:
        issue = commit_issue_ledger(paths, args)
        commit_send_ledger(paths, args, eml, issue)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run AI daily deterministic workflow steps.")
    parser.add_argument(
        "action",
        choices=["status", "recipients", "select", "build", "package-test", "package-prod", "test", "prod"],
        help=(
            "status: show paths; recipients: inspect recipient group metadata; "
            "select: AIHot + BestBlogs source selection; build: render HTML; "
            "package-test/prod: create .eml only; test/prod: package and SMTP dry-run/send."
        ),
    )
    parser.add_argument("--date", default=today_iso(), help="Run date YYYY-MM-DD.")
    parser.add_argument("--start", help="Source selection window start ISO datetime.")
    parser.add_argument("--end", help="Source selection window end ISO datetime.")
    parser.add_argument("--previous-content", type=Path, action="append", help="Previous content JSON for dedupe.")
    parser.add_argument("--research-count", type=int, help="Number of selected research items.")
    parser.add_argument(
        "--min-news-count",
        type=int,
        help="Minimum selected industry-news count. The selector has no maximum cap.",
    )
    parser.add_argument(
        "--news-count",
        type=int,
        help="Compatibility alias for --min-news-count; no longer a maximum cap.",
    )
    parser.add_argument("--paper-lookback-hours", type=int, help="Paper feed lookback hours for source selection.")
    parser.add_argument(
        "--news-lookback-hours",
        type=int,
        help="Maximum ordinary-news lookback hours. Research paper lookback is separate.",
    )
    parser.add_argument(
        "--allow-wide-news-window",
        action="store_true",
        help="Allow ordinary news to use the full --start/--end window. Use only for explicit backfills.",
    )
    parser.add_argument(
        "--no-aihot-daily",
        action="store_true",
        help="Disable the optional AIHot Daily editorial prefilter for this source-selection run.",
    )
    parser.add_argument(
        "--no-bestblogs",
        action="store_true",
        help="Disable BestBlogs' high-score AI RSS supplement for this source-selection run.",
    )
    parser.add_argument(
        "--bestblogs-max-items",
        type=int,
        help="Maximum in-window BestBlogs items to resolve to primary sources.",
    )
    parser.add_argument("--source-selection", type=Path, help="Override source selection output path.")
    parser.add_argument("--content", type=Path, help="Override content JSON path.")
    parser.add_argument("--normalized-content", type=Path, help="Override normalized content JSON path.")
    parser.add_argument("--brief-json", type=Path, help="Override parsed research brief JSON path.")
    parser.add_argument("--prompt-file", type=Path, help="Override research image prompt path.")
    parser.add_argument("--image-manifest", type=Path, help="Override research image manifest path.")
    parser.add_argument("--html", type=Path, help="Override HTML output/input path.")
    parser.add_argument("--test-package-dir", type=Path, help="Override test email package directory.")
    parser.add_argument("--prod-package-dir", type=Path, help="Override prod email package directory.")
    parser.add_argument("--image-source", type=Path, help="Reviewed image source to stage before build.")
    parser.add_argument("--source-kind", choices=["imagegen", "generated", "provided"], default="imagegen")
    parser.add_argument(
        "--image-provider",
        help="Image provider id. Defaults to codex_imagegen for native images and manual_provided for manual images.",
    )
    parser.add_argument("--image-provider-registry", type=Path, help="Image provider registry path.")
    parser.add_argument("--image-generation-manifest", type=Path, help="API image generator manifest.")
    parser.add_argument("--image-quality-decision", choices=["approved", "pending", "rejected"])
    parser.add_argument("--image-comparison-notes", help="Manual A/B comparison notes for a candidate provider.")
    parser.add_argument(
        "--allow-candidate-image-provider",
        action="store_true",
        help="Permit non-baseline image providers for comparison/test HTML only.",
    )
    parser.add_argument("--allow-provided-research-image", action="store_true")
    parser.add_argument("--theme", default="clay_orange")
    parser.add_argument("--news-layout", default="paper")
    parser.add_argument("--series", choices=["trial", "official", "none"], default="trial")
    parser.add_argument("--issue", type=int)
    parser.add_argument(
        "--issue-ledger",
        type=Path,
        default=Path("daily_runs") / "issue_ledger.json",
        help="Issue ledger JSON path. Only prod --send commits by default.",
    )
    parser.add_argument(
        "--send-ledger",
        type=Path,
        default=Path("daily_runs") / "send_ledger.json",
        help="Successful prod-send ledger used by source selection dedupe.",
    )
    parser.add_argument("--skip-build", action="store_true", help="For test/prod actions, use existing HTML.")
    parser.add_argument(
        "--recipient-config",
        type=Path,
        default=DEFAULT_RECIPIENT_CONFIG,
        help="Local recipient registry JSON. Future deployments replace this adapter with a database service.",
    )
    parser.add_argument("--email-settings", type=Path, default=DEFAULT_EMAIL_SETTINGS)
    parser.add_argument("--from-addr")
    parser.add_argument("--smtp-host")
    parser.add_argument("--smtp-port", type=int)
    parser.add_argument("--smtp-security", choices=["ssl", "starttls", "none"])
    parser.add_argument("--smtp-user")
    parser.add_argument("--ask-password", action="store_true")
    parser.add_argument("--send", action="store_true", help="Actually send email. Default is SMTP dry-run.")
    parser.add_argument("--confirm-prod", action="store_true", help="Required with prod --send.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if not args.image_provider:
        if args.source_kind == "imagegen":
            args.image_provider = "codex_imagegen"
        elif args.source_kind == "provided":
            args.image_provider = "manual_provided"
        else:
            raise SystemExit("Workflow failed: --image-provider is required when --source-kind generated.")
    paths = apply_overrides(default_paths(args.date), args)
    try:
        if args.action == "status":
            run_status(paths, args)
        elif args.action == "recipients":
            run_recipients(args)
        elif args.action == "select":
            run_select(paths, args)
        elif args.action == "build":
            run_build(paths, args, write_ledger=False)
        elif args.action == "package-test":
            run_package(paths, "test", paths.package_test, args)
        elif args.action == "package-prod":
            run_package(paths, "prod", paths.package_prod, args)
        elif args.action == "test":
            run_test(paths, args)
        elif args.action == "prod":
            run_prod(paths, args)
    except (OSError, subprocess.CalledProcessError, WorkflowError) as exc:
        raise SystemExit(f"Workflow failed: {exc}") from exc


if __name__ == "__main__":
    main()
