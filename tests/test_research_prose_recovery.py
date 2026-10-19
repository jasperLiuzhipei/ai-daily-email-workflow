from __future__ import annotations

import json
from io import BytesIO
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from ai_gateway_client import ProviderRuntime, provider_snapshot  # noqa: E402
from ai_daily_common import file_sha256, write_json_object  # noqa: E402
from daily_prose import PAPER_LIMITS, PAPER_TOTAL_LIMIT, research_editorial_issues  # noqa: E402
from generate_research_brief_api import (  # noqa: E402
    DEFAULT_SYSTEM_PROMPT, PAPER_PROSE_FIELDS, ResearchApiError, load_system_prompt, main,
    repair_research_prose, review_research_reading, text,
)
from research_cache import build_cache_contract, cache_manifest_path, source_material_sha256, write_cache_contract  # noqa: E402


def runtime() -> ProviderRuntime:
    return ProviderRuntime(
        kind="text", base_url="https://example.invalid", api_key="fixture",
        model="gpt-6-sol", endpoint_path="chat/completions", timeout_seconds=30,
        max_retries=2, request_defaults={"response_format": {"type": "json_object"},
                                        "max_completion_tokens": 12000, "reasoning_effort": "medium"},
    )


def original_output() -> dict:
    return {
        "paper": {
            "title": "SPACE 沙箱边界测试",
            "tags": ["安全"], "audience": "研发与安全团队",
            "conclusion": "主机逃逸未被观察到，有限联网仍出现出站绕过。",
            "problem": "测试宿主机隔离与网络策略。",
            "mechanism": "DNS 欺骗与共享 IP 是不同路径。关系：模型｜伪造 DNS 应答｜网关",
            "evidence": "测试包含完全断网和有限联网设置。",
            "insight": "分别验证主机隔离与网络策略。",
            "boundary": "结果限于报告中的配置和测试范围。",
        },
        "visual_handoff": {
            "subject": "SPACE 边界", "takeaway": "区分两类边界",
            "mechanism": "DNS 欺骗与共享 IP 两条路径",
            "evidence": "报告中的测试结果", "scope": "仅限测试配置",
            "labels": ["DNS", "Fastly"],
        },
    }


def overloaded_evidence() -> str:
    # Actual failure pattern from the October 8 issue: several independent
    # rankings and measurement definitions crowded into one evidence field.
    return (
        "在覆盖12个模型的共同任务消融中，121,680次有效尝试有79,853次未通过可执行检查；"
        "其中67.24%的失败仍正常结束、调用过修改状态的工具，且没有报告最终工具错误。"
        "对这些表面顺利的失败，状态检查发现字段值错误、意外额外效果和缺失必要效果，"
        "报告比例分别为77.61%、43.30%和25.36%；三类发现可重叠。"
        "完整榜单涉及18个模型。Claude Opus 5.5的总体pass@1为67.16%；"
        "Kimi-K3在507项中有476项至少成功一次（93.89%），但只有68项达到observed 20/20（13.41%）。"
        "Claude Opus 5至少成功一次的任务占79.09%，却有241项达到20/20（47.53%）；"
        "Opus 5.5的20/20任务数同为241。成本估算中，GPT-5.4每次成功尝试约0.131美元；"
        "按完整20轮活动费用除以20/20任务数，它以128项、每项约6.80美元列所示模型之首，"
        "而GPT-6 Astra为231项、7.45美元，Opus 5.5为241项、7.80美元。"
        "失败诊断中“工具使用”占失败的79.9%，但这是逐模型比例的非加权平均、可观察的诊断标签，"
        "不是已证实的唯一成因。"
    )


def compact_paper() -> dict[str, str]:
    return {
        "conclusion": "Agent说已办妥，并不代表后台结果正确或每次都能成功。",
        "problem": "只看工具调用顺利结束，会漏掉错误写入和重复执行时的不稳定。",
        "mechanism": "ThinkingBox让Agent从相同后台状态执行任务，并用可执行检查核对终态及副作用；每项任务重复20次。",
        "evidence": "在507项任务中，Kimi-K3有476项至少成功过一次，但20次全部成功的只有68项，显示任务覆盖面与稳定完成能力存在明显差距。",
        "insight": "Agent选型需要同时关注做过多少种任务，以及同一任务能否反复做对。",
        "boundary": "这里的20次全成功只是本次测试观察，不能保证未来调用或其他业务环境同样可靠。",
    }


def reading_fixture(paper: dict[str, str]) -> tuple[dict, dict]:
    source = {"source_text": (
        "A valid tool call is not an outcome. "
        "ThinkingBox checks terminal backend state and side effects. "
        "Every task runs 20 independent times from an identical clean backend. "
        "Of failed attempts, 67.24% terminated cleanly and reported no final tool error. "
        "Kimi-K3 passed 476 of 507 tasks at least once but 68 in all 20 attempts."
    )}
    response = {"requirements": [
        {"id": "problem", "kind": "problem", "requirement": "说明工具调用顺利不代表业务结果正确。",
         "source_quote": "A valid tool call is not an outcome.", "covered": True,
         "paper_quote": paper["problem"]},
        {"id": "terminal_check", "kind": "mechanism", "requirement": "解释可执行终态检查。",
         "source_quote": "ThinkingBox checks terminal backend state and side effects.", "covered": True,
         "paper_quote": paper["mechanism"]},
        {"id": "repeatability", "kind": "finding", "requirement": "解释重复执行稳定性与任务覆盖的对照。",
         "source_quote": "Kimi-K3 passed 476 of 507 tasks at least once but 68 in all 20 attempts.",
         "covered": True, "paper_quote": paper["evidence"]},
        {"id": "silent_failure", "kind": "finding", "requirement": "补充看似正常结束仍失败的代表性证据及含义。",
         "source_quote": "Of failed attempts, 67.24% terminated cleanly and reported no final tool error.",
         "covered": False, "paper_quote": ""},
    ], "editorial_issues": []}
    return source, response


def approved_reading_fixture() -> tuple[dict, dict, dict]:
    fixture_root = Path(__file__).resolve().parent / "fixtures"
    context = json.loads((fixture_root / "approved_research_v2_source_review.json").read_text(encoding="utf-8"))
    paper = json.loads((fixture_root / context["paper_fixture"]).read_text(encoding="utf-8"))["paper"]
    return paper, context["source"], context["expected_review"]


class ResearchProseRecoveryTests(unittest.TestCase):
    def test_approved_v2_complete_research_passes_without_shortening(self) -> None:
        paper, source, expected = approved_reading_fixture()
        with patch("generate_research_brief_api.chat_json", return_value=(expected, {})) as model:
            review = review_research_reading(runtime(), source=source, paper=paper)
            final, audit = repair_research_prose(runtime(), source=source, paper=paper, reading_review=review)
        self.assertEqual(review["status"], "complete")
        self.assertEqual(research_editorial_issues(final), [])
        self.assertEqual(final, paper)
        self.assertEqual(audit["rounds_used"], 0)
        self.assertEqual(model.call_count, 1)  # An offline fixture response, not a live paid review.

    def test_approved_v2_missing_finding_is_restored_without_losing_other_explanation(self) -> None:
        paper, source, expected = approved_reading_fixture()
        incomplete = {**paper, "evidence": paper["evidence"].split("\n\n")[1]}
        coverage = [{"id": item["id"], "paper_quote": item["paper_quote"]} for item in expected["requirements"]]
        for item in expected["requirements"]:
            if item["id"] == "silent_failure":
                item["covered"], item["paper_quote"] = False, ""
        repaired_response = {"replacements": {field: paper[field] for field in PAPER_PROSE_FIELDS},
                             "reading_coverage": coverage}
        with patch("generate_research_brief_api.chat_json", side_effect=[(expected, {}), (repaired_response, {})]) as model:
            review = review_research_reading(runtime(), source=source, paper=incomplete)
            final, audit = repair_research_prose(runtime(), source=source, paper=incomplete, reading_review=review)
        self.assertEqual(review["status"], "needs_revision")
        self.assertEqual(final, paper)
        self.assertEqual(audit["rounds_used"], 1)
        self.assertEqual(model.call_count, 2)  # One review, one bounded repair; both mocked.

    def test_review_cannot_ground_findings_only_in_title_or_fallback(self) -> None:
        paper = compact_paper()
        for field in ("title", "fallback_excerpt"):
            source, response = reading_fixture(paper)
            quote = response["requirements"][-1]["source_quote"]
            source["source_text"] = source["source_text"].replace(quote, "")
            source[field] = quote
            with self.subTest(field=field), \
                 patch("generate_research_brief_api.chat_json", return_value=(response, {})):
                with self.assertRaisesRegex(ResearchApiError, "source quotation"):
                    review_research_reading(runtime(), source=source, paper=paper)

    def test_review_requires_primary_body_before_a_model_call(self) -> None:
        with patch("generate_research_brief_api.chat_json") as model:
            with self.assertRaisesRegex(ResearchApiError, "primary-source text"):
                review_research_reading(runtime(), source={"title": "标题", "fallback_excerpt": "摘要"}, paper=compact_paper())
        model.assert_not_called()

    def test_missing_finding_cannot_be_claimed_fixed_by_requoting_old_text(self) -> None:
        paper = compact_paper()
        source, response = reading_fixture(paper)
        coverage = [{"id": item["id"], "paper_quote": paper["evidence"] if item["kind"] == "finding"
                     else paper["mechanism"] if item["kind"] == "mechanism" else paper["problem"]}
                    for item in response["requirements"]]
        for repaired in (paper, {**paper, "insight": "评测结果需要区分能力覆盖与稳定性，才能判断流程的适用范围。"}):
            with self.subTest(repaired=repaired), \
                 patch("generate_research_brief_api.chat_json", return_value=(
                     {"replacements": repaired, "reading_coverage": coverage}, {})) as model:
                with self.assertRaisesRegex(ResearchApiError, "unchanged|pre-existing"):
                    repair_research_prose(runtime(), source=source, paper=paper,
                                          reading_review={"requirements": response["requirements"]})
                self.assertEqual(model.call_count, 1)

    def test_main_rejects_empty_fetched_source_before_provider_resolution(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            materials = root / "materials.json"
            materials.write_text(json.dumps({"research": [{"title": "标题", "fetch_status": "fetched",
                                                           "fallback_excerpt": "摘要", "source_text": "  "}]}))
            argv = ["generate_research_brief_api.py", "--materials", str(materials), "--date", "2026-10-08",
                    "--out-json", str(root / "brief.json"), "--prompt-out", str(root / "prompt.txt")]
            with patch.object(sys, "argv", argv), \
                 patch("generate_research_brief_api.load_runtime_config") as provider:
                with self.assertRaisesRegex(SystemExit, "primary-source text"):
                    main()
            provider.assert_not_called()
            self.assertFalse((root / "brief.json").exists())

    def test_main_rejects_output_aliases_without_overwriting_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            materials = root / "materials.json"
            original = json.dumps({"research": []})
            materials.write_text(original)
            for prompt in (root / "brief.json", materials, cache_manifest_path(root / "brief.json")):
                argv = ["generate_research_brief_api.py", "--materials", str(materials), "--date", "2026-10-08",
                        "--out-json", str(root / "brief.json"), "--prompt-out", str(prompt), "--force"]
                with self.subTest(prompt=prompt), patch.object(sys, "argv", argv), \
                     patch("generate_research_brief_api.load_runtime_config") as provider:
                    with self.assertRaisesRegex(SystemExit, "paths must be distinct"):
                        main()
                provider.assert_not_called()
                self.assertEqual(materials.read_text(), original)
                self.assertFalse((root / "brief.json").exists())

    def test_failed_refresh_revokes_receipt_and_preserves_old_deliverables(self) -> None:
        source = {"title": "研究", "primary_url": "https://example.com/research",
                  "source_text": "完整研究来源", "fetch_status": "fetched"}
        initial = original_output()
        # Even identical raw output must not revive an approval after a fresh
        # semantic review has failed. A changed raw file is also hash-bound.
        for identical_raw in (False, True):
            with self.subTest(identical_raw=identical_raw), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                materials, out, raw, prompt, markdown = [root / name for name in
                    ("materials.json", "brief.json", "raw.json", "prompt.txt", "brief.md")]
                materials.write_text(json.dumps({"research": [source]}), encoding="utf-8")
                previous = {out: '{"previous":true}', prompt: "旧图解提示词", markdown: "旧正文"}
                for path, value in previous.items():
                    path.write_text(value, encoding="utf-8")
                write_json_object(raw, initial if identical_raw else {"previous": "raw"})
                artifacts = {"brief_json": out, "image_prompt": prompt,
                             "raw_output": raw, "brief_markdown": markdown}
                receipt = build_cache_contract(source=source, system_prompt=load_system_prompt(DEFAULT_SYSTEM_PROMPT),
                                               artifacts=artifacts)
                cache = cache_manifest_path(out)
                write_cache_contract(cache, receipt)
                argv = ["generate_research_brief_api.py", "--materials", str(materials), "--date", "2026-10-08",
                        "--out-json", str(out), "--prompt-out", str(prompt), "--raw-output", str(raw),
                        "--brief-markdown", str(markdown), "--force"]
                with patch.object(sys, "argv", argv), \
                     patch("generate_research_brief_api.load_runtime_config", return_value={}), \
                     patch("generate_research_brief_api.resolve_provider_fallbacks", return_value=(runtime(),)), \
                     patch("generate_research_brief_api.generate_research_with_fallbacks",
                           return_value=(initial, {"runtime_index": 0})), \
                     patch("generate_research_brief_api.review_research_reading",
                           side_effect=ResearchApiError("review failed")):
                    with self.assertRaisesRegex(SystemExit, "review failed"):
                        main()
                self.assertFalse(cache.exists())
                for path, value in previous.items():
                    self.assertEqual(path.read_text(encoding="utf-8"), value)
                self.assertEqual(json.loads(raw.read_text()), initial)
                self.assertEqual(file_sha256(raw) == receipt["artifacts"]["raw_output"]["sha256"], identical_raw)

    def test_source_cache_hash_is_order_independent_but_content_bound(self) -> None:
        self.assertEqual(source_material_sha256({"title": "研究", "source_text": "原文"}),
                         source_material_sha256({"source_text": "原文", "title": "研究"}))
        self.assertNotEqual(source_material_sha256({"source_text": "原文"}),
                            source_material_sha256({"source_text": "改动原文"}))

    def test_main_rejects_invalid_visual_handoff_before_paid_reading_review(self) -> None:
        for failure in ("missing_evidence", "bad_relation"):
            initial = original_output()
            if failure == "missing_evidence":
                del initial["visual_handoff"]["evidence"]
            else:
                initial["visual_handoff"]["mechanism"] = "关系：模型｜调用工具"
            source = {"title": "测试研究", "primary_url": "https://example.com/report",
                      "fetch_status": "fetched", "source_text": "研究原文，包含方法和证据。"}
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                materials, out, raw = (root / name for name in ("materials.json", "brief.json", "raw.json"))
                materials.write_text(json.dumps({"research": [source]}))
                argv = ["generate_research_brief_api.py", "--materials", str(materials), "--date", "2026-10-08",
                        "--out-json", str(out), "--prompt-out", str(root / "prompt.txt"), "--raw-output", str(raw)]
                with patch.object(sys, "argv", argv), \
                     patch("generate_research_brief_api.load_runtime_config", return_value={}), \
                     patch("generate_research_brief_api.resolve_provider_fallbacks", return_value=(runtime(),)), \
                     patch("generate_research_brief_api.generate_research_with_fallbacks",
                           return_value=(initial, {"runtime_index": 0})), \
                     patch("generate_research_brief_api.review_research_reading") as review:
                    with self.assertRaises(SystemExit):
                        main()
                review.assert_not_called()
                self.assertEqual(json.loads(raw.read_text()), initial)
                self.assertFalse(out.exists())
                self.assertFalse((root / "prompt.txt").exists())

    def test_short_draft_with_missing_principal_finding_is_not_complete(self) -> None:
        paper = compact_paper()
        self.assertEqual(research_editorial_issues(paper), [])
        source, response = reading_fixture(paper)
        with patch("generate_research_brief_api.chat_json", return_value=(response, {})) as model:
            review = review_research_reading(runtime(), source=source, paper=paper)
        self.assertEqual(review["status"], "needs_revision")
        self.assertEqual(review["requirements"][-1]["id"], "silent_failure")
        self.assertEqual(model.call_count, 1)
        self.assertEqual(model.call_args.args[0].max_retries, 1)
        self.assertEqual(model.call_args.args[0].timeout_seconds, runtime().timeout_seconds)
        self.assertEqual(model.call_args.args[0].model, runtime().model)
        self.assertEqual(model.call_args.args[0].request_defaults, runtime().request_defaults)
        self.assertNotIn("request_overrides", model.call_args.kwargs)
        self.assertFalse(model.call_args.kwargs["allow_json_repair"])

    def test_reading_review_rejects_untraceable_source_or_body_claims(self) -> None:
        paper = compact_paper()
        for failure in ("source", "paper", "missing_kind", "editorial_quote"):
            source, response = reading_fixture(paper)
            if failure == "source":
                response["requirements"][0]["source_quote"] = "An invented source sentence."
            elif failure == "paper":
                response["requirements"][0]["paper_quote"] = "正文并不存在这一句。"
            elif failure == "missing_kind":
                response["requirements"] = response["requirements"][2:]
            else:
                response["editorial_issues"] = [{"field": "evidence", "message": "重复",
                                                  "paper_quote": "正文没有的重复句。"}]
            with self.subTest(failure=failure), \
                 patch("generate_research_brief_api.chat_json", return_value=(response, {})) as model:
                with self.assertRaises(ResearchApiError):
                    review_research_reading(runtime(), source=source, paper=paper)
                self.assertEqual(model.call_count, 1)  # A content failure is not a transient failure.

    def test_review_retries_transient_http_failure_once_with_same_request(self) -> None:
        paper = compact_paper()
        source, result = reading_fixture(paper)
        response = BytesIO(json.dumps({"choices": [{"message": {"content": json.dumps(result)}}]}).encode())
        response.status, response.headers = 200, {}
        failure = HTTPError("https://example.invalid/chat/completions", 502, "temporary", {}, BytesIO(b"temporary"))
        with patch("ai_gateway_client.urlopen", side_effect=[failure, response]) as request, \
             patch("ai_gateway_client.time.sleep") as backoff:
            review = review_research_reading(runtime(), source=source, paper=paper)
        self.assertEqual(review["status"], "needs_revision")
        self.assertEqual(review["provider"]["max_retries"], 1)
        self.assertEqual(request.call_count, 2)
        self.assertEqual(request.call_args_list[0].args[0].data, request.call_args_list[1].args[0].data)
        payload = json.loads(request.call_args_list[0].args[0].data)
        self.assertEqual(payload["max_completion_tokens"], 12000)
        self.assertEqual(payload["reasoning_effort"], "medium")
        user_prompt = payload["messages"][1]["content"]
        self.assertTrue(user_prompt.startswith("请根据以下引用资料"))
        self.assertTrue(user_prompt.endswith("</research_reading_input>"))
        embedded = json.loads(user_prompt.split("<research_reading_input>\n", 1)[1]
                              .rsplit("\n</research_reading_input>", 1)[0])
        self.assertEqual(embedded["source_material"]["source_text"], source["source_text"])
        self.assertEqual(embedded["paper"], paper)
        backoff.assert_called_once_with(0.8)

    def test_repair_keeps_configured_route_token_and_reasoning_budgets(self) -> None:
        paper = {**compact_paper(), "evidence": overloaded_evidence() * 2}
        result = {"replacements": compact_paper()}
        response = BytesIO(json.dumps({"choices": [{"message": {"content": json.dumps(result)}}]}).encode())
        response.status, response.headers = 200, {}
        with patch("ai_gateway_client.urlopen", return_value=response) as request:
            repaired, audit = repair_research_prose(runtime(), source={}, paper=paper)
        self.assertEqual(repaired["evidence"], compact_paper()["evidence"])
        payload = json.loads(request.call_args.args[0].data)
        self.assertEqual(payload["max_completion_tokens"], 12000)
        self.assertEqual(payload["reasoning_effort"], "medium")
        self.assertEqual(audit["rounds_used"], 1)
        self.assertEqual(audit["provider"]["max_retries"], 0)
        self.assertEqual(request.call_count, 1)

    def test_review_transport_retry_budget_does_not_expand(self) -> None:
        paper = compact_paper()
        source, _ = reading_fixture(paper)
        failures = [HTTPError("https://example.invalid/chat/completions", 502, "temporary", {}, BytesIO(b"temporary"))
                    for _ in range(2)]
        with patch("ai_gateway_client.urlopen", side_effect=failures) as request, \
             patch("ai_gateway_client.time.sleep") as backoff:
            with self.assertRaisesRegex(ResearchApiError, "HTTP 502"):
                review_research_reading(runtime(), source=source, paper=paper)
        self.assertEqual(request.call_count, 2)
        backoff.assert_called_once_with(0.8)

    def test_review_malformed_content_does_not_retry_or_repair_json(self) -> None:
        paper = compact_paper()
        source, _ = reading_fixture(paper)
        response = BytesIO(json.dumps({"choices": [{"message": {"content": "not a JSON object"}}]}).encode())
        response.status, response.headers = 200, {}
        with patch("ai_gateway_client.urlopen", return_value=response) as request, \
             patch("ai_gateway_client.time.sleep") as backoff:
            with self.assertRaisesRegex(ResearchApiError, "JSON object"):
                review_research_reading(runtime(), source=source, paper=paper)
        self.assertEqual(request.call_count, 1)
        backoff.assert_not_called()

    def test_source_finding_omission_triggers_bounded_rewrite_without_length_floor(self) -> None:
        paper = compact_paper()
        source, response = reading_fixture(paper)
        review = {"requirements": response["requirements"]}
        corrected = {**paper, "evidence": "失败尝试中，67.24%正常结束且没有最终工具报错，说明执行顺利仍可能未达成目标。" + paper["evidence"]}
        coverage = [{"id": item["id"], "paper_quote": corrected["evidence"] if item["kind"] == "finding"
                     else corrected["mechanism"] if item["kind"] == "mechanism" else corrected["problem"]}
                    for item in review["requirements"]]
        with patch("generate_research_brief_api.chat_json", return_value=(
                {"replacements": corrected, "reading_coverage": coverage}, {})) as model:
            result, audit = repair_research_prose(runtime(), source=source, paper=paper, reading_review=review)
        self.assertEqual(model.call_count, 1)
        self.assertIn("67.24%", result["evidence"])
        self.assertIn("68", result["evidence"])
        self.assertEqual(audit["issues_before"][0]["code"], "missing_research_content")
        self.assertEqual(audit["reading_coverage"], coverage)
        self.assertEqual(json.loads(model.call_args.kwargs["user_prompt"])["reading_requirements"], review["requirements"])

    def test_rewrite_without_actual_coverage_is_blocked_without_second_attempt(self) -> None:
        paper = compact_paper()
        source, response = reading_fixture(paper)
        review = {"requirements": response["requirements"]}
        for coverage in ([], [{"id": item["id"], "paper_quote": "声称补好了但正文没有。"}
                             for item in review["requirements"]]):
            with self.subTest(coverage=coverage), \
                 patch("generate_research_brief_api.chat_json", return_value=(
                     {"replacements": paper, "reading_coverage": coverage}, {})) as model:
                with self.assertRaises(ResearchApiError):
                    repair_research_prose(runtime(), source=source, paper=paper, reading_review=review)
                self.assertEqual(model.call_count, 1)

    def test_complete_single_finding_reading_does_not_need_padding_or_rewrite(self) -> None:
        paper = compact_paper()
        source, response = reading_fixture(paper)
        response["requirements"].pop()  # A different source need not share this study's second finding.
        source["source_text"] = source["source_text"].replace(
            "Of failed attempts, 67.24% terminated cleanly and reported no final tool error. ", "")
        with patch("generate_research_brief_api.chat_json", return_value=(response, {})) as model:
            review = review_research_reading(runtime(), source=source, paper=paper)
            result, audit = repair_research_prose(runtime(), source=source, paper=paper, reading_review=review)
        self.assertEqual(review["status"], "complete")
        self.assertEqual(result, paper)
        self.assertEqual(audit["rounds_used"], 0)
        self.assertEqual(model.call_count, 1)  # Review only; there is no minimum-length filler pass.

    def test_complete_reading_can_omit_source_auxiliary_rankings(self) -> None:
        paper = compact_paper()
        source, response = reading_fixture(paper)
        response["requirements"].pop()
        source["source_text"] = source["source_text"].replace(
            "Of failed attempts, 67.24% terminated cleanly and reported no final tool error. ", "")
        source["source_text"] += (
            " Appendix: auxiliary price rankings compare provider list rates. "
            "An additional table lists older model versions and failure categories."
        )
        with patch("generate_research_brief_api.chat_json", return_value=(response, {})) as model:
            review = review_research_reading(runtime(), source=source, paper=paper)
            result, audit = repair_research_prose(runtime(), source=source, paper=paper, reading_review=review)
        self.assertEqual(review["status"], "complete")
        self.assertEqual(result, paper)
        self.assertEqual(audit["rounds_used"], 0)
        self.assertEqual(model.call_count, 1)
        # The review's source-specific necessities, not source section count,
        # determine whether a second editorial call is needed.
        self.assertEqual(len(review["requirements"]), 3)

    def test_research_text_is_not_silently_truncated(self) -> None:
        value = "来源细节" * 700 + "；但仅限实验环境。"
        self.assertEqual(text(value, "paper.evidence"), value)
        with self.assertRaisesRegex(ResearchApiError, "rather than truncate"):
            text("过长标题" * 40, "paper.title", limit=120)

    def test_no_leak_makes_no_extra_model_call(self) -> None:
        paper = {"mechanism": "模型伪造 DNS 应答，网关随后处理该响应。"}
        with patch("generate_research_brief_api.chat_json") as model:
            repaired, audit = repair_research_prose(runtime(), source={}, paper=paper)
        self.assertEqual(repaired, paper)
        self.assertEqual(audit["mode"], "not_needed")
        self.assertEqual((audit["rounds_used"], audit["round_limit"]), (0, 1))
        model.assert_not_called()

    def test_main_repairs_only_prose_and_keeps_original_response(self) -> None:
        initial = original_output()
        replacement = ("第一类绕过是模型伪造外部解析器的 DNS 响应，将错误解析结果注入网关。"
                       "第二类绕过利用获准的共享 IP 访问其他服务，两条路径分别成立。"
                       "报告还描述了相应修复，结论限于当时的配置和复测。")
        source = {"title": "SPACE 报告", "primary_url": "https://example.com/report",
                  "source_text": "来源正文说明两类路径分别成立。", "fetch_status": "fetched"}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            materials = root / "materials.json"
            materials.write_text(json.dumps({"research": [source]}), encoding="utf-8")
            out = root / "brief.json"
            raw = root / "raw.json"
            markdown = root / "brief.md"
            argv = ["generate_research_brief_api.py", "--materials", str(materials),
                    "--date", "2026-09-29", "--out-json", str(out),
                    "--prompt-out", str(root / "prompt.txt"), "--raw-output", str(raw),
                    "--brief-markdown", str(markdown)]
            generation = {"provider": provider_snapshot(runtime()), "runtime_index": 0,
                          "fallback_model_used": False, "failed_attempts": [], "response": {}}
            with patch.object(sys, "argv", argv), \
                 patch("generate_research_brief_api.load_runtime_config", return_value={}), \
                 patch("generate_research_brief_api.resolve_provider_fallbacks", return_value=(runtime(),)), \
                 patch("generate_research_brief_api.generate_research_with_fallbacks",
                       return_value=(initial, generation)), \
                 patch("generate_research_brief_api.review_research_reading", return_value={"requirements": []}), \
                 patch("generate_research_brief_api.chat_json",
                       return_value=({"replacements": {"mechanism": replacement}}, {"usage": {}})) as model:
                main()
            self.assertEqual(model.call_count, 1)
            self.assertEqual(model.call_args.args[0].max_retries, 0)
            self.assertNotIn("request_overrides", model.call_args.kwargs)
            self.assertEqual(model.call_args.args[0].request_defaults, runtime().request_defaults)
            self.assertFalse(model.call_args.kwargs["allow_json_repair"])
            result = json.loads(out.read_text())
            self.assertEqual(result["paper"]["mechanism"], replacement)
            self.assertEqual(result["paper"]["problem"], initial["paper"]["problem"])
            self.assertEqual(result["visual_handoff"]["mechanism"], initial["visual_handoff"]["mechanism"])
            self.assertEqual(result["generator"]["prose_recovery"]["fields"], ["mechanism"])
            self.assertEqual((result["generator"]["prose_recovery"]["rounds_used"],
                              result["generator"]["prose_recovery"]["round_limit"]), (1, 1))
            self.assertEqual(json.loads(raw.read_text()), initial)
            receipt = json.loads(cache_manifest_path(out).read_text(encoding="utf-8"))
            expected = build_cache_contract(source=source, system_prompt=load_system_prompt(DEFAULT_SYSTEM_PROMPT),
                                            artifacts={"brief_json": out, "image_prompt": root / "prompt.txt",
                                                       "raw_output": raw, "brief_markdown": markdown})
            self.assertEqual(receipt, expected)
            self.assertTrue(all(value and len(value["sha256"]) == 64 for value in receipt["artifacts"].values()))

    def test_short_or_still_leaking_repair_is_not_accepted(self) -> None:
        paper = original_output()["paper"]
        source = {"title": "SPACE 报告", "source_text": "报告正文"}
        for replacement in ("关系：模型｜访问｜网关", "略。"):
            with self.subTest(replacement=replacement), \
                 patch("generate_research_brief_api.chat_json",
                       return_value=({"replacements": {"mechanism": replacement}}, {})) as model:
                with self.assertRaisesRegex(Exception, "recovery"):
                    repair_research_prose(runtime(), source=source, paper=paper)
                self.assertEqual(model.call_count, 1)

    def test_overloaded_evidence_triggers_one_editorial_rewrite(self) -> None:
        paper = {**compact_paper(), "title": "ThinkingBox", "evidence": overloaded_evidence() * 2}
        source_tail = "后文限定：20/20不是未来成功保证。"
        source = {"source_text": "原始材料" * 4000 + source_tail}
        replacement = compact_paper()
        with patch("generate_research_brief_api.chat_json",
                   return_value=({"replacements": replacement}, {})) as model:
            result, audit = repair_research_prose(runtime(), source=source, paper=paper)
        self.assertEqual(model.call_count, 1)
        self.assertEqual(audit["mode"], "editorial_rewrite")
        self.assertEqual(audit["fields"], list(PAPER_PROSE_FIELDS))
        self.assertIn("too_long", {issue["code"] for issue in audit["issues_before"]})
        request = json.loads(model.call_args.kwargs["user_prompt"])
        self.assertEqual(request["field_character_limits"], PAPER_LIMITS)
        self.assertEqual(request["total_character_limit"], PAPER_TOTAL_LIMIT)
        self.assertTrue(request["source_material"]["source_text"].endswith(source_tail))
        self.assertEqual(result["title"], "ThinkingBox")
        self.assertEqual(result["boundary"], replacement["boundary"])
        self.assertEqual(research_editorial_issues(result), [])

    def test_failed_editorial_rewrite_blocks_without_more_calls(self) -> None:
        paper = {**compact_paper(), "evidence": overloaded_evidence() * 2}
        for replacements in (paper, {"evidence": compact_paper()["evidence"]}):
            with self.subTest(replacements=replacements), \
                 patch("generate_research_brief_api.chat_json",
                       return_value=({"replacements": replacements}, {})) as model:
                with self.assertRaisesRegex(ResearchApiError, "recovery"):
                    repair_research_prose(runtime(), source={}, paper=paper)
                self.assertEqual(model.call_count, 1)

    def test_main_editorial_rewrite_preserves_full_visual_and_raw_artifacts(self) -> None:
        initial = original_output()
        initial["paper"].update(compact_paper())
        initial["paper"]["evidence"] = overloaded_evidence() * 2
        initial["visual_handoff"]["evidence"] = overloaded_evidence()
        initial["visual_handoff"]["mechanism"] = "完整图解方法。" * 70
        source = {"title": "ThinkingBox", "primary_url": "https://example.com/report",
                  "source_text": overloaded_evidence(), "fetch_status": "fetched"}
        generation = {"runtime_index": 0, "provider": provider_snapshot(runtime())}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            materials, out, raw = (root / name for name in ("materials.json", "brief.json", "raw.json"))
            materials.write_text(json.dumps({"research": [source]}), encoding="utf-8")
            argv = ["generate_research_brief_api.py", "--materials", str(materials),
                    "--date", "2026-10-08", "--out-json", str(out),
                    "--prompt-out", str(root / "prompt.txt"), "--raw-output", str(raw)]
            with patch.object(sys, "argv", argv), \
                 patch("generate_research_brief_api.load_runtime_config", return_value={}), \
                 patch("generate_research_brief_api.resolve_provider_fallbacks", return_value=(runtime(),)), \
                 patch("generate_research_brief_api.generate_research_with_fallbacks",
                       return_value=(initial, generation)), \
                 patch("generate_research_brief_api.review_research_reading", return_value={"requirements": []}), \
                 patch("generate_research_brief_api.chat_json",
                       return_value=({"replacements": compact_paper()}, {})):
                main()
            result = json.loads(out.read_text())
            self.assertEqual(result["paper"]["evidence"], compact_paper()["evidence"])
            for field in ("mechanism", "evidence", "scope"):
                self.assertEqual(result["visual_handoff"][field], initial["visual_handoff"][field])
            self.assertEqual(json.loads(raw.read_text()), initial)

    def test_main_preserves_original_but_emits_no_brief_when_rewrite_fails(self) -> None:
        initial = original_output()
        initial["paper"].update(compact_paper())
        initial["paper"]["evidence"] = overloaded_evidence() * 2
        replacements = {field: initial["paper"][field] for field in PAPER_PROSE_FIELDS}
        source = {"title": "ThinkingBox", "primary_url": "https://example.com/report",
                  "source_text": overloaded_evidence(), "fetch_status": "fetched"}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            materials, out, raw = (root / name for name in ("materials.json", "brief.json", "raw.json"))
            materials.write_text(json.dumps({"research": [source]}), encoding="utf-8")
            argv = ["generate_research_brief_api.py", "--materials", str(materials),
                    "--date", "2026-10-08", "--out-json", str(out),
                    "--prompt-out", str(root / "prompt.txt"), "--raw-output", str(raw)]
            with patch.object(sys, "argv", argv), \
                 patch("generate_research_brief_api.load_runtime_config", return_value={}), \
                 patch("generate_research_brief_api.resolve_provider_fallbacks", return_value=(runtime(),)), \
                 patch("generate_research_brief_api.generate_research_with_fallbacks",
                       return_value=(initial, {"runtime_index": 0})), \
                 patch("generate_research_brief_api.review_research_reading", return_value={"requirements": []}), \
                 patch("generate_research_brief_api.chat_json",
                       return_value=({"replacements": replacements}, {})):
                with self.assertRaisesRegex(SystemExit, "editorial issues"):
                    main()
            self.assertFalse(out.exists())
            self.assertFalse((root / "prompt.txt").exists())
            self.assertEqual(json.loads(raw.read_text()), initial)


if __name__ == "__main__":
    unittest.main()
