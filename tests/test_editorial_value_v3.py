import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch, Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from test_event_editorial import assessment, packet, candidate, POLICY
from event_editorial import checked_policy, validate_assessment, eligibility, EditorialError, assessment_policy, validate_final_selection
from coverage_review import refresh_review, assert_ready, CoverageReviewError
from quiet_day_selection import select_quiet_day
from quiet_day_recovery import recover_pending
from event_editorial_runtime import Evaluator

V3 = checked_policy({**POLICY, 'version': 'event-editorial-v3.0',
                    'admission_contract': 'value-evidence-v3', 'brief_value_floor': 60,
                    'quiet_day': {'enabled': False}})


def scored(levels=(3, 2, 3, 2), recommendation='include', **fields):
    raw = assessment()
    raw.update(editorial_recommendation=recommendation, **fields)
    raw['novelty_basis'] = {'kind': 'new_findings', 'reason': '原文提供了具体的迁移回放验证实践及结果。', 'evidence_refs': [0]}
    for key, level in zip(POLICY['weights'], levels):
        raw['dimensions'][key]['level'] = level
    return validate_assessment(raw, packet(), V3)


class ValueAdmissionTests(unittest.TestCase):
    def test_sixty_point_news_policy_admits_useful_briefs_without_exception(self):
        policy = checked_policy({**V3, 'version':'event-editorial-v3.1', 'publish_threshold':60})
        for levels in [(3,2,3,2),(2,2,3,3)]:
            a = validate_assessment(scored(levels), packet(), policy)
            self.assertEqual(eligibility(a, policy)[0], 'include')
        self.assertEqual(eligibility(validate_assessment(scored((2,2,2,2)),packet(),policy),policy)[0], 'defer')
        a = scored(); a.update(novelty='unclear',editorial_recommendation='defer')
        self.assertEqual(eligibility(validate_assessment(a,packet(),policy),policy)[0],'defer')
        with self.assertRaises(EditorialError):
            checked_policy({**policy,'brief_value_floor':59})

    def test_concrete_product_change_admitted_without_method_novelty(self):
        a = scored((3, 2, 4, 2), evidence_status='limited', content_kind='model_product')
        self.assertEqual(a['score'], 70)  # Arithmetic is unchanged; route is separate.
        self.assertEqual(a['admission_route'], 'substantive_change')
        self.assertEqual(eligibility(a, V3)[0], 'include')
        balanced = scored((3, 3, 3, 2), content_kind='model_product')
        self.assertEqual(balanced['score'], 70)
        self.assertEqual(eligibility(balanced, V3)[0], 'include')

    def test_controlled_safety_result_need_not_prove_real_world_harm(self):
        a = scored((3, 2, 3, 4), evidence_status='limited', content_kind='research')
        self.assertEqual(a['score'], 74)
        self.assertEqual(eligibility(a, V3)[0], 'include')
        b = scored((2, 2, 3, 4), content_kind='research')
        self.assertEqual(b['admission_route'], 'substantive_learning')
        self.assertEqual(eligibility(b, V3)[0], 'include')

    def test_minor_update_rumour_and_low_reader_value_not_promoted(self):
        for levels in [(2, 2, 2, 2), (1, 1, 2, 1), (3, 2, 1, 3), (3, 1, 3, 2), (3, 2, 3, 0), (3, 2, 3, 3), (2, 2, 3, 3)]:
            with self.subTest(levels=levels):
                self.assertNotEqual(eligibility(scored(levels), V3)[0], 'include')

    def test_unknown_and_unreadable_material_still_block(self):
        self.assertEqual(eligibility(scored((3, None, 3, 3)), V3)[0], 'defer')
        for state in ['unresolved', 'conflicting']:
            with self.assertRaises(EditorialError): scored(evidence_status=state)
        p = packet(); p['sources'][0]['read_status'] = 'discovery_only'
        with self.assertRaises(EditorialError): validate_assessment(scored(), p, V3)

    def test_repeated_or_unclear_event_cannot_recommend_publication(self):
        for novelty in ['repeat', 'unclear']:
            with self.assertRaises(EditorialError): scored(novelty=novelty)

    def test_disagreement_defers_instead_of_closing_as_excluded(self):
        self.assertEqual(eligibility(scored((2, 2, 2, 2)), V3)[0], 'defer')
        self.assertEqual(eligibility(scored((3, 3, 3, 3), 'exclude'), V3)[0], 'defer')
        self.assertEqual(eligibility(scored(recommendation='defer'), V3)[0], 'defer')
        self.assertEqual(eligibility(scored(recommendation='exclude'), V3)[0], 'exclude')

    def test_policy_changes_invalidate_cache_and_old_assessment_needs_rescoring(self):
        self.assertNotEqual(assessment_policy(POLICY), assessment_policy(V3))
        with self.assertRaises(EditorialError): validate_assessment(assessment(), packet(), V3)
        self.assertEqual(eligibility(candidate()['editorial_assessment'], V3)[0], 'defer')

    def test_value_admission_does_not_depend_on_day_size_or_aggregate_heat(self):
        def item(i, levels):
            c = candidate(str(i)); p = c['editorial_packet']; a = scored(levels)
            a['event_id'] = str(i); c['editorial_assessment'] = a
            return c
        focus = item('focus', (3, 2, 4, 2))
        others = [item(i, (3, 3, 3, 3)) for i in range(20)]
        for items in [[focus], others + [focus]]:
            _, news, _, audit = select_quiet_day(items, V3, {}, None, research_count=0)
            self.assertIn('focus', {c['event_id'] for c in news})
            self.assertFalse(audit['triggered'])

    def test_generic_document_cannot_claim_new_event_without_novelty_evidence(self):
        a = scored(); a['novelty_basis']['kind'] = 'none'
        with self.assertRaises(EditorialError): validate_assessment(a, packet(), V3)
        a.update(novelty='unclear', editorial_recommendation='defer')
        self.assertEqual(eligibility(validate_assessment(a, packet(), V3), V3)[0], 'defer')

    def test_new_route_passes_final_gate_but_policy_and_route_tampering_fail(self):
        c = candidate(); c['editorial_assessment'] = scored((3, 2, 4, 2))
        s = {'event_editorial': {'policy': V3}, 'selected_news': [c]}
        content = {'news': [{'url': c['primary_url']}]}
        self.assertEqual(validate_final_selection(s, content), [])
        for key in ['admission_route', 'policy_sha256']:
            bad = copy.deepcopy(s); bad['selected_news'][0]['editorial_assessment'][key] = 'forged'
            self.assertTrue(validate_final_selection(bad, content))

    def test_historical_full_policy_hash_remains_valid(self):
        c = candidate(); c['editorial_assessment']['policy_sha256'] = __import__('event_editorial').digest(POLICY)
        self.assertEqual(validate_final_selection({'event_editorial': {'policy': POLICY}, 'selected_news': [c]},
                                                 {'news': [{'url':c['primary_url']}]}), [])

    def test_catchup_retains_dates_without_day_size_trigger_or_research_laundering(self):
        c = candidate(); c['editorial_assessment'] = scored((3, 3, 3, 3))
        c.update(published_at='2026-09-29T06:00:00+08:00', editorial_news_window_eligible=False,
                 research_window_strategy='unpublished_backlog')
        window = {'end_at':'2026-09-30T08:30:00+08:00'}
        _, news, _, audit = select_quiet_day([c], V3, window, None, research_count=0)
        self.assertEqual(len(news),1)
        self.assertEqual(news[0]['quiet_day_admission']['original_published_at'],c['published_at'])
        self.assertFalse(audit['triggered'])
        for change in [{'research_window_strategy':'paper_lookback'}, {'published_at':'2026-09-20T06:00:00+08:00'},
                       {'backlog_status':'published'}, {'content_duplicate_check':{'is_duplicate':True}}]:
            bad = {**c, **change}
            self.assertEqual(select_quiet_day([bad],V3,window,None,research_count=0)[1], [])


class OmissionReviewTests(unittest.TestCase):
    def run_review(self, item, folder, old=None):
        s, c, r = [Path(folder) / name for name in ['s.json', 'c.json', 'r.json']]
        s.write_text(json.dumps({'event_editorial': {'policy': V3}, 'all_candidates_scored': [item]}))
        c.write_text(json.dumps({'news': []}))
        if old: r.write_text(json.dumps(old))
        return refresh_review(s, c, r), (s, c, r)

    def item(self, recommendation='exclude'):
        c = candidate(); c['editorial_assessment'] = scored(recommendation=recommendation)
        c.update(bestblogs_score=94, editorial_decision={'action': 'exclude',
                 'reason': '这是单项局部变动且缺少相对已有能力的实质增量，不建议单独报道。'})
        return c

    def test_recommendation_cannot_auto_resolve_exclusion(self):
        with tempfile.TemporaryDirectory() as d:
            report, paths = self.run_review(self.item('include'), d)
            self.assertEqual(report['summary']['pending'], 1)
            self.assertEqual(assert_ready(*paths)['summary']['advisory_pending'], 1)

    def test_missing_merged_material_or_primary_failure_stays_pending(self):
        for kind in ['packet', 'verification']:
            c = self.item()
            if kind == 'packet':
                c['editorial_packet']['sources'].append({'source_id': 'missing', 'read_status': 'discovery_only'})
            else: c['primary_verification'] = {'status': 'unresolved'}
            with tempfile.TemporaryDirectory() as d:
                report, _ = self.run_review(c, d)
                self.assertEqual(report['summary']['pending'], 1)

    def test_legacy_model_exclusion_is_reopened_not_carried_forward(self):
        c = self.item(); c['editorial_assessment'].pop('editorial_recommendation')
        with tempfile.TemporaryDirectory() as d:
            report, _ = self.run_review(c, d)
            row = report['items'][0]
            row.update(status='resolved', disposition='exclude', editor='event-evaluator', reason='推荐作为重要新闻条目，在报道中标注其研究边界。')
            report, _ = self.run_review(c, d, report)
            self.assertEqual(report['summary']['pending'], 1)

    def test_readable_explicit_low_value_exclusion_can_resolve(self):
        with tempfile.TemporaryDirectory() as d:
            report, _ = self.run_review(self.item(), d)
            self.assertEqual(report['summary']['pending'], 0)

    def test_new_recommendation_is_reviewed_without_brand_or_popularity(self):
        c = self.item('include'); c.pop('bestblogs_score')
        with tempfile.TemporaryDirectory() as d:
            report, _ = self.run_review(c, d)
            self.assertIn('editor_recommended', report['items'][0]['signals'])


class RecoveryTests(unittest.TestCase):
    def test_reserved_recovery_deadline_stops_calls_before_charging_request_budget(self):
        e = Evaluator.__new__(Evaluator)
        e.providers = [object()]; e.budget = Mock(); e.deadline_remaining = lambda: 0
        with self.assertRaises(EditorialError): e('prompt', {})
        e.budget.reserve.assert_not_called()

    def test_partial_event_retried_even_when_news_count_is_sufficient(self):
        class E:
            identity = ['test']
        c = {'editorial_source_id': 'missing', 'primary_url': 'https://example.com/release',
             'title': 'Runtime migration', 'editorial_news_window_eligible': True}
        events = [{'event_id': str(i), 'sources': [c]} for i in range(4)]
        p = {'sources': [{'source_id': 'missing', 'read_status': 'discovery_only'}]}
        assessed = [(scored((3, 3, 3, 3)), p) for _ in events]
        materials = {'missing': {'read_status': 'discovery_only'}}
        with tempfile.TemporaryDirectory() as d, patch('quiet_day_recovery.source_material', return_value={
                'fetch_status': 'fallback_after_fetch_error', 'fetch_error': 'HTTP 403'}):
            result, audit = recover_pending(events, assessed, materials, policy=V3, evaluator=E(),
                output_dir=d, prompt='test', remaining=lambda: 100, assessor=lambda *args: assessed[0])
        self.assertTrue(audit['triggered'])
        self.assertEqual(audit['events_attempted'], 4)
        self.assertEqual(audit['attempts'][0]['error'], 'HTTP 403')


if __name__ == '__main__': unittest.main()
