#!/usr/bin/env python3
"""Build a complete AI daily email HTML from content JSON.

The AI/editor owns the content fields. This script owns deterministic metadata,
issue numbering, validation, image-path checks, and HTML rendering.
"""

from __future__ import annotations

import argparse
import json
import shlex
from copy import deepcopy
from datetime import date, datetime
from pathlib import Path
from typing import Any

from ai_daily_common import (
    JsonObjectError,
    NEWS_FORBIDDEN_IMAGE_KEYS,
    default_research_image_manifest_path,
    is_external_asset,
    load_json_object,
    relative_to_output,
    resolve_workspace_path,
    write_json_object,
)
from render_ai_daily_email import NEWS_LAYOUTS, THEMES, apply_news_layout, apply_theme, render_email
from validate_ai_daily_content import ValidationError, validate_content
from verify_research_image_lineage import LineageError, verify_lineage
from image_provider_registry import ImageProviderError


WEEKDAYS = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]

DEFAULT_EYEBROW = "AI SIGNAL RADAR"
DEFAULT_NEWS_SECTION = {
    "title": "行业动态",
    "subtitle": "精选今日值得关注的 AI 产品、技术与行业信号",
}
DEFAULT_FOOTER = {
    "title": "试运行说明",
    "body": (
        "本日报目前仍在试运行中，内容选择、栏目结构和展示形式会根据大家反馈持续优化。"
        "欢迎直接回复邮件交流，也欢迎在内部即时通讯工具中留下意见和建议。"
    ),
    "name": "AI Daily Editorial",
    "email": "ai-daily@example.com",
}
DEFAULT_EDITOR_SETTINGS = Path(__file__).resolve().parents[1] / "config" / "editor_settings.json"


class BuildError(Exception):
    pass


def parse_run_date(raw: str | None) -> date:
    if not raw:
        return date.today()
    try:
        return datetime.strptime(raw, "%Y-%m-%d").date()
    except ValueError as exc:
        raise BuildError("--date must use YYYY-MM-DD format.") from exc


def format_date_cn(run_date: date) -> str:
    return f"{run_date.month}月{run_date.day}日"


def format_display_date(run_date: date) -> str:
    return f"{run_date:%Y.%m.%d} {WEEKDAYS[run_date.weekday()]}"


def formal_send_command(
    run_date: date,
    output: Path,
    series: str,
    explicit_issue: int | None,
) -> str:
    """Return the exact guarded prod-send command for a successfully built HTML."""

    project_root = Path(__file__).resolve().parents[2]
    runner = Path(__file__).resolve().with_name("run_ai_daily.py")
    command = [
        "python3",
        str(runner),
        "prod",
        "--date",
        run_date.isoformat(),
        "--html",
        str(output.resolve()),
        "--skip-build",
        "--series",
        series,
        "--ask-password",
        "--send",
        "--confirm-prod",
    ]
    if explicit_issue is not None:
        command.extend(["--issue", str(explicit_issue)])
    return f"cd {shlex.quote(str(project_root))} && {shlex.join(command)}"


def load_ledger(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return load_json_object(path)


def assign_issue(
    ledger: dict[str, Any],
    run_date: date,
    series: str,
    explicit_issue: int | None,
) -> int:
    if series == "none":
        return explicit_issue or 0

    entries = ledger.setdefault(series, {})
    if not isinstance(entries, dict):
        raise BuildError(f"Issue ledger section {series!r} must be an object.")

    key = run_date.isoformat()
    if explicit_issue is not None:
        if explicit_issue <= 0:
            raise BuildError("--issue must be a positive integer.")
        entries[key] = explicit_issue
        return explicit_issue

    if key in entries:
        try:
            return int(entries[key])
        except (TypeError, ValueError) as exc:
            raise BuildError(f"Issue ledger entry for {key} is not an integer.") from exc

    existing_issues: list[int] = []
    for value in entries.values():
        try:
            existing_issues.append(int(value))
        except (TypeError, ValueError) as exc:
            raise BuildError("Issue ledger contains a non-integer issue number.") from exc

    issue = max(existing_issues, default=0) + 1
    entries[key] = issue
    return issue


def make_subtitle(series: str, issue: int) -> str:
    if series == "trial":
        return f"试运行第 {issue} 期：精选 AI 行业动态"
    if series == "official":
        return f"第 {issue} 期：精选 AI 行业动态"
    return "精选 AI 行业动态"


def merge_default_object(data: dict[str, Any], key: str, defaults: dict[str, str]) -> None:
    current = data.get(key)
    if not isinstance(current, dict):
        current = {}
    merged = {**defaults, **current}
    data[key] = merged


def load_footer_defaults(path: Path) -> dict[str, str]:
    """Load optional local editorial identity without making it a repository dependency."""

    if not path.exists():
        return deepcopy(DEFAULT_FOOTER)
    settings = load_json_object(path)
    footer = settings.get("footer", settings)
    if not isinstance(footer, dict):
        raise BuildError(f"{path} must contain a footer object.")
    return {
        **DEFAULT_FOOTER,
        **{key: str(value).strip() for key, value in footer.items() if key in DEFAULT_FOOTER and value},
    }


def enrich_content(
    raw: dict[str, Any],
    run_date: date,
    issue: int,
    series: str,
    footer_defaults: dict[str, str],
) -> dict[str, Any]:
    data = deepcopy(raw)
    date_cn = format_date_cn(run_date)

    data["date"] = date_cn
    data["display_date"] = format_display_date(run_date)
    data["issue"] = issue
    data["series"] = series
    data["title"] = f"今日 AI 情报雷达｜{date_cn}"
    data["subtitle"] = make_subtitle(series, issue)
    data["eyebrow"] = DEFAULT_EYEBROW

    research_items = data.get("research_items")
    if isinstance(research_items, list) and research_items:
        for item in research_items:
            if isinstance(item, dict):
                item.setdefault("section_title", "研究前沿")
        if not isinstance(data.get("paper"), dict) and isinstance(research_items[0], dict):
            data["paper"] = deepcopy(research_items[0])
    else:
        paper = data.get("paper")
        if isinstance(paper, dict):
            paper.setdefault("section_title", "研究前沿")

    merge_default_object(data, "news_section", DEFAULT_NEWS_SECTION)
    merge_default_object(data, "footer", footer_defaults)
    return data


def iter_research_items(data: dict[str, Any]) -> list[dict[str, Any]]:
    items = data.get("research_items")
    if isinstance(items, list) and items:
        return [item for item in items if isinstance(item, dict)]
    paper = data.get("paper")
    if isinstance(paper, dict):
        return [paper]
    return []


def check_pipeline_rules(data: dict[str, Any], workspace: Path) -> None:
    for idx, paper in enumerate(iter_research_items(data)):
        image = paper.get("image")
        if isinstance(image, str) and image.strip() and not is_external_asset(image):
            image_path = resolve_workspace_path(image, workspace)
            if not image_path.exists():
                raise BuildError(f"Missing research_items[{idx}] image: {image_path}")

    for idx, item in enumerate(data.get("news", [])):
        if not isinstance(item, dict):
            continue
        for key in NEWS_FORBIDDEN_IMAGE_KEYS:
            if key in item:
                raise BuildError(f"news[{idx}].{key} is not allowed. News items must stay text-only.")


def rewrite_local_image_paths_for_output(data: dict[str, Any], workspace: Path, output: Path) -> dict[str, Any]:
    render_data = deepcopy(data)
    for paper in iter_research_items(render_data):
        image = paper.get("image")
        if isinstance(image, str) and image.strip() and not is_external_asset(image):
            paper["image"] = relative_to_output(image, workspace, output)
    items = render_data.get("research_items")
    if isinstance(items, list) and items and isinstance(items[0], dict):
        render_data["paper"] = deepcopy(items[0])
    return render_data


def default_output_path(run_date: date) -> Path:
    return Path("output") / f"ai_daily_email_{run_date.isoformat()}.html"


def main() -> None:
    parser = argparse.ArgumentParser(description="Build AI daily email HTML with deterministic metadata.")
    parser.add_argument("--content", required=True, type=Path, help="Content JSON path.")
    parser.add_argument("--date", help="Run date in YYYY-MM-DD. Defaults to today.")
    parser.add_argument(
        "--series",
        choices=["trial", "official", "none"],
        default="trial",
        help="Issue-number display mode.",
    )
    parser.add_argument("--issue", type=int, help="Override issue number for the selected date.")
    parser.add_argument(
        "--issue-ledger",
        type=Path,
        default=Path("daily_runs") / "issue_ledger.json",
        help="Issue ledger JSON path.",
    )
    parser.add_argument("--no-write-ledger", action="store_true", help="Do not persist issue ledger changes.")
    parser.add_argument("--output", type=Path, help="Output HTML path.")
    parser.add_argument("--normalized-output", type=Path, help="Optional enriched JSON output path.")
    parser.add_argument(
        "--editor-settings",
        type=Path,
        default=DEFAULT_EDITOR_SETTINGS,
        help="Optional local footer identity JSON. Missing file uses repository-safe defaults.",
    )
    parser.add_argument(
        "--theme",
        default="clay_orange",
        choices=sorted(THEMES),
        help="Visual theme name.",
    )
    parser.add_argument(
        "--news-layout",
        default="paper",
        choices=NEWS_LAYOUTS,
        help="Industry news card layout variant.",
    )
    parser.add_argument(
        "--require-image-manifest",
        action="store_true",
        help="Require a valid research image lineage manifest before rendering.",
    )
    parser.add_argument(
        "--image-manifest",
        type=Path,
        action="append",
        help="Research image lineage manifest path. May be repeated for multiple research_items.",
    )
    parser.add_argument(
        "--allow-provided-research-image",
        action="store_true",
        help="Allow source_kind=provided in the image manifest. Default requires imagegen.",
    )
    parser.add_argument(
        "--allow-candidate-image-provider",
        action="store_true",
        help="Permit a reviewed non-baseline provider for a comparison HTML build; never use this for a production send.",
    )
    parser.add_argument("--image-provider-registry", type=Path, help="Image provider registry path.")
    args = parser.parse_args()

    try:
        run_date = parse_run_date(args.date)
        ledger = load_ledger(args.issue_ledger)
        issue = assign_issue(ledger, run_date, args.series, args.issue)
        data = enrich_content(
            load_json_object(args.content),
            run_date,
            issue,
            args.series,
            load_footer_defaults(args.editor_settings),
        )
        check_pipeline_rules(data, Path.cwd())
        if args.require_image_manifest:
            manifest_paths = args.image_manifest or [default_research_image_manifest_path(run_date.isoformat())]
            for manifest_path in manifest_paths:
                verify_lineage(
                    data,
                    manifest_path,
                    Path.cwd(),
                    require_imagegen=not args.allow_provided_research_image,
                    allow_candidate_provider=(
                        args.allow_candidate_image_provider or args.allow_provided_research_image
                    ),
                    provider_registry_path=args.image_provider_registry,
                )
        validate_content(data)
    except (
        json.JSONDecodeError,
        OSError,
        JsonObjectError,
        ImageProviderError,
        BuildError,
        LineageError,
        ValidationError,
    ) as exc:
        raise SystemExit(f"Build failed: {exc}") from exc

    if args.series != "none" and not args.no_write_ledger:
        write_json_object(args.issue_ledger, ledger)

    if args.normalized_output:
        write_json_object(args.normalized_output, data)

    output = args.output or default_output_path(run_date)
    apply_theme(args.theme)
    apply_news_layout(args.news_layout)
    output.parent.mkdir(parents=True, exist_ok=True)
    render_data = rewrite_local_image_paths_for_output(data, Path.cwd(), output)
    output.write_text(render_email(render_data), encoding="utf-8")

    issue_text = "no issue" if args.series == "none" else f"issue {issue}"
    print(f"Wrote {output} ({format_display_date(run_date)}, {issue_text}, theme {args.theme}, news {args.news_layout})")
    print("正式发送命令（正式名单，发送前会要求输入 SMTP 密码）：")
    print(formal_send_command(run_date, output, args.series, args.issue))


if __name__ == "__main__":
    main()
