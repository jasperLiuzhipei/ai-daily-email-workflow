"""Command-line interface for the AI Daily workflow; no execution side effects."""
from __future__ import annotations

import argparse
from pathlib import Path

from email_recipients import DEFAULT_RECIPIENT_CONFIG
from render_ai_daily_email import DEFAULT_THEME
from work_calendar import DEFAULT_CALENDAR, today_iso
from workflow_context import DEFAULT_EMAIL_SETTINGS


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the deployable AI Daily workflow.")
    parser.add_argument(
        "action",
        choices=[
            "status",
            "state",
            "recipients",
            "check-api",
            "select",
            "prepare",
            "prepare-reviewed",
            "publish-reviewed",
            "preview",
            "api-compose",
            "api-image",
            "diagnose",
            "replay",
            "build",
            "package-test",
            "package-prod",
            "test",
            "prod",
        ],
        help=(
            "status: show paths; state: show/create run manifest; recipients: inspect recipient group metadata; "
            "check-api: validate the configured text/Image2 API runtime; select: AIHot + BestBlogs source selection; "
            "prepare: select, fetch, generate API content, and create an Image2 candidate; "
            "preview: stage the Image2 candidate and render comparison HTML without production approval; "
            "api-compose: fetch source material and generate research/content through the text API; "
            "api-image: generate an Image2 candidate through the image API; diagnose: compare selection to final content; "
            "replay: compile reviewed diagnostics; build: render HTML; "
            "package-test/prod: create .eml only; test/prod: package and SMTP dry-run/send."
        ),
    )
    parser.add_argument("--date", default=today_iso(), help="Run date YYYY-MM-DD.")
    parser.add_argument("--work-calendar", type=Path, default=DEFAULT_CALENDAR)
    parser.add_argument("--scheduled", action="store_true", help="Apply same-day 12:00 Shanghai preparation catch-up cutoff.")
    parser.add_argument("--rebuild", action="store_true", help="Explicitly rebuild a completed, unpublished preparation.")
    parser.add_argument("--start", help="Source selection window start ISO datetime.")
    parser.add_argument("--end", help="Source selection window end ISO datetime.")
    parser.add_argument("--previous-content", type=Path, action="append", help="Previous content JSON for dedupe.")
    parser.add_argument("--research-count", type=int, help="Number of selected research items.")
    parser.add_argument(
        "--min-news-count",
        type=int,
        help="Minimum selected industry-news count.",
    )
    parser.add_argument(
        "--news-count",
        type=int,
        help="Compatibility alias for --min-news-count.",
    )
    parser.add_argument(
        "--max-news-count",
        type=int,
        help="Hard maximum number of selected industry-news cards.",
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
        help="Allow wider ordinary-news coverage within the global maximum lookback. Use for explicit backfills.",
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
    parser.add_argument("--selection-policy", type=Path, help="Override the versioned source-selection policy JSON.")
    parser.add_argument("--audience-profiles", type=Path, help="Override the versioned audience profile registry JSON.")
    parser.add_argument("--audience-profile", help="Reader profile id used for a source-selection comparison.")
    parser.add_argument("--source-selection", type=Path, help="Override source selection output path.")
    parser.add_argument("--source-materials", type=Path, help="Override bounded source-material JSON path for API editing.")
    parser.add_argument("--selection-diagnostic", type=Path, help="Override selection diagnostics JSON path.")
    parser.add_argument("--selection-review", type=Path, help="Override editor selection review JSON path.")
    parser.add_argument("--workflow-state", type=Path, help="Override per-run workflow state manifest path.")
    parser.add_argument("--overwrite-selection-review", action="store_true")
    parser.add_argument("--runs-root", type=Path, default=Path("daily_runs"), help="Selection replay input root.")
    parser.add_argument(
        "--replay-output",
        type=Path,
        default=Path("daily_runs") / "selection_replay.jsonl",
        help="Selection replay JSONL output path.",
    )
    parser.add_argument("--include-pending-reviews", action="store_true")
    parser.add_argument("--content", type=Path, help="Override content JSON path.")
    parser.add_argument("--normalized-content", type=Path, help="Override normalized content JSON path.")
    parser.add_argument("--brief-json", type=Path, help="Override parsed research brief JSON path.")
    parser.add_argument("--brief-markdown", type=Path, help="Override readable API research brief Markdown path.")
    parser.add_argument("--research-api-raw", type=Path, help="Override raw text-model research JSON path.")
    parser.add_argument("--content-api-raw", type=Path, help="Override raw text-model content JSON path.")
    parser.add_argument("--prompt-file", type=Path, help="Override research image prompt path.")
    parser.add_argument("--image-manifest", type=Path, help="Override research image manifest path.")
    parser.add_argument("--api-image", type=Path, help="Override API-generated Image2 candidate path.")
    parser.add_argument(
        "--api-image-generation-manifest",
        type=Path,
        help="Override API Image2 generation manifest path.",
    )
    parser.add_argument(
        "--ai-runtime-config",
        type=Path,
        help="Runtime config for text and image API providers. Secrets remain in environment variables.",
    )
    parser.add_argument(
        "--image-fallback-runtime-config",
        type=Path,
        help="Optional Image2 fallback config. Activated only when its configured API key exists in the job environment.",
    )
    parser.add_argument(
        "--strict-image-route",
        action="store_true",
        help=(
            "Use only the configured primary Image2 route. A gateway failure leaves the run pending instead of "
            "generating through an alternate image provider."
        ),
    )
    parser.add_argument(
        "--live-models",
        action="store_true",
        help="With check-api, call GET /models on the configured relay after local validation.",
    )
    parser.add_argument(
        "--kind",
        dest="api_kind",
        choices=("all", "text", "image"),
        default="all",
        help="With check-api, validate both providers or isolate the text/image provider.",
    )
    parser.add_argument(
        "--refresh-source-materials",
        action="store_true",
        help="Re-fetch bounded primary-source material before API editorial generation.",
    )
    parser.add_argument(
        "--refresh-api-content",
        action="store_true",
        help="Overwrite existing API research and content artifacts for this date.",
    )
    parser.add_argument(
        "--refresh-api-image",
        action="store_true",
        help="Overwrite the existing Image2 candidate for this date.",
    )
    parser.add_argument(
        "--image-size",
        help="Override the Image2 canvas for this run, for example 1920x1080. Default comes from ai_runtime.json.",
    )
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
    parser.add_argument("--content-sections", type=Path, help="Override the versioned content section registry JSON.")
    parser.add_argument("--editorial-policy", type=Path, help="Override the versioned editorial policy JSON.")
    parser.add_argument("--image-generation-manifest", type=Path, help="API image generator manifest.")
    parser.add_argument("--image-quality-decision", choices=["approved", "pending", "rejected"])
    parser.add_argument("--image-comparison-notes", help="Manual A/B comparison notes for a candidate provider.")
    parser.add_argument(
        "--image-editorial-review",
        type=Path,
        help="Completed structured image review required before approving a research image.",
    )
    parser.add_argument(
        "--allow-candidate-image-provider",
        action="store_true",
        help="Permit non-baseline image providers for comparison/test HTML only.",
    )
    parser.add_argument("--allow-provided-research-image", action="store_true")
    parser.add_argument("--theme", default=DEFAULT_THEME)
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
    parser.add_argument("--feedback-base-url", help="Override the reachable internal feedback-service URL.")
    parser.add_argument("--disable-feedback", action="store_true", help="Build/send without rating or open tracking.")
    parser.add_argument("--require-feedback", action="store_true", help="Refuse packaging when feedback is not configured.")
    parser.add_argument("--from-addr")
    parser.add_argument("--from-name")
    parser.add_argument("--smtp-host")
    parser.add_argument("--smtp-port", type=int)
    parser.add_argument("--smtp-security", choices=["ssl", "starttls", "none"])
    parser.add_argument("--smtp-user")
    parser.add_argument("--ask-password", action="store_true")
    parser.add_argument("--send", action="store_true", help="Actually send email. Default is SMTP dry-run.")
    parser.add_argument("--confirm-prod", action="store_true", help="Required with prod --send.")
    parser.add_argument(
        "--allow-resend-prod",
        action="store_true",
        help="Allow an intentional duplicate prod send for the same date after explicit review.",
    )
    return parser.parse_args()
