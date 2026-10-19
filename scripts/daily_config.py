#!/usr/bin/env python3
"""Versioned configuration helpers for the AI Daily workflow.

The workflow deliberately keeps editorial policy in JSON rather than code so
source selection, recipients, rendering, and later middle-platform deployment
can evolve independently. Each loader merges a checked-in default with a local
override, which keeps old commands runnable when a new config has not yet been
deployed.
"""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from typing import Any


WORKFLOW_DIR = Path(__file__).resolve().parents[1]
CONFIG_DIR = WORKFLOW_DIR / "config"


class DailyConfigError(ValueError):
    pass


DEFAULT_SELECTION_POLICY: dict[str, Any] = {'schema_version': 1,
 'editorial_engine': 'event_v2',
 'freshness': {'news_lookback_hours': 48, 'research_lookback_hours': 72},
 'selection': {'research_count': 1, 'max_news_count': None},
 'reading': {'soft_budget_minutes': 10,
             'research_minutes': 3.0,
             'news_minutes': {'default': 0.9,
                              'complex_groups': ['methodology',
                                                 'security_boundary',
                                                 'enterprise_integration',
                                                 'infrastructure'],
                              'complex_minutes': 1.2,
                              'standard_groups': ['model_release',
                                                  'consumer_entry',
                                                  'developer_tool',
                                                  'creative_ops'],
                              'standard_minutes': 1.0}}}


DEFAULT_AUDIENCE_PROFILES: dict[str, Any] = {
    "schema_version": 1,
    "default_profile": "balanced_product_ops",
    "profiles": {
        "balanced_product_ops": {
            "name": "产品、用户运营与运营中台",
            "reader_preference_by_group": {
                "methodology": 100,
                "enterprise_integration": 90,
                "developer_tool": 88,
                "consumer_entry": 86,
                "security_boundary": 84,
                "cost_governance": 82,
                "creative_ops": 80,
                "model_release": 70,
                "usage_research": 68,
                "governance_risk": 64,
                "inference_speed": 56,
                "general_router": 55,
                "agent_memory": 50,
                "research_frontier": 48,
                "media_product": 42,
                "other": 45,
            },
            "title_adjustments": [
                {"terms": ["prompt", "提示词", "评估"], "delta": 12, "reason": "技术分析与提示词/评估偏好"},
                {"terms": ["openclaw"], "delta": 14, "reason": "团队已有真实使用"},
                {"terms": ["cursor"], "delta": 8, "reason": "AI Coding 与工作流入口"},
                {"terms": ["redknot", "小红书"], "delta": -8, "reason": "当前职能转译距离较远"},
                {"terms": ["everos"], "delta": -10, "reason": "当前职能转译距离较远"},
            ],
        }
    },
}


DEFAULT_CONTENT_SECTIONS: dict[str, Any] = {
    "schema_version": 1,
    "research": {"key": "research", "title": "研究前沿", "label": "RESEARCH", "anchor": "digest-research"},
    "news_categories": [
        {"key": "model_update", "title": "模型更新", "label": "MODEL UPDATES", "match_terms": ["模型", "model release", "大模型"]},
        {"key": "agent_engineering", "title": "智能体工程", "label": "AGENT ENGINEERING", "match_terms": ["agent", "智能体", "skill", "mcp", "harness", "编排"]},
        {"key": "product_distribution", "title": "产品与入口", "label": "PRODUCT & DISTRIBUTION", "match_terms": ["搜索", "search", "入口", "overview", "分发", "流量"]},
        {"key": "product_release", "title": "产品发布", "label": "PRODUCT RELEASES", "match_terms": ["产品", "入口", "app", "agent"]},
        {"key": "industry_signal", "title": "行业动态", "label": "INDUSTRY SIGNALS", "match_terms": []},
        {"key": "methods_and_views", "title": "技巧与观点", "label": "METHODS & VIEWS", "match_terms": ["skill", "prompt", "评测", "coding", "方法"]},
    ],
    "fallback_news_category": "industry_signal",
}


DEFAULT_EDITORIAL_POLICY: dict[str, Any] = {
    "schema_version": 1,
    "email_shell": {
        "eyebrow": "AI SIGNAL RADAR",
        "news_section": {
            "title": "行业动态",
            "subtitle": "精选今日值得关注的 AI 产品、技术与行业信号",
        },
    },
    "observation": {
        "allowed_labels": ["行业视角", "工作启发"],
        "default_label": "行业视角",
    },
    "research_structure": {
        "problem": "研究问题",
        "mechanism": "方法机制",
        "evidence": "关键证据",
        "insight": "应用启发",
        "boundary_label": "边界提示",
    },
    "boundary": {
        "render_research_boundary": True,
        "render_news_only_when_material": True,
        "risk_cues": ["安全", "隐私", "漏洞", "泄露", "攻击", "恶意", "供应链", "权限", "合规", "法律", "诉讼", "争议"],
        "source_uncertainty_cues": ["传闻", "未证实", "非官方", "内测", "灰度", "beta", "preview"],
    },
}


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = deepcopy(value)
    return merged


def load_config(path: Path | None, defaults: dict[str, Any], label: str) -> dict[str, Any]:
    """Load one policy JSON and merge it onto conservative defaults."""
    if path is None or not path.exists():
        return deepcopy(defaults)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DailyConfigError(f"Unable to load {label} config {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise DailyConfigError(f"{label} config {path} must contain a JSON object.")
    version = raw.get("schema_version", defaults.get("schema_version", 1))
    if version != defaults.get("schema_version", 1):
        raise DailyConfigError(
            f"{label} config {path} uses unsupported schema_version {version!r}."
        )
    return _deep_merge(defaults, raw)


def load_selection_policy(path: Path | None = None) -> dict[str, Any]:
    policy = load_config(path or CONFIG_DIR / "selection_policy.json", DEFAULT_SELECTION_POLICY, "selection policy")
    if policy.get('editorial_engine') != 'event_v2':
        raise DailyConfigError('Production requires event_v2; use the explicit legacy replay loader for history.')
    obsolete = set(policy.get('selection', {})) - {'research_count', 'max_news_count'}
    if 'scoring' in policy or obsolete:
        raise DailyConfigError('Legacy scoring/selection fields belong in legacy_selection_policy.json; use --legacy-scoring for replay.')
    return policy


def load_legacy_selection_policy(path: Path | None = None) -> dict[str, Any]:
    """Explicit v1 replay policy; never merged into production defaults."""
    frozen = json.loads((CONFIG_DIR / 'legacy_selection_policy.json').read_text())
    if path and path.resolve() != (CONFIG_DIR / 'selection_policy.json').resolve():
        frozen = _deep_merge(frozen, load_config(path, {}, 'legacy override'))
    return frozen


def load_audience_profiles(path: Path | None = None) -> dict[str, Any]:
    return load_config(path or CONFIG_DIR / "audience_profiles.json", DEFAULT_AUDIENCE_PROFILES, "audience profiles")


def load_content_sections(path: Path | None = None) -> dict[str, Any]:
    return load_config(path or CONFIG_DIR / "content_sections.json", DEFAULT_CONTENT_SECTIONS, "content sections")


def load_editorial_policy(path: Path | None = None) -> dict[str, Any]:
    return load_config(path or CONFIG_DIR / "editorial_policy.json", DEFAULT_EDITORIAL_POLICY, "editorial policy")


def snapshot(config: dict[str, Any]) -> dict[str, Any]:
    """Return a serializable copy for run records and replay diagnostics."""
    return deepcopy(config)
