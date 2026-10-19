"""Exercise recovery budgets, exact-artifact review and owner-only notification."""
from contextlib import ExitStack
import json
import os
from pathlib import Path
import smtplib
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import email_quality_control as quality
import quality_review as gate
import run_ai_daily as runner
import workflow_context
from email_review_feedback import with_review_feedback

PASS = {"decision": "pass", "source_grounded": True, "timing_consistent": True, "readable_chinese": True,
        "structure_complete": True, "no_internal_notes": True, "no_duplicates": True, "issues": []}
POLICY = {"schema_version": 1, "ai_review_enabled": True, "max_attempts": 3, "retry_delays_seconds": [0, 0],
          "alert_recipient_env": "AI_DAILY_ALERT_TO"}


class EmailQualityTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.paths = SimpleNamespace(run_date="2026-10-12", run_dir=self.root)
        for key in ("content", "normalized_content", "source_materials", "source_selection", "image_manifest"):
            setattr(self.paths, key, self.root / f"{key}.json")
            getattr(self.paths, key).write_text('{}')
        self.paths.html = self.root / 'mail.html'
        self.paths.html.write_text('<html><body><h1>原始新闻标题</h1><p>最终可见正文</p></body></html>')
        self.paths.normalized_content.write_text(json.dumps({"date": "10月12日",
            "presentation": {"source_content_sha256": quality.file_sha256(self.paths.content)}}))
        self.args = SimpleNamespace(ai_runtime_config=None, editorial_policy=None, rebuild=False,
            email_settings=self.root / 'email.json', send=True, confirm_prod=True,
            allow_candidate_image_provider=False, allow_resend_prod=False, skip_build=True)
        self.args.email_settings.write_text(json.dumps({"from_addr": "owner@example.com", "smtp": {
            "host": "smtp.example.com", "port": 465, "security": "ssl", "user": "owner@example.com"}}))
        self.addCleanup(patch.stopall)
        patch.object(quality, 'policy', return_value=POLICY).start()
        patch.dict(os.environ, {'AI_DAILY_ALERT_TO': 'owner@example.com', 'AI_DAILY_SMTP_PASSWORD': 'secret-test-value'}).start()
        patch('ai_gateway_client.load_env_file').start()

    def review_patches(self, stack):
        stack.enter_context(patch.object(workflow_context, 'assert_content_lineage'))
        stack.enter_context(patch('validate_ai_daily_content.validate_content'))
        stack.enter_context(patch.object(gate, 'deterministic_editorial_checks'))
        stack.enter_context(patch('newspaper_text.enabled', return_value=False))
        stack.enter_context(patch.object(gate, 'qa_runtime', return_value=None))
        return stack.enter_context(patch.object(gate, 'call_quality_model', return_value=(dict(PASS), {})))

    def test_shared_review_changes_invalidate_cached_approval(self):
        with ExitStack() as stack:
            model = self.review_patches(stack)
            original = quality.review_current_email(self.paths, self.args)
            actual_hash = quality.file_sha256
            def updated_hash(path):
                return 'changed-review-implementation' if Path(path).name == 'quality_review.py' else actual_hash(path)
            with patch.object(quality, 'file_sha256', side_effect=updated_hash):
                updated = quality.review_current_email(self.paths, self.args)
                cached = quality.review_current_email(self.paths, self.args)
            self.assertEqual(model.call_count, 2)
            self.assertNotEqual(original['review_id'], updated['review_id'])
            self.assertEqual(updated['review_id'], cached['review_id'])

    def test_success_after_repair_preserves_feedback_without_alert(self):
        operation = Mock(side_effect=[ValueError('内部备注不应进入正文'), {'status': 'passed'}])
        feedback = []
        repair = lambda reason: feedback.append(with_review_feedback('原编辑规则'))
        with patch.object(quality, 'send_failure_alert') as alert:
            result = quality.recover(self.paths, self.args, scope='prepare', operation=operation, repair=repair)
        self.assertEqual(result['status'], 'passed')
        self.assertEqual(operation.call_count, 2)
        self.assertIn('内部备注', feedback[0])
        self.assertNotIn('AI_DAILY_REPAIR_FEEDBACK', os.environ)
        self.assertNotIn('AI_DAILY_QUALITY_LOG_DIR', os.environ)
        alert.assert_not_called()

    def test_exhaustion_and_restart_do_not_exceed_three_attempts_or_duplicate_alert(self):
        operation = Mock(side_effect=ValueError('模型 503 timeout secret-test-value'))
        repair = Mock()
        smtp = Mock()
        smtp.__enter__ = Mock(return_value=smtp)
        smtp.__exit__ = Mock(return_value=False)
        smtp.send_message.return_value = {}
        with patch.object(quality.smtplib, 'SMTP_SSL', return_value=smtp):
            for _ in range(2):
                with self.assertRaises(quality.EmailQualityError):
                    quality.recover(self.paths, self.args, scope='prepare', operation=operation, repair=repair)
        self.assertEqual(operation.call_count, 3)
        self.assertEqual(repair.call_count, 2)
        smtp.send_message.assert_called_once()
        self.assertEqual(smtp.send_message.call_args.kwargs['to_addrs'], ['owner@example.com'])
        email = smtp.send_message.call_args.args[0]
        self.assertIn('解决建议', email.get_content())
        self.assertIn('第 3 轮', email.get_content())
        self.assertNotIn('secret-test-value', email.get_content())
        self.assertIn('配额', email.get_content())
        self.assertNotIn('secret-test-value', (self.root/'prepare_recovery.json').read_text())

    def test_interrupted_attempt_counts_towards_restart_budget(self):
        (self.root/'prepare_recovery.json').write_text(json.dumps({"scope": "prepare", "status": "running",
            "attempts": [{"number": 1, "stage": "generation"}, {"number": 2, "stage": "generation"}]}))
        operation = Mock(side_effect=ValueError('still down'))
        with patch.object(quality, 'send_failure_alert', return_value={'status': 'sent'}):
            with self.assertRaises(quality.EmailQualityError):
                quality.recover(self.paths, self.args, scope='prepare', operation=operation, repair=Mock())
        operation.assert_called_once()

    def test_malformed_or_contradictory_ai_decision_never_passes(self):
        for result in ({'decision': 'pass'}, {**PASS, 'source_grounded': 'true'}, {**PASS, 'source_grounded': False},
                       {**PASS, 'timing_consistent': False}, {**PASS, 'issues': ['unsupported number']}):
            with self.subTest(result=result), ExitStack() as stack:
                model = self.review_patches(stack)
                model.return_value = (result, {})
                with self.assertRaises(quality.EmailQualityError):
                    quality.review_current_email(self.paths, self.args)
                self.assertEqual(json.loads((self.root/'email_self_review.json').read_text())['status'], 'failed')

    def test_optional_advice_passes_without_repair_or_failure(self):
        with ExitStack() as stack:
            model = self.review_patches(stack)
            model.return_value = ({**PASS, 'advisories': ['可补充事件日期，但文案没有声称今日发生。'], 'repairs': []}, {})
            receipt = quality.review_current_email(self.paths, self.args)
        self.assertEqual(receipt['status'], 'passed')
        self.assertEqual(len(receipt['checks']['editorial']['result']['advisories']), 1)

    def test_failed_then_passed_review_keeps_both_request_ids_and_exact_inputs(self):
        with ExitStack() as stack:
            model = self.review_patches(stack)
            model.side_effect = [({**PASS, 'decision': 'fail', 'issues': ['unsupported number']},
                {'request_id': 'first-call', 'usage': {'total_tokens': 100}}),
                (dict(PASS), {'request_id': 'second-call', 'usage': {'total_tokens': 90}})]
            with self.assertRaises(quality.EmailQualityError):
                quality.review_current_email(self.paths, self.args)
            first = json.loads((self.root/'email_self_review.json').read_text())
            second = quality.review_current_email(self.paths, self.args)
            cached = quality.review_current_email(self.paths, self.args)
        self.assertEqual(model.call_count, 2)
        self.assertEqual(cached['review_id'], second['review_id'])
        failed = json.loads((self.root/first['history_path']/'failed.json').read_text())
        passed = json.loads((self.root/second['history_path']/'result.json').read_text())
        self.assertEqual(failed['checks']['editorial']['metadata']['request_id'], 'first-call')
        self.assertEqual(passed['checks']['editorial']['metadata']['request_id'], 'second-call')
        request = json.loads((self.root/first['history_path']/'editorial_input.json').read_text())
        self.assertIn('最终可见正文', request['user_content']['final_email_text'])

    def test_only_identical_final_artifacts_reuse_a_pass(self):
        with ExitStack() as stack:
            model = self.review_patches(stack)
            quality.review_current_email(self.paths, self.args)
            quality.review_current_email(self.paths, self.args)
            self.assertEqual(model.call_count, 1)
            self.paths.html.write_text('<p>新版可见正文</p>')
            quality.review_current_email(self.paths, self.args)
            self.assertEqual(model.call_count, 2)
            self.assertIn('新版可见正文', model.call_args.kwargs['user_content'])
            self.paths.source_materials.write_text('{"news": []}')
            quality.review_current_email(self.paths, self.args)
            self.assertEqual(model.call_count, 3)

    def test_mutation_during_review_fails_closed(self):
        with ExitStack() as stack:
            model = self.review_patches(stack)
            def mutate(*args, **kwargs):
                self.paths.html.write_text('<p>Concurrent change</p>')
                return dict(PASS), {}
            model.side_effect = mutate
            with self.assertRaisesRegex(quality.EmailQualityError, 'changed during review'):
                quality.review_current_email(self.paths, self.args)

    def test_review_only_sends_final_story_facts_not_candidate_judgments(self):
        included = {'title': '已刊条目', 'primary_url': 'https://example.org/included',
            'source_text': 'Published Oct 10: model quality improves by 3%.',
            'source_quality_tier': 'a-tier', 'score': 70, 'editorial_decision': {'action': 'include'},
            'primary_verification': {'date_conflict': True, 'observed_date': '2026-10-10'}}
        omitted = {'title': '不应提交模型的落选线索', 'primary_url': 'https://example.org/omitted',
                   'source_text': 'The evaluator wants this added.', 'score': 100}
        self.paths.source_selection.write_text(json.dumps({'selected_news': [included],
            'all_candidates_scored': [included, omitted], 'event_editorial': {'policy': 'not-revalidated'}}))
        self.paths.source_materials.write_text(json.dumps({'news': [included, omitted]}))
        normalized = json.loads(self.paths.normalized_content.read_text())
        normalized['news'] = [{'title': '已刊条目', 'url': included['primary_url']}]
        self.paths.normalized_content.write_text(json.dumps(normalized))
        with ExitStack() as stack:
            model = self.review_patches(stack)
            stack.enter_context(patch('event_editorial.validate_final_selection', side_effect=AssertionError('No selection re-review')))
            stack.enter_context(patch.object(gate, 'deterministic_editorial_checks', side_effect=AssertionError('No generation history re-review')))
            receipt = quality.review_current_email(self.paths, self.args)
            packet = json.loads(model.call_args.kwargs['user_content'])
        self.assertEqual(receipt['scope'], 'final_copy_and_visual_handoff')
        self.assertEqual(receipt['checks']['selected_content']['present_count'], 1)
        self.assertEqual(len(packet['timing_conflicts']), 1)
        self.assertEqual(len(packet['source_packet']['news']), 1)
        payload = json.dumps(packet, ensure_ascii=False)
        for forbidden in ('不应提交模型', 'all_candidates_scored', 'editorial_decision', 'source_quality_tier', '"score"'):
            self.assertNotIn(forbidden, payload)

    def test_selected_news_or_research_loss_blocks_before_model(self):
        for kind in ('selected_news', 'selected_research'):
            with self.subTest(kind=kind), ExitStack() as stack:
                self.paths.source_selection.write_text(json.dumps({kind: [
                    {'title': '已选却缺失', 'primary_url': 'https://example.org/missing'}]}))
                model = self.review_patches(stack)
                with self.assertRaisesRegex(quality.EmailQualityError, 'Selected content missing'):
                    quality.review_current_email(self.paths, self.args)
                model.assert_not_called()

    def test_copy_review_receives_visual_handoff_and_image_failure_still_blocks(self):
        from research_image_contract import FIELDS
        image_path = self.root/'research.png'
        image_path.write_bytes(b'test image bytes; visual inspection is mocked')
        handoff = {key: '对应事实：' + key for key in FIELDS}
        brief = self.root/'brief.json'
        brief.write_text(json.dumps({'visual_handoff': handoff}))
        self.paths.image_manifest.write_text(json.dumps({'brief_json': str(brief)}))
        paper = {'title': '图片研究', 'image': str(image_path), 'url': 'https://example.org/study'}
        self.paths.content.write_text(json.dumps({'paper': paper}))
        normalized = json.loads(self.paths.normalized_content.read_text())
        normalized['paper'] = paper
        normalized['presentation']['source_content_sha256'] = quality.file_sha256(self.paths.content)
        self.paths.normalized_content.write_text(json.dumps(normalized))
        with ExitStack() as stack:
            model = self.review_patches(stack)
            stack.enter_context(patch('newspaper_text.text_only', return_value=False))
            visual = stack.enter_context(patch.object(gate, 'visual_review', side_effect=gate.AutoPublishBlocked('Visual QA did not pass: reversed mechanism')))
            with self.assertRaisesRegex(gate.AutoPublishBlocked, 'reversed mechanism'):
                quality.review_current_email(self.paths, self.args)
            self.assertEqual(json.loads(model.call_args.kwargs['user_content'])['visual_handoff'], handoff)
            visual.assert_called_once()
        self.assertEqual(json.loads((self.root/'email_self_review.json').read_text())['status'], 'failed')

    def test_smtp_failure_preserves_alert_eml_and_original_cause(self):
        report = {'scope': 'prepare', 'attempts': [{'number': 1, 'error': 'original image failure'}]}
        with patch.object(quality.smtplib, 'SMTP_SSL', side_effect=OSError('SMTP offline')):
            state = quality.send_failure_alert(self.paths, self.args, report)
        self.assertEqual(state['status'], 'failed')
        from email.parser import BytesParser
        from email.policy import default
        saved_email = BytesParser(policy=default).parsebytes((self.root/'prepare_failure_alert.eml').read_bytes())
        self.assertIn('original image failure', saved_email.get_content())
        self.assertIn('SMTP offline', state['reason'])

    def test_ambiguous_smtp_delivery_is_not_blindly_retried(self):
        report = {'scope': 'prepare', 'attempts': [{'number': 1, 'error': 'review failed'}]}
        smtp = Mock()
        smtp.__enter__ = Mock(return_value=smtp)
        smtp.__exit__ = Mock(return_value=False)
        smtp.send_message.side_effect = smtplib.SMTPServerDisconnected('connection lost after DATA')
        with patch.object(quality.smtplib, 'SMTP_SSL', return_value=smtp):
            first = quality.send_failure_alert(self.paths, self.args, report)
            second = quality.send_failure_alert(self.paths, self.args, report)
        self.assertEqual(first['status'], 'delivery_unknown')
        self.assertEqual(second['status'], 'delivery_unknown')
        smtp.send_message.assert_called_once()

    def test_owner_address_cannot_expand_to_a_distribution_list(self):
        with patch.dict(os.environ, {'AI_DAILY_ALERT_TO': 'owner@example.com, group@example.com'}):
            with self.assertRaises(quality.EmailQualityError):
                quality.alert_recipient(POLICY)

    def test_failed_prod_review_cannot_reach_packaging_or_smtp(self):
        with patch.object(runner, 'has_existing_prod_send', return_value=False), \
             patch.object(runner, 'check_production_email', side_effect=ValueError('bad final email')) as review, \
             patch.object(runner, 'repair_email') as repair, \
             patch.object(quality, 'send_failure_alert', return_value={'status': 'sent'}), \
             patch.object(runner, 'run_package') as package, patch.object(runner, 'run_send') as send:
            with self.assertRaises(quality.EmailQualityError):
                runner.run_prod(self.paths, self.args)
            self.assertEqual(review.call_count, 3)
            self.assertEqual(repair.call_count, 2)
            package.assert_not_called()
            send.assert_not_called()

    def test_partly_delivered_issue_cannot_be_regenerated(self):
        (self.root/'smtp_prod_delivery.json').write_text('{"sent":{"someone@example.com":{}}}')
        with patch.object(runner, 'run_prepare') as generate:
            with self.assertRaisesRegex(runner.WorkflowError, 'partly sent'):
                runner.repair_email(self.paths, self.args, 'editorial QA failed')
            generate.assert_not_called()

    def test_quality_transport_failure_retries_review_without_regeneration(self):
        with patch.object(runner, 'run_prepare') as generate:
            runner.repair_email(self.paths, self.args, 'Quality model request failed: timeout')
            generate.assert_not_called()

    def production_mocks(self, stack, *, mutate_package=False):
        self.paths.package_prod = self.root/'package'
        def review(*args):
            quality.write_json_atomic(self.root/'email_self_review.json', {
                'status': 'passed', 'hashes': quality.artifact_hashes(self.paths)})
        def package(*args):
            if mutate_package:
                self.paths.html.write_text('<p>Changed after review</p>')
            from email.message import EmailMessage
            message = EmailMessage()
            message.set_content('Reviewed test email')
            path = self.root/'package/email.eml'
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(message.as_bytes())
            return path
        values = {'has_existing_prod_send': False, 'record_phase': None,
                  'snapshot_delivery_metadata': {}, 'commit_issue_ledger': 1,
                  'commit_send_ledger': {}, 'sync_feedback_delivery': None,
                  'send_failure_alert': {'status': 'sent'}}
        for name, value in values.items():
            stack.enter_context(patch.object(runner, name, return_value=value))
        stack.enter_context(patch.object(runner, 'check_production_email', side_effect=review))
        stack.enter_context(patch.object(runner, 'run_package', side_effect=package))
        return stack.enter_context(patch.object(runner, 'run_send'))

    def test_successful_production_review_allows_exactly_one_delivery(self):
        with ExitStack() as stack:
            send = self.production_mocks(stack)
            runner.run_prod(self.paths, self.args)
            send.assert_called_once()

    def test_post_review_change_is_caught_immediately_before_smtp(self):
        with ExitStack() as stack:
            send = self.production_mocks(stack, mutate_package=True)
            with self.assertRaisesRegex(runner.WorkflowError, 'changed after self-review'):
                runner.run_prod(self.paths, self.args)
            send.assert_not_called()

    def test_scheduled_review_reuses_successful_generation(self):
        quality.write_json_atomic(self.root/'prepare_recovery.json', {'status': 'passed', 'attempts': []})
        with patch.object(runner, 'run_prepare') as generate, \
             patch.object(runner, 'run_api_preview') as render, \
             patch.object(runner, 'review_current_email', return_value={'status': 'passed'}) as review, \
             patch.object(runner, 'record_phase'):
            runner.run_prepare_reviewed(self.paths, self.args)
            generate.assert_not_called()
            render.assert_not_called()
            review.assert_called_once()

    def test_existing_scheduled_prepare_command_enters_reviewed_workflow(self):
        args = SimpleNamespace(action='prepare', scheduled=True, send=False, date='2026-10-12', work_calendar=None)
        # Stop at the calendar boundary after observing command migration.
        with patch.object(runner, 'parse_args', return_value=args), \
             patch.object(runner, 'workday_decision', return_value=(False, 'fixture')):
            runner.main()
        self.assertEqual(args.action, 'prepare-reviewed')

    def test_pending_gateway_authorization_never_calls_model(self):
        with patch.object(quality, 'policy', return_value={**POLICY, 'ai_review_enabled': False}), \
             patch.object(gate, 'call_quality_model') as model:
            with self.assertRaisesRegex(quality.EmailQualityError, 'authorization'):
                quality.review_current_email(self.paths, self.args)
            model.assert_not_called()

    def test_repair_feedback_does_not_break_research_cache_or_image_prompt_lineage(self):
        from generate_research_brief_api import load_system_prompt, user_prompt
        from generate_research_image import load_prompt
        from research_cache import build_cache_contract
        from stage_research_image import validate_generation_prompt
        import hashlib
        prompt_path = self.root/'prompt.txt'
        prompt_path.write_text('固定生成规则\n')
        artifacts = {name: prompt_path for name in ('brief_json', 'image_prompt', 'raw_output', 'brief_markdown')}
        def contract():
            return build_cache_contract(source={'source_text': 'source'},
                system_prompt=load_system_prompt(prompt_path), artifacts=artifacts)
        before = contract()
        with quality.repair_feedback('修正图中主箭头方向'):
            self.assertIn('修正图中主箭头方向', user_prompt({'source_text': 'source'}))
            self.assertEqual(before, contract())
            actual_request = load_prompt(prompt_path)
            validate_generation_prompt({'prompt_sha256': hashlib.sha256(actual_request.encode()).hexdigest()}, prompt_path)
        self.assertEqual(before, contract())

    def test_preview_feedback_is_excluded_but_body_links_are_reviewed(self):
        self.paths.html.write_text('<body><p>最终可见正文</p><a href="https://example.com/news">正文来源</a>'
            '<div data-ai-daily-feedback="1"><a href="#" data-ai-daily-rating="1">☆</a>'
            '<a href="#" data-ai-daily-comment="1">写一句建议</a></div></body>')
        with ExitStack() as stack:
            model = self.review_patches(stack)
            quality.review_current_email(self.paths, self.args)
            packet = json.loads(model.call_args.kwargs['user_content'])
        self.assertNotIn('写一句建议', packet['final_email_text'])
        self.assertEqual(packet['links'], [{'label': '正文来源', 'url': 'https://example.com/news'}])

    def test_actual_packaged_feedback_requires_non_placeholder_urls(self):
        from email.message import EmailMessage
        eml = self.root/'email.eml'
        for url, valid in [('#', False), ('', False), ('javascript:alert(1)', False), ('https://feedback.example.com/f/token?score=5', True)]:
            message = EmailMessage()
            message.set_content(f'<a data-ai-daily-rating="5" href="{url}">☆</a>', subtype='html')
            eml.write_bytes(message.as_bytes())
            if valid:
                quality.validate_packaged_feedback(eml)
            else:
                with self.assertRaisesRegex(quality.EmailQualityError, 'placeholder'):
                    quality.validate_packaged_feedback(eml)


if __name__ == '__main__':
    unittest.main()
