from contextlib import ExitStack
from copy import deepcopy
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import automatic_delivery as auto
import email_quality_control as quality
import run_ai_daily as runner
from PIL import Image
from review_research_image_editorial import create_audit, validate_editorial_review_for_approval, EditorialReviewError

POLICY = {'schema_version': 1, 'ai_review_enabled': True, 'automatic_send_enabled': True,
          'automatic_send_authorization': 'user approved in test', 'max_attempts': 3, 'retry_delays_seconds': [0, 0]}
EDITORIAL = {'decision': 'pass', 'issues': [], 'source_grounded': True, 'readable_chinese': True,
             'structure_complete': True, 'no_internal_notes': True, 'no_duplicates': True}
VISUAL = {'decision': 'pass', 'issues': [], 'main_story_readable': True,
          'mechanism_materially_correct': True, 'claims_grounded': True, 'critical_text_readable': True}


class AutomaticDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.paths = SimpleNamespace(run_dir=root, run_date='2026-10-12')
        for name in ('content', 'normalized_content', 'source_selection', 'html'):
            setattr(self.paths, name, root / (name + '.json'))
            getattr(self.paths, name).write_text('{}')
        self.args = SimpleNamespace(rebuild=False, ai_runtime_config=None)
        (root/'email_self_review.json').write_text('{"status":"passed"}')

    def pipeline(self, stack):
        stack.enter_context(patch.object(auto, 'policy', return_value=POLICY))
        stack.enter_context(patch.object(quality, 'policy', return_value=POLICY))
        stack.enter_context(patch.object(runner, 'has_existing_prod_send', return_value=False))
        names = ['review_current_email', 'approve_reviewed_image', 'review_coverage']
        mocks = {n: stack.enter_context(patch.object(auto, n)) for n in names}
        for n in ('run_prepare', 'run_api_preview', 'repair_email', 'check_production_email', 'run_prod'):
            mocks[n] = stack.enter_context(patch.object(runner, n))
        mocks['alert'] = stack.enter_context(patch.object(quality, 'send_failure_alert', return_value={'status': 'sent'}))
        return mocks

    def test_success_sends_once_after_all_reviews_with_no_extra_recovery(self):
        events = []
        with ExitStack() as stack:
            m = self.pipeline(stack)
            for name in ('review_current_email', 'approve_reviewed_image', 'check_production_email', 'run_prod'):
                m[name].side_effect = lambda *a, label=name, **kw: events.append(label)
            auto.run_publish_reviewed(self.paths, self.args)
            self.assertEqual(events, ['review_current_email', 'approve_reviewed_image', 'check_production_email', 'run_prod'])
            m['review_coverage'].assert_not_called()
            m['run_prepare'].assert_not_called()
            sent_args = m['run_prod'].call_args.args[1]
            self.assertTrue(sent_args.send and sent_args.confirm_prod and sent_args.require_feedback)
            self.assertFalse(sent_args.allow_candidate_image_provider)
            self.assertTrue(m['run_prod'].call_args.kwargs['prechecked'])
            m['alert'].assert_not_called()

    def test_three_failed_reviews_alert_and_never_send_even_after_restart(self):
        with ExitStack() as stack:
            m = self.pipeline(stack)
            m['review_current_email'].side_effect = ValueError('Material factual error in final copy')
            for _ in range(2):
                with self.assertRaises(quality.EmailQualityError):
                    auto.run_publish_reviewed(self.paths, self.args)
            self.assertEqual(m['review_current_email'].call_count, 3)
            self.assertEqual(m['repair_email'].call_count, 2)
            m['run_prod'].assert_not_called()

    def test_advisory_coverage_does_not_retry_regenerate_or_alert(self):
        self.paths.source_selection.write_text(json.dumps({'all_candidates_scored': [{
            'title': 'A recommended but unselected study', 'primary_url': 'https://example.org/study',
            'aihot_daily_featured': True, 'aihot_daily_rank': 1}]}))
        self.paths.normalized_content.write_text('{"news": []}')
        with ExitStack() as stack:
            m = self.pipeline(stack)
            m['review_coverage'].side_effect = AssertionError('Selection review must not run')
            model = stack.enter_context(patch('quality_review.call_quality_model'))
            auto.run_publish_reviewed(self.paths, self.args)
            m['run_prod'].assert_called_once()
            m['repair_email'].assert_not_called()
            m['alert'].assert_not_called()
            m['review_coverage'].assert_not_called()
            model.assert_not_called()

    def test_initial_generation_is_inside_recovery_budget(self):
        self.paths.html.unlink()
        with ExitStack() as stack:
            m = self.pipeline(stack)
            m['run_prepare'].side_effect = ValueError('generation failed')
            m['repair_email'].side_effect = ValueError('repair failed')
            with self.assertRaises(quality.EmailQualityError):
                auto.run_publish_reviewed(self.paths, self.args)
            m['run_prepare'].assert_called_once()
            self.assertEqual(m['repair_email'].call_count, 2)
            m['run_prod'].assert_not_called()

    def test_partial_delivery_blocks_regeneration_and_repackaging(self):
        (self.paths.run_dir/'smtp_prod_delivery.json').write_text(json.dumps({'sent': ['already@example.com']}))
        with ExitStack() as stack:
            m = self.pipeline(stack)
            alert = stack.enter_context(patch.object(auto, 'send_failure_alert'))
            with self.assertRaisesRegex(quality.EmailQualityError, 'already started'):
                auto.run_publish_reviewed(self.paths, self.args)
            m['run_prepare'].assert_not_called()
            m['run_prod'].assert_not_called()
            alert.assert_called_once()

    def test_disabled_authorization_blocks_all_actions(self):
        with patch.object(auto, 'policy', return_value={**POLICY, 'automatic_send_enabled': False}), patch.object(runner, 'run_prod') as send:
            with self.assertRaises(quality.EmailQualityError):
                auto.run_publish_reviewed(self.paths, self.args)
            send.assert_not_called()

    def test_unacknowledged_delivery_never_retries_automatically(self):
        (self.paths.run_dir/'automatic_delivery_state.json').write_text('{"status":"started"}')
        with ExitStack() as stack:
            m = self.pipeline(stack)
            stack.enter_context(patch.object(auto, 'send_failure_alert'))
            with self.assertRaisesRegex(quality.EmailQualityError, 'already started'):
                auto.run_publish_reviewed(self.paths, self.args)
            m['review_current_email'].assert_not_called()
            m['run_prod'].assert_not_called()

    def test_packaging_failure_notifies_owner(self):
        with ExitStack() as stack:
            m = self.pipeline(stack)
            m['run_prod'].side_effect = OSError('package failed')
            alert = stack.enter_context(patch.object(auto, 'send_failure_alert'))
            with self.assertRaises(OSError):
                auto.run_publish_reviewed(self.paths, self.args)
            alert.assert_called_once()
            self.assertEqual(alert.call_args.args[2]['scope'], 'delivery')

    def test_coverage_requires_complete_per_item_decisions(self):
        row = {'id': 'candidate', 'status': 'pending', 'included': False, 'urls': ['https://example.org/a'],
               'title': 'Distinct model release', 'review_issues': []}
        report = {'items': [row], 'final_selection_errors': []}
        policy = {'coverage_review': {'enabled': True, 'required_from': '2026-01-01'}}
        self.paths.source_selection.write_text(json.dumps({'all_candidates_scored': []}))
        for response in ({'decision': 'pass', 'issues': [], 'items': []},
                         {'decision': 'pass', 'issues': [], 'items': [{'id': 'candidate', 'disposition': 'exclude', 'reason': '低分'}]},
                         {'decision': 'pass', 'issues': [], 'items': [{'id': 'candidate', 'disposition': 'defer', 'reason': '发布说明未核实，需要确认具体能力和可用范围'}]}):
            with patch('daily_config.load_selection_policy', return_value=policy), patch('coverage_review.refresh_review', return_value=deepcopy(report)), patch('coverage_review.assert_ready') as ready, patch('quality_review.qa_runtime'), patch('quality_review.call_quality_model', return_value=(response, {'provider': {'model': 'test'}})):
                if response['items'] and response['items'][0]['disposition'] == 'defer':
                    auto.review_coverage(self.paths, self.args)
                    ready.assert_called_once()
                    stored = json.loads((self.paths.run_dir/'high_value_coverage_review.json').read_text())
                    self.assertEqual(stored['items'][0]['editor'], 'AI independent coverage review')
                else:
                    with self.assertRaises(quality.EmailQualityError):
                        auto.review_coverage(self.paths, self.args)
                    ready.assert_not_called()

    def test_visual_automation_is_explicit_and_rejects_missing_evidence_or_changed_pixels(self):
        source = self.paths.run_dir/'image.png'
        Image.new('RGB', (50, 50), 'white').save(source)
        brief = self.paths.run_dir/'brief.json'
        brief.write_text(json.dumps({'visual_handoff': {'main_claim': 'source-specific mechanism'}}))
        audit = create_audit(brief_json=brief, image_source=source, layout='mechanism_hub', render_mode='model_native', derivation_manifest=None)
        audit.update(validation_mode='automated_visual', decision='pass', reviewer='AI visual reviewer', review_notes='Actual model result',
                     staged_image=str(source), staged_image_sha256=quality.file_sha256(source),
                     automated_checks={'visual': {'result': VISUAL}, 'editorial': {'result': EDITORIAL}})
        path = self.paths.run_dir/'audit.json'
        path.write_text(json.dumps(audit))
        validate_editorial_review_for_approval(path, image_source=source, brief_json=brief)
        self.assertTrue(all(c['decision'] == 'pending' for c in audit['manual_checks']))
        audit['automated_checks']['visual']['result'] = {'decision': 'pass'}
        path.write_text(json.dumps(audit))
        with self.assertRaises(EditorialReviewError):
            validate_editorial_review_for_approval(path, image_source=source, brief_json=brief)
        Image.new('RGB', (50, 50), 'black').save(source)
        with self.assertRaises(EditorialReviewError):
            validate_editorial_review_for_approval(path, image_source=source, brief_json=brief)

    def test_existing_scheduled_prepare_uses_authorized_automatic_path(self):
        args = SimpleNamespace(action='prepare', scheduled=True, date='2026-10-12', send=False,
                               work_calendar=None, rebuild=False, start='existing')
        with patch.object(runner, 'parse_args', return_value=args), patch.object(quality, 'policy', return_value=POLICY), patch.object(runner, 'workday_decision', return_value=(False, 'closed')), patch.object(runner, 'execute_workflow') as execute:
            runner.main()
            self.assertEqual(args.action, 'publish-reviewed')
            execute.assert_not_called()

    def test_image_approval_rebinds_only_metadata_and_rejects_changed_assets(self):
        self.paths.image_manifest = self.paths.run_dir/'manifest.json'
        self.paths.normalized_content.write_text(json.dumps({'paper': {'title': 'Reviewed image'}}))
        manifest = {'quality_gate': {'decision': 'pending'}, 'image_source': 'source.png',
                    'brief_json': 'brief.json', 'staged_image': 'staged.jpg', 'staged_image_sha256': 'pixels'}
        before = {'content': 'copy', 'image_0': 'pixels', 'research_brief': 'handoff', 'image_manifest': 'old'}
        for changed in [False, True]:
            with self.subTest(changed=changed), ExitStack() as stack:
                self.paths.image_manifest.write_text(json.dumps(manifest))
                receipt = {'status': 'passed', 'hashes': before.copy(),
                           'checks': {'editorial': {'result': EDITORIAL, 'metadata': {}},
                                      'visual': {'result': VISUAL, 'metadata': {}}}}
                after = {**before, 'image_manifest': 'approved'}
                if changed: after['image_0'] = 'new pixels'
                stack.enter_context(patch('email_quality_control.artifact_hashes', side_effect=[before, after]))
                stack.enter_context(patch('newspaper_text.text_only', return_value=False))
                stack.enter_context(patch('verify_research_image_lineage.verify_lineage'))
                stack.enter_context(patch('review_research_image_editorial.create_audit', return_value={'structural_decision': 'pass'}))
                stack.enter_context(patch('quality_review.approve_image_manifest'))
                args = SimpleNamespace(image_provider_registry=None)
                if changed:
                    with self.assertRaisesRegex(quality.EmailQualityError, 'assets changed'):
                        auto.approve_reviewed_image(self.paths, args, receipt)
                    self.assertEqual(receipt['hashes'], before)
                else:
                    auto.approve_reviewed_image(self.paths, args, receipt)
                    saved = json.loads((self.paths.run_dir/'email_self_review.json').read_text())
                    self.assertEqual(saved['hashes'], after)
                    self.assertEqual(saved['checks'], receipt['checks'])

    def test_exclusion_is_reviewed_again_when_final_email_changes(self):
        from coverage_review import refresh_review, assert_ready, CoverageReviewError
        candidate = {'title': 'Featured AI model', 'primary_url': 'https://example.org/model',
                     'aihot_daily_featured': True, 'aihot_daily_rank': 1}
        self.paths.source_selection.write_text(json.dumps({'all_candidates_scored': [candidate]}))
        self.paths.content.write_text('{"news": []}')
        review = self.paths.run_dir/'coverage.json'
        report = refresh_review(self.paths.source_selection, self.paths.content, review)
        self.assertEqual(report['summary']['pending'], 1)
        report['items'][0].update(disposition='exclude', editor='AI independent coverage review',
                                 reason='该条目与邮件已解释的机制重叠，缺少独立的新增结果。')
        review.write_text(json.dumps(report))
        assert_ready(self.paths.source_selection, self.paths.content, review)
        self.paths.content.write_text('{"news": [], "summary": "邮件已改写，原有重叠解释不再可靠"}')
        updated = refresh_review(self.paths.source_selection, self.paths.content, review)
        self.assertEqual(updated['summary']['pending'], 1)
        self.assertEqual(updated['items'][0]['reason'], '')
        self.assertEqual(updated['items'][0]['editor'], '')
        self.assertIsNone(updated['items'][0]['automated_review'])


if __name__ == '__main__':
    unittest.main()
