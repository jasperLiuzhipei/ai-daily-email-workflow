#!/usr/bin/env python3
"""Parse ai-frontier-brief-editor output into workflow artifacts.

This script does not call an LLM. It turns the editor's Markdown output into:
- a paper JSON patch for the daily email content schema
- a plain imagegen prompt derived from the Visual Handoff
- a machine-readable copy of the bridge and handoff sections
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

from ai_daily_common import slugify, write_json_object
from research_image_contract import FIELDS, HandoffError, compile_prompt, compilation_record, load_policy, normalize_handoff


BRIDGE_FIELDS = [
    "Target Section",
    "Image Allowed",
    "Research Title",
    "Core Conclusion",
    "Research Problem",
    "Method Mechanism",
    "Key Evidence",
    "Application Insight",
    "Core Summary",
    "Research Question",
    "Technical Path",
    "Evidence Cards",
    "Application Seed",
    "Boundary",
    "Primary Links",
    "Image Asset Hint",
]

VISUAL_FIELDS = [
    "Core Idea",
    "Named Subject",
    "Distinctive Detail",
    "Display Title",
    "Optional Subtitle",
    "Title Policy",
    "Primary Explanation",
    "Credibility Support",
    "Primary Visual Scene",
    "Visual Hook",
    "Must Preserve",
    "Main Evidence Anchor",
    "Supporting Evidence Details",
    "Boundary Cue",
    "Do Not Imply",
    "Visual Hierarchy",
    "Complexity Budget",
    "Clarity Check",
    "People Policy",
    "Text Policy",
    "Visual Tone",
    "Creative Freedom",
    "Avoid",
    "Reject If",
]


class ParseError(Exception):
    pass


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def section(text: str, title: str) -> str:
    pattern = re.compile(
        rf"^###\s+{re.escape(title)}\s*$([\s\S]*?)(?=^###\s+|\Z)",
        re.MULTILINE,
    )
    match = pattern.search(text)
    if not match:
        raise ParseError(f"Missing section: {title}")
    return match.group(1).strip()


def parse_fields(block: str, known_fields: list[str]) -> dict[str, str]:
    known = set(known_fields)
    field_re = re.compile(r"^([A-Za-z][A-Za-z /-]*):\s*(.*)$")
    out: dict[str, list[str]] = {}
    current: str | None = None

    for raw_line in block.splitlines():
        line = raw_line.rstrip()
        match = field_re.match(line)
        if match and match.group(1) in known:
            current = match.group(1)
            out.setdefault(current, [])
            rest = match.group(2).strip()
            if rest:
                out[current].append(rest)
            continue
        if current is not None:
            out[current].append(line)

    return {key: clean_value("\n".join(lines)) for key, lines in out.items()}


def clean_value(value: str) -> str:
    lines = [line.rstrip() for line in value.strip().splitlines()]
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    return "\n".join(lines).strip()


def parse_bullets(value: str) -> list[str]:
    items: list[str] = []
    current: list[str] = []
    for raw_line in value.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        bullet = re.match(r"^[-*]\s+(.*)$", line)
        if bullet:
            if current:
                items.append(" ".join(current).strip())
            current = [bullet.group(1).strip()]
        elif current:
            current.append(line)
    if current:
        items.append(" ".join(current).strip())
    if items:
        return items
    return [line.strip() for line in value.splitlines() if line.strip()]


def parse_label_text_items(value: str) -> list[dict[str, str]]:
    parsed: list[dict[str, str]] = []
    for item in parse_bullets(value):
        if ":" in item:
            label, text = item.split(":", 1)
        elif "：" in item:
            label, text = item.split("：", 1)
        else:
            label, text = "要点", item
        parsed.append({"label": label.strip(), "text": strip_citations(text.strip())})
    return parsed


def parse_links(value: str) -> list[dict[str, str]]:
    links: list[dict[str, str]] = []
    for item in parse_bullets(value):
        url_match = re.search(r"https?://\S+", item)
        if not url_match:
            continue
        url = url_match.group(0).rstrip(").,，。")
        label = item[: url_match.start()].strip(" :-：")
        links.append({"label": label or "原文", "url": url})
    return links


def strip_citations(value: str) -> str:
    value = re.sub(r"\s*\(\[.*?\]\(https?://.*?\)\)", "", value)
    return re.sub(r"\s+", " ", value).strip()


def bridge_value(bridge: dict[str, str], *keys: str) -> str:
    for key in keys:
        value = bridge.get(key)
        if isinstance(value, str) and value.strip():
            return strip_citations(value)
    return ""


def legacy_evidence_text(bridge: dict[str, str]) -> str:
    evidence_items = parse_label_text_items(bridge.get("Evidence Cards", ""))[:3]
    return "；".join(
        f"{item['label']}：{item['text']}" if item.get("label") else item.get("text", "")
        for item in evidence_items
        if item.get("text")
    )


def make_paper_json(bridge: dict[str, str], image_path: str) -> dict[str, Any]:
    title = bridge.get("Research Title") or "研究前沿"
    evidence = bridge_value(bridge, "Key Evidence") or legacy_evidence_text(bridge)
    links = parse_links(bridge.get("Primary Links", ""))
    if not evidence:
        raise ParseError("Daily Email Bridge has no parseable Key Evidence.")
    if not links:
        raise ParseError("Daily Email Bridge has no parseable Primary Links.")

    return {
        "section_title": "研究前沿",
        "title": strip_citations(title),
        "image": image_path,
        "image_alt": f"{strip_citations(title)} 图解",
        "conclusion": bridge_value(bridge, "Core Conclusion", "Core Summary"),
        "problem": bridge_value(bridge, "Research Problem", "Research Question"),
        "mechanism": bridge_value(bridge, "Method Mechanism", "Technical Path"),
        "evidence": evidence,
        "insight": bridge_value(bridge, "Application Insight", "Application Seed"),
        "boundary": bridge_value(bridge, "Boundary"),
        "links": links,
    }


def visual_text(visual: dict[str, str]) -> str:
    fields = [
        "Core Idea",
        "Named Subject",
        "Distinctive Detail",
        "Primary Explanation",
        "Credibility Support",
        "Primary Visual Scene",
        "Visual Hook",
        "Visual Tone",
    ]
    return " ".join(visual.get(field, "") for field in fields).lower()


def has_any(text: str, keywords: list[str]) -> bool:
    return any(keyword.lower() in text for keyword in keywords)


def make_palette_guidance(visual: dict[str, str]) -> str:
    """Compatibility entrypoint; palette policy has one versioned owner."""
    return load_policy()["palette"]


def make_image_prompt(visual_handoff_raw: str, visual: dict[str, str], _output_hint: str) -> str:
    """Compile structured handoff only; raw Markdown stays in the audit artifact."""
    if not visual:
        visual = parse_fields(visual_handoff_raw, list(FIELDS) + VISUAL_FIELDS)
    return compile_prompt(visual)


def main() -> None:
    parser = argparse.ArgumentParser(description="Parse ai-frontier brief Markdown into daily email artifacts.")
    parser.add_argument("brief", type=Path, help="Markdown output from ai-frontier-brief-editor.")
    parser.add_argument("--date", required=True, help="Run date, YYYY-MM-DD. Used for default image path.")
    parser.add_argument("--asset-slug", help="Override image asset slug.")
    parser.add_argument("--out-json", type=Path, help="Output parsed JSON artifact.")
    parser.add_argument("--prompt-out", type=Path, help="Output imagegen prompt text.")
    args = parser.parse_args()

    try:
        text = read_text(args.brief)
        bridge_raw = section(text, "Daily Email Bridge")
        visual_raw = section(text, "Visual Handoff")
        bridge = parse_fields(bridge_raw, BRIDGE_FIELDS)
        visual = parse_fields(visual_raw, list(FIELDS) + VISUAL_FIELDS)
        if bridge.get("Target Section", "").strip().lower() != "research":
            raise ParseError("Daily Email Bridge is not a research bridge.")
        if bridge.get("Image Allowed", "").strip().lower() != "true":
            raise ParseError("Research bridge does not allow image generation.")

        slug = args.asset_slug or bridge.get("Image Asset Hint") or visual.get("Named Subject") or "research-cover"
        slug = slugify(slug)
        image_path = f"assets/email/{args.date}/{slug}.png"
        paper = make_paper_json(bridge, image_path)
        prompt = make_image_prompt(visual_raw, visual, image_path)
    except (OSError, ParseError, HandoffError) as exc:
        raise SystemExit(f"Parse failed: {exc}") from exc

    artifact = {
        "paper": paper,
        "image_asset_path": image_path,
        "image_asset_slug": slug,
        "daily_email_bridge": bridge,
        "visual_handoff": visual,
        "visual_handoff_compact": normalize_handoff(visual),
        "prompt_compilation": compilation_record(visual, prompt),
        "visual_handoff_raw": visual_raw,
        "imagegen_prompt": prompt,
    }

    if args.out_json:
        write_json_object(args.out_json, artifact)
        print(f"Wrote {args.out_json}")

    if args.prompt_out:
        args.prompt_out.parent.mkdir(parents=True, exist_ok=True)
        args.prompt_out.write_text(prompt, encoding="utf-8")
        print(f"Wrote {args.prompt_out}")

    if not args.out_json and not args.prompt_out:
        print(json.dumps(artifact, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
