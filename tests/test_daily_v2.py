"""V2 release contracts and fault injection; all delivery outcomes are simulated."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from email import policy
from email.parser import BytesParser
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from daily_v2.contracts import GateError, coverage_window, digest, normalize, preflight, recipients, subject
from daily_v2.ledger import Ledger
from daily_v2.workbench import build_document, check_document, edit_content, export_preview
from daily_v2.execution import run_unit, UnitFailure, review_with_provider


def fixture(count=5):
    cards = [{"title": f"第{i}项智能体评测更新", "one_liner": f"团队公开第{i}项测试结果与测试条件。",
              "why": "帮助区分工具执行与回答质量。", "observation": "对照相同任务检查输出。",
              "category": "industry_signal", "url": f"https://example.org/source/{i}"} for i in range(count)]
    raw = {"issue": 45, "editor_note": "本期关注可复现评测及适用条件。", "news": cards}
    materials = {"news": [{"primary_url": c['url'], "source_text": c['one_liner'], "fetch_status": "ok"} for c in cards]}
    return raw, materials


class V2Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.raw, cls.materials = fixture()
        cls.doc = build_document(cls.raw, '2026-09-15', ['one@example.invalid', 'two@example.invalid'],
                                 materials=cls.materials, selection={'coverage_cutoff': '2026-09-15T08:30:00+08:00'})

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'ledger.sqlite3'
        self.ledger = Ledger(self.path)

    def tearDown(self):
        self.ledger.close()
        self.temp.cleanup()

    def reviewed(self, doc=None):
        rid = self.ledger.freeze(doc or self.doc)
        record = {'target_hash': self.ledger.revision(rid)['document_hash'], 'kind': 'human', 'actor': 'test-fixture',
                  'notes': 'Synthetic source fixture checked for this test only', 'result': 'pass', 'issues': []}
        self.ledger.record_review(rid, record)
        return rid

    def approved(self, doc=None):
        rid = self.reviewed(doc)
        return self.ledger.approve(rid, 'test-fixture', acknowledge=(doc or self.doc)['preflight']['warnings'])

    def test_structural_preflight_and_static_text(self):
        self.assertEqual(self.doc['preflight']['errors'], [])
        self.assertNotIn('<img', self.doc['html'])
        self.assertNotIn('data-ai-daily-feedback', self.doc['html'])
        self.assertEqual(self.doc['subject'], 'AI 情报雷达｜2026年第38周')

    def test_iso_year_boundary(self):
        self.assertEqual(subject('2027-01-01'), 'AI 情报雷达｜2026年第53周')

    def test_legacy_issue_uses_explicit_subtitle_without_advancing_counter(self):
        raw = deepcopy(self.raw)
        raw.pop('issue')
        raw['subtitle'] = '试运行第 14 期：精选 AI 行业动态'
        doc = build_document(raw, '2026-07-13', ['one@example.invalid'], materials=self.materials)
        self.assertEqual(doc['content']['issue'], 14)
        self.assertEqual(doc['issue_number_source'], 'legacy_subtitle')
        raw['subtitle'] = '没有刊号'
        with self.assertRaises(GateError):
            build_document(raw, '2026-07-13', ['one@example.invalid'])

    def test_recipients_normalized_and_header_injection_rejected(self):
        self.assertEqual(recipients([' A@EXAMPLE.COM ', 'a@example.com']), ['a@example.com'])
        for address in ('x@example.com\nBcc:y@example.com', 'Name <a@example.com>', ''):
            with self.assertRaises(GateError):
                recipients([address])

    def test_empty_explicit_research_does_not_resurrect_legacy_paper(self):
        raw = {**self.raw, 'research_items': [], 'paper': {'title': 'stale'}}
        self.assertEqual(normalize(raw)['deep_features'], [])

    def test_freeze_idempotent_and_immutable(self):
        rid = self.ledger.freeze(self.doc)
        self.assertEqual(rid, self.ledger.freeze(deepcopy(self.doc)))
        with self.assertRaises(sqlite3.IntegrityError):
            self.ledger.db.execute("UPDATE revisions SET document='{}' WHERE id=?", (rid,))

    def test_modified_revision_invalidates_old_approval(self):
        release = self.approved()
        doc = deepcopy(self.doc)
        doc['recipients'] = ['three@example.invalid']
        doc['preflight'] = check_document(doc)
        rid = self.ledger.freeze(doc)
        self.assertEqual(self.ledger.release(release)['status'], 'SUPERSEDED')
        with self.assertRaises(GateError):
            self.ledger.claim(release)
        with self.assertRaises(GateError):
            self.ledger.approve(rid, 'test-fixture')

    def test_old_revision_cannot_be_approved(self):
        rid = self.reviewed()
        doc = deepcopy(self.doc)
        doc['recipients'] = ['three@example.invalid']
        self.ledger.freeze(doc)
        with self.assertRaises(GateError):
            self.ledger.approve(rid, 'fixture')

    def test_review_hash_must_match(self):
        rid = self.ledger.freeze(self.doc)
        with self.assertRaises(GateError):
            self.ledger.record_review(rid, {'target_hash': 'wrong', 'kind': 'human', 'result': 'pass'})

    def test_pending_review_cannot_approve(self):
        rid = self.ledger.freeze(self.doc)
        with self.assertRaises(GateError):
            self.ledger.approve(rid, 'fixture')

    def test_later_failed_review_overrides_earlier_pass(self):
        rid = self.reviewed()
        self.ledger.record_review(rid, {'target_hash': self.ledger.revision(rid)['document_hash'], 'kind': 'human',
            'actor': 'fixture', 'notes': 'found a conflicting fact', 'result': 'revise', 'issues': []})
        with self.assertRaises(GateError):
            self.ledger.approve(rid, 'fixture')

    def test_preflight_recomputed_at_approval(self):
        doc = deepcopy(self.doc)
        doc['html'] += '<script>alert(1)</script>'
        rid = self.reviewed(doc)
        with self.assertRaises(GateError):
            self.ledger.approve(rid, 'fixture')

    def test_missing_evidence_blocks_even_with_human_pass(self):
        doc = deepcopy(self.doc)
        doc['evidence'] = {}
        doc['preflight'] = check_document(doc)
        rid = self.reviewed(doc)
        with self.assertRaises(GateError):
            self.ledger.approve(rid, 'fixture')

    def test_news_count_warning_requires_acknowledgement(self):
        raw, materials = fixture(2)
        doc = build_document(raw, '2026-09-15', ['one@example.invalid'], materials=materials)
        rid = self.reviewed(doc)
        with self.assertRaises(GateError):
            self.ledger.approve(rid, 'fixture')
        self.ledger.approve(rid, 'fixture', acknowledge=doc['preflight']['warnings'])

    def test_zero_news_is_blocked(self):
        raw, materials = fixture(0)
        doc = build_document(raw, '2026-09-15', ['one@example.invalid'], materials=materials)
        self.assertTrue(any('news_count' in e for e in doc['preflight']['errors']))

    def test_draft_not_counted_as_published(self):
        self.approved()
        self.assertEqual(self.ledger.published_identities(), set())

    def test_only_accepted_delivery_counts_for_publication(self):
        release = self.approved()
        claim = self.ledger.claim(release)
        self.ledger.finish(claim, 'ACCEPTED', 'simulation')
        self.assertEqual(len(self.ledger.published_identities()), 5)

    def test_concurrent_publishers_only_one_claim(self):
        release = self.approved()
        barrier = threading.Barrier(2)
        def worker():
            ledger = Ledger(self.path)
            try:
                barrier.wait()
                return ledger.claim(release)
            except GateError:
                return None
            finally:
                ledger.close()
        with ThreadPoolExecutor(2) as pool:
            results = list(pool.map(lambda _: worker(), range(2)))
        self.assertEqual(sum(r is not None for r in results), 1)

    def test_unknown_stops_all_automatic_retries(self):
        release = self.approved()
        claim = self.ledger.claim(release)
        self.ledger.finish(claim, 'UNKNOWN', 'simulated disconnect after DATA')
        self.assertEqual(self.ledger.summary(release)['status'], 'SEND_UNCERTAIN')
        with self.assertRaises(GateError):
            self.ledger.claim(release)

    def test_crash_recovery_requires_evidence_and_stale_worker_rejected(self):
        release = self.approved()
        old_claim = self.ledger.claim(release)
        reopened = Ledger(self.path)
        try:
            with self.assertRaises(GateError):
                reopened.claim(release)
            reopened.reconcile(release, old_claim['recipient_id'], 'REFUSED', 'fixture', 'relay log confirms not accepted')
            new_claim = reopened.claim(release)
            self.assertNotEqual(old_claim['attempt_id'], new_claim['attempt_id'])
            with self.assertRaises(GateError):
                reopened.finish(old_claim, 'ACCEPTED', 'stale worker')
        finally:
            reopened.close()

    def test_only_refused_is_retried(self):
        release = self.approved()
        claim = self.ledger.claim(release)
        self.ledger.finish(claim, 'REFUSED', 'simulated explicit refusal')
        retry = self.ledger.claim(release)
        self.assertEqual(claim['recipient_id'], retry['recipient_id'])
        self.ledger.finish(retry, 'ACCEPTED', 'simulation')
        following = self.ledger.claim(release)
        self.assertNotEqual(retry['recipient_id'], following['recipient_id'])

    def test_feedback_failure_does_not_change_delivery(self):
        release = self.approved()
        for _ in range(2):
            self.ledger.finish(self.ledger.claim(release), 'ACCEPTED', 'simulation')
        self.ledger.sync_feedback(Mock(side_effect=ConnectionError('offline')))
        self.assertEqual(self.ledger.summary(release)['status'], 'SENT')
        self.assertEqual(self.ledger.summary(release)['feedback_pending'], 2)
        sink = {}
        self.ledger.sync_feedback(lambda r: sink.update({(r['release_id'], r['recipient_id']): r}))
        self.ledger.sync_feedback(lambda r: sink.update({(r['release_id'], r['recipient_id']): r}))
        self.assertEqual(len(sink), 2)
        self.assertEqual(self.ledger.summary(release)['feedback_pending'], 0)

    def test_mime_is_frozen_and_individual(self):
        release = self.approved()
        messages = self.ledger.db.execute('SELECT * FROM messages WHERE release_id=?', (release,)).fetchall()
        for row in messages:
            msg = BytesParser(policy=policy.default).parsebytes(row['mime'])
            self.assertEqual(str(msg['To']), row['address'])
            self.assertEqual(str(msg['Subject']), self.doc['subject'])
            self.assertEqual(str(msg['X-AI-Daily-Mode']), 'shadow')
            self.assertEqual(len([p for p in msg.walk() if p.get_content_type() == 'text/html']), 1)
        with self.assertRaises(sqlite3.IntegrityError):
            self.ledger.db.execute("UPDATE messages SET mime=x'00'")

    def test_weekly_root_is_per_recipient_first_accepted_not_monday(self):
        release = self.approved()
        claim = self.ledger.claim(release)
        self.ledger.finish(claim, 'ACCEPTED', 'Tuesday accepted')
        doc = build_document(self.raw, '2026-09-16', self.doc['recipients'], materials=self.materials)
        following = self.approved(doc)
        for row in self.ledger.db.execute('SELECT * FROM messages WHERE release_id=?', (following,)):
            msg = BytesParser(policy=policy.default).parsebytes(row['mime'])
            self.assertEqual(bool(msg.get('In-Reply-To')), row['recipient_id'] == claim['recipient_id'])

    def test_reissue_requires_reason_and_no_uncertain_predecessor(self):
        release = self.approved()
        claim = self.ledger.claim(release)
        self.ledger.finish(claim, 'UNKNOWN', 'simulation')
        doc = deepcopy(self.doc)
        doc['recipients'] = ['three@example.invalid']
        rid = self.reviewed(doc)
        with self.assertRaises(GateError):
            self.ledger.approve(rid, 'fixture', reissue_of=release, reason='correction')
        self.ledger.reconcile(release, claim['recipient_id'], 'ACCEPTED', 'fixture', 'accepted in relay log')
        self.ledger.approve(rid, 'fixture', reissue_of=release, reason='correction')
        with self.assertRaises(GateError):
            self.ledger.claim(release)

    def test_backup_retains_accepted_and_unknown(self):
        release = self.approved()
        self.ledger.finish(self.ledger.claim(release), 'ACCEPTED', 'simulation')
        self.ledger.finish(self.ledger.claim(release), 'UNKNOWN', 'simulation')
        backup = Path(self.temp.name) / 'backup.sqlite3'
        self.ledger.backup(backup)
        restored = Ledger(backup)
        try:
            self.assertEqual(restored.summary(release)['counts'], {'ACCEPTED': 1, 'UNKNOWN': 1})
            with self.assertRaises(GateError):
                restored.claim(release)
        finally:
            restored.close()

    def test_preview_export_redacts_recipient_addresses(self):
        rid = self.ledger.freeze(self.doc)
        index = export_preview(self.ledger, rid, Path(self.temp.name) / 'previews')
        self.assertNotIn('one@example.invalid', index.read_text())
        self.assertNotIn('one@example.invalid', index.with_name('manifest.preview.json').read_text())

    def test_edit_transaction_and_order(self):
        source = normalize(self.raw)
        ids = [c['card_id'] for c in source['news']]
        edited, audit = edit_content(source, [{'op': 'reorder', 'card_ids': list(reversed(ids))}], actor='fixture', reason='promote lead')
        self.assertEqual(source['news'][0]['card_id'], ids[0])
        doc = build_document(edited, '2026-09-15', ['one@example.invalid'], materials=self.materials)
        from bs4 import BeautifulSoup
        lead = BeautifulSoup(doc['html'], 'html.parser').select_one('#np-lead')
        self.assertIn(self.raw['news'][-1]['title'], lead.get_text())
        self.assertTrue(audit['requires_new_preview_and_review'])
        with self.assertRaises(GateError):
            edit_content(source, [{'op': 'remove', 'card_id': ids[0]}, {'op': 'reorder', 'card_ids': ids}], actor='fixture', reason='invalid transaction')
        self.assertEqual(len(source['news']), 5)

    def test_unsafe_markup_broken_anchor_and_duplicates_block(self):
        for markup, expected in [('<a href="javascript:alert(1)">x</a>', 'unsafe_link'),
                                 ('<a href="#missing">x</a>', 'broken_anchor'),
                                 ('<div onclick="bad()">x</div>', 'html_event_handler'),
                                 ('<div style="background:url(https://example.org/x)">x</div>', 'external_or_active_css')]:
            check = preflight(self.doc['content'], self.doc['html'] + markup, '2026-09-15', self.doc['recipients'])
            self.assertIn(expected, check['errors'])

    def test_coverage_uses_selection_cutoff_and_bounds_missing_gap(self):
        window = coverage_window('2026-09-14T08:30:00+08:00', '2026-09-11T08:30:00+08:00')
        self.assertEqual(window['hours'], 72)
        long = coverage_window('2026-09-14T08:30:00+08:00', '2026-09-07T08:30:00+08:00')
        self.assertEqual(long['hours'], 96)
        self.assertEqual(long['uncovered_gap_hours'], 72)
        self.assertFalse(coverage_window('2026-09-14T08:30:00+08:00')['previous_cutoff_known'])

    def unit(self, call, **kwargs):
        inputs = {'evidence': 'bounded evidence', 'model': 'gpt-5.6-sol', 'provider': 'fixture', 'prompt_version': 'v1', 'schema_version': 2, 'task': 'card'}
        return run_unit(self.ledger, inputs, call, lambda r: None, sleep=lambda _: None, **kwargs)

    def test_successful_card_checkpoint_not_regenerated(self):
        call = Mock(return_value={'title': '有效稿件'})
        self.assertFalse(self.unit(call)['cached'])
        self.assertTrue(self.unit(call)['cached'])
        self.assertEqual(call.call_count, 1)

    def test_schema_repair_once_and_budget_survives_retry(self):
        call = Mock(side_effect=UnitFailure('schema'))
        self.assertEqual(self.unit(call)['status'], 'DROPPED')
        self.assertEqual(call.call_count, 2)
        self.unit(call)
        self.assertEqual(call.call_count, 2)

    def test_timeout_then_success_and_must_include_failure(self):
        call = Mock(side_effect=[TimeoutError(), {'title': '成功'}])
        self.assertEqual(self.unit(call)['status'], 'SUCCESS')
        self.assertEqual(call.call_count, 2)
        other = Ledger(Path(self.temp.name) / 'other.sqlite3')
        try:
            inputs = {'evidence': 'x', 'model': 'gpt-5.6-sol', 'provider': 'fixture', 'prompt_version': 'v1', 'schema_version': 2, 'task': 'card'}
            result = run_unit(other, inputs, Mock(side_effect=UnitFailure('input')), lambda _: None, must_include=True, sleep=lambda _: None)
            self.assertEqual(result['status'], 'BLOCKED')
        finally:
            other.close()

    def test_nonretryable_failure_does_not_resume_unchanged(self):
        call = Mock(side_effect=UnitFailure('input'))
        self.unit(call)
        self.unit(call)
        self.assertEqual(call.call_count, 1)

    def test_interrupted_model_call_is_not_blindly_repeated(self):
        call = Mock(side_effect=KeyboardInterrupt())
        with self.assertRaises(KeyboardInterrupt):
            self.unit(call)
        result = self.unit(call)
        self.assertEqual(result['reason'], 'unit_in_flight_or_interrupted')
        self.assertEqual(call.call_count, 1)

    def test_independent_review_uses_response_identity_without_env_loading(self):
        from ai_gateway_client import ProviderRuntime
        provider = ProviderRuntime('text', 'https://example.invalid/v1', 'fixture-key', 'gpt-5.6-sol', 'chat/completions', 10, 3, {})
        rid = self.ledger.freeze(self.doc)
        response = {'model': 'gpt-5.6-sol', 'choices': [{'message': {'content': json.dumps({'result': 'pass', 'notes': 'synthetic fixture only', 'issues': []})}}]}
        with patch('ai_gateway_client.request_json', return_value=(response, {}, 200)) as request, patch('ai_gateway_client.load_env_file') as env:
            review_id = review_with_provider(self.ledger, rid, provider)
        env.assert_not_called()
        self.assertEqual(request.call_args.args[0].max_retries, 0)
        record = json.loads(self.ledger.db.execute('SELECT record FROM reviews WHERE id=?', (review_id,)).fetchone()[0])
        self.assertEqual(record['execution']['identity_source'], 'response')
        self.assertNotIn('fixture-key', json.dumps(record))

    def test_different_returned_model_cannot_silently_pass_review(self):
        from ai_gateway_client import ProviderRuntime
        provider = ProviderRuntime('text', 'https://example.invalid/v1', 'fixture-key', 'gpt-5.6-sol', 'chat/completions', 10, 0, {})
        rid = self.ledger.freeze(self.doc)
        with patch('ai_gateway_client.request_json', return_value=({'model': 'unapproved-model'}, {}, 200)) as request:
            review_id = review_with_provider(self.ledger, rid, provider)
        self.assertEqual(request.call_count, 1)
        record = json.loads(self.ledger.db.execute('SELECT record FROM reviews WHERE id=?', (review_id,)).fetchone()[0])
        self.assertEqual(record['result'], 'unavailable')
        with self.assertRaises(GateError):
            self.ledger.approve(rid, 'fixture')


if __name__ == '__main__':
    unittest.main()
