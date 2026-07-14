#!/usr/bin/env python3
"""Run repeatable quality/stability checks for an API image provider.

The script deliberately generates candidate assets only. It never stages an
image into a production daily and never sends an email. The resulting report
captures transport reliability and a pending manual review rubric, which are
the two prerequisites for deciding whether a provider deserves an A/B trial.
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import statistics
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from ai_daily_common import file_sha256, write_json_object
from image_provider_registry import ImageProviderError, load_provider_registry, resolve_provider


SCRIPT_DIR = Path(__file__).resolve().parent


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def image_metadata(path: Path) -> dict[str, Any]:
    try:
        from PIL import Image
    except Exception as exc:
        raise RuntimeError(f"Pillow is required for image validation: {exc}") from exc

    with Image.open(path) as image:
        image.load()
        return {
            "path": str(path),
            "format": image.format,
            "width": image.width,
            "height": image.height,
            "bytes": path.stat().st_size,
            "sha256": file_sha256(path),
        }


def percentile_95(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, round(0.95 * (len(ordered) - 1))))
    return round(ordered[index], 1)


def build_contact_sheet(images: list[tuple[str, Path]], output: Path) -> Path | None:
    if not images:
        return None
    try:
        from PIL import Image, ImageDraw
    except Exception:
        return None

    cell_width, cell_height, label_height, gap = 340, 260, 30, 16
    canvas_width = len(images) * cell_width + (len(images) + 1) * gap
    canvas_height = cell_height + label_height + gap * 2
    canvas = Image.new("RGB", (canvas_width, canvas_height), "#f5f7fa")
    draw = ImageDraw.Draw(canvas)
    for index, (label, path) in enumerate(images):
        with Image.open(path) as original:
            image = original.convert("RGB")
            image.thumbnail((cell_width, cell_height), Image.Resampling.LANCZOS)
        left = gap + index * (cell_width + gap)
        top = gap + label_height
        canvas.paste(image, (left + (cell_width - image.width) // 2, top + (cell_height - image.height) // 2))
        draw.text((left, gap), label, fill="#24364b")
    output.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(output, format="JPEG", quality=88, optimize=True)
    return output


def safe_tail(value: str, limit: int = 800) -> str:
    return value.replace("\n", " ").strip()[-limit:]


def gateway_preflight(args: argparse.Namespace) -> dict[str, Any]:
    """Fail early when the configured API gateway is not reachable here."""

    if args.dry_run:
        return {"status": "skipped", "reason": "dry_run"}
    if args.skip_gateway_preflight:
        return {"status": "skipped", "reason": "explicitly_skipped"}

    try:
        registry = load_provider_registry(args.provider_registry)
        _, provider = resolve_provider(args.provider, registry)
    except ImageProviderError as exc:
        return {"status": "failed", "stage": "provider_config", "error": str(exc)}

    base_url_env = str(provider.get("base_url_env") or "OPENAI_BASE_URL")
    base_url = os.environ.get(base_url_env)
    if not base_url:
        return {"status": "skipped", "reason": f"{base_url_env}_not_set"}

    parsed = urlparse(base_url)
    if not parsed.hostname:
        return {
            "status": "failed",
            "stage": "base_url",
            "error": f"{base_url_env} does not contain a valid hostname.",
        }

    host = parsed.hostname
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    result: dict[str, Any] = {"host": host, "port": port, "scheme": parsed.scheme or "http"}
    try:
        addresses = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        return {"status": "failed", "stage": "dns", "error": str(exc), **result}

    family, sock_type, protocol, _, sockaddr = addresses[0]
    try:
        with socket.socket(family, sock_type, protocol) as connection:
            connection.settimeout(args.gateway_preflight_timeout_seconds)
            connection.connect(sockaddr)
    except OSError as exc:
        return {"status": "failed", "stage": "tcp", "error": str(exc), **result}
    return {"status": "passed", **result}


def blocked_report(args: argparse.Namespace, preflight: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": 2,
        "created_at": utc_now_iso(),
        "provider": args.provider,
        "prompt_file": str(args.prompt_file),
        "prompt_sha256": file_sha256(args.prompt_file),
        "request": {
            "attempts": args.attempts,
            "size": args.size,
            "quality": args.quality,
            "timeout_seconds": args.timeout_seconds,
            "max_retries": args.max_retries,
            "dry_run": args.dry_run,
        },
        "gateway_preflight": preflight,
        "summary": {
            "success_count": 0,
            "failure_count": 0,
            "success_rate": 0.0,
            "latency_ms": {"min": None, "median": None, "p95": None, "max": None},
            "transport_gate": "blocked",
        },
        "baseline_image": str(args.baseline_image) if args.baseline_image else None,
        "contact_sheet": None,
        "attempts": [],
        "manual_review": manual_review_template(),
    }


def run_attempt(args: argparse.Namespace, index: int, out_dir: Path) -> dict[str, Any]:
    output = out_dir / f"candidate-{index:02d}.png"
    generation_manifest = out_dir / f"candidate-{index:02d}.generation.json"
    cmd = [
        sys.executable,
        str(SCRIPT_DIR / "generate_research_image.py"),
        "--provider",
        args.provider,
        "--prompt-file",
        str(args.prompt_file),
        "--out",
        str(output),
        "--generation-manifest",
        str(generation_manifest),
        "--size",
        args.size,
        "--quality",
        args.quality,
        "--timeout-seconds",
        str(args.timeout_seconds),
        "--max-retries",
        str(args.max_retries),
    ]
    if args.provider_registry:
        cmd.extend(["--provider-registry", str(args.provider_registry)])
    if args.force:
        cmd.append("--force")
    if args.dry_run:
        cmd.append("--dry-run")

    started_at = utc_now_iso()
    started = time.perf_counter()
    completed = subprocess.run(cmd, text=True, capture_output=True)
    elapsed_ms = round((time.perf_counter() - started) * 1000)
    record: dict[str, Any] = {
        "attempt": index,
        "started_at": started_at,
        "elapsed_ms": elapsed_ms,
        "status": "success" if completed.returncode == 0 else "failed",
        "output": str(output),
        "generation_manifest": str(generation_manifest),
    }
    if completed.returncode != 0:
        record["error"] = safe_tail(completed.stderr or completed.stdout)
        return record
    if args.dry_run:
        record["status"] = "dry_run"
        return record
    if not output.exists() or not generation_manifest.exists():
        record.update({"status": "failed", "error": "Generator returned success but output or manifest is missing."})
        return record
    try:
        record["image"] = image_metadata(output)
        generation = json.loads(generation_manifest.read_text(encoding="utf-8"))
        response = generation.get("response")
        if isinstance(response, dict):
            record["api_elapsed_ms"] = response.get("elapsed_ms")
            if response.get("usage") is not None:
                record["usage"] = response["usage"]
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        record.update({"status": "failed", "error": f"Generated file validation failed: {exc}"})
    return record


def manual_review_template() -> dict[str, Any]:
    return {
        "status": "pending",
        "checks": [
            {"id": "mechanism", "question": "是否准确表达 Visual Handoff 的核心机制？", "decision": "pending"},
            {"id": "text", "question": "标题与少量中文标签是否清晰、无乱码？", "decision": "pending"},
            {"id": "evidence", "question": "是否没有编造数字、指标、来源或结论？", "decision": "pending"},
            {"id": "palette", "question": "是否有独立科技感配色，而非单一主题色铺满？", "decision": "pending"},
            {"id": "email_fit", "question": "缩放到邮件宽度后是否仍能在几秒内读懂？", "decision": "pending"},
        ],
        "promotion_rule": "只有稳定性通过且与 Codex ImageGen 基线人工 A/B 不低于基线，才可申请 production_allowed。",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", default="image2")
    parser.add_argument("--provider-registry", type=Path)
    parser.add_argument("--prompt-file", required=True, type=Path)
    parser.add_argument("--out-dir", required=True, type=Path)
    parser.add_argument("--attempts", type=int, default=3, help="Same-prompt request count for stability testing.")
    parser.add_argument("--size", default="1024x1024")
    parser.add_argument("--quality", default="high")
    parser.add_argument("--timeout-seconds", type=int, default=120)
    parser.add_argument("--max-retries", type=int, default=1)
    parser.add_argument(
        "--gateway-preflight-timeout-seconds",
        type=int,
        default=3,
        help="DNS/TCP reachability timeout before issuing image requests.",
    )
    parser.add_argument(
        "--skip-gateway-preflight",
        action="store_true",
        help="Bypass the API gateway DNS/TCP check when the runtime handles service discovery separately.",
    )
    parser.add_argument("--baseline-image", type=Path, help="Optional native baseline image to include in the contact sheet.")
    parser.add_argument("--force", action="store_true", help="Overwrite existing candidate files in --out-dir.")
    parser.add_argument("--dry-run", action="store_true", help="Validate the provider command without calling the API.")
    parser.add_argument("--allow-failures", action="store_true", help="Write a report but return zero even when attempts fail.")
    args = parser.parse_args()

    if args.attempts < 1:
        raise SystemExit("Stability test failed: --attempts must be at least 1.")
    if args.timeout_seconds <= 0 or args.max_retries < 0 or args.gateway_preflight_timeout_seconds <= 0:
        raise SystemExit("Stability test failed: invalid timeout or retry setting.")
    if not args.prompt_file.exists():
        raise SystemExit(f"Stability test failed: missing prompt file: {args.prompt_file}")
    if args.baseline_image and not args.baseline_image.exists():
        raise SystemExit(f"Stability test failed: missing baseline image: {args.baseline_image}")

    args.out_dir.mkdir(parents=True, exist_ok=True)
    preflight = gateway_preflight(args)
    if preflight["status"] == "failed":
        report = blocked_report(args, preflight)
        report_path = args.out_dir / "stability_report.json"
        write_json_object(report_path, report)
        print(f"Stability report: {report_path}")
        print(
            "Gateway preflight blocked image requests: "
            f"{preflight.get('stage', 'unknown')} - {preflight.get('error', 'unknown error')}"
        )
        if not args.allow_failures:
            raise SystemExit("Stability test blocked: configured image gateway is not reachable. See stability_report.json.")
        return

    attempts = [run_attempt(args, index, args.out_dir) for index in range(1, args.attempts + 1)]
    successful = [item for item in attempts if item["status"] == "success"]
    latencies = [float(item["elapsed_ms"]) for item in successful]

    contact_sheet_inputs: list[tuple[str, Path]] = []
    if not args.dry_run:
        if args.baseline_image:
            contact_sheet_inputs.append(("baseline", args.baseline_image))
        for item in successful:
            contact_sheet_inputs.append((f"candidate {item['attempt']:02d}", Path(item["output"])))
    contact_sheet = build_contact_sheet(contact_sheet_inputs, args.out_dir / "contact_sheet.jpg")

    report = {
        "schema_version": 2,
        "created_at": utc_now_iso(),
        "provider": args.provider,
        "prompt_file": str(args.prompt_file),
        "prompt_sha256": file_sha256(args.prompt_file),
        "request": {
            "attempts": args.attempts,
            "size": args.size,
            "quality": args.quality,
            "timeout_seconds": args.timeout_seconds,
            "max_retries": args.max_retries,
            "dry_run": args.dry_run,
        },
        "gateway_preflight": preflight,
        "summary": {
            "success_count": len(successful),
            "failure_count": sum(1 for item in attempts if item["status"] == "failed"),
            "success_rate": round(len(successful) / args.attempts, 3),
            "latency_ms": {
                "min": round(min(latencies), 1) if latencies else None,
                "median": round(statistics.median(latencies), 1) if latencies else None,
                "p95": percentile_95(latencies),
                "max": round(max(latencies), 1) if latencies else None,
            },
            "transport_gate": (
                "not_evaluated" if args.dry_run else "pass" if len(successful) == args.attempts else "fail"
            ),
        },
        "baseline_image": str(args.baseline_image) if args.baseline_image else None,
        "contact_sheet": str(contact_sheet) if contact_sheet else None,
        "attempts": attempts,
        "manual_review": manual_review_template(),
    }
    report_path = args.out_dir / "stability_report.json"
    write_json_object(report_path, report)
    print(f"Stability report: {report_path}")
    if args.dry_run:
        print("Dry-run passed: provider request contract validated; no API call was made.")
    else:
        print(
            f"Transport: {report['summary']['success_count']}/{args.attempts} successful | "
            f"median {report['summary']['latency_ms']['median']} ms"
        )
    if contact_sheet:
        print(f"Contact sheet: {contact_sheet}")
    if report["summary"]["transport_gate"] == "fail" and not args.allow_failures:
        raise SystemExit("Stability test failed: one or more attempts failed. See stability_report.json.")


if __name__ == "__main__":
    main()
