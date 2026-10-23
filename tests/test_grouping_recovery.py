import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from event_editorial import group_sources
from event_grouping_recovery import recover_disjoint_groups
from aihot_discovery import discover_pending


def group(*ids):
    return {'source_ids': list(ids), 'reason': 'These sources explicitly report the same concrete release.'}


class GroupRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.sources = [{'editorial_source_id': s} for s in 'abcdef']

    def test_quarantines_both_overlaps_but_keeps_unrelated_group(self):
        accepted, audit = recover_disjoint_groups(self.sources,
            [group('a', 'b'), group('b', 'c'), group('d', 'e')])
        self.assertEqual(accepted, [group('d', 'e')])
        self.assertEqual(audit['quarantined_source_ids'], ['b'])
        events = group_sources(self.sources, accepted)
        self.assertEqual(sorted(len(e['sources']) for e in events), [1, 1, 1, 1, 2])

    def test_malformed_groups_do_not_hide_overlap_or_invent_ids(self):
        accepted, audit = recover_disjoint_groups(self.sources,
            [group('a', 'b'), group('b', 'unknown'), None, {'source_ids': [['x'], 'c']}, group('d', 'e')])
        self.assertEqual(accepted, [group('d', 'e')])
        self.assertEqual(len(audit['rejected_group_indices']), 4)

    def test_duplicate_id_inside_group_is_never_merged(self):
        accepted, _ = recover_disjoint_groups(self.sources, [group('a', 'a'), group('b', 'c')])
        self.assertEqual(accepted, [group('b', 'c')])

    def test_valid_and_nonlist_responses(self):
        self.assertEqual(recover_disjoint_groups(self.sources, [group('a', 'b')])[0], [group('a', 'b')])
        self.assertEqual(recover_disjoint_groups(self.sources, {'groups': []})[0], [])

    def test_runtime_retains_valid_groups_after_two_failed_responses(self):
        from event_editorial import unique_sources
        from event_editorial_runtime import evaluate_candidates
        candidates = [{'primary_url': f'https://example.com/{s}', 'title': f'Release {s}'} for s in 'abcdef']
        ids = [c['editorial_source_id'] for c in unique_sources(candidates)]
        proposed = [group(ids[0], ids[1]), group(ids[1], ids[2]), group(ids[3], ids[4])]
        class E:
            identity = ['test']
            calls = 0
            def __call__(self, system, data):
                self.calls += 1
                return {'groups': proposed}, {}
        class StopAfterGrouping(Exception):
            pass
        evaluator = E()
        with tempfile.TemporaryDirectory() as folder:
            with patch('event_editorial_runtime.prepare_material', side_effect=lambda c, **kw: {'source_id': c['editorial_source_id']}), \
                 patch('paper_assessment_batch.assess_events', side_effect=StopAfterGrouping) as assess:
                with self.assertRaises(StopAfterGrouping):
                    evaluate_candidates(candidates, output_dir=Path(folder), evaluator=evaluator, offline=True)
            events = assess.call_args.args[0]
            self.assertEqual(evaluator.calls, 2)
            self.assertEqual(sorted(len(e['sources']) for e in events), [1, 1, 1, 1, 2])
            trace = json.loads(next((Path(folder)/'groups').glob('*.json')).read_text())
            self.assertEqual(trace['validation'][-1]['status'], 'partial_disjoint_recovery')
            self.assertEqual(trace['groups'], [proposed[-1]])

    def test_pending_evidence_search_is_not_starved_by_hot_topics(self):
        candidates = [{'title': f'HotProduct{i} release', 'aihot_hot_topics': ['hot'],
            'high_value_reasons': ['release'], 'primary_verification': {'status': 'unresolved'}} for i in range(5)]
        candidates += [{'title': name + ' release', 'backlog_recall_lane': 'pending_evidence'}
                       for name in ['NovelRuntime', 'OtherFramework', 'ThirdPackage']]
        response = {'schemaVersion': 1, 'query': {'ordering': 'publishedAtDesc'},
                    'items': [], 'page': {'hasMore': False}}
        with tempfile.TemporaryDirectory() as folder:
            _, _, audit = discover_pending(candidates,
                datetime(2026, 10, 19, tzinfo=timezone.utc), datetime(2026, 10, 23, tzinfo=timezone.utc),
                folder, [], lambda *a, **kw: json.dumps(response), max_queries=4)
        self.assertEqual(len(audit), 4)
        self.assertEqual([r['trigger'] for r in audit], ['backlog_pending', 'backlog_pending',
                                                     'current_primary_unresolved', 'current_primary_unresolved'])
        self.assertEqual(len({r['query'] for r in audit}), 4)


if __name__ == '__main__':
    unittest.main()
