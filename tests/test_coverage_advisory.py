"""Delivery must not depend on settling every unselected discovery lead."""
from copy import deepcopy
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import automatic_delivery as auto
from coverage_review import assert_ready, refresh_review, CoverageReviewError
from email_quality_control import EmailQualityError

POLICY = {'coverage_review': {'enabled': True, 'required_from': '2026-01-01',
                             'omission_policy': 'advisory'},
          'source_verification': {'bestblogs_featured_min_score': 90}}


class CoverageAdvisoryTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        self.paths = SimpleNamespace(run_dir=root, run_date='2026-10-12',
            source_selection=root/'selection.json', normalized_content=root/'content.json')
        self.review = root/'high_value_coverage_review.json'
        self.args = SimpleNamespace(ai_runtime_config=None)
        self.item = {'title': 'Independent research', 'primary_url': 'https://example.org/study',
                     'bestblogs_score': 94, 'primary_verification': {'status': 'unresolved'}}
        self.window = {'start_at': '2026-10-08T08:30:00+08:00', 'end_at': '2026-10-12T08:30:00+08:00'}

    def save(self, item=None, *, chosen=False, included=False, in_pool=True):
        item = item or self.item
        self.paths.source_selection.write_text(json.dumps({'window': self.window,
            'all_candidates_scored': [item] if in_pool else [],
            'selected_news': [item] if chosen else []}))
        self.paths.normalized_content.write_text(json.dumps({'news':
            [{'title': item['title'], 'url': item['primary_url'], 'boundary': '原始发布时间存在差异。'}] if included else []}))

    def ready(self, policy=POLICY):
        return assert_ready(self.paths.source_selection, self.paths.normalized_content, self.review, policy)

    def test_unverified_unselected_lead_is_pending_advisory_without_model_call(self):
        self.save()
        with patch('daily_config.load_selection_policy', return_value=POLICY), \
             patch('quality_review.qa_runtime') as runtime, \
             patch('quality_review.call_quality_model') as model:
            auto.review_coverage(self.paths, self.args)
            runtime.assert_not_called()
            model.assert_not_called()
        report = self.ready()
        self.assertEqual(report['summary']['blocking_pending'], 0)
        self.assertEqual(report['summary']['advisory_pending'], 1)
        self.assertEqual(report['items'][0]['status'], 'pending')
        self.assertEqual(report['items'][0]['editor'], '')
        self.assertIsNone(report['items'][0]['automated_review'])

    def test_unselected_timing_conflict_does_not_become_a_mail_error(self):
        self.item['primary_verification']['date_conflict'] = True
        self.save()
        self.assertEqual(self.ready()['summary']['advisory_pending'], 1)

    def test_missing_selected_ordinary_item_blocks_even_outside_scored_pool(self):
        item = {'title': 'An ordinary selected story', 'primary_url': 'https://example.org/ordinary'}
        self.save(item, chosen=True, in_pool=False)
        with self.assertRaises(CoverageReviewError):
            self.ready()
        with patch('daily_config.load_selection_policy', return_value=POLICY), \
             patch('quality_review.call_quality_model') as model:
            with self.assertRaisesRegex(EmailQualityError, 'selected content missing'):
                auto.review_coverage(self.paths, self.args)
            model.assert_not_called()

    def test_exclusion_note_cannot_waive_selected_item_lost_in_generation(self):
        self.save(chosen=True)
        report = refresh_review(self.paths.source_selection, self.paths.normalized_content, self.review, POLICY)
        report['items'][0].update(disposition='exclude', reason='The topic is covered by another edition.', editor='test')
        self.review.write_text(json.dumps(report))
        with self.assertRaises(CoverageReviewError):
            self.ready()

    def test_included_timing_conflict_still_requires_actual_review(self):
        self.item.pop('bestblogs_score')
        self.item['primary_verification'] = {'status': 'verified', 'date_conflict': True}
        self.save(chosen=True, included=True)
        with self.assertRaises(CoverageReviewError):
            self.ready()
        report = json.loads(self.review.read_text())
        row = report['items'][0]
        self.assertNotIn('selected_but_missing', row['review_issues'])
        response = {'decision': 'pass', 'issues': [], 'items': [{'id': row['id'],
            'disposition': 'retain_with_timing_note', 'reason': '邮件已明确原始发布时间存在差异，未将其包装为当日首发。'}]}
        with patch('daily_config.load_selection_policy', return_value=POLICY), \
             patch('quality_review.qa_runtime'), \
             patch('quality_review.call_quality_model', return_value=(response, {})) as model:
            auto.review_coverage(self.paths, self.args)
            request = json.loads(model.call_args.kwargs['user_content'])
            self.assertEqual(request['selection_window'], self.window)
        self.assertEqual(self.ready()['summary']['blocking_pending'], 0)

    def test_failed_included_timing_review_still_blocks(self):
        self.item['primary_verification']['date_conflict'] = True
        self.save(chosen=True, included=True)
        with patch('daily_config.load_selection_policy', return_value=POLICY), \
             patch('quality_review.qa_runtime'), \
             patch('quality_review.call_quality_model', return_value=({'decision': 'fail', 'issues': ['Wrong release date'], 'items': []}, {})):
            with self.assertRaises(EmailQualityError):
                auto.review_coverage(self.paths, self.args)

    def test_selected_story_present_with_clean_timing_needs_no_omission_review(self):
        self.save(chosen=True, included=True)
        self.assertEqual(self.ready()['summary']['pending'], 0)

    def test_advisory_policy_does_not_waive_final_source_violations(self):
        self.save()
        with patch('coverage_review.validate_final_selection', return_value=['Unselected/unscored final source']):
            with self.assertRaises(CoverageReviewError):
                self.ready()

    def test_legacy_policy_remains_strict_and_unknown_mode_is_rejected(self):
        self.save()
        for mode in ['blocking', 'typo']:
            policy = deepcopy(POLICY)
            policy['coverage_review']['omission_policy'] = mode
            with self.subTest(mode=mode), self.assertRaises(CoverageReviewError):
                self.ready(policy)


if __name__ == '__main__':
    unittest.main()
