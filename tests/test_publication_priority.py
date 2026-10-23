"""Publication priority must improve placement without bypassing evidence gates."""
from copy import deepcopy
import unittest

from test_news_budget import item, POLICY, WINDOW, BUDGET
from event_editorial import checked_policy, validate_assessment, validate_final_selection, eligibility, select_events, EditorialError
from event_editorial_runtime import apply_selection
from news_budget import apply_budget
from publication_priority import CONTRACT
from quiet_day_selection import select_quiet_day, apply_quiet_labels

SETTINGS = {**BUDGET, 'priority_mode': CONTRACT, 'catchup_competes': True,
            'catchup_preserve_mix': True, 'lead_catchup_extra': 2}
NEW = checked_policy({**POLICY, 'priority_contract': CONTRACT, 'news_budget': SETTINGS})


def prioritized(index, priority='lead', **kwargs):
    c = item(index, **kwargs)
    c['editorial_assessment']['publication_priority'] = {
        'level': priority, 'reason': '本次开放的生产执行层改变研发团队对推理部署及自建选型的具体判断。',
        'evidence_refs': [0]}
    c['editorial_assessment'] = validate_assessment(c['editorial_assessment'], c['editorial_packet'], NEW)
    return c


class PublicationPriorityTests(unittest.TestCase):
    def test_priority_is_independent_of_original_score(self):
        c = prioritized(1)
        self.assertEqual(c['editorial_assessment']['score'], 75)
        self.assertEqual(eligibility(c['editorial_assessment'], NEW)[0], 'include')

    def test_news_rank_can_precede_a_richer_explanation(self):
        lead, richer = prioritized(1), prioritized(2, 'important')
        richer['editorial_assessment']['dimensions']['reader_value']['level'] = 4
        richer['editorial_assessment']['dimensions']['learning_value']['level'] = 4
        richer['editorial_assessment'] = validate_assessment(richer['editorial_assessment'], richer['editorial_packet'], NEW)
        self.assertEqual(richer['editorial_assessment']['score'], 86)
        _, selected, _, _ = select_quiet_day([richer, lead], NEW, WINDOW, None, research_count=0)
        self.assertEqual([c['event_id'] for c in selected], [lead['event_id'], richer['event_id']])
        self.assertEqual(selected[0]['display_tier'], 'deep')

    def test_old_policies_do_not_consume_new_priority_labels(self):
        lead, rich = prioritized(1), prioritized(2, 'important', level=4)
        selected, _, audit = apply_budget([lead, rich], BUDGET)
        self.assertEqual(selected[0]['event_id'], rich['event_id'])
        self.assertNotIn('selected_priorities', audit)

    def test_historical_assessments_require_fresh_priority_review(self):
        c = item(1)
        with self.assertRaises(EditorialError): validate_assessment(c['editorial_assessment'], c['editorial_packet'], NEW)
        with self.assertRaises(EditorialError): checked_policy({**POLICY, 'news_budget': SETTINGS})

    def test_unsupported_lead_priority_fails_closed(self):
        c = prioritized(1)
        for change in ('no_evidence', 'wrong_ref', 'weak_impact', 'unknown', 'excluded'):
            a = deepcopy(c['editorial_assessment'])
            if change == 'no_evidence': a['publication_priority']['evidence_refs'] = []
            if change == 'wrong_ref': a['publication_priority']['evidence_refs'] = [99]
            if change == 'weak_impact': a['dimensions']['impact']['level'] = 2
            if change == 'unknown': a['evidence_status'] = 'unresolved'
            if change == 'excluded': a['editorial_recommendation'] = 'exclude'
            with self.subTest(change=change), self.assertRaises(EditorialError):
                validate_assessment(a, c['editorial_packet'], NEW)

    def test_priority_does_not_bypass_admission_or_duplicate_checks(self):
        c = prioritized(1)
        c['publication_duplicate_check'] = {'is_duplicate': True, 'published_on': '2026-10-15'}
        _, selected, _ = select_events([c], NEW, research_count=0)
        self.assertEqual(selected, [])
        c = prioritized(2)
        c['editorial_assessment']['evidence_status'] = 'unresolved'
        self.assertEqual(eligibility(c['editorial_assessment'], NEW)[0], 'defer')

    def test_catchup_exception_is_bounded_and_respects_total_cap(self):
        fresh = [prioritized(i, 'standard') for i in range(15)]
        old = [prioritized(100+i, catchup=True) for i in range(6)]
        selected, deferred, audit = apply_budget(fresh+old, SETTINGS)
        self.assertEqual(len(selected), 15)
        self.assertEqual(audit['selected_catchup_count'], 4)
        self.assertEqual(len(audit['lead_catchup_exceptions']), 2)
        self.assertEqual(len(deferred), 6)
        selected, _, audit = apply_budget(fresh+old, SETTINGS, explicit_max=2)
        self.assertEqual(len(selected), 2)

    def test_standard_catchup_never_uses_lead_exception(self):
        old = [prioritized(100+i, 'important', level=4, catchup=True) for i in range(6)]
        selected, _, audit = apply_budget([prioritized(i, 'standard') for i in range(15)]+old, SETTINGS)
        self.assertEqual(audit['selected_catchup_count'], 2)
        self.assertEqual(audit['lead_catchup_exceptions'], [])

    def test_old_or_future_news_cannot_use_priority_to_reset_time(self):
        for date in ('2026-10-01T12:00:00+08:00', '2026-10-17T12:00:00+08:00'):
            c = prioritized(1, catchup=True)
            c['published_at'] = date
            _, news, _, _ = select_quiet_day([c], NEW, WINDOW, None, research_count=0)
            self.assertEqual(news, [])

    def test_research_slot_keeps_explanatory_ranking(self):
        lead = prioritized(1, research=True)
        rich = prioritized(2, 'important', level=4, research=True)
        research, news, _ = select_events([lead, rich], NEW, research_count=1)
        self.assertEqual(research[0]['event_id'], rich['event_id'])
        self.assertEqual(news[0]['event_id'], lead['event_id'])

    def test_new_priority_and_catchup_receipts_survive_final_publication_validation(self):
        items = [prioritized(i, 'important') for i in range(15)] + [prioritized(100, catchup=True)]
        selection = apply_selection({'window': WINDOW}, items,
            {'policy': NEW, 'version': 'event-editorial-v3.1'}, research_count=0)
        content = {'news': [{'url': c['primary_url']} for c in selection['selected_news']]}
        apply_quiet_labels(content['news'], selection['selected_news'])
        self.assertEqual(validate_final_selection(selection, content), [])
        self.assertEqual(selection['selected_news'][0]['event_id'], '100')


if __name__ == '__main__': unittest.main()
