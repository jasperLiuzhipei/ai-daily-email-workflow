"""Cached production mail must pass current prose checks and match its build."""
from contextlib import ExitStack
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import build_ai_daily_email as builder
import newspaper_text
import run_ai_daily as runner


class CachedProductionProseTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.paths = SimpleNamespace(
            run_date="2026-10-11", content=self.root / "content.json",
            normalized_content=self.root / "normalized.json", html=self.root / "daily.html",
            package_prod=self.root / "package", image_manifest=self.root / "image.json",
            source_selection=self.root / "selection.json", source_materials=self.root / "materials.json",
        )
        self.paths.source_selection.write_text(json.dumps({"selected_news": []}))
        selection_sha = hashlib.sha256(self.paths.source_selection.read_bytes()).hexdigest()
        self.paths.source_materials.write_text(json.dumps({"selection_sha256": selection_sha}))
        raw = {
            "date": self.paths.run_date,
            "editor_note": "今日关注开放模型在本地检索中的应用。",
            "news_section": {"title": "行业动态", "subtitle": "今日产品与技术进展"},
            "news": [{
                "title": "团队开放跨模态检索模型", "category": "model_update",
                "event_id": "fixture-retrieval", "display_tier": "brief", "daily_rank": 1,
                "tags": ["模型"], "one_liner": "团队开放跨模态检索模型，可将图片和文本映射到同一向量空间。",
                "why": "同一检索入口可覆盖两类素材。", "observation_label": "行业视角",
                "observation": "本地运行降低了敏感素材外传的需求。", "url": "https://example.com/retrieval",
            }],
            "summary_headline": "图片与文本共享检索入口", "summary": "开放模型增加了本地跨模态检索的选择。",
            "generation": {"selection_sha256": selection_sha,
                           "source_material_sha256": hashlib.sha256(self.paths.source_materials.read_bytes()).hexdigest()},
        }
        self.paths.content.write_text(json.dumps(raw, ensure_ascii=False))
        self.build_argv = ["build_ai_daily_email.py", "--content", str(self.paths.content),
                "--date", self.paths.run_date, "--series", "trial", "--issue", "60",
                "--issue-ledger", str(self.root / "issue_ledger.json"), "--no-write-ledger",
                "--output", str(self.paths.html), "--normalized-output", str(self.paths.normalized_content),
                "--disable-feedback"]
        self.build()
        self.args = SimpleNamespace(send=True, confirm_prod=True, skip_build=True,
                                    allow_candidate_image_provider=False, allow_resend_prod=False,
                                    editorial_policy=None, image_provider_registry=None)

    def build(self):
        with patch.object(sys, "argv", self.build_argv), patch.object(sys, "stdout", io.StringIO()):
            builder.main()

    def load(self):
        return json.loads(self.paths.normalized_content.read_text())

    def save(self, data):
        self.paths.normalized_content.write_text(json.dumps(data, ensure_ascii=False))

    def production_mocks(self, stack):
        mocked = {}
        values = {"has_existing_prod_send": False, "run_build": None,
                  "run_coverage_review": None, "review_current_email": None,
                  "run_package": self.root / "package" / "email.eml", "run_send": None,
                  "record_phase": None, "snapshot_delivery_metadata": {},
                  "commit_issue_ledger": 60, "commit_send_ledger": {}, "sync_feedback_delivery": None}
        for name, value in values.items():
            mocked[name] = stack.enter_context(patch.object(runner, name, return_value=value))
        return mocked

    def assert_blocked(self, error):
        with ExitStack() as stack:
            mocked = self.production_mocks(stack)
            with self.assertRaisesRegex(runner.WorkflowError, error):
                runner.check_production_email(self.paths, self.args)
            mocked["run_build"].assert_not_called()
            mocked["run_package"].assert_not_called()
            mocked["run_send"].assert_not_called()

    def test_build_binds_content_and_html_without_writing_issue_ledger(self):
        data = self.load()
        self.assertEqual(data["presentation"]["content_sha256"], newspaper_text.build_content_signature(data))
        self.assertEqual(data["presentation"]["html_sha256"], hashlib.sha256(self.paths.html.read_bytes()).hexdigest())
        self.assertEqual(data["presentation"]["source_content_sha256"], hashlib.sha256(self.paths.content.read_bytes()).hexdigest())
        self.assertFalse((self.root / "issue_ledger.json").exists())

    def test_source_content_edit_cannot_send_an_older_build(self):
        data = json.loads(self.paths.content.read_text())
        data["editor_note"] = "编辑已更新今天的关注重点。"
        self.paths.content.write_text(json.dumps(data, ensure_ascii=False))
        self.assert_blocked("content validation failed.*Source content")

    def test_legacy_cache_without_source_signature_requires_rebuild(self):
        data = self.load()
        data["presentation"].pop("source_content_sha256")
        self.save(data)
        self.assert_blocked("content validation failed.*Source content")

    def test_refreshed_source_selection_cannot_send_an_older_build(self):
        self.paths.source_selection.write_text(json.dumps({"selected_news": [{"title": "新增选择"}]}))
        self.assert_blocked("Source selection changed")

    def test_refreshed_source_material_cannot_send_an_older_build(self):
        materials = json.loads(self.paths.source_materials.read_text())
        materials["news"] = [{"text": "修订后的材料"}]
        self.paths.source_materials.write_text(json.dumps(materials, ensure_ascii=False))
        self.assert_blocked("Content was not generated from the current")

    def test_empty_research_list_with_legacy_paper_cannot_skip_image_gate(self):
        raw = json.loads(self.paths.content.read_text())
        fixture = json.loads((Path(__file__).resolve().parent / "fixtures/newspaper_issue.json").read_text())
        raw.update(research_items=[], paper=fixture["paper"])
        image = self.root / "research.png"
        image.write_bytes(b"synthetic local asset; no image decoding is needed by this test")
        raw["paper"]["image"] = str(image)
        self.paths.content.write_text(json.dumps(raw, ensure_ascii=False))
        self.assertTrue(runner.content_has_research(self.paths))
        self.build()
        self.assertNotIn("research_items", self.load())
        self.assert_blocked("research image quality gate failed")

    def test_failed_render_preserves_previous_snapshot_and_issue_ledger(self):
        before = self.paths.normalized_content.read_bytes()
        html_before = self.paths.html.read_bytes()
        self.build_argv.remove("--no-write-ledger")
        with patch.object(builder.newspaper_text, "render_email", side_effect=ValueError("stale override")):
            with self.assertRaisesRegex(ValueError, "stale override"):
                self.build()
        self.assertEqual(self.paths.normalized_content.read_bytes(), before)
        self.assertEqual(self.paths.html.read_bytes(), html_before)
        self.assertFalse((self.root / "issue_ledger.json").exists())

    def test_valid_cached_issue_reaches_self_review_without_rebuild(self):
        with ExitStack() as stack:
            mocked = self.production_mocks(stack)
            runner.check_production_email(self.paths, self.args)
            mocked["run_build"].assert_not_called()
            mocked["review_current_email"].assert_called_once()
            mocked["run_package"].assert_not_called()
            mocked["run_send"].assert_not_called()

    def test_cached_verbose_draft_fails_current_validator_even_with_signatures(self):
        data = self.load()
        data["news"][0]["one_liner"] = "模型还列出多项实验与完整榜单，进一步说明各组任务的详细结果。" * 18
        # Represent a cache produced before the prose policy existed. Matching
        # artifact signatures are no substitute for today's editorial checks.
        old_html = self.paths.html.read_text().replace(
            "团队开放跨模态检索模型，可将图片和文本映射到同一向量空间。", data["news"][0]["one_liner"])
        self.paths.html.write_text(old_html)
        data["presentation"]["html_sha256"] = hashlib.sha256(old_html.encode()).hexdigest()
        data["presentation"]["content_sha256"] = newspaper_text.build_content_signature(data)
        self.save(data)
        self.assert_blocked("content validation failed")

    def test_json_only_edit_cannot_reuse_older_html(self):
        data = self.load()
        data["news"][0]["one_liner"] = "团队开放模型权重，支持图片与文本的本地检索。"
        self.save(data)
        self.assert_blocked("text edition check failed.*(?:signature|Content|content)")

    def test_legacy_cache_without_content_signature_requires_rebuild(self):
        data = self.load()
        data["presentation"].pop("content_sha256")
        self.save(data)
        self.assert_blocked("text edition check failed.*(?:signature|Content|content)")

    def test_editor_note_only_edit_cannot_reuse_older_html(self):
        data = self.load()
        data["editor_note"] = "今日关注模型开放后对本地检索部署的影响。"
        self.save(data)
        self.assert_blocked("text edition check failed.*(?:signature|Content|content)")

    def test_missing_normalized_content_cannot_fall_back_to_unchecked_html(self):
        self.paths.normalized_content.unlink()
        self.assert_blocked("content validation failed")

    def test_different_issue_content_is_rejected_before_packaging(self):
        data = self.load()
        data["date"] = "2026-10-10"
        self.save(data)
        self.assert_blocked("content validation failed.*date")


if __name__ == "__main__":
    unittest.main()
