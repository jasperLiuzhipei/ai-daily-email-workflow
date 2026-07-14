#!/usr/bin/env python3
"""Render a Foxmail-friendly AI daily email from structured JSON content.

The AI should generate only the content JSON. This script owns layout, visual
style, escaping, and the stable HTML structure.
"""

from __future__ import annotations

import argparse
import html
import json
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


FONT_STACK = (
    "'Avenir Next','Helvetica Neue','PingFang SC','Microsoft YaHei',Arial,sans-serif"
)

TEXT = "#1a1d23"
TEXT_SOFT = "#3a3f4a"
TEXT_DIM = "#6b7280"
TEXT_FAINT = "#94a3b8"
BORDER = "#e5e7eb"
BORDER_STRONG = "#d8dee8"

THEMES = {
    "clay_orange": {
        "accent": "#D97757",
        "accent_dark": "#9A3412",
        "accent_soft": "#FFFCF9",
        "accent_border": "#EEE4DC",
        "tag_border": "#FDBA74",
    },
    "tech_blue": {
        "accent": "#2563EB",
        "accent_dark": "#1E3A8A",
        "accent_soft": "#F8FBFF",
        "accent_border": "#DCE8FB",
        "tag_border": "#93C5FD",
    },
    "ai_violet": {
        "accent": "#7C3AED",
        "accent_dark": "#5B21B6",
        "accent_soft": "#FCFAFF",
        "accent_border": "#E9D5FF",
        "tag_border": "#C4B5FD",
    },
    "finance_amber": {
        "accent": "#D97706",
        "accent_dark": "#92400E",
        "accent_soft": "#FFFDF7",
        "accent_border": "#EFE2C9",
        "tag_border": "#FBBF24",
    },
    "graphite_blue": {
        "accent": "#475569",
        "accent_dark": "#334155",
        "accent_soft": "#FAFBFC",
        "accent_border": "#E2E8F0",
        "tag_border": "#CBD5E1",
    },
    "editorial_mono": {
        "accent": "#171717",
        "accent_dark": "#111111",
        "accent_soft": "#FAFAF9",
        "accent_border": "#D9D9D6",
        "tag_border": "#C9C9C5",
    },
    "newsroom_mono": {
        "accent": "#111111",
        "accent_dark": "#111111",
        "accent_soft": "#F7F7F5",
        "accent_border": "#D9D9D6",
        "tag_border": "#C9C9C5",
    },
    "newsroom_navy": {
        "accent": "#2F6FAE",
        "accent_dark": "#0B2E52",
        "accent_soft": "#F3F7FB",
        "accent_border": "#D6E2ED",
        "tag_border": "#A9C3DA",
    },
    "digest_navy": {
        "accent": "#2F7FA4",
        "accent_dark": "#1E5E7C",
        "accent_soft": "#F4F9FC",
        "accent_border": "#D5E5EF",
        "tag_border": "#AFCBDB",
    },
}

ACCENT = THEMES["clay_orange"]["accent"]
ACCENT_DARK = THEMES["clay_orange"]["accent_dark"]
ACCENT_SOFT = THEMES["clay_orange"]["accent_soft"]
ACCENT_BORDER = THEMES["clay_orange"]["accent_border"]
TAG_BORDER = THEMES["clay_orange"]["tag_border"]
CURRENT_THEME = "clay_orange"
SOURCE_PRIMARY_LABEL = "阅读原文"
NEWS_LAYOUTS = ("classic", "paper", "compact", "rail")
NEWS_LAYOUT = "paper"


def apply_theme(theme_name: str) -> None:
    theme = THEMES[theme_name]
    global ACCENT, ACCENT_DARK, ACCENT_SOFT, ACCENT_BORDER, TAG_BORDER, CURRENT_THEME
    ACCENT = theme["accent"]
    ACCENT_DARK = theme["accent_dark"]
    ACCENT_SOFT = theme["accent_soft"]
    ACCENT_BORDER = theme["accent_border"]
    TAG_BORDER = theme["tag_border"]
    CURRENT_THEME = theme_name


def apply_news_layout(layout_name: str) -> None:
    if layout_name not in NEWS_LAYOUTS:
        raise ValueError(f"Unknown news layout: {layout_name}")
    global NEWS_LAYOUT
    NEWS_LAYOUT = layout_name


def esc(value: Any) -> str:
    return html.escape(str(value or ""), quote=True)


def observation_label(item: dict[str, Any]) -> str:
    """Use a precise editorial lens without inferring it from audience tags."""
    label = str(item.get("observation_label") or "").strip()
    if label in {"行业视角", "工作启发"}:
        return label
    return "行业视角"


def load_content(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def tag_html(label: str, idx: int) -> str:
    return (
        f'<td style="background:#ffffff;border:1px solid {ACCENT_BORDER};color:{ACCENT_DARK};'
        f"font-size:10px;line-height:1;padding:3px 7px;border-radius:6px;"
        f'font-weight:700;white-space:nowrap;">{esc(label)}</td>'
    )


def render_tags(tags: list[str]) -> str:
    cells: list[str] = []
    for idx, tag in enumerate(tags):
        if idx:
            cells.append('<td style="width:8px;font-size:0;line-height:0;">&nbsp;</td>')
        cells.append(tag_html(tag, idx))
    if not cells:
        return ""
    return (
        '<table role="presentation" cellpadding="0" cellspacing="0" style="margin:0 0 14px 0;">'
        f"<tr>{''.join(cells)}</tr>"
        "</table>"
    )


def source_host(url: str) -> str:
    parsed = urlparse(str(url or ""))
    host = parsed.netloc.lower().split("@")[-1].split(":")[0]
    if host.startswith("www."):
        host = host[4:]
    return host


def source_display(url: str) -> str:
    host = source_host(url)
    if not host:
        return ""

    source_names = {
        "mp.weixin.qq.com": "微信公众号",
        "x.com": "x.com",
        "twitter.com": "x.com",
        "github.com": "GitHub",
        "gitlab.com": "GitLab",
        "huggingface.co": "Hugging Face",
        "arxiv.org": "arxiv.org",
        "aihot.virxact.com": "AIHot",
    }
    if host in source_names:
        return source_names[host]
    return host


def source_link_label(link: dict[str, str], idx: int, context: str = "research") -> str:
    url = str(link.get("url") or "")
    host = source_host(url)
    raw_label = str(link.get("label") or "").lower()
    raw_url = url.lower()

    if context == "news":
        return SOURCE_PRIMARY_LABEL
    if any(token in raw_label or token in raw_url for token in ["github", "gitlab", "repo", "repository", "code", "代码"]):
        return "代码仓库"
    if "huggingface.co" in raw_url and any(token in raw_label for token in ["repo", "model", "dataset", "仓库", "模型"]):
        return "代码仓库"
    if idx > 0 and any(token in raw_label or token in raw_url for token in ["arxiv", "paper", "pdf", "论文"]):
        return "论文链接"
    if any(token in raw_label or token in raw_url for token in ["demo", "playground", "体验"]):
        return "在线体验"
    return SOURCE_PRIMARY_LABEL


def render_source_button(link: dict[str, str], idx: int = 0, context: str = "research") -> str:
    url = str(link.get("url") or "")
    if not url:
        return ""
    label = source_link_label(link, idx, context)
    source = source_display(url)
    source_html = ""
    if source:
        source_html = (
            f'<span style="font-size:11px;line-height:1;color:{TEXT_DIM};'
            f"font-family:'SFMono-Regular','Cascadia Mono',Consolas,monospace;"
            f'font-weight:600;padding-left:8px;">{esc(source)}</span>'
        )
    return (
        f'<td style="border:1px solid {ACCENT_BORDER};border-radius:6px;background:#ffffff;">'
        f'<a href="{esc(url)}" style="display:inline-block;padding:6px 9px;'
        f"color:{ACCENT_DARK};text-decoration:none;font-size:12px;line-height:1;"
        f'font-weight:700;white-space:nowrap;">'
        f'<span style="color:{ACCENT_DARK};">{esc(label)}</span>'
        f"{source_html}"
        "</a>"
        "</td>"
    )


def render_buttons(links: list[dict[str, str]]) -> str:
    cells: list[str] = []
    for idx, link in enumerate(links):
        if idx:
            cells.append('<td style="width:8px;font-size:0;line-height:0;">&nbsp;</td>')
        cells.append(render_source_button(link, idx, "research"))
    if not cells:
        return ""
    return (
        '<table role="presentation" cellpadding="0" cellspacing="0" style="margin-top:12px;">'
        f"<tr>{''.join(cells)}</tr>"
        "</table>"
    )


def render_link_button(url: str, label: str = SOURCE_PRIMARY_LABEL) -> str:
    if not url:
        return ""
    return (
        '<table role="presentation" cellpadding="0" cellspacing="0" style="margin-top:12px;">'
        "<tr>"
        f'{render_source_button({"url": url, "label": label}, 0, "news")}'
        "</tr>"
        "</table>"
    )


def render_section_heading(title: str, subtitle: str = "", label: str = "", number: str = "") -> str:
    number_html = ""
    if number:
        number_html = (
            f'<span style="font-size:24px;line-height:1.35;color:{ACCENT};font-weight:500;'
            f'letter-spacing:1.2px;padding-right:10px;vertical-align:baseline;">{esc(number)}</span>'
        )
    label_html = ""
    if label:
        label_html = (
            f'<span style="font-size:11px;line-height:1;color:{ACCENT};font-weight:700;'
            f'letter-spacing:2.2px;padding-left:12px;vertical-align:middle;">'
            f"{esc(label.lower())}</span>"
        )
    subtitle_html = ""
    if subtitle:
        subtitle_html = (
            f'<div style="font-size:13px;line-height:1.75;color:{TEXT_DIM};padding-top:6px;">'
            f"{esc(subtitle)}</div>"
        )
    return f"""
      <tr>
        <td style="padding:22px 34px 12px 46px;">
          <table role="presentation" width="100%" cellpadding="0" cellspacing="0">
            <tr>
              <td style="padding-top:8px;">
                <table role="presentation" width="100%" cellpadding="0" cellspacing="0">
                  <tr>
                    <td valign="top">
                      <div style="font-size:24px;line-height:1.35;font-weight:500;color:{TEXT};letter-spacing:1.2px;">{number_html}{esc(title)}{label_html}</div>
                      {subtitle_html}
                    </td>
                  </tr>
                </table>
              </td>
            </tr>
          </table>
        </td>
      </tr>
    """


def render_paper_step(title: str, text: str) -> str:
    if not text:
        return ""
    return f"""
      <tr>
        <td valign="top" style="padding:14px 2px 15px 16px;border-bottom:1px solid #edf1f7;">
          <div style="font-size:14px;line-height:1.45;color:{TEXT};font-weight:800;">{esc(title)}</div>
          <div style="font-size:13px;line-height:1.8;color:{TEXT_SOFT};padding-top:6px;">{esc(text)}</div>
        </td>
      </tr>
    """


def paper_field(paper: dict[str, Any], key: str, legacy_key: str = "") -> str:
    value = paper.get(key)
    if isinstance(value, str) and value.strip():
        return value.strip()
    if legacy_key:
        legacy_value = paper.get(legacy_key)
        if isinstance(legacy_value, str) and legacy_value.strip():
            return legacy_value.strip()
    return ""


def paper_evidence_text(paper: dict[str, Any]) -> str:
    value = paper.get("evidence")
    if isinstance(value, str) and value.strip():
        return value.strip()
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            if not isinstance(item, dict):
                continue
            label = str(item.get("label") or "").strip()
            text = str(item.get("text") or "").strip()
            if label and text:
                parts.append(f"{label}：{text}")
            elif text:
                parts.append(text)
        return "；".join(parts)
    return ""


def render_evidence(items: list[dict[str, str]]) -> str:
    cells: list[str] = []
    for idx, item in enumerate(items[:3]):
        if idx:
            cells.append('<td style="width:10px;font-size:0;line-height:0;">&nbsp;</td>')
        cells.append(
            f'<td width="33%" valign="top" style="background:#fafbfc;border:1px solid {BORDER};'
            'border-radius:10px;padding:12px 12px;">'
            f'<div style="font-size:11px;line-height:1;color:{TEXT_DIM};font-weight:800;">{esc(item.get("label"))}</div>'
            f'<div style="font-size:15px;line-height:1.55;color:{TEXT};font-weight:800;padding-top:7px;">{esc(item.get("text"))}</div>'
            "</td>"
        )
    if not cells:
        return ""
    return (
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin-top:16px;">'
        f"<tr>{''.join(cells)}</tr>"
        "</table>"
    )


def render_news_boundary(text: str, margin_top: str = "12px") -> str:
    if not text:
        return ""
    return f"""
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin-top:{margin_top};background:#fffaf0;border:1px solid #fdecc8;border-radius:10px;">
        <tr>
          <td style="padding:9px 11px;font-size:12px;line-height:1.7;color:#8a5a12;">
            <strong>边界说明：</strong>{esc(text)}
          </td>
        </tr>
      </table>
    """


def render_news_field(label: str, text: str, pad_top: str = "0") -> str:
    return f"""
      <tr>
        <td valign="top" width="72" style="font-size:12px;line-height:1.75;color:{TEXT_DIM};font-weight:800;padding-top:{pad_top};">{esc(label)}</td>
        <td style="font-size:13px;line-height:1.75;color:{TEXT_SOFT};padding-top:{pad_top};">{esc(text)}</td>
      </tr>
    """


def render_news_step(label: str, text: str, is_last: bool = False) -> str:
    border = f"border-bottom:1px solid {BORDER};" if not is_last else ""
    return f"""
      <tr>
        <td style="padding:0;">
          <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="{border}">
            <tr>
              <td valign="top" width="78" style="padding:10px 0 11px 0;">
                <div style="font-size:13px;line-height:1.7;color:{TEXT_DIM};font-weight:800;">{esc(label)}</div>
              </td>
              <td valign="top" style="padding:10px 0 11px 0;">
                <div style="font-size:13px;line-height:1.7;color:{TEXT_SOFT};">{esc(text)}</div>
              </td>
            </tr>
          </table>
        </td>
      </tr>
    """


def render_news_card_classic(item: dict[str, Any], index: int) -> str:
    boundary = render_news_boundary(item.get("boundary", ""))
    return f"""
      <tr>
        <td style="padding:0 34px 12px 34px;">
          <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="border:1px solid {BORDER};border-radius:12px;background:#ffffff;">
            <tr>
              <td valign="top" width="56" style="padding:18px 0 18px 18px;">
                <div style="font-size:13px;line-height:1;color:{ACCENT};font-weight:800;">{index:02d}</div>
              </td>
              <td valign="top" style="padding:17px 18px 17px 0;">
                {render_tags(item.get("tags", []))}
                <div style="font-size:16px;line-height:1.45;font-weight:800;color:{TEXT};">{esc(item.get("title"))}</div>
                <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin-top:12px;">
                  {render_news_field("核心摘要", item.get("one_liner", ""))}
                  {render_news_field("关注价值", item.get("why", ""), "5px")}
                  {render_news_field(observation_label(item), item.get("observation", ""), "5px")}
                </table>
                {boundary}
              </td>
            </tr>
            <tr>
              <td colspan="2" style="padding:0 18px 17px 18px;">
                {render_link_button(item.get("url"))}
              </td>
            </tr>
          </table>
        </td>
      </tr>
    """


def render_news_card_paper(item: dict[str, Any], index: int) -> str:
    boundary = render_news_boundary(item.get("boundary", ""))
    return f"""
      <tr>
        <td style="padding:0 34px 12px 34px;">
          <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="border:1px solid {BORDER};border-radius:12px;background:#ffffff;">
            <tr>
              <td style="padding:18px 18px 18px 18px;">
                <div style="font-size:16px;line-height:1.45;font-weight:800;color:{TEXT};">{esc(item.get("title"))}</div>
                <div style="padding-top:12px;">{render_tags(item.get("tags", []))}</div>
                <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin-top:6px;">
                  {render_news_step("核心摘要", item.get("one_liner", ""))}
                  {render_news_step("关注价值", item.get("why", ""))}
                  {render_news_step(observation_label(item), item.get("observation", ""), True)}
                </table>
                {boundary}
                {render_link_button(item.get("url"))}
              </td>
            </tr>
          </table>
        </td>
      </tr>
    """


def render_news_list_item_paper(item: dict[str, Any], is_last: bool = False) -> str:
    boundary = render_news_boundary(item.get("boundary", ""))
    bottom_padding = "20px" if is_last else "0"
    divider = ""
    if not is_last:
        divider = f"""
          <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin-top:20px;">
            <tr>
              <td width="14" style="font-size:0;line-height:0;">&nbsp;</td>
              <td style="height:1px;background:{BORDER};font-size:0;line-height:0;">&nbsp;</td>
              <td width="14" style="font-size:0;line-height:0;">&nbsp;</td>
            </tr>
          </table>
        """
    return f"""
      <tr>
        <td style="padding:20px 20px {bottom_padding} 20px;">
          <table role="presentation" width="100%" cellpadding="0" cellspacing="0">
            <tr>
              <td valign="top" width="3" style="padding-top:4px;font-size:0;line-height:0;">
                <div style="width:3px;height:18px;background:{ACCENT};border-radius:2px;font-size:0;line-height:0;">&nbsp;</div>
              </td>
              <td valign="top" style="padding-left:10px;">
                <div style="font-size:16px;line-height:1.45;font-weight:800;color:{TEXT};">{esc(item.get("title"))}</div>
              </td>
            </tr>
          </table>
          <div style="padding-top:12px;">{render_tags(item.get("tags", []))}</div>
          <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin-top:6px;">
            {render_news_step("核心摘要", item.get("one_liner", ""))}
            {render_news_step("关注价值", item.get("why", ""))}
            {render_news_step(observation_label(item), item.get("observation", ""), True)}
          </table>
          {boundary}
          {render_link_button(item.get("url"))}
          {divider}
        </td>
      </tr>
    """


def render_news_list_paper(items: list[dict[str, Any]]) -> str:
    if not items:
        return ""
    rows = "\n".join(
        render_news_list_item_paper(item, idx == len(items) - 1)
        for idx, item in enumerate(items)
    )
    return f"""
      <tr>
        <td style="padding:0 34px 12px 34px;">
          <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="border:1px solid {BORDER};border-radius:12px;background:#ffffff;">
            {rows}
          </table>
        </td>
      </tr>
    """


def render_news_card_compact(item: dict[str, Any], index: int) -> str:
    boundary = render_news_boundary(item.get("boundary", ""))
    return f"""
      <tr>
        <td style="padding:0 34px 10px 34px;">
          <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="border:1px solid {BORDER};border-radius:12px;background:#ffffff;">
            <tr>
              <td style="padding:16px 18px 17px 18px;">
                <table role="presentation" width="100%" cellpadding="0" cellspacing="0">
                  <tr>
                    <td valign="top" width="42">
                      <div style="font-size:13px;line-height:1;color:{ACCENT};font-weight:800;padding-top:5px;">{index:02d}</div>
                    </td>
                    <td valign="top">
                      {render_tags(item.get("tags", []))}
                      <div style="font-size:16px;line-height:1.45;font-weight:800;color:{TEXT};">{esc(item.get("title"))}</div>
                    </td>
                  </tr>
                </table>
                <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin-top:13px;background:{ACCENT_SOFT};border:1px solid {ACCENT_BORDER};border-radius:10px;">
                  <tr>
                    <td style="padding:12px 14px;">
                      <div style="font-size:12px;line-height:1;color:{TEXT_DIM};font-weight:800;padding-bottom:6px;">核心摘要</div>
                      <div style="font-size:13px;line-height:1.75;color:{TEXT};font-weight:700;">{esc(item.get("one_liner"))}</div>
                    </td>
                  </tr>
                </table>
                <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin-top:12px;">
                  {render_news_field("关注价值", item.get("why", ""))}
                  {render_news_field(observation_label(item), item.get("observation", ""), "6px")}
                </table>
                {boundary}
                {render_link_button(item.get("url"))}
              </td>
            </tr>
          </table>
        </td>
      </tr>
    """


def render_news_card_rail(item: dict[str, Any], index: int) -> str:
    boundary = render_news_boundary(item.get("boundary", ""))
    return f"""
      <tr>
        <td style="padding:0 34px 12px 34px;">
          <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="border:1px solid {BORDER};border-radius:12px;background:#ffffff;">
            <tr>
              <td valign="top" width="74" style="background:{ACCENT_SOFT};border-right:1px solid {ACCENT_BORDER};padding:18px 0;text-align:center;">
                <div style="font-size:24px;line-height:1;color:{ACCENT};font-weight:800;">{index:02d}</div>
              </td>
              <td valign="top" style="padding:17px 18px 17px 18px;">
                {render_tags(item.get("tags", []))}
                <div style="font-size:16px;line-height:1.45;font-weight:800;color:{TEXT};">{esc(item.get("title"))}</div>
                <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin-top:12px;">
                  {render_news_field("核心摘要", item.get("one_liner", ""))}
                  {render_news_field("关注价值", item.get("why", ""), "6px")}
                  {render_news_field(observation_label(item), item.get("observation", ""), "6px")}
                </table>
                {boundary}
                {render_link_button(item.get("url"))}
              </td>
            </tr>
          </table>
        </td>
      </tr>
    """


def render_news_card(item: dict[str, Any], index: int) -> str:
    if NEWS_LAYOUT == "paper":
        return render_news_card_paper(item, index)
    if NEWS_LAYOUT == "compact":
        return render_news_card_compact(item, index)
    if NEWS_LAYOUT == "rail":
        return render_news_card_rail(item, index)
    return render_news_card_classic(item, index)


def research_items(data: dict[str, Any]) -> list[dict[str, Any]]:
    items = data.get("research_items")
    if isinstance(items, list) and items:
        return [item for item in items if isinstance(item, dict)]
    paper = data.get("paper")
    if isinstance(paper, dict):
        return [paper]
    return []


def render_research_card(paper: dict[str, Any]) -> str:
    item_title = paper.get("title")
    conclusion = paper_field(paper, "conclusion", "one_liner")
    problem = paper_field(paper, "problem", "question")
    mechanism = paper_field(paper, "mechanism", "core_idea")
    evidence = paper_evidence_text(paper)
    return f"""
          <tr>
            <td style="padding:0 34px 8px 34px;">
              <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="border:1px solid {BORDER};background:#ffffff;border-radius:12px;">
                <tr>
                  <td style="padding:18px 18px 12px 18px;">
                    <table role="presentation" width="100%" cellpadding="0" cellspacing="0">
                      <tr>
                        <td valign="top" width="3" style="padding-top:5px;font-size:0;line-height:0;">
                          <div style="width:3px;height:18px;background:{ACCENT};border-radius:2px;font-size:0;line-height:0;">&nbsp;</div>
                        </td>
                        <td valign="top" style="padding-left:10px;">
                          <div style="font-size:18px;line-height:1.45;color:{TEXT};font-weight:700;letter-spacing:.2px;">{esc(item_title)}</div>
                        </td>
                      </tr>
                    </table>
                  </td>
                </tr>
                <tr>
                  <td style="padding:0 14px 0 14px;">
                    <img src="{esc(paper.get('image'))}" width="584" alt="{esc(paper.get('image_alt'))}" style="display:block;width:584px;max-width:100%;height:auto;border:0;border-radius:12px;">
                  </td>
                </tr>
                <tr>
                  <td style="padding:18px 18px 18px 18px;">
                    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:{ACCENT_SOFT};border:1px solid {ACCENT_BORDER};border-radius:10px;">
                      <tr>
                        <td style="padding:15px 16px;">
                          <div style="font-size:12px;line-height:1;color:{TEXT_DIM};font-weight:800;padding-bottom:7px;">核心结论</div>
                          <div style="font-size:15px;line-height:1.85;color:{TEXT};font-weight:700;">{esc(conclusion)}</div>
                        </td>
                      </tr>
                    </table>

                    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin-top:16px;">
                      {render_paper_step("研究问题", problem)}
                      {render_paper_step("方法机制", mechanism)}
                      {render_paper_step("关键证据", evidence)}
                      {render_paper_step("应用启发", paper.get("insight"))}
                    </table>

                    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin-top:14px;background:#fffaf0;border:1px solid #fdecc8;border-radius:12px;">
                      <tr>
                        <td style="padding:12px 14px;font-size:13px;line-height:1.75;color:#8a5a12;">
                          <strong>边界说明：</strong>{esc(paper.get("boundary"))}
                        </td>
                      </tr>
                    </table>

                    {render_buttons(paper.get("links", []))}
                  </td>
                </tr>
              </table>
            </td>
          </tr>
    """


def render_research_cards(items: list[dict[str, Any]]) -> str:
    return "\n".join(render_research_card(item) for item in items)


def render_mono_section_heading(title: str, subtitle: str, label: str, number: str) -> str:
    return f"""
      <tr>
        <td style="padding:30px 44px 14px 44px;">
          <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="border-top:1px solid #171717;">
            <tr>
              <td style="padding-top:15px;">
                <div style="font-size:12px;line-height:1;color:#737373;font-weight:700;letter-spacing:2px;">{esc(number)}</div>
                <div style="padding-top:9px;font-size:25px;line-height:1.28;color:#111111;font-weight:500;letter-spacing:1px;">
                  {esc(title)}
                  <span style="font-size:11px;line-height:1;color:#737373;font-weight:700;letter-spacing:2.2px;padding-left:12px;vertical-align:middle;">{esc(label.lower())}</span>
                </div>
                <div style="font-size:13px;line-height:1.75;color:#666666;padding-top:7px;">{esc(subtitle)}</div>
              </td>
            </tr>
          </table>
        </td>
      </tr>
    """


def render_mono_note(label: str, text: str, tone: str = "muted") -> str:
    if not text:
        return ""
    color = "#595959" if tone == "muted" else "#303030"
    return f"""
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin-top:15px;border-top:1px solid #D9D9D6;">
        <tr>
          <td style="padding:10px 0 0 10px;border-left:2px solid #A3A3A3;font-size:12px;line-height:1.75;color:{color};">
            <strong style="color:#303030;">{esc(label)}：</strong>{esc(text)}
          </td>
        </tr>
      </table>
    """


def render_mono_research_step(title: str, text: str, is_first: bool = False) -> str:
    if not text:
        return ""
    top = "border-top:1px solid #E2E2DF;" if not is_first else ""
    return f"""
      <tr>
        <td style="padding:13px 0 14px 0;{top}">
          <table role="presentation" width="100%" cellpadding="0" cellspacing="0">
            <tr>
              <td valign="top" width="90" style="font-size:11px;line-height:1.7;color:#707070;font-weight:700;letter-spacing:1px;">{esc(title)}</td>
              <td valign="top" style="font-size:13px;line-height:1.78;color:#383838;">{esc(text)}</td>
            </tr>
          </table>
        </td>
      </tr>
    """


def render_research_card_mono(paper: dict[str, Any]) -> str:
    conclusion = paper_field(paper, "conclusion", "one_liner")
    problem = paper_field(paper, "problem", "question")
    mechanism = paper_field(paper, "mechanism", "core_idea")
    evidence = paper_evidence_text(paper)
    steps = "".join(
        [
            render_mono_research_step("研究问题", problem, True),
            render_mono_research_step("方法机制", mechanism),
            render_mono_research_step("关键证据", evidence),
            render_mono_research_step("应用启发", paper_field(paper, "insight")),
        ]
    )
    return f"""
      <tr>
        <td style="padding:0 44px 2px 44px;">
          <table role="presentation" width="100%" cellpadding="0" cellspacing="0">
            <tr>
              <td style="padding:3px 0 18px 0;">
                <table role="presentation" width="100%" cellpadding="0" cellspacing="0">
                  <tr>
                    <td valign="top" width="3" style="padding-top:4px;font-size:0;line-height:0;">
                      <div style="width:3px;height:22px;background:#111111;font-size:0;line-height:0;">&nbsp;</div>
                    </td>
                    <td valign="top" style="padding-left:11px;font-size:20px;line-height:1.42;color:#111111;font-weight:600;letter-spacing:.35px;">{esc(paper.get("title"))}</td>
                  </tr>
                </table>
              </td>
            </tr>
            <tr>
              <td style="border:1px solid #D9D9D6;">
                <img src="{esc(paper.get('image'))}" width="590" alt="{esc(paper.get('image_alt'))}" style="display:block;width:590px;max-width:100%;height:auto;border:0;">
              </td>
            </tr>
            <tr>
              <td style="padding:19px 0 0 0;">
                <div style="border-left:3px solid #111111;padding:0 0 0 13px;">
                  <div style="font-size:11px;line-height:1;color:#707070;font-weight:700;letter-spacing:1.2px;padding-bottom:7px;">核心结论</div>
                  <div style="font-size:15px;line-height:1.82;color:#202020;font-weight:600;">{esc(conclusion)}</div>
                </div>
                <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin-top:18px;">
                  {steps}
                </table>
                {render_mono_note("边界说明", paper_field(paper, "boundary"))}
                {render_buttons(paper.get("links", []))}
              </td>
            </tr>
          </table>
        </td>
      </tr>
    """


def render_mono_news_line(label: str, text: str, is_first: bool = False) -> str:
    top = "" if is_first else "border-top:1px solid #E4E4E1;"
    return f"""
      <tr>
        <td style="padding:11px 0 12px 0;{top}">
          <table role="presentation" width="100%" cellpadding="0" cellspacing="0">
            <tr>
              <td valign="top" width="78" style="font-size:11px;line-height:1.72;color:#707070;font-weight:700;letter-spacing:1px;">{esc(label)}</td>
              <td valign="top" style="font-size:13px;line-height:1.78;color:#383838;">{esc(text)}</td>
            </tr>
          </table>
        </td>
      </tr>
    """


def render_mono_news_item(item: dict[str, Any], index: int, is_last: bool) -> str:
    divider = "" if is_last else '<tr><td style="height:1px;background:#CFCFCC;font-size:0;line-height:0;">&nbsp;</td></tr>'
    return f"""
      <tr>
        <td style="padding:23px 0 24px 0;">
          <table role="presentation" width="100%" cellpadding="0" cellspacing="0">
            <tr>
              <td valign="top" width="34" style="padding-top:4px;font-size:11px;line-height:1;color:#777777;font-weight:700;letter-spacing:1px;">{index:02d}</td>
              <td valign="top">
                <div style="font-size:18px;line-height:1.42;color:#121212;font-weight:600;letter-spacing:.15px;">{esc(item.get("title"))}</div>
                <div style="padding-top:11px;">{render_tags(item.get("tags", []))}</div>
                <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin-top:3px;">
                  {render_mono_news_line("核心摘要", item.get("one_liner", ""), True)}
                  {render_mono_news_line("关注价值", item.get("why", ""))}
                  {render_mono_news_line(observation_label(item), item.get("observation", ""))}
                </table>
                {render_mono_note("说明", item.get("boundary", ""))}
                {render_link_button(item.get("url"))}
              </td>
            </tr>
          </table>
        </td>
      </tr>
      {divider}
    """


def render_news_list_mono(items: list[dict[str, Any]]) -> str:
    rows = "".join(
        render_mono_news_item(item, idx + 1, idx == len(items) - 1)
        for idx, item in enumerate(items)
    )
    return f"""
      <tr>
        <td style="padding:0 44px 8px 44px;">
          <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="border-top:1px solid #171717;border-bottom:1px solid #171717;">
            {rows}
          </table>
        </td>
      </tr>
    """


def render_mono_closing(title: str, body: str, contact: str = "") -> str:
    contact_html = ""
    if contact:
        contact_html = f'<div style="font-size:12px;line-height:1.8;color:#666666;padding-top:10px;">{contact}</div>'
    return f"""
      <tr>
        <td style="padding:22px 44px 0 44px;">
          <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="border-top:1px solid #171717;">
            <tr>
              <td style="padding-top:14px;">
                <div style="font-size:13px;line-height:1.5;color:#171717;font-weight:700;letter-spacing:1px;">{esc(title)}</div>
                <div style="font-size:13px;line-height:1.84;color:#4B4B4B;padding-top:7px;">{esc(body)}</div>
                {contact_html}
              </td>
            </tr>
          </table>
        </td>
      </tr>
    """


def render_email_editorial_mono(data: dict[str, Any]) -> str:
    research = research_items(data)
    paper = research[0]
    news_items = data.get("news", [])
    footer = data.get("footer", {})
    news_section = data.get("news_section", {})
    display_date = data.get("display_date") or data.get("date")
    contact = f'{esc(footer.get("name"))}｜<a href="mailto:{esc(footer.get("email"))}" style="color:#111111;text-decoration:underline;">{esc(footer.get("email"))}</a>'

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{esc(data.get("title"))}</title>
</head>
<body style="margin:0;padding:0;background:#F4F4F2;font-family:{FONT_STACK};color:#111111;">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="width:100%;background:#F4F4F2;margin:0;padding:28px 0;">
    <tr>
      <td align="center" style="padding:0 12px;">
        <table role="presentation" width="680" cellpadding="0" cellspacing="0" style="width:680px;background:#FFFFFF;border:1px solid #D9D9D6;">
          <tr>
            <td style="padding:42px 44px 28px 44px;">
              <div style="font-size:11px;line-height:1;color:#5E5E5E;font-weight:700;letter-spacing:3.5px;padding-bottom:18px;">{esc(data.get("eyebrow", "AI SIGNAL RADAR"))}</div>
              <div style="font-size:36px;line-height:1.16;color:#101010;font-weight:700;letter-spacing:.6px;">今日 <span style="font-weight:800;">AI</span> 情报雷达</div>
              <div style="font-size:13px;line-height:1.8;color:#666666;padding-top:15px;letter-spacing:.5px;">
                <span style="font-weight:700;color:#202020;">{esc(display_date)}</span>
                <span style="color:#A0A0A0;padding:0 9px;">/</span>
                <span>{esc(data.get("subtitle"))}</span>
              </div>
              <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin-top:26px;"><tr><td width="52" style="height:2px;background:#171717;font-size:0;line-height:0;">&nbsp;</td><td style="height:1px;background:#D9D9D6;font-size:0;line-height:0;">&nbsp;</td></tr></table>
            </td>
          </tr>

          <tr>
            <td style="padding:0 44px 2px 44px;">
              <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="border-top:1px solid #D9D9D6;">
                <tr>
                  <td style="padding:14px 0 4px 12px;border-left:3px solid #171717;">
                    <div style="font-size:12px;line-height:1;color:#2F2F2F;font-weight:700;letter-spacing:1px;padding-bottom:7px;">今日编辑简评</div>
                    <div style="font-size:14px;line-height:1.9;color:#424242;">{esc(data.get("editor_note"))}</div>
                  </td>
                </tr>
              </table>
            </td>
          </tr>

          {render_mono_section_heading(paper.get("section_title", "研究前沿"), "把前沿方法转译成可理解、可讨论的工作启发", "RESEARCH PICK", "01")}
          {render_research_card_mono(paper)}
          {render_mono_section_heading(news_section.get("title", "行业动态"), news_section.get("subtitle", ""), "INDUSTRY SIGNALS", "02")}
          {render_news_list_mono(news_items)}
          {render_mono_closing("今日小结", data.get("summary", ""))}
          {render_mono_closing(footer.get("title", "试运行说明"), footer.get("body", ""), contact)}
          <tr><td style="height:36px;font-size:0;line-height:0;">&nbsp;</td></tr>
        </table>
      </td>
    </tr>
  </table>
</body>
</html>
"""


def render_newsroom_tags(tags: list[str]) -> str:
    visible = [str(tag).strip() for tag in tags if str(tag).strip()]
    if not visible:
        return ""
    return (
        f'<div style="font-size:11px;line-height:1.5;color:{ACCENT};font-weight:700;letter-spacing:.6px;">'
        f"{esc(' · '.join(visible))}"
        "</div>"
    )


def render_newsroom_source_link(url: str) -> str:
    if not url:
        return ""
    source = source_display(url)
    suffix = f" / {esc(source)}" if source else ""
    return (
        '<div style="font-size:12px;line-height:1.5;padding-top:14px;">'
        f'<a href="{esc(url)}" style="color:{ACCENT_DARK};text-decoration:underline;font-weight:700;">阅读原文</a>'
        f'<span style="color:#808080;font-family:\'SFMono-Regular\',\'Cascadia Mono\',Consolas,monospace;font-size:11px;">{suffix}</span>'
        "</div>"
    )


def render_newsroom_research_detail(label: str, text: str) -> str:
    if not text:
        return ""
    return f"""
      <tr>
        <td style="padding:0 0 15px 0;">
          <div style="font-size:11px;line-height:1.35;color:{ACCENT};font-weight:700;letter-spacing:1.2px;padding-bottom:5px;">{esc(label)}</div>
          <div style="font-size:13px;line-height:1.76;color:#3D3D3D;">{esc(text)}</div>
        </td>
      </tr>
    """


def render_newsroom_boundary_hint(text: str) -> str:
    if not text:
        return ""
    background = "#F0F6FC" if CURRENT_THEME == "newsroom_navy" else "#F4F4F2"
    return f"""
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin-top:4px;background:{background};">
        <tr>
          <td style="padding:12px 14px;font-size:12px;line-height:1.75;color:#555555;">
            <strong style="color:{ACCENT_DARK};">适用边界：</strong>{esc(text)}
          </td>
        </tr>
      </table>
    """


def render_research_feature_newsroom(paper: dict[str, Any]) -> str:
    conclusion = paper_field(paper, "conclusion", "one_liner")
    details = "".join(
        [
            render_newsroom_research_detail("研究问题", paper_field(paper, "problem", "question")),
            render_newsroom_research_detail("方法机制", paper_field(paper, "mechanism", "core_idea")),
            render_newsroom_research_detail("关键证据", paper_evidence_text(paper)),
            render_newsroom_research_detail("应用启发", paper_field(paper, "insight")),
        ]
    )
    boundary = paper_field(paper, "boundary")
    return f"""
      <tr>
        <td style="padding:0 44px 10px 44px;">
          <table role="presentation" width="100%" cellpadding="0" cellspacing="0">
            <tr>
              <td style="padding:3px 0 16px 0;">
                <div style="font-size:26px;line-height:1.3;color:#111111;font-weight:700;letter-spacing:.2px;">{esc(paper.get("title"))}</div>
              </td>
            </tr>
            <tr>
              <td style="border:1px solid {ACCENT_BORDER};background:#FFFFFF;">
                <img src="{esc(paper.get('image'))}" width="590" alt="{esc(paper.get('image_alt'))}" style="display:block;width:590px;max-width:100%;height:auto;border:0;">
              </td>
            </tr>
            <tr>
              <td style="padding:20px 0 0 0;">
                <div style="font-size:17px;line-height:1.65;color:{TEXT};font-weight:600;">{esc(conclusion)}</div>
              </td>
            </tr>
            <tr>
              <td style="padding-top:20px;">
                <table role="presentation" width="100%" cellpadding="0" cellspacing="0">{details}</table>
                {render_newsroom_boundary_hint(boundary)}
                <table role="presentation" cellpadding="0" cellspacing="0" style="margin-top:14px;"><tr><td style="font-size:12px;line-height:1.5;">{''.join(render_newsroom_source_link(str(link.get('url') or '')) for link in paper.get('links', []))}</td></tr></table>
              </td>
            </tr>
          </table>
        </td>
      </tr>
    """


def render_newsroom_item(item: dict[str, Any]) -> str:
    boundary = item.get("boundary", "")
    observation = item.get("observation", "")
    why = item.get("why", "")
    boundary_html = ""
    if boundary:
        boundary_html = f'<div style="font-size:11px;line-height:1.7;color:#777777;padding-top:10px;">注：{esc(boundary)}</div>'
    return f"""
      <tr>
        <td style="padding:0 44px 30px 44px;">
          <table role="presentation" width="100%" cellpadding="0" cellspacing="0">
            <tr>
              <td valign="top">
                {render_newsroom_tags(item.get("tags", []))}
                <div style="font-size:20px;line-height:1.38;color:#111111;font-weight:700;letter-spacing:.1px;padding-top:7px;">{esc(item.get("title"))}</div>
                <div style="font-size:15px;line-height:1.72;color:#262626;font-weight:600;padding-top:10px;">{esc(item.get("one_liner"))}</div>
                <div style="font-size:13px;line-height:1.8;color:#494949;padding-top:13px;"><strong style="color:{ACCENT_DARK};">为什么值得看：</strong>{esc(why)}</div>
                <div style="font-size:13px;line-height:1.8;color:#494949;padding-top:7px;"><strong style="color:{ACCENT_DARK};">{esc(observation_label(item))}：</strong>{esc(observation)}</div>
                {boundary_html}
                {render_newsroom_source_link(item.get("url", ""))}
              </td>
            </tr>
          </table>
        </td>
      </tr>
    """


def render_newsroom_news_list(items: list[dict[str, Any]]) -> str:
    return "".join(render_newsroom_item(item) for item in items)


def render_newsroom_section_heading(number: str, title: str, label: str, subtitle: str) -> str:
    return f"""
      <tr>
        <td style="padding:38px 44px 18px 44px;">
          <div style="font-size:12px;line-height:1;color:{ACCENT};font-weight:700;letter-spacing:2px;">{esc(number)} / {esc(label)}</div>
          <div style="font-size:27px;line-height:1.25;color:#111111;font-weight:700;letter-spacing:.3px;padding-top:9px;">{esc(title)}</div>
          <div style="font-size:13px;line-height:1.75;color:#666666;padding-top:7px;">{esc(subtitle)}</div>
        </td>
      </tr>
    """


def render_email_newsroom_mono(data: dict[str, Any]) -> str:
    research = research_items(data)
    paper = research[0]
    news_section = data.get("news_section", {})
    footer = data.get("footer", {})
    display_date = data.get("display_date") or data.get("date")
    footer_contact = (
        f'{esc(footer.get("name"))}｜<a href="mailto:{esc(footer.get("email"))}" '
        f'style="color:{ACCENT_DARK};text-decoration:underline;">'
        f'{esc(footer.get("email"))}</a>'
    )
    is_navy = CURRENT_THEME == "newsroom_navy"
    header_muted = "#C9D8E7" if is_navy else "#C7C7C4"
    header_eyebrow = "#AFC4D8" if is_navy else "#BEBEBB"
    header_rule = "#416C95" if is_navy else "#4D4D4B"
    page_background = "#EEF3F7" if is_navy else "#F0F0EE"
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{esc(data.get("title"))}</title>
</head>
<body style="margin:0;padding:0;background:{page_background};font-family:{FONT_STACK};color:#111111;">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="width:100%;background:{page_background};margin:0;padding:28px 0;">
    <tr>
      <td align="center" style="padding:0 12px;">
        <table role="presentation" width="680" cellpadding="0" cellspacing="0" style="width:680px;background:#FFFFFF;">
          <tr>
            <td style="padding:39px 44px 34px 44px;background:{ACCENT_DARK};color:#FFFFFF;">
              <div style="font-size:11px;line-height:1;color:{header_eyebrow};font-weight:700;letter-spacing:3.6px;padding-bottom:20px;">{esc(data.get("eyebrow", "AI SIGNAL RADAR"))}</div>
              <div style="font-size:38px;line-height:1.12;color:#FFFFFF;font-weight:700;letter-spacing:.5px;">AI 情报雷达</div>
              <div style="font-size:13px;line-height:1.8;color:{header_muted};padding-top:16px;letter-spacing:.5px;">
                <span style="font-weight:700;color:#FFFFFF;">{esc(display_date)}</span>
                <span style="color:#777777;padding:0 9px;">/</span>
                <span>{esc(data.get("subtitle"))}</span>
              </div>
              <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin-top:27px;"><tr><td width="60" style="height:2px;background:#FFFFFF;font-size:0;line-height:0;">&nbsp;</td><td style="height:1px;background:{header_rule};font-size:0;line-height:0;">&nbsp;</td></tr></table>
            </td>
          </tr>
          <tr>
            <td style="padding:27px 44px 5px 44px;">
              <div style="font-size:11px;line-height:1;color:{ACCENT};font-weight:700;letter-spacing:1.5px;padding-bottom:9px;">TODAY'S TAKE</div>
              <div style="font-size:17px;line-height:1.72;color:#252525;font-weight:500;">{esc(data.get("editor_note"))}</div>
            </td>
          </tr>
          {render_newsroom_section_heading("01", paper.get("section_title", "研究前沿"), "RESEARCH FRONTIER", "一篇主稿，拆开方法、证据与边界")}
          {render_research_feature_newsroom(paper)}
          {render_newsroom_section_heading("02", news_section.get("title", "行业动态"), "INDUSTRY NEWS", "精选今日值得关注的 AI 产品、技术与行业信号")}
          {render_newsroom_news_list(data.get("news", []))}
          <tr>
            <td style="padding:7px 44px 0 44px;background:{ACCENT_DARK};">
              <div style="font-size:11px;line-height:1;color:{header_eyebrow};font-weight:700;letter-spacing:1.5px;padding-top:22px;padding-bottom:9px;">EDITOR'S WRAP</div>
              <div style="font-size:18px;line-height:1.7;color:#FFFFFF;font-weight:500;padding-bottom:25px;">{esc(data.get("summary"))}</div>
            </td>
          </tr>
          <tr>
            <td style="padding:23px 44px 34px 44px;background:#FFFFFF;">
              <div style="font-size:12px;line-height:1;color:#555555;font-weight:700;letter-spacing:1.2px;padding-bottom:8px;">{esc(footer.get("title", "试运行说明"))}</div>
              <div style="font-size:12px;line-height:1.8;color:#656565;">{esc(footer.get("body", ""))}</div>
              <div style="font-size:12px;line-height:1.8;color:#656565;padding-top:10px;">{footer_contact}</div>
            </td>
          </tr>
        </table>
      </td>
    </tr>
  </table>
</body>
</html>
"""


DIGEST_CATEGORY_ORDER = (
    ("model_update", "模型更新", "MODEL UPDATES"),
    ("product_release", "产品发布", "PRODUCT RELEASES"),
    ("industry_signal", "行业动态", "INDUSTRY SIGNALS"),
    ("methods_and_views", "技巧与观点", "METHODS & VIEWS"),
)


def digest_news_category(item: dict[str, Any]) -> str:
    raw = str(item.get("category") or "").strip()
    if raw in {category[0] for category in DIGEST_CATEGORY_ORDER}:
        return raw

    tags = " ".join(str(tag) for tag in item.get("tags", [])).lower()
    title = str(item.get("title") or "").lower()
    if any(token in tags or token in title for token in ["模型", "model release", "大模型"]):
        return "model_update"
    if any(token in tags or token in title for token in ["skill", "prompt", "评测", "coding", "方法"]):
        return "methods_and_views"
    if any(token in tags or token in title for token in ["产品", "入口", "app", "agent"]):
        return "product_release"
    return "industry_signal"


def digest_news_groups(items: list[dict[str, Any]]) -> list[tuple[str, str, str, list[dict[str, Any]]]]:
    grouped: dict[str, list[dict[str, Any]]] = {category[0]: [] for category in DIGEST_CATEGORY_ORDER}
    for item in items:
        if isinstance(item, dict):
            grouped[digest_news_category(item)].append(item)
    return [
        (key, title, label, grouped[key])
        for key, title, label in DIGEST_CATEGORY_ORDER
        if grouped[key]
    ]


def render_digest_sources(links: list[dict[str, str]]) -> str:
    anchors: list[str] = []
    for link in links:
        url = str(link.get("url") or "")
        if not url:
            continue
        label = str(link.get("label") or source_display(url) or "原始来源")
        anchors.append(
            f'<a class="digest-link" href="{esc(url)}" style="color:#718096;text-decoration:none;">{esc(label)}</a>'
        )
    if not anchors:
        return ""
    return (
        '<div style="font-size:12px;line-height:1.6;color:#8A97A5;padding-top:6px;letter-spacing:.2px;">'
        f'<span style="color:{ACCENT};font-weight:600;letter-spacing:.8px;">来源</span>'
        ' <span style="color:#C0C8D0;padding:0 4px;">/</span> '
        + " <span style=\"color:#C0C8D0;padding:0 4px;\">·</span> ".join(anchors)
        + "</div>"
    )


def render_digest_news_source(url: str) -> str:
    if not url:
        return ""
    label = source_display(url) or "原始来源"
    return (
        '<div style="font-size:12px;line-height:1.6;color:#8A97A5;padding-top:6px;letter-spacing:.2px;">'
        f'<span style="color:{ACCENT};font-weight:600;letter-spacing:.8px;">来源</span>'
        f' <span style="color:#C0C8D0;padding:0 4px;">/</span> <a class="digest-link" href="{esc(url)}" style="color:#718096;text-decoration:none;">{esc(label)}</a>'
        "</div>"
    )


def digest_story_metadata(item: dict[str, Any]) -> tuple[list[str], str]:
    audience = str(item.get("audience") or "").strip()
    topics: list[str] = []
    for raw_tag in item.get("tags", []):
        tag = str(raw_tag).strip()
        if not tag:
            continue
        if tag.startswith("适合："):
            audience = audience or tag.removeprefix("适合：").strip()
        else:
            topics.append(tag)
    return topics, audience


def render_digest_metadata(item: dict[str, Any]) -> str:
    topics, audience = digest_story_metadata(item)
    parts: list[str] = []
    if topics:
        parts.append(esc(" · ".join(topics)))
    if audience:
        parts.append(f'<span style="color:{ACCENT_DARK};">适合：</span>{esc(audience)}')
    if not parts:
        return ""
    return (
        f'<div style="font-size:12px;line-height:1.55;color:{ACCENT};font-weight:600;'
        'letter-spacing:.45px;padding-top:8px;">'
        + ' <span style="color:#B6C4D0;padding:0 4px;">/</span> '.join(parts)
        + "</div>"
    )


def render_digest_section_header(
    number: str,
    title: str,
    label: str,
    count: int,
    anchor: str = "",
) -> str:
    anchor_markup = ""
    if anchor:
        anchor_markup = (
            f'<a id="{esc(anchor)}" name="{esc(anchor)}" '
            'style="display:block;height:0;font-size:0;line-height:0;">&nbsp;</a>'
        )
    return f"""
      <tr>
        <td style="padding:47px 44px 18px 44px;">
          {anchor_markup}
          <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="border-bottom:1px solid #D9E3EB;">
            <tr>
              <td style="padding:0 0 13px 0;">
                <span style="font-size:18px;line-height:1.2;color:{ACCENT};font-weight:700;letter-spacing:.6px;">{esc(number)}</span>
                <span style="font-size:20px;line-height:1.2;color:#243D4E;font-weight:600;padding-left:10px;">{esc(title)}</span>
                <span style="font-size:11px;line-height:1;color:#8194A5;font-weight:600;letter-spacing:1.8px;padding-left:10px;">{esc(label)}</span>
              </td>
              <td align="right" valign="bottom" style="padding:0 0 13px 0;font-size:12px;line-height:1;color:#657D8E;font-weight:700;white-space:nowrap;">{count} 篇</td>
            </tr>
          </table>
        </td>
      </tr>
    """


def render_digest_highlight_row(
    number: str,
    title: str,
    lead: str,
    count: int,
    is_last: bool,
    anchor: str,
) -> str:
    border = "" if is_last else "border-bottom:1px solid #E7EEF4;"
    link_style = "display:block;color:inherit;text-decoration:none;"
    return f"""
      <tr>
        <td style="padding:14px 0 {15 if is_last else 13}px 0;{border}">
          <table role="presentation" width="100%" cellpadding="0" cellspacing="0">
            <tr>
              <td valign="top" width="42" style="padding-top:1px;font-size:12px;line-height:1.4;color:{ACCENT};font-weight:700;letter-spacing:.6px;">{esc(number)}</td>
              <td valign="top">
                <a class="digest-toc-link" href="#{esc(anchor)}" style="{link_style}">
                  <div class="digest-toc-title" style="font-size:14px;line-height:1.4;color:#2D4859;font-weight:700;">{esc(title)}</div>
                  <div class="digest-toc-lead" style="font-size:13px;line-height:1.65;color:#708599;padding-top:4px;">{esc(lead)}</div>
                </a>
              </td>
              <td align="right" valign="top" width="36" style="font-size:12px;line-height:1.4;color:#738395;font-weight:700;white-space:nowrap;">{count}</td>
            </tr>
          </table>
        </td>
      </tr>
    """


def render_digest_highlights(paper: dict[str, Any], groups: list[tuple[str, str, str, list[dict[str, Any]]]]) -> str:
    entries: list[tuple[str, str, str, int, str]] = [
        ("01", "研究前沿", str(paper.get("title") or ""), 1, "digest-research")
    ]
    for idx, (key, title, _, items) in enumerate(groups, start=2):
        entries.append(
            (
                f"{idx:02d}",
                title,
                str(items[0].get("title") or ""),
                len(items),
                f"digest-{key.replace('_', '-')}",
            )
        )
    rows = "".join(
        render_digest_highlight_row(number, title, lead, count, idx == len(entries) - 1, anchor)
        for idx, (number, title, lead, count, anchor) in enumerate(entries)
    )
    total = 1 + sum(len(items) for _, _, _, items in groups)
    return f"""
      <tr>
        <td style="padding:30px 44px 0 44px;">
          <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:{ACCENT_SOFT};border:1px solid {ACCENT_BORDER};border-radius:8px;">
            <tr>
              <td style="padding:18px 20px 4px 20px;">
                <table role="presentation" width="100%" cellpadding="0" cellspacing="0">
                  <tr>
                    <td style="font-size:16px;line-height:1.2;color:#294455;font-weight:600;">今日看点</td>
                    <td align="right" style="font-size:12px;line-height:1.2;color:#7B90A1;white-space:nowrap;">{total} 篇内容 · 约 5 分钟</td>
                  </tr>
                </table>
                <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin-top:9px;">{rows}</table>
              </td>
            </tr>
          </table>
        </td>
      </tr>
    """


def render_digest_boundary(text: str) -> str:
    if not text:
        return ""
    return f"""
      <div style="margin-top:11px;background:#F2F7FB;padding:10px 12px;font-size:12px;line-height:1.75;color:#718697;">
        <strong style="color:{ACCENT_DARK};">边界提示：</strong>{esc(text)}
      </div>
    """


def should_render_digest_boundary(item: dict[str, Any], *, is_research: bool = False) -> bool:
    """Keep caveats visible only when they materially change how an item is read."""
    if is_research:
        return bool(str(item.get("boundary") or "").strip())

    boundary = str(item.get("boundary") or "").strip()
    if not boundary:
        return False
    topic_context = " ".join(
        [
            str(item.get("title") or ""),
            " ".join(str(tag) for tag in item.get("tags", [])),
        ]
    ).lower()
    risk_cues = (
        "安全",
        "隐私",
        "漏洞",
        "泄露",
        "攻击",
        "恶意",
        "供应链",
        "权限",
        "合规",
        "法律",
        "诉讼",
        "争议",
    )
    source_uncertainty_cues = (
        "传闻",
        "未证实",
        "非官方",
        "内测",
        "灰度",
        "beta",
        "preview",
    )
    return any(cue in topic_context for cue in risk_cues) or any(
        cue in boundary.lower() for cue in source_uncertainty_cues
    )


def render_digest_research_detail(label: str, text: str) -> str:
    if not text:
        return ""
    return f"""
      <tr>
        <td style="padding:0 0 16px 0;">
          <div style="font-size:12px;line-height:1.45;color:{ACCENT};font-weight:700;letter-spacing:.8px;padding-bottom:5px;">{esc(label)}</div>
          <div style="font-size:14px;line-height:1.78;color:#4D6475;">{esc(text)}</div>
        </td>
      </tr>
    """


def render_digest_research(paper: dict[str, Any]) -> str:
    details = "".join(
        [
            render_digest_research_detail("研究问题", paper_field(paper, "problem", "question")),
            render_digest_research_detail("方法机制", paper_field(paper, "mechanism", "core_idea")),
            render_digest_research_detail("关键证据", paper_evidence_text(paper)),
            render_digest_research_detail("应用启发", paper_field(paper, "insight")),
        ]
    )
    boundary = ""
    if should_render_digest_boundary(paper, is_research=True):
        boundary = render_digest_boundary(paper_field(paper, "boundary"))
    return f"""
      <tr>
        <td style="padding:0 44px 4px 44px;">
          <div style="font-size:16px;line-height:1.5;color:#243D4E;font-weight:600;letter-spacing:.02px;">{esc(paper.get("title"))}</div>
          {render_digest_metadata(paper)}
          {render_digest_sources(paper.get("links", []))}
          <div style="padding-top:16px;border:1px solid {ACCENT_BORDER};">
            <img src="{esc(paper.get('image'))}" width="590" alt="{esc(paper.get('image_alt'))}" style="display:block;width:590px;max-width:100%;height:auto;border:0;">
          </div>
          <div style="font-size:14px;line-height:1.8;color:#385668;font-weight:600;padding-top:18px;">{esc(paper_field(paper, "conclusion", "one_liner"))}</div>
          <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin-top:20px;">{details}</table>
          {boundary}
        </td>
      </tr>
    """


def digest_summary_parts(data: dict[str, Any]) -> tuple[str, str]:
    """Split the editorial conclusion into a visible thesis and supporting read."""
    configured_headline = str(data.get("summary_headline") or "").strip()
    summary = str(data.get("summary") or "").strip()
    if configured_headline:
        return configured_headline, summary

    for prefix in ("今天这期的主线是：", "今天的主线是：", "今日主线：", "编辑判断："):
        if summary.startswith(prefix):
            summary = summary[len(prefix) :].strip()
            break

    for punctuation in ("。", "！", "？"):
        position = summary.find(punctuation)
        if position < 0:
            continue
        headline = summary[:position].strip()
        supporting_text = summary[position + 1 :].strip()
        if headline and supporting_text:
            return headline, supporting_text

    return "今日主线", summary


def render_digest_story(item: dict[str, Any], is_last: bool) -> str:
    boundary = ""
    if should_render_digest_boundary(item):
        boundary = render_digest_boundary(str(item.get("boundary") or ""))
    return f"""
      <tr>
        <td style="padding:0 44px {0 if is_last else 33}px 44px;">
          <div style="font-size:16px;line-height:1.5;color:#263E4E;font-weight:600;letter-spacing:.02px;">{esc(item.get("title"))}</div>
          {render_digest_metadata(item)}
          {render_digest_news_source(str(item.get("url") or ""))}
          <div style="font-size:14px;line-height:1.78;color:#4D6475;padding-top:12px;">{esc(item.get("one_liner"))}</div>
          <div style="font-size:14px;line-height:1.8;color:#62798A;padding-top:10px;"><strong style="color:{ACCENT_DARK};">值得关注：</strong>{esc(item.get("why"))}</div>
          <div style="font-size:14px;line-height:1.8;color:#62798A;padding-top:6px;"><strong style="color:{ACCENT_DARK};">{esc(observation_label(item))}：</strong>{esc(item.get("observation"))}</div>
          {boundary}
        </td>
      </tr>
    """


def render_digest_group(
    number: int,
    key: str,
    title: str,
    label: str,
    items: list[dict[str, Any]],
) -> str:
    stories = "".join(
        render_digest_story(item, idx == len(items) - 1)
        for idx, item in enumerate(items)
    )
    return (
        render_digest_section_header(
            f"{number:02d}",
            title,
            label,
            len(items),
            anchor=f"digest-{key.replace('_', '-')}",
        )
        + stories
    )


def render_email_digest_navy(data: dict[str, Any]) -> str:
    research = research_items(data)
    paper = research[0]
    groups = digest_news_groups(data.get("news", []))
    footer = data.get("footer", {})
    display_date = data.get("display_date") or data.get("date")
    issue = data.get("issue")
    summary_headline, summary_body = digest_summary_parts(data)
    masthead_volume = str(issue) if issue else str(display_date).split()[0]
    total = 1 + sum(len(items) for _, _, _, items in groups)
    grouped_content = "".join(
        render_digest_group(index, key, title, label, items)
        for index, (key, title, label, items) in enumerate(groups, start=2)
    )
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{esc(data.get("title"))}</title>
  <style type="text/css">
    .digest-link:hover {{ color:{ACCENT} !important; text-decoration:underline !important; }}
    .digest-toc-link:hover .digest-toc-title {{ color:{ACCENT} !important; }}
    .digest-toc-link:hover .digest-toc-lead {{ color:#56758A !important; }}
  </style>
</head>
<body style="margin:0;padding:0;background:#F6F9FB;font-family:{FONT_STACK};color:#263E4E;">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="width:100%;background:#F6F9FB;margin:0;padding:28px 0;">
    <tr>
      <td align="center" style="padding:0 12px;">
        <table role="presentation" width="680" cellpadding="0" cellspacing="0" style="width:680px;background:#FFFFFF;">
          <tr>
            <td style="padding:34px 44px 0 44px;">
              <div style="font-size:11px;line-height:1;color:#7B90A1;font-weight:700;letter-spacing:3px;">VOL.{esc(masthead_volume)} · {total} STORIES · AI SIGNAL DAILY</div>
              <div style="font-size:30px;line-height:1.2;color:#203C4D;font-weight:600;letter-spacing:.15px;padding-top:24px;"><span style="color:{ACCENT};">AI</span> 情报雷达</div>
              <div style="font-size:13px;line-height:1.8;color:#7B90A1;padding-top:14px;letter-spacing:.5px;">{esc(display_date)}　·　精选 AI 行业动态</div>
              <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin-top:28px;"><tr><td style="height:3px;background:{ACCENT_DARK};font-size:0;line-height:0;">&nbsp;</td></tr><tr><td style="height:3px;background:#F4F7FA;font-size:0;line-height:0;">&nbsp;</td></tr><tr><td style="height:1px;background:{ACCENT};font-size:0;line-height:0;">&nbsp;</td></tr></table>
            </td>
          </tr>
          {render_digest_highlights(paper, groups)}
          {render_digest_section_header("01", paper.get("section_title", "研究前沿"), "RESEARCH", 1, anchor="digest-research")}
          {render_digest_research(paper)}
          {grouped_content}
          <tr>
            <td style="padding:56px 44px 0 44px;">
              <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="border-top:1px solid #D5E5EF;">
                <tr>
                  <td style="padding-top:22px;">
                    <div style="font-size:11px;line-height:1;color:{ACCENT};font-weight:700;letter-spacing:1.7px;">今日主线 <span style="color:#8A9EAE;font-weight:600;letter-spacing:1.35px;padding-left:5px;">EDITOR'S TAKE</span></div>
                    <div style="border-left:4px solid {ACCENT};margin-top:13px;padding:0 0 2px 16px;">
                      <div style="font-size:17px;line-height:1.55;color:#243D4E;font-weight:600;letter-spacing:.02px;">{esc(summary_headline)}</div>
                      <div style="font-size:14px;line-height:1.85;color:#62798A;padding-top:10px;">{esc(summary_body)}</div>
                    </div>
                  </td>
                </tr>
              </table>
            </td>
          </tr>
          <tr>
            <td style="padding:26px 44px 36px 44px;">
              <div style="font-size:12px;line-height:1.6;color:#62798A;font-weight:700;">{esc(footer.get("title", "试运行说明"))}</div>
              <div style="font-size:12px;line-height:1.8;color:#8194A5;padding-top:7px;">{esc(footer.get("body", ""))}</div>
              <div style="font-size:12px;line-height:1.8;color:#8194A5;padding-top:9px;">{esc(footer.get("name", ""))}｜<a href="mailto:{esc(footer.get("email", ""))}" style="color:{ACCENT};text-decoration:underline;">{esc(footer.get("email", ""))}</a></div>
            </td>
          </tr>
        </table>
      </td>
    </tr>
  </table>
</body>
</html>
"""


def render_email(data: dict[str, Any]) -> str:
    if CURRENT_THEME == "digest_navy":
        return render_email_digest_navy(data)
    if CURRENT_THEME in {"newsroom_mono", "newsroom_navy"}:
        return render_email_newsroom_mono(data)
    if CURRENT_THEME == "editorial_mono":
        return render_email_editorial_mono(data)
    research = research_items(data)
    paper = research[0]
    news_items = data.get("news", [])
    if NEWS_LAYOUT == "paper":
        news = render_news_list_paper(news_items)
    else:
        news = "\n".join(render_news_card(item, idx + 1) for idx, item in enumerate(news_items))
    footer = data.get("footer", {})
    news_section = data.get("news_section", {})
    paper_section_title = paper.get("section_title", "研究前沿")
    display_date = data.get("display_date") or data.get("date")

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{esc(data.get("title"))}</title>
</head>
<body style="margin:0;padding:0;background:#fafbfc;font-family:{FONT_STACK};color:{TEXT};">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="width:100%;background:#fafbfc;margin:0;padding:28px 0;">
    <tr>
      <td align="center" style="padding:0 12px;">
        <table role="presentation" width="680" cellpadding="0" cellspacing="0" style="width:680px;background:#ffffff;border:1px solid {BORDER};border-radius:14px;overflow:hidden;">
          <tr>
            <td style="padding:30px 34px 24px 34px;background:#ffffff;">
              <table role="presentation" width="100%" cellpadding="0" cellspacing="0">
                <tr>
                  <td valign="top">
                    <div style="font-size:11px;line-height:1;color:{ACCENT};font-weight:800;letter-spacing:3px;padding-bottom:16px;">{esc(data.get("eyebrow", "AI SIGNAL RADAR"))}</div>
                    <div style="font-size:30px;line-height:1.25;color:{TEXT};font-weight:800;letter-spacing:.2px;">
                      今日 <span style="color:{ACCENT};">AI</span> 情报雷达
                    </div>
                    <div style="font-size:13px;line-height:1.8;color:{TEXT_DIM};padding-top:12px;letter-spacing:.8px;">
                      <span style="font-weight:700;color:{TEXT_SOFT};">{esc(display_date)}</span>
                      <span style="color:{TEXT_FAINT};padding:0 8px;">·</span>
                      <span>{esc(data.get("subtitle"))}</span>
                    </div>
                  </td>
                </tr>
                <tr>
                  <td style="padding-top:24px;">
                    <table role="presentation" width="100%" cellpadding="0" cellspacing="0">
                      <tr>
                        <td width="38" style="height:1px;background:{ACCENT};font-size:0;line-height:0;">&nbsp;</td>
                        <td style="height:1px;background:{BORDER};font-size:0;line-height:0;">&nbsp;</td>
                      </tr>
                    </table>
                  </td>
                </tr>
              </table>
            </td>
          </tr>

          <tr>
            <td style="padding:0 34px 4px 34px;background:#ffffff;">
              <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:{ACCENT_SOFT};border:1px solid {ACCENT_BORDER};border-radius:12px;">
                <tr>
                  <td style="width:4px;background:{ACCENT};border-radius:12px 0 0 12px;font-size:0;line-height:0;">&nbsp;</td>
                  <td style="padding:16px 18px;">
                    <div style="font-size:13px;line-height:1;color:{ACCENT};font-weight:800;padding-bottom:8px;">今日编辑简评</div>
                    <div style="font-size:14px;line-height:1.9;color:{TEXT_SOFT};">{esc(data.get("editor_note"))}</div>
                  </td>
                </tr>
              </table>
            </td>
          </tr>

          {render_section_heading(paper_section_title, "把前沿方法转译成可理解、可讨论的工作启发", "RESEARCH PICK", "01")}

          {render_research_cards(research)}

          {render_section_heading(news_section.get("title", "行业动态"), news_section.get("subtitle", ""), "INDUSTRY SIGNALS", "02")}

          {news}

          <tr>
            <td style="padding:12px 34px 22px 34px;">
              <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#ffffff;border:1px solid {BORDER};border-radius:12px;">
                <tr>
                  <td style="padding:16px 18px;">
                    <div style="font-size:14px;line-height:1.7;color:{TEXT};font-weight:800;">今日小结</div>
                    <div style="font-size:13px;line-height:1.85;color:{TEXT_SOFT};padding-top:6px;">{esc(data.get("summary"))}</div>
                  </td>
                </tr>
              </table>
            </td>
          </tr>

          <tr>
            <td style="padding:0 34px 30px 34px;">
              <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#ffffff;border:1px solid {BORDER};border-radius:12px;">
                <tr>
                  <td style="padding:16px 18px;">
                    <div style="font-size:14px;line-height:1.7;color:{TEXT};font-weight:800;">{esc(footer.get("title"))}</div>
                    <div style="font-size:13px;line-height:1.85;color:{TEXT_SOFT};padding-top:6px;">{esc(footer.get("body"))}</div>
                    <div style="font-size:13px;line-height:1.8;color:{TEXT_SOFT};padding-top:10px;">
                      {esc(footer.get("name"))}｜<a href="mailto:{esc(footer.get("email"))}" style="color:{ACCENT};text-decoration:none;">{esc(footer.get("email"))}</a>
                    </div>
                  </td>
                </tr>
              </table>
            </td>
          </tr>
        </table>
      </td>
    </tr>
  </table>
</body>
</html>
"""


def main() -> None:
    parser = argparse.ArgumentParser(description="Render AI daily email HTML.")
    parser.add_argument("--content", required=True, type=Path, help="Path to content JSON.")
    parser.add_argument("--output", required=True, type=Path, help="Path to output HTML.")
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
    args = parser.parse_args()

    apply_theme(args.theme)
    apply_news_layout(args.news_layout)
    data = load_content(args.content)
    html_text = render_email(data)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(html_text, encoding="utf-8")
    print(f"Wrote {args.output}")


if __name__ == "__main__":
    main()
