from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path
from types import SimpleNamespace
from PIL import Image


WORKFLOW_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WORKFLOW_DIR / "scripts"))

from ai_daily_common import file_sha256  # noqa: E402
from holiday_auto_publish import AutoPublishBlocked, approve_image_manifest, compact_source_packet, deterministic_editorial_checks, visual_review, run_gate  # noqa: E402
from verify_research_image_lineage import LineageError, verify_lineage  # noqa: E402
from review_research_image_editorial import create_audit, approve_audit  # noqa: E402


def model_metadata() -> dict:
    return {"mode": "model", "fallback_model_used": False, "failed_attempts": []}


def quality_content() -> dict:
    return {
        "news": [
            {
                "title": "测试新闻",
                "category": "行业动态",
                "one_liner": "这是一条经过模型编辑的测试新闻。",
                "why": "它有明确的业务影响。",
                "observation": "需要继续观察后续落地情况。",
                "source_url": "https://example.com/news",
                "url": "https://example.com/news",
                "tags": ["测试"],
            }
        ],
        "generation": {
            "response": {
                "mode": "per_item_ai_editorial",
                "card_count": 1,
                "model_card_count": 1,
                "fallback_card_count": 0,
                "cards": [model_metadata()],
                "shell": model_metadata(),
            }
        },
    }


class HolidayAutoPublishTests(unittest.TestCase):
    def test_dry_run_checks_editorial_record_before_visual_model(self) -> None:
        original_cwd = Path.cwd()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = SimpleNamespace(run_dir=root, content=root/'content.json',
                normalized_content=root/'normalized.json', html=root/'preview.html',
                workflow_state=root/'workflow.json', source_materials=root/'sources.json',
                image_manifest=root/'manifest.json')
            source = root/'image.png'
            Image.new('RGB', (800, 450), 'white').save(source)
            for name in ('content', 'normalized_content', 'workflow_state', 'source_materials'):
                getattr(paths, name).write_text(json.dumps({'paper': {'image': 'image.png'}}) if name == 'content' else '{}')
            paths.html.write_text('preview')
            paths.image_manifest.write_text(json.dumps({'image_source': 'image.png', 'quality_gate': {'decision': 'pending'}}))
            before = paths.image_manifest.read_bytes()
            args = SimpleNamespace(date='2026-09-28', window_start='2026-09-28', window_end='2026-09-28',
                allow_outside_window=False, workspace=root, dry_run=True, runtime_config=None, image_provider_registry=None)
            try:
                with patch('holiday_auto_publish.default_paths', return_value=paths), \
                     patch('holiday_auto_publish.assert_content_lineage'), \
                     patch('holiday_auto_publish.deterministic_editorial_checks'), \
                     patch('holiday_auto_publish.qa_runtime'), \
                     patch('holiday_auto_publish.editorial_review', return_value=({'decision': 'pass'}, {})), \
                     patch('holiday_auto_publish.verify_lineage'), \
                     patch('holiday_auto_publish.visual_review') as visual:
                    with self.assertRaisesRegex(AutoPublishBlocked, 'missing editorial_review'):
                        run_gate(args)
                    visual.assert_not_called()
                self.assertEqual(before, paths.image_manifest.read_bytes())
                self.assertEqual('blocked', json.loads((root/'holiday_auto_publish_quality_gate.json').read_text())['status'])
            finally:
                os.chdir(original_cwd)

    def test_missing_editorial_record_does_not_mutate_pending_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'image.png'
            Image.new('RGB', (800, 450), 'white').save(source)
            manifest_path = root / 'manifest.json'
            manifest_path.write_text(json.dumps({
                'quality_gate': {'decision': 'pending'},
                'image_source': 'image.png', 'staged_image_sha256': file_sha256(source),
            }))
            before = manifest_path.read_bytes()
            with self.assertRaisesRegex(LineageError, 'missing editorial_review'):
                approve_image_manifest(manifest_path, workspace=root,
                    editorial={'decision': 'pass'}, editorial_metadata={},
                    visual={'decision': 'pass'}, visual_metadata={})
            self.assertEqual(before, manifest_path.read_bytes())

    def test_complete_bound_editorial_record_can_be_approved(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'image.png'
            Image.new('RGB', (800, 450), 'white').save(source)
            brief = root / 'brief.json'
            brief.write_text(json.dumps({'visual_handoff': {'subject': 'fixture'}}))
            review = create_audit(brief_json=brief, image_source=source,
                layout='mechanism_hub', render_mode='model_native', derivation_manifest=None)
            for check in review['manual_checks']:
                check.update(decision='pass', notes='Explicit test fixture inspection')
            review = approve_audit(review, reviewer='test fixture', notes='Fixture only')
            review_path = root / 'review.json'
            review_path.write_text(json.dumps(review))
            manifest_path = root / 'manifest.json'
            manifest_path.write_text(json.dumps({
                'quality_gate': {'decision': 'pending'}, 'brief_json': 'brief.json',
                'image_source': 'image.png', 'staged_image_sha256': file_sha256(source),
                'editorial_review': {'path': 'review.json', 'sha256': file_sha256(review_path),
                    'decision': 'pass', 'structural_decision': 'pass'},
            }))
            approve_image_manifest(manifest_path, workspace=root,
                editorial={'decision': 'pass'}, editorial_metadata={},
                visual={'decision': 'pass'}, visual_metadata={})
            result = json.loads(manifest_path.read_text())
            self.assertEqual('approved', result['quality_gate']['decision'])
            self.assertEqual(file_sha256(source), result['quality_gate']['automated_review']['staged_image_sha256'])

    def test_visual_gate_checks_material_facts_once(self) -> None:
        result = {
            'decision': 'pass', 'main_story_readable': True,
            'mechanism_materially_correct': True, 'claims_grounded': True,
            'critical_text_readable': True, 'issues': [],
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            image = root / 'preview.png'
            # This test isolates the QA response gate; decoding is enforced by staging.
            image.write_bytes(b'\x89PNG\r\n\x1a\n' + b'x' * 25000)
            content = {'paper': {'title': '研究', 'image': 'preview.png'}}
            manifest = {'staged_image_sha256': file_sha256(image)}
            with patch('quality_review.call_quality_model', return_value=(result, {})) as model:
                visual_review(None, workspace=root, content=content, manifest=manifest)
                self.assertEqual(model.call_args.kwargs['max_output_tokens'], 650)
                self.assertNotIn('relation_checks', model.call_args.kwargs['system_prompt'])
            for key in ('main_story_readable', 'mechanism_materially_correct', 'claims_grounded', 'critical_text_readable'):
                invalid = {**result, key: False}
                with self.subTest(key=key), patch('quality_review.call_quality_model', return_value=(invalid, {})):
                    with self.assertRaises(AutoPublishBlocked):
                        visual_review(None, workspace=root, content=content, manifest=manifest)

            brief = root / 'brief.json'
            brief.write_text(json.dumps({'visual_handoff': {
                'subject': '测试', 'takeaway': '返回截图', 'mechanism': '关系：浏览器｜返回截图｜智能体',
                'evidence': '来源事实', 'scope': '仅当前研究', 'labels': ['浏览器', '智能体'],
            }}))
            manifest['brief_json'] = 'brief.json'
            with patch('quality_review.call_quality_model', return_value=(result, {})) as model:
                visual_review(None, workspace=root, content=content, manifest=manifest)
                sent = model.call_args.kwargs['user_content'][0]['text']
                self.assertIn('返回截图', sent)
                self.assertNotIn('expected_relations', sent)

    def test_editorial_packet_retains_late_source_evidence(self) -> None:
        marker = "The measured scroll anchor is recorded by content identity."
        materials = {"news": [{"title": "Engineering article", "source_text": "x" * 5000 + marker}]}
        packet = compact_source_packet(materials)
        self.assertIn(marker, packet["news"][0]["source_text"])

    def test_deterministic_editorial_gate_accepts_complete_model_output(self) -> None:
        deterministic_editorial_checks(quality_content())

    def approved_v2_content(self) -> dict:
        content = json.loads((WORKFLOW_DIR / 'tests/fixtures/approved_editorial_v2_2026-10-08.json').read_text())
        for item in content['news']:
            if item.get('newspaper_role') == 'brief':
                item['observation'] = ''
        # The prose fixture intentionally omits runtime provenance. Supply a
        # synthetic complete audit to isolate copy/role compatibility here.
        count = len(content['news'])
        content['generation'] = {'response': {
            'mode': 'per_item_ai_editorial', 'card_count': count,
            'model_card_count': count, 'fallback_card_count': 0,
            'cards': [model_metadata() for _ in range(count)], 'shell': model_metadata(),
        }}
        return content

    def test_v2_fifty_four_cards_accept_empty_brief_observation(self) -> None:
        content = self.approved_v2_content()
        self.assertEqual(len(content['news']), 54)
        before = json.dumps(content, ensure_ascii=False, sort_keys=True)
        deterministic_editorial_checks(content)
        self.assertEqual(json.dumps(content, ensure_ascii=False, sort_keys=True), before)

    def test_v2_feature_observation_is_required_despite_brief_source_tier(self) -> None:
        for role in ('lead', 'supplement'):
            content = self.approved_v2_content()
            feature = next(item for item in content['news'] if item.get('newspaper_role') == role)
            self.assertEqual(feature['display_tier'], 'brief')
            feature['observation'] = ''
            with self.subTest(role=role), self.assertRaisesRegex(AutoPublishBlocked, 'observation'):
                deterministic_editorial_checks(content)

    def test_empty_issue_still_blocks(self) -> None:
        content = quality_content()
        content['news'] = []
        with self.assertRaisesRegex(AutoPublishBlocked, 'No final news cards'):
            deterministic_editorial_checks(content)

    def test_deterministic_editorial_gate_blocks_fallback_card(self) -> None:
        content = quality_content()
        content["generation"]["response"]["cards"][0]["mode"] = "source_grounded_fallback"
        content["generation"]["response"]["fallback_card_count"] = 1
        with self.assertRaisesRegex(AutoPublishBlocked, "fallback"):
            deterministic_editorial_checks(content)

    def test_automated_flags_cannot_replace_actual_editorial_review(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            image = root / "assets" / "research.png"
            image.parent.mkdir(parents=True)
            image.write_bytes(b"\x89PNG\r\n\x1a\nquality-gated-image")
            prompt = root / "prompt.txt"
            prompt.write_text("A white background research infographic", encoding="utf-8")
            image_hash = file_sha256(image)
            content = {"paper": {"title": "测试研究", "image": "assets/research.png"}}
            content_path = root / "content.json"
            content_path.write_text(json.dumps(content), encoding="utf-8")
            manifest = {
                "schema_version": 2,
                "provider": {"id": "gpt_image_2"},
                "quality_gate": {
                    "decision": "approved",
                    "automated_review": {
                        "validation_mode": "automated_visual",
                        "decision": "pass",
                        "staged_image_sha256": image_hash,
                        "editorial": {"decision": "pass"},
                        "visual": {"decision": "pass"},
                    },
                },
                "review_status": "pass",
                "staged_image": "assets/research.png",
                "paper_image": "assets/research.png",
                "staged_image_sha256": image_hash,
                "prompt_file": "prompt.txt",
                "prompt_sha256": file_sha256(prompt),
                "image_source": "assets/research.png",
                "image_source_sha256": image_hash,
            }
            manifest_path = root / "manifest.json"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

            with self.assertRaisesRegex(Exception, "missing editorial_review"):
                verify_lineage(content, manifest_path, root, require_imagegen=False, allow_candidate_provider=False)

            manifest["quality_gate"]["automated_review"]["staged_image_sha256"] = "wrong"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaisesRegex(Exception, "automated visual review"):
                verify_lineage(content, manifest_path, root, require_imagegen=False, allow_candidate_provider=False)


if __name__ == "__main__":
    unittest.main()
