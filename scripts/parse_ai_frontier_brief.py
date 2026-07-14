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
    """Return a content-aware color strategy for image generation.

    Research explainers should prioritize a precise technology feel rather than
    inherit any visual theme from the email shell.
    """
    text = visual_text(visual)
    foundation = (
        "冷白或浅灰、石墨黑、银灰作为基础，保持高对比、清晰的科技图解感；"
        "用 2-4 个主色层次组织信息，不要让任何单一色相铺满全图。"
    )

    if has_any(
        text,
        [
            "risk",
            "safety",
            "attack",
            "hallucination",
            "boundary",
            "failure",
            "风险",
            "安全",
            "攻击",
            "幻觉",
            "边界",
            "失败",
        ],
    ):
        accent = "辅色优先使用克制红、暗李子色、石墨和银灰；让风险/边界提示清晰但不刺眼。"
    elif has_any(
        text,
        [
            "business",
            "operations",
            "workflow",
            "startup",
            "ceo",
            "cost",
            "router",
            "agent",
            "业务",
            "运营",
            "经营",
            "创业",
            "成本",
            "路由",
            "中台",
        ],
    ):
        accent = "辅色优先使用深海军蓝、蓝灰、石墨和银灰；只有在表达状态、决策或路径分叉时，才少量使用琥珀色或低饱和珊瑚色。"
    elif has_any(
        text,
        [
            "model",
            "architecture",
            "benchmark",
            "token",
            "reasoning",
            "infrastructure",
            "模型",
            "架构",
            "基准",
            "评测",
            "推理",
            "token",
            "基础设施",
        ],
    ):
        accent = "辅色可加入蓝灰、靛蓝、银灰、低饱和青色或克制紫色，体现技术结构但不要变成单一冷蓝。"
    else:
        accent = "辅色从文章主题中选择，可用深海军蓝、蓝灰、靛蓝、银灰、克制紫色或风险红；色彩必须帮助理解机制和边界。"

    guardrail = (
        "不要继承邮件主题色。暖色只可作为有意义的小型状态或风险提示，不能默认出现；"
        "Reject if the image is dominated by orange, blue, or any single hue; low-contrast; or looks like the email template turned into an illustration. "
        "不要使用绿色作为主色。"
    )
    return f"{foundation}{accent}{guardrail}"


def make_image_prompt(visual_handoff_raw: str, visual: dict[str, str], _output_hint: str) -> str:
    title = visual.get("Display Title", "")
    subtitle = visual.get("Optional Subtitle", "")
    palette_guidance = make_palette_guidance(visual)
    return f"""生成一张 1200x630 横版中文研究图解，用于 AI 日报邮件。

主题：{title or "研究前沿"}
{f"副标题：{subtitle}" if subtitle else ""}

画面目标：
把下面的 Visual Handoff 转成一张清晰、现代、可读的中文研究解释图。读者应在约三秒内看懂这项研究在解释什么机制，以及它的证据和边界。

画面结构：
1. 主视觉只讲一个核心机制，不要堆很多模块。
2. 保留一个小型证据锚点，可以是对照条、简洁实验面板或少量指标卡；默认只表达相对趋势，不写具体数字。
3. 保留一个边界提示，放在角落或次级区域，不要抢主视觉。

风格要求：
浅色或中性背景，干净现代，有高对比的 AI 产品感、系统感和研究解释感；配色必须服务论文机制，而不是机械继承日报主题。{palette_guidance}中文标签要短、清晰、字号足够大，适合邮件里阅读。

配色隔离硬约束：
图片不得复制邮件主题色。Reject if the image is dominated by orange, blue, or any single hue; lacks contrast; or looks like the email template recolored into an illustration rather than a content-specific research explainer. 暖色只能作为有明确语义的少量状态或风险提示，不能作为默认底色或主视觉。

文字要求：
标题尽量使用“{title or "研究前沿"}”{f"，副标题尽量使用“{subtitle}”" if subtitle else ""}。只使用简体中文短标签，不要长段文字，不要英文大段说明，不要水印。

必须避免：
不要出现人物、机器人、大脑、来源方或品牌 logo、论文截图、密集表格、复杂曲线；不要把机制画成联网搜索；不要暗示能力万能、流程越长越好，或模型获得了未说明的新知识。

数字证据要求：
不要自行编造或补全任何百分比、分数、pt 变化、样本数、排名、坐标轴数值或精确指标。只有 Visual Handoff 明确写出“允许使用以下精确数字”时，才可以在图中使用那些数字；否则所有证据面板都只能用高低不同的趋势条、箭头、强弱对比、阶段标签或“较弱 / 部分恢复 / 明显恢复 / 更可靠”等相对表达。

Visual Handoff:
{visual_handoff_raw}
"""


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
        visual = parse_fields(visual_raw, VISUAL_FIELDS)
        if bridge.get("Target Section", "").strip().lower() != "research":
            raise ParseError("Daily Email Bridge is not a research bridge.")
        if bridge.get("Image Allowed", "").strip().lower() != "true":
            raise ParseError("Research bridge does not allow image generation.")

        slug = args.asset_slug or bridge.get("Image Asset Hint") or visual.get("Named Subject") or "research-cover"
        slug = slugify(slug)
        image_path = f"assets/email/{args.date}/{slug}.png"
        paper = make_paper_json(bridge, image_path)
        prompt = make_image_prompt(visual_raw, visual, image_path)
    except (OSError, ParseError) as exc:
        raise SystemExit(f"Parse failed: {exc}") from exc

    artifact = {
        "paper": paper,
        "image_asset_path": image_path,
        "image_asset_slug": slug,
        "daily_email_bridge": bridge,
        "visual_handoff": visual,
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
