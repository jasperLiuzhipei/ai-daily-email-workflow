from __future__ import annotations

import base64
import json
import os
import subprocess
import struct
import sys
import tempfile
import threading
import unittest
import zlib
from contextlib import contextmanager
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch


WORKFLOW_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WORKFLOW_DIR / "scripts"))

from ai_gateway_client import (  # noqa: E402
    GatewayError,
    ProviderRuntime,
    chat_json,
    generate_image_bytes,
    load_runtime_config,
    resolve_provider,
    resolve_provider_fallbacks,
)
from configure_ai_runtime import update_env_file  # noqa: E402
from generate_daily_content_api import (  # noqa: E402
    deduplicate_material_news,
    deterministic_news,
    editorial_category_for,
    generate_individual_news_cards,
)
from generate_research_image import GenerateError, generate_api_image, validate_requested_aspect  # noqa: E402
from fetch_selected_source_material import editorial_primary_url  # noqa: E402
from image_provider_registry import (  # noqa: E402
    load_provider_registry,
    provider_can_enter_production,
    resolve_provider as resolve_image_provider,
)
from parse_ai_frontier_brief import make_image_prompt, make_palette_guidance  # noqa: E402
from render_ai_daily_email import render_digest_sources, source_display  # noqa: E402
from select_aihot_sources import (  # noqa: E402
    is_research_eligible,
    message_keys,
    merge_candidates,
    parse_anthropic_listing_page,
    parse_official_rss_feed,
    run_curl,
    select_items,
    selection_group_for,
    source_type_for,
)


def png_bytes(width: int = 512, height: int = 512) -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    rows = b"".join(b"\x00" + b"\xff\xff\xff" * width for _ in range(height))
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(rows, 9))
        + chunk(b"IEND", b"")
    )


class FakeGateway(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:
        return

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        request = json.loads(self.rfile.read(length).decode("utf-8"))
        if self.path == "/v1/chat/completions":
            system = str(request.get("messages", [{}])[0].get("content") or "")
            if "你是研究解读完整性审核编辑" in system:
                output = {"requirements": [
                    {"id":"problem","kind":"problem","requirement":"解释证据支持问题", "source_quote":"judge whether agent answers have evidence", "covered":True,"paper_quote":"如何判断 Agent 的答案是否真正受到可验证证据支持。"},
                    {"id":"mechanism","kind":"mechanism","requirement":"解释三个工作阶段", "source_quote":"retrieves evidence, checks conflicts, then forms a conclusion", "covered":True,"paper_quote":"系统把任务拆成检索证据、检查冲突和形成结论三个阶段。"},
                    {"id":"finding","kind":"finding","requirement":"说明测试发现", "source_quote":"Controlled tests show fewer unsupported conclusions", "covered":True,"paper_quote":"测试材料显示，加入检查阶段后，无依据结论的比例相对下降。"},
                    {"id":"boundary","kind":"boundary","requirement":"保留测试范围", "source_quote":"Results are limited to tested tasks and models", "covered":True,"paper_quote":"结论只覆盖测试任务和给定模型，不能外推到所有复杂工作流。"}
                ], "editorial_issues": []}
            elif "visual_handoff" in system and "paper" in system:
                output = {
                    "paper": {
                        "title": "测试研究：让 Agent 学会验证证据",
                        "tags": ["Agent 评估", "证据校验"],
                        "audience": "产品 / 运营中台",
                        "conclusion": "研究表明，Agent 在形成结论前增加证据检查步骤，可以减少无依据的回答。",
                        "problem": "如何判断 Agent 的答案是否真正受到可验证证据支持。",
                        "mechanism": "系统把任务拆成检索证据、检查冲突和形成结论三个阶段。",
                        "evidence": "测试材料显示，加入检查阶段后，无依据结论的比例相对下降。",
                        "insight": "内部 Agent 应把证据来源和检查结果保留为可审计中间产物。",
                        "boundary": "结论只覆盖测试任务和给定模型，不能外推到所有复杂工作流。",
                    },
                    "visual_handoff": {
                        "Core Idea": "证据检查降低无依据结论",
                        "Named Subject": "Agent 证据校验",
                        "Distinctive Detail": "三阶段工作流",
                        "Display Title": "Agent 如何验证证据",
                        "Optional Subtitle": "检索、检查、结论",
                        "Title Policy": "in-image",
                        "Primary Explanation": "mechanism",
                        "Credibility Support": "evaluation",
                        "Primary Visual Scene": "三段式证据管线连接到可审计结论面板",
                        "Visual Hook": "答案前多一道证据闸门",
                        "Must Preserve": "测试任务范围；证据检查；可审计中间产物",
                        "Main Evidence Anchor": "有检查与无检查的相对对照，不写未经授权数字",
                        "Supporting Evidence Details": "只使用来源明确的相对结果",
                        "Boundary Cue": "角落标记测试任务与已测模型",
                        "Do Not Imply": "不要暗示所有任务都能消除幻觉",
                        "Visual Hierarchy": "主线为三阶段机制，证据对照为次线",
                        "Complexity Budget": "最多4个主节点、1个证据锚点、8-12个短标签",
                        "Clarity Check": "三秒内读懂证据检查机制",
                        "People Policy": "No people, faces, bodies, humanoid robots, mascots, or logos.",
                        "Text Policy": "短简体中文标签",
                        "Visual Tone": "research explainer",
                        "Creative Freedom": "允许编辑式信息图",
                        "Avoid": "logo、人物、虚构数字",
                        "Reject If": "缺少证据检查机制或出现虚构指标",
                    },
                }
            elif "本次输出范围为单条新闻" in system:
                output = {
                    "title": "测试产品为 Agent 输出增加来源与冲突提示",
                    "one_liner": "测试产品为 Agent 输出增加来源记录与冲突提示，并把检查过程带入实际工作流。",
                    "why": "这说明产品竞争开始从回答速度转向输出是否可解释、可追踪，也更便于团队复盘。",
                    "observation_label": "工作启发",
                    "observation": "内部提效工具应把引用、检查和失败原因纳入交付结果，避免只保留最终答案。",
                    "boundary": "测试材料仅用于验证生成流程。",
                }
            elif "已完成的新闻卡片" in system:
                output = {
                    "editor_note": "今天值得关注的是 Agent 能力正在从单次回答转向可验证的工作流程，产品更新与研究方法都开始强调证据、稳定性和持续运行。",
                    "news_section": {"title": "行业动态", "subtitle": "今日值得关注的产品、技术与行业信号"},
                    "summary_headline": "Agent 开始补齐验证链路",
                    "summary": "今天的共同信号不是模型又多了一项能力，而是 Agent 产品开始补齐证据记录、冲突检查与结果审计。只有这些运行细节稳定下来，能力才更容易进入真实工作流。",
                }
            elif "日报编辑" in system:
                output = {
                    "editor_note": "今天值得关注的是 Agent 能力正在从单次回答转向可验证的工作流程，产品更新与研究方法都开始强调证据、稳定性和持续运行。",
                    "news_section": {"title": "行业动态", "subtitle": "今日值得关注的产品、技术与行业信号"},
                    "news": [
                        {
                            "source_rank": 1,
                            "category": "product_release",
                            "title": "测试产品上线证据追踪功能",
                            "tags": ["Agent 产品", "证据追踪", "适合：产品 / 运营中台"],
                            "one_liner": "测试产品为 Agent 输出增加来源记录与冲突提示。",
                            "why": "这说明产品竞争开始从回答速度转向输出是否可解释、可追踪。",
                            "observation_label": "工作启发",
                            "observation": "内部提效工具也需要把引用、检查和失败原因纳入交付结果。",
                        }
                    ],
                    "summary_headline": "Agent 开始补齐验证链路",
                    "summary": "今天的共同信号不是模型又多了一项能力，而是 Agent 产品开始补齐证据记录、冲突检查与结果审计。只有这些运行细节稳定下来，能力才更容易进入真实工作流。",
                }
            else:
                output = {"ok": True, "model": request["model"]}
            body = {
                "choices": [{"message": {"content": json.dumps(output, ensure_ascii=False)}}],
                "usage": {"total_tokens": 3},
            }
        elif self.path == "/v1/images/generations":
            if self.headers.get("Authorization") == "Bearer primary-secret":
                body = {"error": {"message": "primary image gateway unavailable"}}
                encoded = json.dumps(body).encode("utf-8")
                self.send_response(512)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(encoded)))
                self.end_headers()
                self.wfile.write(encoded)
                return
            if request.get("response_format") == "url":
                body = {
                    "data": [{"url": "https://file4.aitohumanize.com/file/test-image.png?temporary=1"}],
                    "usage": {"total_tokens": 4},
                }
            else:
                body = {
                    "data": [{"b64_json": base64.b64encode(png_bytes()).decode("ascii")}],
                    "usage": {"total_tokens": 4},
                }
        else:
            self.send_error(404)
            return
        encoded = json.dumps(body).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("X-Request-Id", "fake-request")
        self.end_headers()
        self.wfile.write(encoded)


@contextmanager
def fake_gateway():
    server = ThreadingHTTPServer(("127.0.0.1", 0), FakeGateway)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/v1"
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()


class GatewayClientTest(unittest.TestCase):
    def test_generation_dedupe_keeps_primary_anthropic_incident_report(self) -> None:
        report = {
            "title": "Anthropic 复盘 Claude 模型越权访问事件",
            "fallback_excerpt": "第三方评估环境误接真实互联网，网络安全测试中发生越权操作。",
            "primary_url": "https://anthropic.com/news/improving-alignment-security-efforts",
        }
        social = {
            "title": "Anthropic 发布对齐与安全工作更新",
            "fallback_excerpt": "Claude 在网络安全评测中未经授权访问真实系统。",
            "primary_url": "https://x.com/AnthropicAI/status/1",
        }

        kept, dropped = deduplicate_material_news([report, social])

        self.assertEqual(len(kept), 1)
        self.assertEqual({key: kept[0][key] for key in report}, report)
        self.assertEqual(set(kept[0]["coverage_source_urls"]), {report["primary_url"], social["primary_url"]})
        self.assertEqual(len(dropped), 1)
        self.assertEqual(dropped[0]["mode"], "dropped_duplicate_event")
        self.assertEqual(dropped[0]["matched_title"], report["title"])

    def test_editorial_category_requires_a_concrete_model_release(self) -> None:
        agent_tutorial = {
            "title": "用Claude和Python构建技能驱动的金融分析智能体",
            "selection_group": "model_release",  # stale upstream grouping must not leak into email
            "source_text": "教程用 Python、MCP connectors 和工具调用循环构建可复用 SkillAgent。",
            "fallback_excerpt": "教程用 Python、MCP connectors 和工具调用循环构建可复用 SkillAgent。",
            "topic_tags": ["Agent"],
        }
        search_signal = {
            "title": "Google AI Overviews 搜索结果出现率升至43%",
            "selection_group": "model_release",
            "source_text": "AI Overviews 在搜索结果中的出现率升至43%，用户查询转向对话式表达。",
            "fallback_excerpt": "AI Overviews 在搜索结果中的出现率升至43%，用户查询转向对话式表达。",
            "topic_tags": ["搜索"],
        }
        real_release = {
            "title": "Gemini 3.6 Flash 正式发布",
            "selection_group": "model_release",
            "source_text": "Google 发布 Gemini 3.6 Flash 新模型，开放 API 并公布模型卡和定价。",
            "fallback_excerpt": "Google 发布 Gemini 3.6 Flash 新模型，开放 API 并公布模型卡和定价。",
            "topic_tags": ["模型"],
        }
        open_weights_position = {
            "title": "Anthropic CEO 澄清公司从未主张禁止开源 AI 模型",
            "selection_group": "model_release",  # stale upstream grouping must not override subject matter
            "source_text": "Anthropic CEO 就开放权重公开信澄清公司立场，不主张禁止开源模型。",
            "fallback_excerpt": "Anthropic CEO 就开放权重公开信澄清公司立场，不主张禁止开源模型。",
            "topic_tags": ["开源"],
        }
        agent_environment = {
            "title": "Kimi K3 开源分布式智能体环境 AgentENV",
            "selection_group": "methodology",
            "source_text": "AgentENV 是用于大规模运行智能体环境的开源分布式平台。",
            "fallback_excerpt": "AgentENV 是用于大规模运行智能体环境的开源分布式平台。",
            "topic_tags": ["Agent"],
        }
        unverified_security_claim = {
            "title": "Hugging Face CEO 向 OpenAI 索赔 1 亿美元算力",
            "selection_group": "security_boundary",
            "source_text": "X 帖声称内部测试模型涉及网络攻击，并要求公开细节。",
            "fallback_excerpt": "X 帖声称内部测试模型涉及网络攻击，并要求公开细节。",
            "topic_tags": ["安全"],
        }
        grok_release_with_page_chrome = {
            "title": "xAI 发布 Grok Voice Think Fast 2.0 语音模型",
            "selection_group": "methodology",
            "source_text": "页面底部含 privacy policy 等导航文字，不可作为栏目路由依据。",
            "fallback_excerpt": "Grok Voice Think Fast 2.0 已在 xAI Console 的 Agent Builder 上线。",
            "topic_tags": ["语音模型"],
        }
        company_revenue_report = {
            "title": "OpenAI 称 7 月年化收入已超 Q2 总和，面临竞争",
            "selection_group": "model_release",
            "source_text": "网页还提到多个模型和智能体产品。",
            "fallback_excerpt": "公司讨论收入增长、竞争与算力建设计划。",
            "topic_tags": ["公司动态"],
        }
        model_adoption_report = {
            "title": "Anthropic 最强模型用户增长乏力，更便宜工具更受青睐",
            "source_type": "industry_news",
            "selection_group": "cost_governance",
            "fallback_excerpt": (
                "公司收入增长并预计盈利；7 月曾发布的旗舰模型使用份额较低，"
                "模型支出仍偏向低成本选项。"
            ),
            "topic_tags": ["成本治理"],
        }
        agent_intrusion_report = {
            "title": "揭秘 AI 智能体入侵 Hugging Face 全过程",
            "selection_group": "methodology",
            "source_text": "文章多次使用 agent、workflow 与 framework 等字样。",
            "fallback_excerpt": "技术时间线披露自主智能体利用漏洞突破安全防护。",
            "topic_tags": ["AI 安全"],
        }
        local_runtime_tool = {
            "title": "在 M1 Max 上运行 Kimi K3：Deltafin 项目实现本地推理",
            "selection_group": "other",
            "source_type": "tool",
            "fallback_excerpt": "开源工具提供 OpenAI 兼容 API，在本地运行 MoE 模型并报告推理速度。",
            "topic_tags": ["开源工具", "通用大模型"],
        }
        unverified_model_capability = {
            "title": "OpenAI Astra 以约2000美元证明10项数学难题",
            "selection_group": "model_release",
            "source_type": "model_release",
            "fallback_excerpt": "第三方社交消息称 Astra 完成多项数学证明。",
            "topic_tags": ["通用大模型"],
            "boundary": "该条来自第三方社交来源，尚未取得官方公告；仅作为待核验的高价值能力信号跟踪。",
        }
        codex_subagent_recipe = {
            "title": "Codex 用 Sol 指挥 Luna 处理有边界的实现任务",
            "selection_group": "developer_tool",
            "source_type": "industry_news",
            "fallback_excerpt": "经验帖建议由 Sol 拆任务和审代码，再把实现、测试和重构委托给 Luna 子代理。",
            "topic_tags": ["AI Coding"],
        }
        security_scanner = {
            "title": "AgentShield：面向 MCP 与智能体工具的安全扫描器",
            "selection_group": "developer_tool",
            "source_type": "tool",
            "fallback_excerpt": "该工具说明可发现命令注入与漏洞，并比较多个竞争项目的覆盖范围。",
            "topic_tags": ["智能体安全", "MCP 工具"],
        }
        framework_comparison = {
            "title": "Flue 2以Hook重构智能体组合方式",
            "selection_group": "methodology",
            "source_type": "methodology",
            "fallback_excerpt": "访谈比较了竞争框架，并说明智能体框架的运行与开发方式。",
            "topic_tags": ["智能体框架", "组合范式"],
        }
        official_glm_release = {
            "title": "GLM-5.3-Flash",
            "selection_group": "model_release",
            "source_type": "model_release",
            "source_quality_tier": "primary",
            "fallback_excerpt": "Z.ai 官方发布说明记录 GLM-5.3-Flash 上线。",
            "topic_tags": ["通用大模型", "AI Coding"],
        }
        model_api_methodology = {
            "title": "Experiential 开源智能体工作流网关与路由器",
            "selection_group": "methodology",
            "source_type": "methodology",
            "fallback_excerpt": "开源网关提供 OpenAI 兼容 API，并按质量和成本路由多个模型。",
            "topic_tags": ["智能体工程", "模型路由"],
        }
        openclaw_product_launch = {
            "title": "OpenClaw 2.0 发布多人云端 AI 编程平台",
            "selection_group": "consumer_entry",
            "source_type": "product_launch",
            "fallback_excerpt": "OpenClaw 推出多人共享项目数据与 AI 代理的云端开发环境。",
            "topic_tags": ["通用大模型"],
        }
        anthropic_eval_incident = {
            "title": "Anthropic 复盘 Claude 网络安全评测越权事件",
            "selection_group": "model_release",
            "source_type": "model_release",
            "source_quality_tier": "primary",
            "fallback_excerpt": "官方更新说明第三方评测环境中的未经授权访问与后续安全加固。",
            "topic_tags": ["通用大模型"],
        }

        self.assertEqual(editorial_category_for(agent_tutorial), "agent_engineering")
        self.assertEqual(editorial_category_for(search_signal), "product_distribution")
        self.assertEqual(editorial_category_for(real_release), "model_update")
        self.assertEqual(editorial_category_for(open_weights_position), "industry_signal")
        self.assertEqual(editorial_category_for(agent_environment), "agent_engineering")
        self.assertEqual(editorial_category_for(unverified_security_claim), "industry_signal")
        self.assertEqual(editorial_category_for(grok_release_with_page_chrome), "model_update")
        self.assertEqual(editorial_category_for(company_revenue_report), "industry_signal")
        self.assertEqual(editorial_category_for(model_adoption_report), "industry_signal")
        self.assertEqual(editorial_category_for(agent_intrusion_report), "industry_signal")
        self.assertEqual(editorial_category_for(local_runtime_tool), "agent_engineering")
        self.assertEqual(editorial_category_for(unverified_model_capability), "industry_signal")
        self.assertEqual(editorial_category_for(codex_subagent_recipe), "agent_engineering")
        self.assertEqual(editorial_category_for(security_scanner), "agent_engineering")
        self.assertEqual(editorial_category_for(framework_comparison), "agent_engineering")
        self.assertEqual(editorial_category_for(official_glm_release), "model_update")
        self.assertEqual(editorial_category_for(model_api_methodology), "agent_engineering")
        self.assertEqual(editorial_category_for(openclaw_product_launch), "product_release")
        self.assertEqual(editorial_category_for(anthropic_eval_incident), "industry_signal")

    def test_high_volume_fallback_uses_chinese_summary_not_scraped_page_body(self) -> None:
        cards = deterministic_news(
            [
                {
                    "title": "Google AI Overviews 搜索结果出现率升至43%",
                    "primary_url": "https://example.test/source",
                    "source_type": "tool",
                    "selection_group": "model_release",
                    "fetch_status": "fetched",
                    "topic_tags": ["通用大模型"],
                    "fallback_excerpt": "Google AI Overviews 在搜索结果中的出现率升至43%，用户搜索正转向更长的自然对话式查询。 🔗 阅读原文 via AI HOT · https://example.test/discovery",
                    "source_text": "Google's AI Overviews now appear in 43% of searches. Navigation and footer text.",
                }
            ]
        )

        self.assertEqual(len(cards), 1)
        self.assertIn("自然对话式查询", cards[0]["one_liner"])
        self.assertNotIn("https://", cards[0]["one_liner"])
        self.assertNotIn("Google's", cards[0]["one_liner"])
        self.assertNotIn("高分选源", cards[0]["why"])

    def test_primary_release_note_keeps_bounded_english_facts_for_translation(self) -> None:
        cards = deterministic_news(
            [
                {
                    "title": "GLM-5.3-Flash",
                    "selection_group": "model_release",
                    "source_type": "model_release",
                    "source_quality_tier": "primary",
                    "fallback_excerpt": (
                        "Efficient hybrid architecture combines linear and sparse attention "
                        "with 320B total parameters and 18B activated."
                    ),
                    "primary_url": "https://docs.z.ai/release-notes/new-released",
                    "topic_tags": ["通用大模型"],
                    "fetch_status": "fetched",
                }
            ]
        )

        self.assertIn("320B total parameters", cards[0]["one_liner"])
        self.assertEqual(cards[0]["category"], "model_update")
        self.assertNotIn("boundary", cards[0])

    def test_high_volume_news_uses_per_item_model_editorial_copy(self) -> None:
        provider = ProviderRuntime(
            kind="text",
            base_url="https://example.test/v1",
            api_key="test-secret",
            model="test-model",
            endpoint_path="chat/completions",
            timeout_seconds=5,
            max_retries=0,
            request_defaults={},
        )
        source = {
            "title": "Google AI Overviews 搜索结果出现率升至43%",
            "primary_url": "https://example.test/source",
            "source_type": "tool",
            "selection_group": "model_release",
            "fetch_status": "fetched",
            "topic_tags": ["通用大模型"],
            "fallback_excerpt": "Google AI Overviews 在搜索结果中的出现率升至43%，用户搜索正转向更长的自然对话式查询。",
        }
        generated = {
            "title": "Google AI Overviews 覆盖率提升，搜索入口继续变化",
            "one_liner": "Google 的 AI 搜索摘要覆盖率升至43%，用户检索正在从短关键词转向更长的对话式问题。",
            "why": "搜索入口被生成式答案重塑，内容发现、流量分配与用户意图理解都可能随之变化。",
            "observation_label": "工作启发",
            "observation": "评估搜索或内容产品时，可把对话式提问和答案页曝光纳入用户路径观察，而非只看传统关键词指标。",
            "boundary": "数据来自外部报道，具体统计口径仍需查看原始研究。",
        }
        with patch("generate_daily_content_api.chat_json", return_value=(generated, {"request_id": "card"})):
            cards, metadata = generate_individual_news_cards(provider, materials_news=[source], labels=["行业视角", "工作启发"])

        self.assertEqual(metadata["model_card_count"], 1)
        self.assertEqual(metadata["fallback_card_count"], 0)
        self.assertEqual(cards[0]["url"], source["primary_url"])
        self.assertEqual(cards[0]["title"], "Google AI Overviews 覆盖率提升，搜索入口继续变化")
        self.assertEqual(metadata["cards"][0]["title_mode"], "model")
        self.assertIn("内容发现", cards[0]["why"])
        self.assertEqual(cards[0]["observation_label"], "工作启发")

    def test_news_editorial_uses_configured_model_fallback_before_template_copy(self) -> None:
        primary = ProviderRuntime(
            kind="text", base_url="https://example.test/v1", api_key="test-secret", model="gpt-5.6-sol",
            endpoint_path="chat/completions", timeout_seconds=5, max_retries=0, request_defaults={},
        )
        fallback = ProviderRuntime(
            kind="text", base_url="https://example.test/v1", api_key="test-secret", model="gpt-5.4",
            endpoint_path="chat/completions", timeout_seconds=5, max_retries=0, request_defaults={},
        )
        source = {
            "title": "DeepSeek V4 Flash 正式版 API 上线",
            "primary_url": "https://example.test/source",
            "source_type": "model_release",
            "selection_group": "model_release",
            "fetch_status": "fetched",
            "topic_tags": ["通用大模型"],
            "fallback_excerpt": "DeepSeek V4 Flash 正式版 API 上线，并披露智能体能力相关基准。",
        }
        generated = {
            "title": "DeepSeek V4 Flash 正式开放开发者 API 调用",
            "one_liner": "DeepSeek V4 Flash 正式版 API 已上线，相关材料披露其智能体和指令遵循能力更新。",
            "why": "这提供了国产模型在 API 可用性和智能体任务能力上的新观察点，需继续核对评测口径。",
            "observation_label": "行业视角",
            "observation": "模型选型应同时比较实际调用稳定性、任务适配与评测条件，而不是只比较单项成绩。",
            "boundary": "具体指标和适用范围仍应以原始模型资料为准。",
        }
        with patch(
            "generate_daily_content_api.chat_json",
            side_effect=[GatewayError("primary route unavailable"), (generated, {"request_id": "fallback"})],
        ):
            cards, metadata = generate_individual_news_cards(
                (primary, fallback), materials_news=[source], labels=["行业视角", "工作启发"]
            )

        self.assertEqual(metadata["model_card_count"], 1)
        self.assertEqual(metadata["fallback_card_count"], 0)
        self.assertTrue(metadata["cards"][0]["fallback_model_used"])
        self.assertEqual(metadata["cards"][0]["provider"]["model"], "gpt-5.4")
        self.assertIn("DeepSeek", cards[0]["one_liner"])

    def test_raw_github_title_never_becomes_a_publishable_card_title(self) -> None:
        provider = ProviderRuntime(
            kind="text", base_url="https://example.test/v1", api_key="test-secret", model="test-model",
            endpoint_path="chat/completions", timeout_seconds=5, max_retries=0, request_defaults={},
        )
        source = {
            "title": "GitHub - aiconnai/agentshield: 🛡️ 100% offline Rust SAST + 1-click auto-fix",
            "primary_url": "https://github.com/aiconnai/agentshield",
            "source_type": "methodology",
            "selection_group": "methodology",
            "fetch_status": "fetched",
            "topic_tags": ["安全边界"],
            "fallback_excerpt": "AgentShield 是用于检查 MCP 与智能体工具风险的开源项目。",
        }
        generated = {
            "one_liner": "材料介绍一款面向 MCP 与智能体工具的开源安全扫描项目，具体性能仍待独立验证。",
            "why": "它提示安全检查可前移到工具接入与代码交付环节，但规则覆盖和误报率仍需实测。",
            "observation_label": "行业视角",
            "observation": "企业落地时应同时验证提交前扫描、运行时权限隔离和真实攻击场景下的防护效果。",
        }
        with patch("generate_daily_content_api.chat_json", return_value=(generated, {"request_id": "card"})):
            cards, metadata = generate_individual_news_cards(provider, materials_news=[source], labels=["行业视角", "工作启发"])

        self.assertEqual(cards, [])
        self.assertEqual(metadata["model_card_count"], 0)
        self.assertEqual(metadata["fallback_card_count"], 0)
        self.assertEqual(metadata["dropped_card_count"], 1)
        self.assertEqual(metadata["dropped_cards"][0]["mode"], "dropped")
        self.assertEqual(metadata["dropped_cards"][0]["source_title"], source["title"])

    def test_failed_news_card_is_dropped_without_discarding_successful_cards(self) -> None:
        provider = ProviderRuntime(
            kind="text", base_url="https://example.test/v1", api_key="test-secret", model="test-model",
            endpoint_path="chat/completions", timeout_seconds=5, max_retries=0, request_defaults={},
        )
        sources = [
            {
                "title": "OpenAI 发布新的企业工作流能力",
                "primary_url": "https://example.test/good",
                "source_type": "product_release",
                "selection_group": "product_release",
                "fetch_status": "fetched",
                "topic_tags": ["工作流"],
                "fallback_excerpt": "OpenAI 发布面向企业任务的新工作流能力。",
            },
            {
                "title": "GitHub - example/raw-project README",
                "primary_url": "https://example.test/bad",
                "source_type": "methodology",
                "selection_group": "methodology",
                "fetch_status": "fetched",
                "topic_tags": ["开源工具"],
                "fallback_excerpt": "该项目介绍一个开源工具。",
            },
        ]
        good = {
            "title": "OpenAI 为企业任务推出新的工作流能力",
            "one_liner": "OpenAI 发布面向企业任务的新工作流能力，支持更完整的流程执行。",
            "why": "这使模型从回答问题进一步转向执行企业流程。",
            "observation_label": "工作启发",
            "observation": "评估时应同时检查权限、失败恢复与审计能力。",
        }
        bad = {
            "one_liner": "该项目介绍一个开源工具。",
            "why": "需要进一步核验实际能力。",
            "observation_label": "行业视角",
            "observation": "不能仅凭 README 判断生产价值。",
        }

        with patch(
            "generate_daily_content_api.chat_json",
            side_effect=lambda *args, **kwargs: (
                (bad, {"request_id": "bad"}) if "raw-project" in kwargs["user_prompt"]
                else (good, {"request_id": "good"})
            ),
        ):
            cards, metadata = generate_individual_news_cards(
                provider, materials_news=sources, labels=["行业视角", "工作启发"]
            )

        self.assertEqual(len(cards), 1)
        self.assertEqual(cards[0]["url"], sources[0]["primary_url"])
        self.assertEqual(metadata["card_count"], 1)
        self.assertEqual(metadata["model_card_count"], 1)
        self.assertEqual(metadata["fallback_card_count"], 0)
        self.assertEqual(metadata["dropped_card_count"], 1)
        self.assertEqual(metadata["dropped_cards"][0]["source_index"], 1)

    def test_runtime_config_exposes_same_gateway_text_fallback_models(self) -> None:
        config = {
            "schema_version": 2,
            "defaults": {"api_key_env": "TEXT_KEY", "base_url_env": "TEXT_URL"},
            "text": {
                "enabled": True,
                "model": "gpt-5.6-sol",
                "fallback_models": ["gpt-5.4", "gpt-5.6-sol"],
                "endpoint_path": "chat/completions",
            },
            "image": {"enabled": False},
        }
        with patch.dict(os.environ, {"TEXT_KEY": "test-secret", "TEXT_URL": "https://example.test/v1"}, clear=False):
            providers = resolve_provider_fallbacks("text", config)

        self.assertEqual([provider.model for provider in providers], ["gpt-5.6-sol", "gpt-5.4"])

    def test_chat_json_repairs_markdown_when_gateway_ignores_json_mode(self) -> None:
        provider = ProviderRuntime(
            kind="text",
            base_url="https://example.test/v1",
            api_key="test-secret",
            model="test-model",
            endpoint_path="chat/completions",
            timeout_seconds=5,
            max_retries=0,
            request_defaults={"response_format": {"type": "json_object"}},
        )
        first_response = {
            "choices": [{"message": {"content": "# Draft\nThis gateway ignored JSON mode."}}],
            "usage": {"total_tokens": 10},
        }
        repair_response = {
            "choices": [{"message": {"content": "```json\n{\"ok\": true}\n```"}}],
            "usage": {"total_tokens": 4},
        }
        with patch(
            "ai_gateway_client.request_json",
            side_effect=[(first_response, {"x-request-id": "first"}, 200), (repair_response, {"request-id": "repair"}, 200)],
        ) as request:
            response, metadata = chat_json(provider, system_prompt="Return an object with ok.", user_prompt="Test.")

        self.assertEqual(response, {"ok": True})
        self.assertTrue(metadata["json_repair"]["attempted"])
        self.assertEqual(metadata["json_repair"]["request_id"], "repair")
        self.assertEqual(request.call_count, 2)
        repair_payload = request.call_args_list[1].args[1]
        self.assertEqual(repair_payload["messages"][0]["role"], "system")
        self.assertIn("<draft>", repair_payload["messages"][1]["content"])

    def test_image_aspect_validation_allows_density_changes_but_rejects_wrong_orientation(self) -> None:
        validate_requested_aspect({"width": 1280, "height": 720}, "1920x1080")
        with self.assertRaises(GenerateError):
            validate_requested_aspect({"width": 1024, "height": 1024}, "1920x1080")

    def test_secure_runtime_configuration_replaces_values(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            env_file = Path(directory) / ".env"
            env_file.write_text("OPENAI_API_KEY=legacy\nAI_DAILY_API_BASE_URL=https://old.example/v1\n", encoding="utf-8")
            update_env_file(
                env_file,
                {
                    "AI_DAILY_API_KEY": "test-secret",
                    "AI_DAILY_API_BASE_URL": "https://relay.example/v1",
                    "AI_DAILY_TEXT_MODEL": "gpt-5.6-sol",
                    "AI_DAILY_IMAGE_MODEL": "gpt-image-2",
                },
            )
            configured = env_file.read_text(encoding="utf-8")
            self.assertIn("AI_DAILY_API_KEY=test-secret", configured)
            self.assertIn("AI_DAILY_API_BASE_URL=https://relay.example/v1", configured)
            self.assertEqual(env_file.stat().st_mode & 0o777, 0o600)

    def test_shared_runtime_routes_text_and_image(self) -> None:
        with tempfile.TemporaryDirectory() as directory, fake_gateway() as base_url:
            config_path = Path(directory) / "runtime.json"
            config_path.write_text(
                json.dumps(
                    {
                        "schema_version": 2,
                        "defaults": {
                            "api_key_env": "TEST_API_KEY",
                            "base_url_env": "TEST_API_BASE_URL",
                            "timeout_seconds": 5,
                            "max_retries": 0,
                        },
                        "text": {
                            "enabled": True,
                            "model": "gpt-5.6-sol",
                            "endpoint_path": "chat/completions",
                            "request_defaults": {"temperature": 0.2},
                        },
                        "image": {
                            "enabled": True,
                            "model": "gpt-image-2",
                            "endpoint_path": "images/generations",
                            "request_defaults": {"response_format": "b64_json", "output_format": "png"},
                        },
                    }
                ),
                encoding="utf-8",
            )
            with patch.dict(
                os.environ,
                {"TEST_API_KEY": "test-secret", "TEST_API_BASE_URL": base_url},
                clear=False,
            ):
                config = load_runtime_config(config_path)
                text_provider = resolve_provider("text", config)
                image_provider = resolve_provider("image", config)
                response, metadata = chat_json(
                    text_provider,
                    system_prompt="Return JSON.",
                    user_prompt="Test.",
                )
                image, request, image_metadata = generate_image_bytes(image_provider, prompt="Test image.")

            self.assertEqual(response, {"ok": True, "model": "gpt-5.6-sol"})
            self.assertEqual(metadata["request_id"], "fake-request")
            self.assertTrue(image.startswith(b"\x89PNG"))
            self.assertEqual(request["model"], "gpt-image-2")
            self.assertEqual(image_metadata["request_id"], "fake-request")

    def test_url_image_response_uses_only_allowlisted_unauthenticated_downloader(self) -> None:
        with tempfile.TemporaryDirectory() as directory, fake_gateway() as base_url:
            config_path = Path(directory) / "runtime.json"
            config_path.write_text(
                json.dumps(
                    {
                        "schema_version": 2,
                        "defaults": {
                            "api_key_env": "URL_API_KEY",
                            "base_url_env": "URL_API_BASE_URL",
                            "timeout_seconds": 5,
                            "max_retries": 0,
                        },
                        "text": {"enabled": False},
                        "image": {
                            "enabled": True,
                            "model": "gpt-image-2",
                            "endpoint_path": "images/generations",
                            "request_defaults": {"response_format": "url", "output_format": "png"},
                            "response_url": {
                                "allowed_host_suffixes": ["aitohumanize.com"],
                                "max_bytes": 1024 * 1024,
                            },
                        },
                    }
                ),
                encoding="utf-8",
            )
            with patch.dict(
                os.environ,
                {"URL_API_KEY": "url-secret", "URL_API_BASE_URL": base_url},
                clear=False,
            ), patch(
                "ai_gateway_client._download_image_url",
                return_value=(png_bytes(), "file4.aitohumanize.com"),
            ) as downloader:
                provider = resolve_provider("image", load_runtime_config(config_path))
                raw, request, metadata = generate_image_bytes(provider, prompt="Test image URL.")

            self.assertTrue(raw.startswith(b"\x89PNG"))
            self.assertEqual(request["response_format"], "url")
            self.assertEqual(metadata["image_response_type"], "url")
            self.assertEqual(metadata["image_url_host"], "file4.aitohumanize.com")
            self.assertEqual(downloader.call_args.kwargs["allowed_suffixes"], ("aitohumanize.com",))
            self.assertNotIn("url-secret", str(downloader.call_args))

    def test_url_image_response_requires_host_allowlist(self) -> None:
        config = {
            "schema_version": 2,
            "defaults": {"api_key_env": "URL_KEY", "base_url_env": "URL_BASE"},
            "text": {"enabled": False},
            "image": {
                "enabled": True,
                "model": "gpt-image-2",
                "endpoint_path": "images/generations",
                "request_defaults": {"response_format": "url"},
            },
        }
        with patch.dict(os.environ, {"URL_KEY": "secret", "URL_BASE": "https://example.test/v1"}, clear=False):
            with self.assertRaises(GatewayError):
                resolve_provider("image", config)

    def test_primary_image_gateway_failure_uses_configured_fallback(self) -> None:
        registry = load_provider_registry(WORKFLOW_DIR / "config" / "image_providers.json")
        provider_id, provider = resolve_image_provider(None, registry)
        with tempfile.TemporaryDirectory() as directory, fake_gateway() as base_url:
            root = Path(directory)
            primary_config = root / "primary.json"
            fallback_config = root / "fallback.json"
            for config_path, key_env, base_env, model in (
                (primary_config, "PRIMARY_KEY", "PRIMARY_BASE", "gpt-image-2"),
                (fallback_config, "FALLBACK_KEY", "FALLBACK_BASE", "gpt-image-2-vip"),
            ):
                config_path.write_text(
                    json.dumps(
                        {
                            "schema_version": 2,
                            "defaults": {
                                "api_key_env": key_env,
                                "base_url_env": base_env,
                                "timeout_seconds": 5,
                                "max_retries": 0,
                            },
                            "text": {"enabled": False},
                            "image": {
                                "enabled": True,
                                "model": model,
                                "endpoint_path": "images/generations",
                                "request_defaults": {
                                    "size": "1024x1024",
                                    "output_format": "png",
                                    "response_format": "b64_json",
                                },
                            },
                        }
                    ),
                    encoding="utf-8",
                )
            output = root / "candidate.png"
            with patch.dict(
                os.environ,
                {
                    "PRIMARY_KEY": "primary-secret",
                    "PRIMARY_BASE": base_url,
                    "FALLBACK_KEY": "fallback-secret",
                    "FALLBACK_BASE": base_url,
                },
                clear=False,
            ):
                request, request_id, response, image_info = generate_api_image(
                    provider_id,
                    provider,
                    "Test fallback image.",
                    output,
                    size=None,
                    quality=None,
                    runtime_config_path=primary_config,
                    fallback_runtime_config_path=fallback_config,
                    dry_run=False,
                )

            self.assertEqual(request["model"], "gpt-image-2-vip")
            self.assertEqual(request_id, "fake-request")
            self.assertTrue(response["fallback"]["used"])
            self.assertIn("HTTP 512", response["fallback"]["primary_error"])
            self.assertEqual(image_info["format"], "png")
            self.assertTrue(output.exists())

            with patch.dict(
                os.environ,
                {
                    "PRIMARY_KEY": "primary-secret",
                    "PRIMARY_BASE": base_url,
                    "FALLBACK_KEY": "fallback-secret",
                    "FALLBACK_BASE": base_url,
                },
                clear=False,
            ):
                with self.assertRaises(GenerateError) as raised:
                    generate_api_image(
                        provider_id,
                        provider,
                        "Test strict route image.",
                        root / "strict.png",
                        size=None,
                        quality=None,
                        runtime_config_path=primary_config,
                        fallback_runtime_config_path=fallback_config,
                        dry_run=False,
                        allow_fallback=False,
                    )
            self.assertIn("image request failed", str(raised.exception))
            self.assertNotIn("fallback also failed", str(raised.exception))

    def test_image_prompt_preserves_visual_handoff_for_the_drawing_backend(self) -> None:
        visual = {
            "Display Title": "Agent 如何验证证据",
            "Core Idea": "证据检查降低无依据结论。",
            "Distinctive Detail": "三个阶段形成可审计结论。",
            "Primary Visual Scene": "RAW_SCENE_SHOULD_NOT_BECOME_A_STORYBOARD",
            "Must Preserve": "检索、检查和可审计中间产物。",
            "Main Evidence Anchor": "检查前后的定性对照。",
            "Boundary Cue": "只覆盖测试任务。",
            "Do Not Imply": "不要暗示所有任务都能消除幻觉。",
            "Avoid": "logo、人物、虚构数字。",
            "Reject If": "缺少证据检查机制。",
            "People Policy": "No people or logos.",
            "Visual Tone": "research explainer",
        }

        raw_handoff = "a deliberately long raw handoff\nPrimary Visual Scene: RAW_SCENE_SHOULD_REACH_THE_DRAWING_BACKEND"
        prompt = make_image_prompt(raw_handoff, visual, "unused.png")

        self.assertNotIn(raw_handoff, prompt)
        for value in (visual["Must Preserve"], visual["Main Evidence Anchor"], visual["Boundary Cue"], visual["Do Not Imply"]):
            self.assertIn(value, prompt)
        self.assertIn("主题：\nAgent 如何验证证据", prompt)
        image_policy = json.loads((WORKFLOW_DIR / 'config/research_image_policy.json').read_text())
        self.assertIn(image_policy['canvas'], prompt)
        self.assertEqual(prompt.count("绘制要求："), 1)
        self.assertNotIn("8–12", prompt)

    def test_provider_specific_credentials_override_shared_runtime(self) -> None:
        config = {
            "schema_version": 2,
            "defaults": {
                "api_key_env": "SHARED_KEY",
                "base_url_env": "SHARED_URL",
            },
            "text": {
                "enabled": True,
                "api_key_env": "TEXT_KEY",
                "base_url_env": "TEXT_URL",
                "model": "gpt-5.6-sol",
                "endpoint_path": "chat/completions",
            },
            "image": {
                "enabled": True,
                "api_key_env": "IMAGE_KEY",
                "base_url_env": "IMAGE_URL",
                "model": "gpt-image-2",
                "endpoint_path": "images/generations",
            },
        }
        with patch.dict(
            os.environ,
            {
                "SHARED_KEY": "shared-secret",
                "SHARED_URL": "https://shared.example/v1",
                "TEXT_KEY": "text-secret",
                "TEXT_URL": "https://text.example/v1",
                "IMAGE_KEY": "image-secret",
                "IMAGE_URL": "https://image.example/v1",
            },
            clear=False,
        ):
            text_provider = resolve_provider("text", config)
            image_provider = resolve_provider("image", config)

        self.assertEqual(text_provider.api_key, "text-secret")
        self.assertEqual(text_provider.base_url, "https://text.example/v1")
        self.assertEqual(image_provider.api_key, "image-secret")
        self.assertEqual(image_provider.base_url, "https://image.example/v1")

    def test_deployable_editorial_and_image_scripts(self) -> None:
        with tempfile.TemporaryDirectory() as directory, fake_gateway() as base_url:
            root = Path(directory)
            config_path = root / "runtime.json"
            config_path.write_text(
                json.dumps(
                    {
                        "schema_version": 2,
                        "defaults": {
                            "api_key_env": "TEST_API_KEY",
                            "base_url_env": "TEST_API_BASE_URL",
                            "timeout_seconds": 5,
                            "max_retries": 0,
                        },
                        "text": {
                            "enabled": True,
                            "model": "gpt-5.6-sol",
                            "endpoint_path": "chat/completions",
                            "request_defaults": {"temperature": 0.2},
                        },
                        "image": {
                            "enabled": True,
                            "model": "gpt-image-2-vip",
                            "endpoint_path": "images/generations",
                            "request_defaults": {
                                "size": "1024x1024",
                                "quality": "high",
                                "output_format": "png",
                                "response_format": "b64_json",
                            },
                        },
                    }
                ),
                encoding="utf-8",
            )
            materials = root / "materials.json"
            materials.write_text(
                json.dumps(
                    {
                        "research": [
                            {
                                "rank": 1,
                                "title": "Test research",
                                "primary_url": "https://example.com/research",
                                "source_text": "The task is to judge whether agent answers have evidence. The workflow retrieves evidence, checks conflicts, then forms a conclusion. Controlled tests show fewer unsupported conclusions. Results are limited to tested tasks and models.",
                                "fetch_status": "fetched",
                            }
                        ],
                        "news": [
                            {
                                "rank": 1,
                                "title": "Test product",
                                "primary_url": "https://example.com/product",
                                "source_text": "The product added evidence tracing and conflict warnings.",
                                "fetch_status": "fetched",
                                "selection_group": "enterprise_integration",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            brief = root / "brief.json"
            prompt = root / "prompt.txt"
            content = root / "content.json"
            image = root / "candidate.png"
            image_manifest = root / "candidate.generation.json"
            environment = {
                **os.environ,
                "TEST_API_KEY": "test-secret",
                "TEST_API_BASE_URL": base_url,
            }
            commands = [
                [
                    sys.executable,
                    str(WORKFLOW_DIR / "scripts" / "generate_research_brief_api.py"),
                    "--materials",
                    str(materials),
                    "--date",
                    "2026-07-15",
                    "--out-json",
                    str(brief),
                    "--prompt-out",
                    str(prompt),
                    "--runtime-config",
                    str(config_path),
                ],
                [
                    sys.executable,
                    str(WORKFLOW_DIR / "scripts" / "generate_daily_content_api.py"),
                    "--materials",
                    str(materials),
                    "--brief-json",
                    str(brief),
                    "--date",
                    "2026-07-15",
                    "--output",
                    str(content),
                    "--runtime-config",
                    str(config_path),
                ],
                [
                    sys.executable,
                    str(WORKFLOW_DIR / "scripts" / "generate_research_image.py"),
                    "--provider",
                    "gpt_image_2",
                    "--prompt-file",
                    str(prompt),
                    "--out",
                    str(image),
                    "--generation-manifest",
                    str(image_manifest),
                    "--runtime-config",
                    str(config_path),
                ],
            ]
            for command in commands:
                completed = subprocess.run(command, cwd=WORKFLOW_DIR, env=environment, text=True, capture_output=True)
                self.assertEqual(completed.returncode, 0, completed.stderr or completed.stdout)

            rendered_content = json.loads(content.read_text(encoding="utf-8"))
            generation = json.loads(image_manifest.read_text(encoding="utf-8"))
            self.assertEqual(rendered_content["news"][0]["url"], "https://example.com/product")
            self.assertEqual(rendered_content["generation"]["provider"]["model"], "gpt-5.6-sol")
            self.assertEqual(generation["provider"]["id"], "gpt_image_2")
            self.assertEqual(generation["output"]["format"], "png")
            self.assertEqual(generation["output"]["width"], 512)
            self.assertTrue(prompt.read_text(encoding="utf-8").strip())


class EditorialRuleTests(unittest.TestCase):
    def test_huggingface_paper_index_resolves_to_arxiv_for_editorial_evidence(self) -> None:
        self.assertEqual(
            editorial_primary_url("https://huggingface.co/papers/2607.22798", "paper"),
            "https://arxiv.org/abs/2607.22798",
        )

    def test_source_fetch_retries_before_failing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "feed.xml"
            attempts = 0

            def fake_run(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
                nonlocal attempts
                attempts += 1
                if attempts < 3:
                    return subprocess.CompletedProcess([], 28, "", "operation timed out")
                output.write_text("<rss><channel /></rss>", encoding="utf-8")
                return subprocess.CompletedProcess([], 0, "", "")

            with patch("select_aihot_sources.subprocess.run", side_effect=fake_run):
                self.assertEqual(run_curl("https://example.com/feed.xml", output), "<rss><channel /></rss>")

            self.assertEqual(attempts, 3)

    def test_explicit_model_release_is_not_misclassified_as_research(self) -> None:
        candidate = {
            "title": "Gemini 3.6 Flash、3.5 Flash-Lite 和 3.5 Flash Cyber 正式发布",
            "description": "官方同步公布多项 benchmark 与模型评测结果。",
            "categories": ["模型发布", "评测"],
        }
        candidate["source_type"] = source_type_for(candidate)
        self.assertEqual(candidate["source_type"], "model_release")
        self.assertEqual(selection_group_for(candidate), "model_release")

    def test_same_day_syndicated_security_event_is_selected_once(self) -> None:
        def item(title: str, score: float, url: str) -> dict[str, object]:
            return {
                "title": title,
                "description": "OpenAI GPT-6 在 Hugging Face 沙箱评估中出现逃逸与入侵争议。",
                "published_at": "2026-07-22T09:00:00+08:00",
                "primary_url": url,
                "primary_host": "example.com",
                "aihot_url": "",
                "categories": ["安全"],
                "topic_tags": ["安全边界"],
                "selection_group": "security_boundary",
                "source_type": "industry_news",
                "research_eligible": False,
                "research_window_strategy": "exact_window",
                "content_duplicate_check": {"is_duplicate": False},
                "continuity_note": "",
                "scores": {"final_news": score, "final_research": 0, "total": score},
                "score_breakdown": {"source_reliability": 80, "industry_impact": 90},
            }

        _, selected, _ = select_items(
            [
                item("OpenAI GPT-6 测试触发沙箱逃逸争议", 92, "https://example.com/official"),
                item("GPT-6 攻破 Hugging Face 生产环境？", 88, "https://example.com/media"),
            ],
            research_count=0,
            min_news_count=1,
        )
        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0]["primary_url"], "https://example.com/official")

    def test_gpt_image_2_requires_per_image_approval_for_production(self) -> None:
        registry = load_provider_registry(WORKFLOW_DIR / "config" / "image_providers.json")
        _, provider = resolve_image_provider("gpt_image_2", registry)
        self.assertTrue(provider["production_allowed"])
        self.assertTrue(provider_can_enter_production(provider, "approved"))
        self.assertFalse(provider_can_enter_production(provider, "pending"))

    def test_marshall_image_2_requires_per_image_approval_for_production(self) -> None:
        registry = load_provider_registry(WORKFLOW_DIR / "config" / "image_providers.json")
        _, provider = resolve_image_provider("marshall_gpt_image_2", registry)
        self.assertTrue(provider["production_allowed"])
        self.assertTrue(provider_can_enter_production(provider, "approved"))
        self.assertFalse(provider_can_enter_production(provider, "pending"))

    def test_marshall_image2_is_the_user_confirmed_default(self) -> None:
        registry = load_provider_registry(WORKFLOW_DIR / "config" / "image_providers.json")
        provider_id, provider = resolve_image_provider(None, registry)
        self.assertEqual(provider_id, "marshall_gpt_image_2")
        self.assertEqual(provider["model"], "gpt-image-2")
        self.assertTrue(provider["production_allowed"])

    def test_nano_banana_keeps_its_grsai_model_fallback(self) -> None:
        registry = load_provider_registry(WORKFLOW_DIR / "config" / "image_providers.json")
        _, provider = resolve_image_provider("nano_banana_image2", registry)
        self.assertEqual(provider["fallback_models"], ["gpt-image-2-vip"])

    def test_digest_source_uses_site_identity_not_action_label(self) -> None:
        url = "https://www.anthropic.com/research/example"
        rendered = render_digest_sources([{"label": "阅读原文", "url": url}])
        self.assertEqual(source_display(url), "Anthropic Research")
        self.assertIn("Anthropic Research", rendered)
        self.assertNotIn("阅读原文", rendered)
        self.assertIn('target="_blank"', rendered)
        self.assertIn('rel="noopener noreferrer"', rendered)
        self.assertIn("text-decoration:underline", rendered)

    def test_usage_research_does_not_take_research_frontier_slot(self) -> None:
        candidate = {
            "research_eligible": True,
            "selection_group": "usage_research",
            "source_type": "report",
            "feed_source": "direct:anthropic_research",
            "categories": ["Official Research", "Economic Research"],
            "primary_host": "anthropic.com",
            "title": "How a country uses Claude",
        }
        self.assertFalse(is_research_eligible(candidate))

    def test_usage_research_palette_uses_single_project_policy(self) -> None:
        guidance = make_palette_guidance(
            {
                "Core Idea": "解释地区产业结构和大模型采用差异",
                "Primary Visual Scene": "区域采用地图与劳动力结构对照",
            }
        )
        image_policy = json.loads((WORKFLOW_DIR / 'config/research_image_policy.json').read_text())
        self.assertEqual(guidance, image_policy['palette'])
        self.assertNotIn("禁止用紫色", guidance)

    def test_official_rss_research_remains_eligible(self) -> None:
        xml = """<?xml version="1.0"?><rss><channel><item>
        <title>Technical evaluation</title>
        <link>https://example.com/research/eval</link>
        <description>Benchmark and method details</description>
        <pubDate>Wed, 15 Jul 2026 02:00:00 GMT</pubDate>
        </item></channel></rss>"""
        candidates, _ = parse_official_rss_feed(
            xml,
            datetime(2026, 7, 14, tzinfo=timezone.utc),
            datetime(2026, 7, 16, tzinfo=timezone.utc),
            [],
            source={
                "id": "test_research",
                "name": "Test Research",
                "pool": "research",
                "authority": "official_research",
                "source_type_override": "paper",
            },
            research_window_strategy="direct_research_lookback",
        )
        self.assertEqual(len(candidates), 1)
        self.assertTrue(candidates[0]["research_eligible"])

    def test_anthropic_economic_research_is_report_not_frontier(self) -> None:
        html = """<a href="/research/how-canada-uses-claude">
        How Canada uses Claude Economic Research Jul 14, 2026
        </a>"""
        candidates, _ = parse_anthropic_listing_page(
            html,
            datetime(2026, 7, 13, tzinfo=timezone.utc),
            datetime(2026, 7, 16, tzinfo=timezone.utc),
            [],
            source={
                "id": "anthropic_research",
                "name": "Anthropic Research",
                "url": "https://www.anthropic.com/research",
                "pool": "research",
                "authority": "official_research",
                "path_prefixes": ["/research/"],
                "exclude_prefixes": [],
                "source_type_override": "paper",
            },
            research_window_strategy="direct_research_lookback",
        )
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["source_type"], "report")
        self.assertFalse(candidates[0]["research_eligible"])

    def test_codex_micro_reports_share_one_message_identity(self) -> None:
        official = message_keys("OpenAI Codex Micro co-lab with Work Louder")
        media = message_keys("OpenAI 首款联名硬件 Codex Micro 键盘登场")
        self.assertIn("openai-codex-micro-keyboard", official)
        self.assertIn("openai-codex-micro-keyboard", media)

    def test_gpt_red_reports_share_one_message_identity(self) -> None:
        official = message_keys("GPT-Red unlocks self-improvement for prompt injection robustness")
        social = message_keys("OpenAI 推出 GPT-Red，用于自动化红队演练和提示注入测试")
        self.assertIn("openai-gpt-red-automated-red-teaming", official)
        self.assertIn("openai-gpt-red-automated-red-teaming", social)

    def test_employee_lawsuit_is_not_classified_as_methodology(self) -> None:
        candidate = {
            "title": "苹果起诉 OpenAI 员工窃密案：邮件沟通披露",
            "description": "一起员工与商业秘密诉讼",
            "categories": [],
            "source_type": "report",
        }
        self.assertEqual(selection_group_for(candidate), "governance_risk")

    def test_apple_intelligence_qwen_integration_is_consumer_entry(self) -> None:
        candidate = {
            "title": "国行 Apple 智能完成备案，阿里千问将集成至苹果 AI 能力",
            "description": "千问将集成至国行 iPhone 的 Apple 智能入口",
            "categories": [],
        }
        self.assertEqual(selection_group_for(candidate), "consumer_entry")

    def test_high_score_items_bypass_group_quota(self) -> None:
        def item(title: str, score: float) -> dict[str, object]:
            slug = title.lower().replace(" ", "-")
            return {
                "title": title,
                "description": title,
                "primary_url": f"https://example.com/{slug}",
                "aihot_url": "",
                "selection_group": "model_release",
                "source_type": "product_launch",
                "research_eligible": False,
                "research_window_strategy": "exact_window",
                "scores": {
                    "final_news": score,
                    "final_research": 0,
                    "total": score,
                },
                "score_breakdown": {"source_reliability": 80, "industry_impact": 80},
            }

        _, selected, _ = select_items(
            [item("Model Alpha", 91), item("Model Beta", 82.8)],
            research_count=0,
            min_news_count=1,
        )
        self.assertEqual([entry["title"] for entry in selected], ["Model Alpha", "Model Beta"])

    def test_news_selection_honors_hard_editorial_cap(self) -> None:
        def item(index: int, score: float) -> dict[str, object]:
            return {
                "title": f"Distinct AI signal {index}",
                "description": "A distinct product update with enough detail.",
                "published_at": "2026-07-28T08:30:00+08:00",
                "primary_url": f"https://example.com/{index}",
                "primary_host": "example.com",
                "aihot_url": "",
                "categories": [],
                "topic_tags": ["AI"],
                "selection_group": "other",
                "source_type": "industry_news",
                "research_eligible": False,
                "research_window_strategy": "exact_window",
                "content_duplicate_check": {"is_duplicate": False},
                "continuity_note": "",
                "scores": {"final_news": score, "final_research": 0, "total": score},
                "score_breakdown": {"source_reliability": 80, "industry_impact": 80},
            }

        _, selected, _ = select_items(
            [item(index, 99 - index) for index in range(8)],
            research_count=0,
            min_news_count=5,
            max_news_count=3,
        )
        self.assertEqual(len(selected), 3)

    def test_same_event_merge_keeps_higher_scoring_source(self) -> None:
        lower = {
            "title": "苹果 Apple 智能通过备案，阿里千问将集成至国行 iPhone",
            "description": "Apple 智能备案并集成千问",
            "primary_url": "https://example.com/roundup",
            "aihot_url": "",
            "feed_source": "all",
            "categories": [],
            "scores": {"final_news": 78.4, "final_research": 0},
            "score_breakdown": {"source_reliability": 62},
        }
        higher = {
            "title": "国行 Apple 智能完成备案，阿里千问将集成至苹果 AI 能力",
            "description": "千问将集成至国行 iPhone 的 Apple 智能入口",
            "primary_url": "https://example.com/focused",
            "aihot_url": "",
            "feed_source": "main",
            "categories": [],
            "scores": {"final_news": 82.8, "final_research": 0},
            "score_breakdown": {"source_reliability": 72},
        }
        merged = merge_candidates([lower, higher])
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["title"], higher["title"])

    def test_same_event_merge_preserves_research_eligibility(self) -> None:
        research = {
            "title": "GPT-Red automated red teaming research",
            "description": "A benchmark and research method for automated red teaming.",
            "primary_url": "https://openai.com/index/unlocking-self-improvement-gpt-red",
            "aihot_url": "",
            "feed_source": "direct:openai_research",
            "source_name": "OpenAI Research",
            "categories": ["Research"],
            "research_eligible": True,
            "research_window_strategy": "exact_window",
            "scores": {"final_news": 95, "final_research": 98},
            "score_breakdown": {"source_reliability": 95},
        }
        news = {
            "title": "OpenAI 推出 GPT-Red 自动化红队系统",
            "description": "GPT-Red 用于自动化红队和提示注入测试。",
            "primary_url": "https://openai.com/index/unlocking-self-improvement-gpt-red",
            "aihot_url": "",
            "feed_source": "bestblogs",
            "source_name": "BestBlogs",
            "categories": ["News"],
            "research_eligible": False,
            "research_window_strategy": "exact_window",
            "scores": {"final_news": 97.3, "final_research": 98},
            "score_breakdown": {"source_reliability": 95},
        }
        merged = merge_candidates([research, news])
        self.assertEqual(len(merged), 1)
        self.assertTrue(merged[0]["research_eligible"])
        self.assertEqual(set(merged[0]["categories"]), {"Research", "News"})


if __name__ == "__main__":
    unittest.main()
