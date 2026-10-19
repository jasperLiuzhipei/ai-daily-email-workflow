from copy import deepcopy
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from news_budget import apply_budget, checked_budget
from event_editorial import checked_policy, validate_assessment, assessment_policy, validate_final_selection, digest
from event_editorial_runtime import apply_selection
from quiet_day_selection import apply_quiet_labels, select_quiet_day
from test_event_editorial import packet, assessment, TEXT

BUDGET = {'enabled': True, 'normal_min': 12, 'normal_max': 15, 'hard_max': 20,
          'max_catchup': 2, 'expansion_score': 85}
POLICY = checked_policy({'publish_threshold': 60, 'admission_contract': 'value-evidence-v3',
                        'brief_value_floor': 60, 'quiet_day': {'enabled': False}, 'news_budget': BUDGET})
WINDOW = {'end_at': '2026-10-16T08:30:00+08:00'}


def item(index, level=3, catchup=False, research=False):
    eid = str(index).zfill(3)
    p, a = packet(eid), assessment(eid, level)
    if level == 4:
        # High-value candidates stay below the separate second-review threshold.
        for key in list(a['dimensions'])[-2:]:
            a['dimensions'][key]['level'] = 3
    p['sources'][0]['url'] = 'https://example.com/' + eid
    a.update(editorial_recommendation='include', novelty_basis={
        'kind': 'new_findings', 'reason': '提供具体的行为回放与渐进验证结果。', 'evidence_refs': [0]})
    a['illustration']['eligible'] = research
    a = validate_assessment(a, p, POLICY)
    return {'event_id': eid, 'title': a['event_title'], 'primary_url': p['sources'][0]['url'],
            'editorial_packet': p, 'editorial_assessment': a, 'source_text': TEXT,
            'editorial_source_text_sha256': digest(TEXT), 'editorial_news_window_eligible': not catchup,
            'published_at': '2026-10-14T12:00:00+08:00' if catchup else '2026-10-15T12:00:00+08:00'}


class NewsBudgetTests(unittest.TestCase):
    def select(self, items, **kwargs):
        return select_quiet_day(items, POLICY, WINDOW, None, **kwargs)

    def test_ordinary_day_selects_fifteen_and_defers_remaining(self):
        candidates = [item(i) for i in range(35)]
        saved = deepcopy(candidates)
        _, news, rejected, audit = self.select(candidates)
        self.assertEqual(len(news), 15)
        self.assertEqual(len(rejected), 20)
        self.assertTrue(all(x['editorial_decision']['action'] == 'defer' for x in rejected))
        self.assertEqual(audit['news_budget']['expansions'], [])
        self.assertEqual(candidates, saved)
        self.assertEqual([x['daily_rank'] for x in news], list(range(1, 16)))

    def test_major_news_expand_to_twenty_never_more(self):
        _, news, _, audit = self.select([item(i, 4) for i in range(30)], max_news_count=50)
        self.assertEqual(len(news), 20)
        self.assertEqual(len(audit['news_budget']['expansions']), 5)

    def test_time_sensitive_overflow_can_expand(self):
        items = [item(i) for i in range(16)]
        items[-1]['editorial_assessment'].update(score=70, urgency='time_sensitive')
        _, news, _, audit = self.select(items)
        self.assertEqual(len(news), 16)
        self.assertEqual(audit['news_budget']['expansions'][0]['event_id'], '015')

    def test_short_day_does_not_lower_admission_or_pad(self):
        for size in (0, 2, 11, 12, 14):
            _, news, _, audit = self.select([item(i) for i in range(size)])
            self.assertEqual(len(news), size)
            self.assertEqual(audit['news_budget']['below_normal_range'], size < 12)

    def test_fresh_news_prevent_catchup_even_if_old_scores_are_higher(self):
        items = [item(i) for i in range(15)] + [item(100+i, 4, catchup=True) for i in range(14)]
        _, news, _, audit = self.select(items)
        self.assertEqual(len(news), 15)
        self.assertEqual(audit['news_budget']['selected_catchup_count'], 0)
        self.assertEqual(audit['added_event_ids'], [])

    def test_catchup_at_most_two_counted_in_total(self):
        for fresh, expected in [(0, 2), (9, 11), (10, 12), (11, 12), (12, 12)]:
            items = [item(i) for i in range(fresh)] + [item(100+i, catchup=True) for i in range(8)]
            _, news, _, audit = self.select(items)
            self.assertEqual(len(news), expected)
            self.assertEqual(len(audit['added_event_ids']), expected - fresh)

    def test_explicit_smaller_cap_is_respected(self):
        for limit in (0, 5, 12, 17):
            _, news, _, _ = self.select([item(i, 4) for i in range(30)], max_news_count=limit)
            self.assertEqual(len(news), limit)

    def test_research_is_separate_and_receipt_replays_with_final_gate(self):
        candidates = [item('paper', research=True)] + [item(i) for i in range(10)] + [item(100+i, catchup=True) for i in range(8)]
        selection = apply_selection({'window': WINDOW}, candidates, {'policy': POLICY})
        self.assertEqual(len(selection['selected_research']), 1)
        self.assertEqual(len(selection['selected_news']), 12)
        chosen = selection['selected_news']
        news = apply_quiet_labels([{'title': c['title'], 'url': c['primary_url']} for c in chosen], chosen)
        content = {'news': news, 'paper': {'title': '研究', 'links': [{'url': selection['selected_research'][0]['primary_url']}]}}
        self.assertEqual(validate_final_selection(selection, content), [])
        tampered = deepcopy(selection)
        tampered['quiet_day_selection']['news_budget']['selected_count'] = 20
        self.assertTrue(validate_final_selection(tampered, content))

    def test_historic_policies_keep_previous_behavior_and_evidence_hash(self):
        old_policy = {k: v for k, v in POLICY.items() if k != 'news_budget'}
        self.assertEqual(assessment_policy(old_policy), assessment_policy(POLICY))
        _, news, _, audit = select_quiet_day([item(i) for i in range(30)], old_policy, WINDOW, None)
        self.assertEqual(len(news), 30)
        self.assertNotIn('news_budget', audit)

    def test_invalid_configuration_is_rejected(self):
        for change in ({'hard_max': 21}, {'normal_min': 16}, {'normal_max': 21}, {'max_catchup': -1}, {'hard_max': True}):
            with self.assertRaises(ValueError):
                checked_budget({**BUDGET, **change})


if __name__ == '__main__':
    unittest.main()
