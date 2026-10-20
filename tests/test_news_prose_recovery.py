"""Regression coverage for the production single-card editorial path."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from ai_gateway_client import ProviderRuntime, GatewayError
from generate_daily_content_api import (
    ContentApiError, batch_system_prompt, editorial_text,
    generate_individual_news_cards, generate_shell_with_fallback, generated_news_copy,
    news_card_system_prompt, news_card_user_prompt,
    nonempty, normalize_news, shell_system_prompt, source_packet,
)


def runtime():
    return ProviderRuntime(kind="text", base_url="https://example.test/v1", api_key="test",
                           model="test", endpoint_path="chat/completions", timeout_seconds=5,
                           max_retries=0, request_defaults={})


def source(**changes):
    return {"rank": 1, "title": "厂商发布检索工具预览", "primary_url": "https://example.test/release",
            "source_type": "tool", "fetch_status": "fetched", "event_id": "retrieval-preview",
            "display_tier": "brief", "daily_rank": 3,
            "coverage_source_urls": ["https://example.test/secondary"],
            "fallback_excerpt": "厂商向企业客户开放检索工具预览。", **changes}


def card(**changes):
    return {"title": "企业检索工具开放客户预览",
            "one_liner": "厂商向企业客户开放检索工具预览，可在同一入口查询多个内部文档库。",
            "why": "知识查询入口开始跨越单个文档系统。",
            "observation_label": "行业视角",
            "observation": "统一查询能减少员工在不同文档系统间切换，但答案的适用范围仍取决于可访问的数据。",
            "boundary": "", **changes}


class NewsProseRecoveryTests(unittest.TestCase):
    def test_rejected_card_preserves_request_usage_and_json_repair_metadata(self):
        bad = card(one_liner="厂商向企业客户开放检索工具预览，" * 25)
        failed = {"request_id": "rejected-1", "usage": {"total_tokens": 123},
                  "json_repair": {"attempted": True, "request_id": "json-repair-1", "usage": {"total_tokens": 45}}}
        success = {"request_id": "accepted-2", "usage": {"total_tokens": 88}}
        with patch("generate_daily_content_api.chat_json", side_effect=[(bad, failed), (card(), success)]):
            cards, metadata = generate_individual_news_cards(runtime(), materials_news=[source()], labels=["行业视角"])
        self.assertEqual(len(cards), 1)
        trace = metadata["cards"][0]
        self.assertEqual(trace["failed_attempts"][0]["response"], failed)
        self.assertEqual(trace["failed_attempts"][0]["failure_stage"], "content_validation")
        self.assertEqual(trace["response"], success)

    def test_gateway_failure_never_reuses_previous_card_response(self):
        bad = card(one_liner="厂商向企业客户开放检索工具预览，" * 25)
        failed = {"request_id": "rejected-1", "usage": {"total_tokens": 123}}
        with patch("generate_daily_content_api.chat_json", side_effect=[(bad, failed), GatewayError("unavailable")]):
            cards, metadata = generate_individual_news_cards(runtime(), materials_news=[source()], labels=["行业视角"])
        self.assertEqual(cards, [])
        attempts = metadata["dropped_cards"][0]["attempts"]
        self.assertEqual(attempts[0]["response"], failed)
        self.assertIsNone(attempts[1]["response"])
        self.assertEqual(attempts[1]["failure_stage"], "gateway")

    def test_shell_preserves_rejected_response_without_reusing_it_on_transport_failure(self):
        bad = {"editor_note": "今日编辑简评" * 70, "summary_headline": "企业检索入口整合",
               "summary": "工具入口整合减少系统切换。"}
        failed = {"request_id": "shell-rejected", "usage": {"total_tokens": 99}}
        with patch("generate_daily_content_api.chat_json", side_effect=[(bad, failed), OSError("unavailable")]):
            _, metadata = generate_shell_with_fallback((runtime(),), paper=None, news=[card()], default_section={})
        self.assertEqual(metadata["attempts"][0]["response"], failed)
        self.assertEqual(metadata["attempts"][0]["failure_stage"], "content_validation")
        self.assertIsNone(metadata["attempts"][1]["response"])

    def generate(self, selected, outputs):
        with patch("generate_daily_content_api.chat_json", side_effect=[(c, {}) for c in outputs]) as chat:
            cards, metadata = generate_individual_news_cards(runtime(), materials_news=[selected], labels=["行业视角"])
            return cards, metadata, chat

    def test_single_card_preserves_source_identity_tier_rank_and_aliases(self):
        selected = source()
        generated = card(event_id="model-invented", display_tier="deep", daily_rank=99)
        cards, _, _ = self.generate(selected, [generated])
        self.assertEqual(cards[0]["event_id"], "retrieval-preview")
        self.assertEqual(cards[0]["display_tier"], "brief")
        self.assertEqual(cards[0]["daily_rank"], 3)
        self.assertIn("https://example.test/secondary", cards[0]["coverage_source_urls"])
        self.assertEqual(cards[0]["newspaper_role"], "lead")
        self.assertTrue(cards[0]["observation"])

    def test_source_notes_are_constraints_not_overrides_for_reader_boundary(self):
        notes = "文末的相关新闻与导航文字不属于本事件。预览仅限企业客户，未独立验证生产效果。"
        selected = source(boundary=notes, editorial_assessment={"limitations": notes})
        generated = card(boundary="目前仅向企业客户预览，生产效果仍待验证。")
        cards, _, chat = self.generate(selected, [generated])
        self.assertEqual(cards[0]["boundary"], generated["boundary"])
        self.assertNotIn("导航文字", json.dumps(cards, ensure_ascii=False))
        self.assertIn(notes, chat.call_args.kwargs["user_prompt"])

    def test_meaningful_limit_cannot_be_silently_dropped(self):
        selected = source(boundary="仅限企业内测，尚未全面开放。")
        repaired = card(boundary="目前只向部分企业开放内测。")
        cards, metadata, chat = self.generate(selected, [card(), repaired])
        self.assertEqual(chat.call_count, 2)
        self.assertTrue(metadata["cards"][0]["repair_used"])
        self.assertEqual(cards[0]["boundary"], repaired["boundary"])
        self.assertIn("evidence limits", chat.call_args.kwargs["user_prompt"])

    def test_internal_only_notes_can_be_omitted_with_recorded_reason(self):
        selected = source(boundary="文末的相关新闻与导航文字不属于本事件。")
        handling = {"decision": "internal_only", "reason": "这是网页清理备注，与检索工具的事实范围无关。"}
        cards, metadata, _ = self.generate(selected, [card(boundary_handling=handling)])
        self.assertNotIn("boundary", cards[0])
        self.assertNotIn("boundary_handling", cards[0])
        self.assertEqual(metadata["cards"][0]["boundary_handling"], handling)

    def test_qualified_summary_does_not_require_duplicate_boundary(self):
        selected = source(boundary="目前仅限企业客户预览。")
        handling = {"decision": "qualified_summary", "reason": "摘要明确写向企业客户开放预览，未外推为全面上线。"}
        cards, _, _ = self.generate(selected, [card(boundary_handling=handling)])
        self.assertIn("企业客户", cards[0]["one_liner"])
        self.assertNotIn("boundary", cards[0])

    def test_internal_only_cannot_hide_a_material_access_limit(self):
        selected = source(boundary="仅限企业内测，尚未全面开放。")
        hidden = card(one_liner="厂商推出检索工具，支持多个文档库的统一搜索。",
                      boundary_handling={"decision": "internal_only", "reason": "仅是内部核验说明。"})
        with self.assertRaisesRegex(ContentApiError, "limited_access"):
            generated_news_copy(hidden, selected)

    def test_qualified_summary_must_actually_include_the_visible_qualifier(self):
        selected = source(boundary="仅限企业内测，尚未全面开放。")
        hidden = card(one_liner="厂商推出检索工具，支持多个文档库的统一搜索。",
                      observation="目前只向企业客户开放内测，尚未全面开放。",
                      boundary_handling={"decision": "qualified_summary", "reason": "已经保留内测范围。"})
        with self.assertRaisesRegex(ContentApiError, "limited_access"):
            generated_news_copy(hidden, selected)
        hidden["one_liner"] = "厂商向企业客户开放检索工具内测，支持多个文档库的统一搜索。"
        self.assertNotIn("boundary", generated_news_copy(hidden, selected))

    def test_short_reader_boundary_is_valid_without_padding(self):
        for notes, boundary in (("仅限企业内测，尚未全面开放。", "仅限企业内测。"),
                                ("性能数据来自厂商自述。", "数据来自厂商。")):
            with self.subTest(boundary=boundary):
                self.assertEqual(generated_news_copy(card(boundary=boundary), source(boundary=notes))["boundary"], boundary)

    def test_verbose_copy_is_rewritten_not_truncated(self):
        verbose = card(one_liner="厂商向企业客户开放检索工具预览，" * 25 + "当前仍限内测，尚未正式上线。")
        repaired = card(one_liner="厂商向企业客户开放检索工具预览，可查询多个文档库；当前仍限内测，尚未正式上线。")
        cards, metadata, chat = self.generate(source(), [verbose, repaired])
        self.assertEqual(cards[0]["one_liner"], repaired["one_liner"])
        self.assertTrue(metadata["cards"][0]["repair_used"])
        self.assertIn("编辑阈值", chat.call_args.kwargs["user_prompt"])
        with self.assertRaises(ContentApiError):
            editorial_text(verbose["one_liner"], "one_liner", limit=100)

    def test_internal_notes_in_generated_copy_trigger_targeted_repair(self):
        invalid = card(boundary="文末的相关新闻与导航文字不属于本事件。")
        cards, metadata, chat = self.generate(source(), [invalid, card()])
        self.assertNotIn("boundary", cards[0])
        self.assertTrue(metadata["cards"][0]["repair_used"])
        self.assertIn("内部材料处理说明", chat.call_args.kwargs["user_prompt"])

    def test_repeated_failure_is_recorded_for_recovery_without_source_fallback(self):
        bad = card(one_liner="厂商向企业客户开放检索工具预览，" * 25)
        cards, metadata, chat = self.generate(source(), [bad, bad])
        self.assertEqual(cards, [])
        self.assertEqual(chat.call_count, 2)
        self.assertEqual(metadata["dropped_card_count"], 1)
        self.assertEqual(metadata["dropped_cards"][0]["source_url"], source()["primary_url"])

    def test_batch_path_uses_same_boundary_and_metadata_contract(self):
        selected = source(boundary="所给材料未提供独立测试，仅限企业客户预览。")
        generated = card(source_rank=1, category="industry_signal", boundary="预览面向企业客户，效果仍缺少独立验证。")
        cards = normalize_news([generated], [selected], category_keys={"industry_signal"}, allowed_labels={"行业视角"})
        self.assertEqual(cards[0]["boundary"], generated["boundary"])
        self.assertEqual(cards[0]["display_tier"], "brief")
        self.assertEqual(cards[0]["daily_rank"], 3)

    def test_all_prompt_modes_load_one_editable_file(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "custom.md"
            path.write_text("唯一编辑规则测试标记 [[OBSERVATION_LABELS]]", encoding="utf-8")
            prompts = [news_card_system_prompt(labels=["行业视角"], prompt_path=path),
                       batch_system_prompt(category_keys=["agent_engineering"], labels=["行业视角"], prompt_path=path),
                       shell_system_prompt(prompt_path=path)]
            for prompt in prompts:
                self.assertIn("唯一编辑规则测试标记", prompt)
                self.assertNotIn("[[OBSERVATION_LABELS]]", prompt)
            with patch("generate_daily_content_api.chat_json", return_value=(card(), {})) as chat:
                _, metadata = generate_individual_news_cards(runtime(), materials_news=[source()], labels=["行业视角"], prompt_path=path)
                self.assertIn("唯一编辑规则测试标记", chat.call_args.kwargs["system_prompt"])
                self.assertEqual(metadata["system_prompt"], str(path))
                self.assertEqual(len(metadata["system_prompt_sha256"]), 64)

    def test_saved_material_notes_reach_both_prompt_paths(self):
        selected = source(source_review_notes=["仅限企业预览。"], boundary="仅限企业预览。",
                          editorial_assessment={"limitations": "性能提升是厂商自述。"})
        packet = source_packet({"news": [selected]})["news"][0]
        self.assertEqual(packet["source_review_notes"], ["仅限企业预览。", "性能提升是厂商自述。"])
        self.assertEqual(packet["display_tier"], "brief")
        self.assertIn("性能提升是厂商自述。", news_card_user_prompt(selected))

    def test_late_primary_release_status_and_result_reach_the_editor(self):
        # October 8 sources placed the actual preview status after char 7219
        # and the WER result after char 10141; the old 6500 limit lost both.
        text = "x" * 7219 + "Release status: private preview."
        text += "x" * (10141 - len(text)) + "WER fell from 55.05% to 29.96%."
        prompt = news_card_user_prompt(source(source_text=text))
        packet = json.loads(prompt.split("<news_material>\n", 1)[1].split("\n</news_material>", 1)[0])
        self.assertIn("private preview", packet["source_evidence"])
        self.assertIn("55.05% to 29.96%", packet["source_evidence"])
        self.assertEqual(packet["source_evidence"], text)
        self.assertFalse(packet["source_evidence_truncated"])

    def test_long_primary_excerpt_reports_actual_truncation(self):
        text = "x" * 12000 + "Only beyond the configured excerpt."
        prompt = news_card_user_prompt(source(source_text=text))
        packet = json.loads(prompt.split("<news_material>\n", 1)[1].split("\n</news_material>", 1)[0])
        self.assertEqual(len(packet["source_evidence"]), 12000)
        self.assertTrue(packet["source_evidence_truncated"])
        self.assertEqual(packet["source_evidence_input_chars"], len(text))
        self.assertNotIn("Only beyond", packet["source_evidence"])

    def test_verbose_shell_receives_one_targeted_rewrite_without_truncation(self):
        good = {"editor_note": "企业知识查询开始跨越单个文档库，但开放范围仍限预览。",
                "summary_headline": "企业检索入口走向整合",
                "summary": "工具入口的整合减少系统切换，实际效果仍取决于知识覆盖和访问范围。"}
        bad = {**good, "editor_note": "企业知识查询开始跨越单个文档库，" * 20 + "但尚未全面上线。"}
        with patch("generate_daily_content_api.chat_json", side_effect=[(bad, {}), (good, {})]) as chat:
            shell, metadata = generate_shell_with_fallback((runtime(),), paper=None, news=[card()], default_section={})
        self.assertEqual(shell["editor_note"], good["editor_note"])
        self.assertTrue(metadata["repair_used"])
        self.assertEqual(chat.call_count, 2)
        self.assertIn("editor_note", chat.call_args.kwargs["user_prompt"])
        with self.assertRaises(ContentApiError):
            nonempty(bad["editor_note"], "editor_note", limit=220)

    def test_shell_repeated_failure_remains_non_publishable(self):
        bad = {"editor_note": "今日编辑简评" * 70, "summary_headline": "企业检索入口整合",
               "summary": "工具入口整合减少系统切换。"}
        with patch("generate_daily_content_api.chat_json", side_effect=[(bad, {}), (bad, {})]) as chat:
            _, metadata = generate_shell_with_fallback((runtime(),), paper=None, news=[card()], default_section={})
        self.assertEqual(chat.call_count, 2)
        self.assertEqual(metadata["mode"], "deterministic_fallback")
        self.assertEqual(len(metadata["attempts"]), 2)

    def test_all_brief_selection_keeps_two_analysis_roles_without_changing_selection(self):
        selected = [source(rank=i + 1, daily_rank=i + 1, event_id=f"event-{i}",
                           primary_url=f"https://example.test/news-{i}", scores={"final_news": 70})
                    for i in range(54)]
        before = json.loads(json.dumps(selected))
        def generate(*args, **kwargs):
            packet = json.loads(kwargs["user_prompt"].split("<news_material>\n", 1)[1].split("\n</news_material>", 1)[0])
            return card(observation=card()["observation"] if packet.get("newspaper_role") in {"lead", "supplement"} else ""), {}
        with patch("generate_daily_content_api.chat_json", side_effect=generate):
            cards, metadata = generate_individual_news_cards(runtime(), materials_news=selected, labels=["行业视角"])
        self.assertEqual(len(cards), 54)
        self.assertEqual(metadata["dropped_card_count"], 0)
        self.assertEqual([c["newspaper_role"] for c in metadata["cards"][:2]], ["lead", "supplement"])
        self.assertEqual(selected, before)
        self.assertEqual([c["event_id"] for c in cards], [s["event_id"] for s in selected])
        self.assertEqual([c["daily_rank"] for c in cards], list(range(1, 55)))
        self.assertEqual([c["display_tier"] for c in cards], ["brief"] * 54)
        self.assertEqual([c["newspaper_role"] for c in cards[:2]], ["lead", "supplement"])
        self.assertTrue(all(c["observation"] for c in cards[:2]))
        self.assertTrue(all(not c["observation"] for c in cards[2:]))

    def test_explicit_historical_roles_override_scoring_tier_and_survive_model_output(self):
        selected = [source(event_id="other", primary_url="https://example.test/other", newspaper_role="brief"),
                    source(event_id="support", primary_url="https://example.test/support", newspaper_role="supplement"),
                    source(event_id="historical-lead", primary_url="https://example.test/lead", newspaper_role="lead")]
        with patch("generate_daily_content_api.chat_json", return_value=(card(newspaper_role="brief"), {})):
            cards, _ = generate_individual_news_cards(runtime(), materials_news=selected, labels=["行业视角"])
        self.assertEqual([c["newspaper_role"] for c in cards], ["brief", "supplement", "lead"])
        self.assertTrue(all(c["display_tier"] == "brief" for c in cards))

    def test_analysis_role_requires_observation_even_when_selection_tier_is_brief(self):
        for role in ("lead", "supplement"):
            with self.subTest(role=role), self.assertRaisesRegex(ContentApiError, "missing observation"):
                generated_news_copy(card(observation=""), source(newspaper_role=role))
        self.assertEqual(generated_news_copy(card(observation=""), source(newspaper_role="brief"))["observation"], "")

    def test_retrying_single_planned_supplement_keeps_its_deep_role(self):
        selected = source(newspaper_role="supplement")
        expanded = card(one_liner="这项检索产品将分散在多个文档系统中的信息集中到一个查询入口。" * 5)
        cards, metadata, _ = self.generate(selected, [expanded])
        self.assertEqual(len(cards), 1)
        self.assertEqual(cards[0]["newspaper_role"], "supplement")
        self.assertEqual(cards[0]["display_tier"], "brief")
        self.assertEqual(cards[0]["one_liner"], expanded["one_liner"])
        self.assertEqual(metadata["cards"][0]["prose_tier"], "deep")

    def test_retrying_single_planned_brief_does_not_promote_or_require_analysis(self):
        selected = source(newspaper_role="brief")
        cards, metadata, _ = self.generate(selected, [card(observation="")])
        self.assertEqual(len(cards), 1)
        self.assertEqual(cards[0]["newspaper_role"], "brief")
        self.assertEqual(cards[0]["observation"], "")
        self.assertEqual(metadata["cards"][0]["prose_tier"], "brief")

    def test_analysis_role_uses_deep_prose_budget_without_changing_selected_tier(self):
        expanded = card(one_liner="这项检索产品将分散在多个文档系统中的信息集中到一个查询入口。" * 4)
        self.assertEqual(generated_news_copy(expanded, source(newspaper_role="lead"))["one_liner"], expanded["one_liner"])
        expanded["one_liner"] += "实际查询仍受用户既有数据访问权限约束，跨库入口不会自动改变权限。"
        self.assertEqual(generated_news_copy(expanded, source(newspaper_role="lead"))["one_liner"], expanded["one_liner"])
        with self.assertRaises(ContentApiError):
            generated_news_copy(expanded, source(newspaper_role="brief"))


if __name__ == "__main__":
    unittest.main()
