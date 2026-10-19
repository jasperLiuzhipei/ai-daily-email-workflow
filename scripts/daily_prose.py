"""Shared editorial checks for reader-facing daily copy, never source/visual notes.

Budgets trigger a source-grounded editorial rewrite or block publication. They
must never be used to slice strings: a qualifier at the end may change a claim.
Count non-whitespace Unicode characters, including Latin names and numbers.
"""
from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Any

POLICY_VERSION = "daily-prose-v2-complete-reading"
PAPER_FIELDS = ("conclusion", "problem", "mechanism", "evidence", "insight", "boundary")
PAPER_LIMITS = dict(conclusion=180, problem=220, mechanism=450, evidence=520, insight=260, boundary=220)
PAPER_TOTAL_LIMIT = 1500
NEWS_LIMITS = {
    "brief": dict(one_liner=140, why=80, observation=85, boundary=110),
    "deep": dict(one_liner=320, why=220, observation=260, boundary=180),
}
# Internal processing comments are not limitations a reader needs. Factual
# limits such as beta access, self-reported results and unverified attribution
# remain allowed and must survive editing when they affect the stated claim.
INTERNAL_COPY = (
    re.compile(r"(?:文末|页面|网页).{0,18}(?:导航文字|导航菜单|不属于本事件)"),
    re.compile(r"(?:提供的|所给|可见).{0,6}正文.{0,15}(?:截断|未读到|中途)"),
    re.compile(r"(?:^|[。！？；\n])\s*(?:提供的正文|所给正文|本次材料|所给材料|所提供正文|所提供材料|原文未读到)"),
    re.compile(r"(?:^|[。！？；\n])\s*材料(?:未|没有|尚未)(?:提供|说明|给出|披露)"),
    re.compile(r"(?:原始材料抓取有限|待编辑复核)"),
    re.compile(r"(?:^|[。！？\n])\s*(?:请)?(?:返回完整\s*JSON|仅输出\s*JSON)[。！]?\s*$", re.I),
    re.compile(r"(?:^|\n)\s*(?:source_text|source_rank|mandatory_boundary|editorial_assessment)\s*[:：=]", re.I),
)


def character_count(value: str) -> int:
    return len(re.sub(r"\s+", "", value))


def _issue(field: str, code: str, message: str) -> dict[str, str]:
    return {"field": field, "code": code, "message": message}


def _checks(item: dict[str, Any], limits: dict[str, int], total: int) -> list[dict[str, str]]:
    issues: list[dict[str, str]] = []
    texts = {field: value.strip() for field in limits
             if isinstance((value := item.get(field)), str) and value.strip()}
    for field, value in texts.items():
        size = character_count(value)
        if size > limits[field]:
            issues.append(_issue(field, "too_long", f"{field}有{size}字符，超过{limits[field]}字符的编辑阈值；围绕主判断重新选择事实，保留必要限定，不能截断。"))
        if any(pattern.search(value) for pattern in INTERNAL_COPY):
            issues.append(_issue(field, "internal_notes", f"{field}混入内部材料处理说明；改写为读者需要的事实限定，内部核验备注不发表。"))
        if re.fullmatch(r"(?:略|同上|暂无|无|待补充)[。.!！]?", value):
            issues.append(_issue(field, "placeholder", f"{field}是占位文字，须给出有来源的日报表达。"))
    size = sum(character_count(value) for value in texts.values())
    if size > total:
        issues.append(_issue("__all__", "total_too_long", f"正文共{size}字符，超过{total}字符的编辑阈值；合并重复判断并删除次要支线，保留主证据和关键限制。"))
    # Catch clear reuse, not legitimate short repetitions of model/metric names.
    values = list(texts.items())
    for index, (left_field, left) in enumerate(values):
        left = re.sub(r"[\W_]+", "", left)
        for right_field, right in values[index + 1:]:
            right = re.sub(r"[\W_]+", "", right)
            if min(len(left), len(right)) < 28:
                continue
            matcher = SequenceMatcher(None, left, right, autojunk=False)
            common = matcher.find_longest_match(0, len(left), 0, len(right)).size
            if matcher.ratio() >= .82 or common >= max(45, min(len(left), len(right)) * .8):
                issues.append(_issue(right_field, "repeated_copy", f"{right_field}与{left_field}大段重复；每段只承担自己的编辑职责。"))
    return issues


def research_editorial_issues(paper: dict[str, Any]) -> list[dict[str, str]]:
    # Validate what the renderer will display, including legacy schemas.
    paper = dict(paper)
    for field, alias in (("conclusion", "one_liner"), ("problem", "question"), ("mechanism", "core_idea")):
        if not paper.get(field):
            paper[field] = paper.get(alias)
    evidence = paper.get("evidence", "")
    if isinstance(evidence, list):
        evidence = '；'.join(str(item.get('label', '')) + '：' + str(item.get('text', ''))
                            for item in evidence if isinstance(item, dict))
        paper['evidence'] = evidence
    # Several independent findings may each need quantitative evidence. A
    # count of numbers alone cannot decide which contribution is expendable.
    return _checks(paper, PAPER_LIMITS, PAPER_TOTAL_LIMIT)


def news_prose_tier(item: dict[str, Any]) -> str:
    if item.get('newspaper_role') in {'lead', 'supplement'}:
        return 'deep'
    return 'brief' if item.get('display_tier') == 'brief' else 'deep'


def news_editorial_issues(item: dict[str, Any]) -> list[dict[str, str]]:
    # Legacy untyped cards get the more permissive feature budget. Missing
    # selection metadata is checked separately at the generation boundary.
    tier = news_prose_tier(item)
    return _checks(item, NEWS_LIMITS[tier], 300 if tier == "brief" else 850)


def shell_editorial_issues(data: dict[str, Any]) -> list[dict[str, str]]:
    return _checks(data, dict(editor_note=220, summary=240, summary_headline=60), 500)


def daily_editorial_issues(data: dict[str, Any]) -> list[dict[str, str]]:
    issues: list[dict[str, str]] = shell_editorial_issues(data)
    papers = data.get("research_items")
    if not isinstance(papers, list) or not papers:
        papers = [data["paper"]] if isinstance(data.get("paper"), dict) else []
    for index, paper in enumerate(papers):
        if isinstance(paper, dict):
            issues.extend({**item, "field": f"research[{index}].{item['field']}"}
                          for item in research_editorial_issues(paper))
    for index, news in enumerate(data.get("news", [])):
        if isinstance(news, dict):
            issues.extend({**item, "field": f"news[{index}].{item['field']}"}
                          for item in news_editorial_issues(news))
    return issues
