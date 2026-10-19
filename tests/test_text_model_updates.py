from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from ai_gateway_client import GatewayError, ProviderRuntime, resolve_provider, resolve_provider_fallbacks, request_json
from managed_text_model import atomic_json, gateway_key, managed_provider, pin_route, read_object, route, SNAPSHOT_ENV
from update_text_models import Budget, CASES, POLICY, candidates, evaluate, policy, price, refresh, validate_case


class ModelUpdatesTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.now = datetime(2026, 10, 15, 8, tzinfo=timezone.utc)
        self.runtime = ProviderRuntime("text", "https://example.test/v1", "secret", "gpt-6-sol",
                                       "chat/completions", 120, 0, {"reasoning_effort": "medium"})
        self.settings = policy(POLICY)
        self.env = patch.dict(os.environ, {}, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)
        self.config = {
            "schema_version": 2, "defaults": {"api_key_env": "TEST_KEY", "base_url_env": "TEST_URL"},
            "text": {"enabled": True, "model": "gpt-6-sol", "endpoint_path": "chat/completions", "fallback_models": []},
            "image": {"enabled": True, "model": "gpt-image-2", "endpoint_path": "images/generations"},
            "managed_text_models": {"enabled": True, "state_dir": str(self.root)}}
        os.environ.update(TEST_KEY="secret", TEST_URL="https://example.test/v1")

    def rates(self):
        for model in ("gpt-6-sol", "gpt-6.1-sol", "gpt-6.2-sol"):
            self.settings["prices_usd_per_million_tokens"][model] = {
                "input": 1, "output": 2, "source": "test fixture", "verified_at": self.now.isoformat()}

    def update(self, *, days=0, models=None, evaluator=None, **kwargs):
        return refresh(self.runtime, self.settings, self.root, now=self.now + timedelta(days=days),
                       discover=lambda *a: models or {"gpt-6-sol", "gpt-6.1-sol", "gpt-image-2.5"},
                       evaluator=evaluator or (lambda *a: None), **kwargs)

    def approval(self, **changes):
        value = {"status": "approved", "model": "gpt-6.1-sol", "baseline_model": "gpt-6-sol",
                 "gateway_key": gateway_key(self.runtime), "effective_at": self.now.isoformat(),
                 "approval_id": "test", "fallback_models": ["gpt-6-sol"]}
        value.update(changes)
        atomic_json(self.root / "active.json", value)
        return value

    def test_selects_numeric_same_major_stable_family(self):
        ids = ["gpt-6.2-sol", "gpt-6.10-sol", "gpt-7-sol", "gpt-6.1-sol-preview", "gpt-6-astra",
               "gpt-image-2", "gpt-5.6-sol", "gpt-6-sol"]
        self.assertEqual(candidates(ids, "gpt-6-sol"), ["gpt-6.10-sol", "gpt-6.2-sol"])

    def test_unknown_prices_refresh_catalog_without_model_calls(self):
        def forbidden(*args):
            self.fail("Unpriced candidates must not call models")
        report = self.update(evaluator=forbidden)
        self.assertEqual(report["status"], "refreshed")
        self.assertEqual(report["candidates"][0]["status"], "awaiting_verified_pricing")
        self.assertEqual(report["budget"]["reserved_requests"], 0)
        self.assertFalse((self.root / "active.json").exists())
        self.assertEqual(len(read_object(self.root / "catalog.json")["models"]), 3)

    def test_catalog_diff_and_failed_or_empty_response_preserve_last_good(self):
        self.update()
        result = self.update(models={"gpt-6-sol", "gpt-6.2-sol"})
        self.assertEqual(result["added"], ["gpt-6.2-sol"])
        self.assertEqual(result["removed"], ["gpt-6.1-sol", "gpt-image-2.5"])
        before = (self.root / "catalog.json").read_bytes()
        for discover in (lambda *a: set(), lambda *a: (_ for _ in ()).throw(TimeoutError())):
            result = refresh(self.runtime, self.settings, self.root, now=self.now, discover=discover)
            self.assertEqual(result["status"], "failed")
            self.assertEqual((self.root / "catalog.json").read_bytes(), before)

    def test_two_passes_on_different_days_activate_next_day(self):
        self.rates()
        self.update()
        self.assertFalse((self.root / "active.json").exists())
        self.update()
        self.assertFalse((self.root / "active.json").exists())
        result = self.update(days=1)
        self.assertEqual(result["candidates"][0]["independent_passes"], 2)
        active = read_object(self.root / "active.json")
        self.assertEqual(active["effective_at"], "2026-10-17T00:00:00+08:00")
        self.assertEqual(route(self.runtime, self.config, now=self.now+timedelta(days=1)), {})
        self.assertEqual(route(self.runtime, self.config, now=self.now+timedelta(days=2))["model"], "gpt-6.1-sol")

    def test_evaluation_failure_does_not_promote_and_does_refresh(self):
        self.rates()
        def fail(*args):
            raise ValueError("bad output")
        report = self.update(evaluator=fail)
        self.assertEqual(report["candidates"][0]["status"], "evaluation_failed")
        self.assertEqual(report["status"], "refreshed")
        self.assertFalse((self.root / "active.json").exists())

    def test_refresh_only_cannot_call_models(self):
        self.rates()
        report = self.update(refresh_only=True, evaluator=lambda *a: self.fail("Called evaluation"))
        self.assertEqual(report["candidates"][0]["status"], "awaiting_evaluation")

    def test_expensive_or_stale_prices_hold_candidate(self):
        self.rates()
        self.settings["prices_usd_per_million_tokens"]["gpt-6.1-sol"]["output"] = 20
        self.assertEqual(self.update()["candidates"][0]["status"], "price_above_limit")
        self.assertEqual(self.update(days=31)["candidates"][0]["status"], "awaiting_verified_pricing")
        self.settings["prices_usd_per_million_tokens"]["gpt-6-sol"]["input"] = float("nan")
        self.assertIsNone(price(self.settings, "gpt-6-sol", self.now))

    def test_changed_baseline_or_gateway_invalidates_approval(self):
        self.approval(effective_at="2020-01-01T00:00:00+00:00")
        self.assertEqual(managed_provider(self.runtime, self.config).model, "gpt-6.1-sol")
        self.assertEqual(managed_provider(replace(self.runtime, base_url="https://other.test"), self.config).model, "gpt-6-sol")
        self.assertEqual(managed_provider(replace(self.runtime, model="gpt-6-astra"), self.config).model, "gpt-6-astra")

    def test_corrupt_or_absent_state_is_nonblocking(self):
        for text in ("not json", "[]", '{"status":"approved"}', '{"status":"approved","effective_at":"bad"}'):
            (self.root / "active.json").write_text(text)
            self.assertEqual(managed_provider(self.runtime, self.config).model, "gpt-6-sol")

    def test_snapshot_pins_route_and_empty_snapshot_pins_baseline(self):
        self.assertEqual(pin_route(self.config)["generation"], "gpt-6-sol")
        self.approval(effective_at="2020-01-01T00:00:00+00:00")
        self.assertEqual(resolve_provider("text", self.config).model, "gpt-6-sol")
        del os.environ[SNAPSHOT_ENV]
        self.assertEqual(pin_route(self.config)["generation"], "gpt-6.1-sol")
        self.approval(model="gpt-6.2-sol", effective_at="2020-01-01T00:00:00+00:00")
        self.assertEqual(resolve_provider("text", self.config).model, "gpt-6.1-sol")

    def test_unmanaged_qa_and_image_stay_fixed_and_baseline_is_fallback(self):
        self.approval(effective_at="2020-01-01T00:00:00+00:00")
        self.assertEqual([p.model for p in resolve_provider_fallbacks("text", self.config)], ["gpt-6.1-sol", "gpt-6-sol"])
        self.assertEqual(resolve_provider("text", self.config, managed=False).model, "gpt-6-sol")
        self.assertEqual(resolve_provider("image", self.config).model, "gpt-image-2")
        from quality_review import qa_runtime
        with patch("quality_review.load_runtime_config", return_value=self.config):
            self.assertEqual(qa_runtime(None).model, "gpt-6-sol")

    def test_technical_failure_quarantines_candidate_and_preserves_fallback(self):
        self.approval(effective_at="2020-01-01T00:00:00+00:00")
        pin_route(self.config)
        provider = resolve_provider("text", self.config)
        with patch("ai_gateway_client.load_runtime_config", return_value=self.config), \
                patch("ai_gateway_client._request_json", side_effect=GatewayError("HTTP 503")):
            with self.assertRaises(GatewayError):
                request_json(provider, {"model": provider.model})
        self.assertIn("gpt-6.1-sol", read_object(self.root / "quarantine.json")[gateway_key(provider)])
        self.assertEqual(resolve_provider("text", self.config).model, "gpt-6-sol")
        self.assertEqual(self.update()["candidates"][0]["status"], "quarantined")

    def test_rollback_returns_to_previously_validated_model(self):
        self.approval(model="gpt-6.2-sol", fallback_models=["gpt-6.1-sol", "gpt-6-sol"], effective_at="2020-01-01T00:00:00+00:00")
        atomic_json(self.root / "quarantine.json", {gateway_key(self.runtime): {"gpt-6.2-sol": {}}})
        self.assertEqual([p.model for p in resolve_provider_fallbacks("text", self.config)], ["gpt-6.1-sol", "gpt-6-sol"])

    def test_budget_stops_before_call_and_no_json_repair(self):
        self.rates()
        self.settings["max_calls_per_run"] = 2
        budget = Budget(self.settings, self.now)
        with patch("update_text_models.chat_json", return_value=({"ok": True}, {})) as call:
            budget.call(self.runtime, "system", {"x": "test"})
            self.assertFalse(call.call_args.kwargs["allow_json_repair"])
            with self.assertRaises(ValueError):
                budget.call(self.runtime, "system", {"x": "test"})
            self.assertEqual(call.call_count, 1)
        self.settings["max_cost_usd_per_run"] = 0.000001
        with patch("update_text_models.chat_json") as call:
            with self.assertRaises(ValueError):
                Budget(self.settings, self.now).call(self.runtime, "system", {"x": "test"})
            call.assert_not_called()

    def test_real_validation_catches_prose_overflow_dates_and_duplicates(self):
        cases = json.loads(CASES.read_text())["cases"]
        news = {"title": "Atlas 开启封闭测试", "one_liner": "新增来源引用与冲突提示。", "why": "便于追溯事实。",
                "observation": "关注团队接入效果。", "boundary": "2026-10-13 公布，目前仅限封闭测试。"}
        validate_case(cases[0], news)
        for bad in ({**news, "boundary": "已经全面开放。"}, {**news, "one_liner": "长" * 141}):
            with self.assertRaises(ValueError):
                validate_case(cases[0], bad)
        validate_case(cases[2], {"keep_ids": ["B"], "duplicate_ids": ["A"], "reason": "A 为旧公告，B 有新功能。"})
        with self.assertRaises(ValueError):
            validate_case(cases[2], {"keep_ids": ["A", "B"], "duplicate_ids": [], "reason": "都保留"})

    def test_judge_rejection_cannot_pass_evaluation(self):
        self.rates()
        budget = Budget(self.settings, self.now)
        budget.reviewer = self.runtime
        case = {"id": "test", "task": "test", "source": "source", "required": ["text"]}
        responses = [({"text": "old"}, {"elapsed_ms": 100}), ({"text": "new"}, {"elapsed_ms": 100}),
                     ({"a_acceptable": False, "b_acceptable": True, "preferred": "B", "issues": ["unsupported"]}, {})]
        with patch.object(budget, "call", side_effect=responses):
            with self.assertRaises(ValueError):
                evaluate(self.runtime, replace(self.runtime, model="gpt-6.1-sol"), [case], budget, {}, lambda: None)

    def test_changed_policy_does_not_reuse_old_success(self):
        self.rates()
        self.update()
        self.settings["max_latency_ratio"] = 1.2
        result = self.update(days=1)
        self.assertEqual(result["candidates"][0]["independent_passes"], 1)
        self.assertFalse((self.root / "active.json").exists())


if __name__ == "__main__":
    unittest.main()
