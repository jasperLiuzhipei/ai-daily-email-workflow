from __future__ import annotations

import sys
import unittest
from datetime import datetime
from pathlib import Path


WORKFLOW_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WORKFLOW_DIR / "scripts"))

from select_aihot_sources import (  # noqa: E402
    TZ,
    boundary_for,
    content_research_route_reason,
    is_high_impact_unverified_model_signal,
    is_research_eligible,
    major_gap_audit_reason,
    merge_candidates,
    parse_feed,
    score_candidate,
    select_items,
    selection_group_for,
    same_day_event_key,
    source_type_for,
)
from event_editorial import news_window_eligible  # noqa: E402


def rss_item(title: str, description: str, published_at: str, item_id: str) -> str:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>AIHot fixture</title>
    <item>
      <title><![CDATA[{title}]]></title>
      <link>https://aihot.virxact.com/items/{item_id}</link>
      <description><![CDATA[{description}]]></description>
      <category>行业动态</category>
      <pubDate>{published_at}</pubDate>
      <guid isPermaLink="false">{item_id}</guid>
    </item>
  </channel>
</rss>"""


class SourceSelectionRoutingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.recall_start = datetime(2026, 7, 24, 13, 47, tzinfo=TZ)
        self.news_start = datetime(2026, 7, 25, 9, 27, tzinfo=TZ)
        self.end_at = datetime(2026, 7, 27, 9, 27, tzinfo=TZ)

    def test_midnight_utc_boundary_news_remains_visible_for_review(self) -> None:
        start = datetime(2026, 9, 28, 8, 30, tzinfo=TZ)
        end = datetime(2026, 9, 29, 8, 30, tzinfo=TZ)
        xml = rss_item(
            "xAI 发布 Team Bots",
            '<a href="https://x.ai/news/team-bots">官方原文</a>',
            "Mon, 28 Sep 2026 00:00:00 GMT",
            "team-bots",
        )
        candidates, _ = parse_feed(
            xml, datetime(2026, 9, 26, 8, 30, tzinfo=TZ), end, [],
            feed_source="main", research_window_strategy="exact_window",
            news_start_at=start,
        )
        self.assertEqual(len(candidates), 1)
        self.assertTrue(candidates[0]["timing_review_required"])
        self.assertEqual(candidates[0]["research_window_strategy"], "date_only_boundary_review")
        self.assertFalse(news_window_eligible(candidates[0], {"start_at": start.isoformat(), "end_at": end.isoformat()}))

    def test_agent_tutorial_and_search_signal_do_not_take_model_release_group(self) -> None:
        agent_tutorial = {
            "title": "用Claude和Python构建技能驱动的金融分析智能体",
            "description": "教程用 Python、MCP connectors 和工具调用循环构建可复用 SkillAgent。",
            "categories": [],
        }
        search_signal = {
            "title": "Google AI Overviews 搜索结果出现率升至43%",
            "description": "AI Overviews 在搜索结果中的出现率升至43%，用户查询转向对话式表达。",
            "categories": [],
        }

        agent_tutorial["source_type"] = source_type_for(agent_tutorial)
        search_signal["source_type"] = source_type_for(search_signal)

        self.assertEqual(agent_tutorial["source_type"], "methodology")
        self.assertEqual(selection_group_for(agent_tutorial), "methodology")
        self.assertEqual(search_signal["source_type"], "industry_news")
        self.assertEqual(selection_group_for(search_signal), "consumer_entry")

    def test_model_adoption_report_is_cost_governance_not_model_release(self) -> None:
        candidate = {
            "title": "Anthropic 最强模型用户增长乏力，更便宜工具更受青睐",
            "description": (
                "Anthropic 年化收入增长并预计盈利；7 月发布的旗舰模型使用份额较低，"
                "企业支出仍偏向更低成本的选择。"
            ),
            "categories": ["行业动态"],
        }

        candidate["source_type"] = source_type_for(candidate)

        self.assertEqual(candidate["source_type"], "industry_news")
        self.assertEqual(selection_group_for(candidate), "cost_governance")

    def test_reported_benchmark_result_is_gap_news_not_research(self) -> None:
        xml = rss_item(
            "Kimi K3 在网络安全漏洞利用测试中大幅落后美国前沿模型，知识蒸馏或为原因",
            (
                "<p>英国AI安全研究所与美国AI标准与创新中心联合评估显示，"
                "Kimi K3在ExploitBench基准上接受多模型对照测试。</p>"
                '<a href="https://the-decoder.com/kimi-k3-cyber-evaluation">阅读原文</a>'
            ),
            "Fri, 24 Jul 2026 09:48:32 GMT",
            "cmryrih7804c9rolge6wdk3v8",
        )

        candidates, duplicates = parse_feed(
            xml,
            self.recall_start,
            self.end_at,
            [],
            feed_source="main",
            research_window_strategy="exact_window",
            news_start_at=self.news_start,
            gap_audit_start_at=self.recall_start,
        )

        self.assertEqual(duplicates, [])
        self.assertEqual(len(candidates), 1)
        candidate = candidates[0]
        self.assertEqual(candidate["content_research_route_reason"], "")
        self.assertEqual(candidate["research_window_strategy"], "major_event_gap_audit")
        self.assertEqual(
            candidate["gap_audit_reason"],
            "major_foundation_model_capability_evaluation",
        )
        self.assertFalse(is_research_eligible(candidate))
        self.assertEqual(candidate["scores"]["final_research"], 45.0)
        self.assertEqual(
            candidate["score_breakdown"]["reported_benchmark_research_score_cap"],
            45.0,
        )

    def test_new_benchmark_artifact_is_routed_to_research_lookback(self) -> None:
        xml = rss_item(
            "研究团队发布 LongSearchBench 新基准",
            (
                "<p>某研究院构建并开源 LongSearchBench，包含 500 道长程搜索题，"
                "同时发布评测框架、数据集和模型实验结果。</p>"
                '<a href="https://research.google/blog/longsearchbench">阅读原文</a>'
            ),
            "Fri, 24 Jul 2026 09:48:32 GMT",
            "new-longsearch-benchmark",
        )

        candidates, duplicates = parse_feed(
            xml,
            self.recall_start,
            self.end_at,
            [],
            feed_source="main",
            research_window_strategy="exact_window",
            news_start_at=self.news_start,
            gap_audit_start_at=self.recall_start,
        )

        self.assertEqual(duplicates, [])
        self.assertEqual(len(candidates), 1)
        candidate = candidates[0]
        self.assertEqual(
            candidate["content_research_route_reason"],
            "new_benchmark_or_evaluation_artifact",
        )
        self.assertEqual(
            candidate["research_window_strategy"],
            "content_reclassified_research_lookback",
        )
        self.assertTrue(is_research_eligible(candidate))

    def test_generic_old_benchmark_claim_is_not_promoted_to_research(self) -> None:
        candidate = {
            "title": "某产品宣称在内部 Benchmark 跑分领先",
            "description": "厂商在产品发布会上展示了一组未经独立复核的内部跑分。",
            "categories": ["行业动态"],
            "primary_url": "https://example.com/product-launch",
            "feed_source": "main",
        }
        candidate, _ = score_candidate(candidate, [])

        self.assertEqual(content_research_route_reason(candidate), "")
        self.assertFalse(is_research_eligible(candidate))

    def test_social_post_about_math_result_cannot_take_research_frontier_slot(self) -> None:
        candidate = {
            "title": "Epoch AI 称 AI 解决一个数论开放问题",
            "description": "一条社交媒体摘要，未提供论文、证明或技术报告。",
            "categories": [],
            "primary_url": "https://x.com/EpochAIResearch/status/2081894720813604997",
            "feed_source": "all",
            "source_type": "paper",
            "selection_group": "other",
        }
        candidate, _ = score_candidate(candidate, [])

        self.assertEqual(candidate["primary_host"], "x.com")
        self.assertEqual(content_research_route_reason(candidate), "")
        self.assertFalse(is_research_eligible(candidate))

    def test_ordinary_news_in_send_gap_remains_excluded(self) -> None:
        xml = rss_item(
            "某 AI 工具更新菜单栏图标",
            '<p>本次更新调整菜单栏图标和配色。</p><a href="https://example.com/update">阅读原文</a>',
            "Fri, 24 Jul 2026 10:00:00 GMT",
            "ordinary-gap-item",
        )

        candidates, duplicates = parse_feed(
            xml,
            self.recall_start,
            self.end_at,
            [],
            feed_source="main",
            research_window_strategy="exact_window",
            news_start_at=self.news_start,
            gap_audit_start_at=self.recall_start,
        )

        self.assertEqual(candidates, [])
        self.assertEqual(duplicates, [])

    def test_gap_audit_only_recognizes_major_model_or_security_signals(self) -> None:
        candidate = {
            "title": "Anthropic 正式发布 Claude Opus 5 大版本",
            "description": "新模型正式上线 API，并公布定价、权重与安全说明。",
            "categories": ["模型更新"],
            "primary_url": "https://www.anthropic.com/news/claude-opus-5",
            "feed_source": "main",
        }
        candidate, _ = score_candidate(candidate, [])

        self.assertEqual(major_gap_audit_reason(candidate), "major_foundation_model_release")

    def test_scope_limited_cryptography_research_does_not_take_daily_security_or_research_slot(self) -> None:
        candidate = {
            "title": "Claude 发现加密算法弱点研究发布",
            "description": (
                "Anthropic 新研究用 Claude 分析候选签名方案 HAWK 和简化版七轮 AES，"
                "改进已知密码分析方法；结果不影响完整 AES 或生产系统。"
            ),
            "categories": ["论文研究"],
            "primary_url": "https://www.anthropic.com/research/discovering-cryptographic-weaknesses",
            "aihot_url": "https://aihot.virxact.com/items/crypto-study",
            "feed_source": "main+paper",
            "research_window_strategy": "exact_window",
            "source_type": "paper",
            "published_at": "2026-07-29T08:00:00+08:00",
        }

        candidate, _ = score_candidate(candidate, [])
        selected_research, selected_news, _ = select_items(
            [candidate], research_count=1, min_news_count=0, max_news_count=12
        )

        self.assertEqual(candidate["selection_group"], "research_frontier")
        self.assertEqual(candidate["scores"]["internal_relevance"], 10)
        self.assertEqual(candidate["score_breakdown"]["scope_limited_research_relevance_cap"], 10)
        self.assertEqual(selected_research, [])
        self.assertEqual(selected_news, [])

    def test_search_augmented_content_pollution_research_clears_relevance_floor(self) -> None:
        candidate, duplicate = score_candidate(
            {
                "title": "单个污染页面即可影响LLM推荐：FORGE基准揭示检索增强推荐系统的脆弱性",
                "description": (
                    "检索增强型 LLM 推荐器会读取被 GEO 内容污染的网页。"
                    "FORGE 基准在消费推荐场景中测量虚假产品被推荐的风险，并评估可信度重排。"
                ),
                "primary_url": "https://arxiv.org/abs/2606.13610",
                "aihot_url": "https://aihot.virxact.com/items/forge-pollution",
                "categories": ["论文"],
                "feed_source": "main+paper",
                "source_type": "paper",
                "research_window_strategy": "paper_lookback",
                "published_at": "2026-08-24T08:00:00+08:00",
            },
            [],
        )

        self.assertIsNone(duplicate)
        self.assertTrue(is_research_eligible(candidate))
        self.assertGreaterEqual(candidate["scores"]["internal_relevance"], 15)
        selected_research, _, _ = select_items(
            [candidate], research_count=1, min_news_count=0, max_news_count=12
        )
        self.assertEqual([item["title"] for item in selected_research], [candidate["title"]])
        self.assertEqual(selected_research[0]["research_selection_mode"], "standard")

    def test_high_confidence_near_floor_research_fills_an_empty_research_slot(self) -> None:
        candidate = {
            "title": "高质量研究候选",
            "description": "一篇具备可复核一手来源的研究论文。",
            "published_at": "2026-08-26T08:00:00+08:00",
            "primary_url": "https://arxiv.org/abs/2608.00001",
            "primary_host": "arxiv.org",
            "aihot_url": "https://aihot.virxact.com/items/near-floor-research",
            "bestblogs_url": "",
            "bestblogs_score": None,
            "source_type": "paper",
            "feed_source": "paper",
            "categories": ["论文"],
            "source_name": "AIHOT paper",
            "source_pool": "research",
            "source_authority": "paper_repository",
            "source_quality_tier": "discovery",
            "research_eligible": True,
            "selection_group": "research_frontier",
            "research_window_strategy": "paper_lookback",
            "topic_tags": ["AI 行业"],
            "content_duplicate_check": {"is_duplicate": False},
            "continuity_note": "",
            "scores": {
                "final_research": 88,
                "internal_relevance": 13,
                "final_news": 60,
                "total": 72,
            },
            "score_breakdown": {"source_reliability": 92, "industry_impact": 60},
        }

        selected_research, _, _ = select_items(
            [candidate], research_count=1, min_news_count=0, max_news_count=12
        )

        self.assertEqual([item["title"] for item in selected_research], [candidate["title"]])
        self.assertEqual(selected_research[0]["research_selection_mode"], "empty_slot_fallback")

    def test_official_www_research_url_merges_with_discovery_variant_and_wins(self) -> None:
        discovery = {
            "title": "Claude 发现加密算法弱点研究发布",
            "description": "二手摘要。",
            "primary_url": "https://anthropic.com/research/discovering-cryptographic-weaknesses",
            "aihot_url": "",
            "feed_source": "main",
            "categories": ["研究"],
            "source_quality_tier": "discovery",
            "scores": {"final_news": 90, "final_research": 72},
            "score_breakdown": {"source_reliability": 62},
        }
        official = {
            "title": "Discovering cryptographic weaknesses with Claude",
            "description": "Anthropic 官方研究页。",
            "primary_url": "https://www.anthropic.com/research/discovering-cryptographic-weaknesses/",
            "aihot_url": "",
            "feed_source": "direct:anthropic_research",
            "source_pool": "research",
            "source_authority": "official_research",
            "source_quality_tier": "primary",
            "categories": ["Research"],
            "scores": {"final_news": 67, "final_research": 72},
            "score_breakdown": {"source_reliability": 95},
        }

        merged = merge_candidates([discovery, official])

        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["title"], official["title"])
        self.assertEqual(
            merged[0]["primary_url"],
            official["primary_url"],
        )
        self.assertEqual(set(merged[0]["feed_source"].split("+")), {"main", "direct:anthropic_research"})

    def test_product_action_day_fingerprint_merges_syndicated_updates(self) -> None:
        first = {
            "title": "ChatGPT Work 云端运行支持移动端",
            "description": "ChatGPT Work 正式上线新的移动端支持功能。",
            "published_at": "2026-07-20T09:00:00+08:00",
        }
        second = {
            "title": "ChatGPT Work 功能：建站、邮件、文档处理",
            "description": "ChatGPT Work 推出更多工作流功能。",
            "published_at": "2026-07-20T15:00:00+08:00",
        }
        next_day = {**second, "published_at": "2026-07-21T09:00:00+08:00"}

        self.assertEqual(same_day_event_key(first), same_day_event_key(second))
        self.assertNotEqual(same_day_event_key(first), same_day_event_key(next_day))

    def test_anthropic_cybersecurity_eval_report_merges_with_official_x_summary(self) -> None:
        report = {
            "title": "Anthropic 调查发现 Claude 模型在网络安全评估中入侵三家组织生产基础设施",
            "description": "Claude 在第三方网络安全评估中未经授权访问三家组织的真实系统。",
            "published_at": "2026-07-31T07:27:54+08:00",
        }
        social_summary = {
            "title": "Anthropic 披露 Claude 在安全评估中入侵真实系统",
            "description": "官方摘要称事件发生在网络安全评估中，并涉及三家组织。",
            "published_at": "2026-07-31T07:02:34+08:00",
        }

        self.assertEqual(same_day_event_key(report), "anthropic-claude-cybersecurity-evals-incident")
        self.assertEqual(same_day_event_key(report), same_day_event_key(social_summary))

    def test_anthropic_cybersecurity_eval_postmortem_merges_current_wording(self) -> None:
        report = {
            "title": "Anthropic 复盘 Claude 模型越权访问事件并公布安全与对齐改进措施",
            "description": "Claude 因第三方评估环境配置错误接入真实互联网，并在网络安全测试中越权操作。",
            "published_at": "2026-09-01T07:00:05+08:00",
        }
        social_summary = {
            "title": "Anthropic 发布对齐与安全工作更新，回应 Claude 模型越权访问事件",
            "description": "三起事件涉及 Claude 在无安全防护的网络安全评测中未经授权访问真实系统。",
            "published_at": "2026-09-01T06:45:08+08:00",
        }

        self.assertEqual(same_day_event_key(report), "anthropic-claude-cybersecurity-evals-incident")
        self.assertEqual(same_day_event_key(report), same_day_event_key(social_summary))

    def test_high_impact_unverified_model_claim_can_be_a_bounded_watch_item(self) -> None:
        astra = {
            "title": "OpenAI Astra 以约2000美元证明10项数学难题",
            "description": "第三方 X 帖称 OpenAI 的 Astra 模型完成多项数学与理论计算机科学证明。",
            "published_at": "2026-08-01T15:39:02+08:00",
            "primary_url": "https://x.com/gdb/status/2083457463337287721",
            "primary_host": "x.com",
            "aihot_url": "https://aihot.virxact.com/items/astra-math",
            "categories": ["AI"],
            "topic_tags": ["通用大模型"],
            "selection_group": "model_release",
            "source_type": "model_release",
            "research_eligible": False,
            "research_window_strategy": "exact_window",
            "content_duplicate_check": {"is_duplicate": False},
            "continuity_note": "",
            "scores": {"final_news": 74.2, "final_research": 42.0, "total": 82.0},
            "score_breakdown": {"source_reliability": 58, "industry_impact": 96.7},
        }

        self.assertTrue(is_high_impact_unverified_model_signal(astra))
        self.assertIn("待核验", boundary_for(astra))
        _, selected, _ = select_items([astra], research_count=0, min_news_count=0, max_news_count=12)
        self.assertEqual([item["title"] for item in selected], [astra["title"]])


if __name__ == "__main__":
    unittest.main()
