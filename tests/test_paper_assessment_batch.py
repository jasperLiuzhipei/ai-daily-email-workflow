"""Batched requests retain per-event evidence, cache, retry and score gates."""
from copy import deepcopy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from event_editorial import checked_policy, EditorialError, validate_assessment
from event_editorial_runtime import assess_event, event_packet, Evaluator, evaluate_candidates
from paper_assessment_batch import assess_events, batch_request, project_source, eligible
from test_source_selection_refactor import fixture_assessment, TEXT


def fixtures(count=4):
    events, materials = [], {}
    for i in range(count):
        sid = f'long-source-identity-{i}'
        events.append({'event_id': f'event-{i}', 'merge_reason': '', 'sources': [{'editorial_source_id': sid}]})
        materials[sid] = {'source_id': sid, 'title': f'Paper {i}', 'published_at': '2026-10-21',
            'url': f'https://arxiv.org/abs/2610.{10000+i}v1', 'read_status': 'read',
            'text': f'Paper {i} [Submitted on 20 Oct 2026] Abstract: {TEXT * 3} Full final result {i}. Comments: 12 pages Subjects: AI Cite as: arXiv:test Submission history From: Author Navigation tools'}
    return events, materials


class Fake:
    identity = ['batch-contract-fixture']
    def __init__(self, broken=None):
        self.calls = []; self.broken = broken
    def __call__(self, prompt, packet):
        self.calls.append(deepcopy(packet))
        if 'events' in packet:
            rows = [fixture_assessment(item) for item in packet['events']]
            if self.broken == 'missing': rows.pop()
            if self.broken == 'duplicate': rows.append(deepcopy(rows[0]))
            if self.broken == 'source': rows[0]['evidence'][0]['source_id'] = 'S999'
            if self.broken == 'quote': rows[0]['evidence'][0]['quote'] = 'An invented statement not supplied in this paper.'
            if self.broken == 'tail': rows[0]['evidence'][0]['quote'] = 'Navigation tools'
            if self.broken == 'transport': raise OSError('unavailable')
            if self.broken == 'high':
                for dimension in rows[0]['dimensions'].values(): dimension['level'] = 4
            return {'assessments': rows}, {'response': {'request_id': 'shared-request', 'usage': {'total_tokens': 100}}}
        return fixture_assessment(packet.get('packet', packet)), {'response': {'request_id': 'individual-retry'}}


class PaperBatchTests(unittest.TestCase):
    def run_batch(self, folder, evaluator, count=4, policy=None):
        events, materials = fixtures(count)
        return assess_events(events, materials, checked_policy(policy or {}), evaluator, Path(folder)/'assessments',
            'unchanged rubric', assess_one=assess_event, make_packet=event_packet)

    def test_full_abstract_preserved_while_navigation_is_removed(self):
        _, materials = fixtures(1); source = next(iter(materials.values()))
        projected = project_source(source)
        self.assertIn('Full final result 0.', projected['text'])
        self.assertIn('Submitted on 20 Oct', projected['text'])
        self.assertIn('12 pages', projected['text'])
        self.assertNotIn('Navigation tools', projected['text'])
        for text in ['No abstract marker ' + TEXT, ' Abstract: ' + TEXT + ' Subjects: ambiguous prose']:
            self.assertEqual(project_source({**source, 'text': text})['text'], text)

    def test_one_request_per_four_papers_scores_and_original_identity_unchanged(self):
        fake = Fake()
        with tempfile.TemporaryDirectory() as d:
            results, summary = self.run_batch(d, fake)
            self.assertEqual(len(fake.calls), 1)
            self.assertEqual(summary['batched_papers'], 4)
            self.assertEqual(summary['batch_requests'], 1)
            for i, (a, packet) in enumerate(results):
                self.assertEqual(a['score'], 75)
                self.assertEqual(a['event_id'], f'event-{i}')
                self.assertEqual(a['primary_source_id'], f'long-source-identity-{i}')
                self.assertIn('Navigation tools', packet['sources'][0]['text'])
                self.assertEqual(validate_assessment(a, packet, checked_policy({})), a)
            records = [json.loads(p.read_text()) for p in (Path(d)/'assessments').glob('*.json')]
            self.assertEqual({r['attempts'][0]['metadata']['response']['request_id'] for r in records}, {'shared-request'})
            self.assertTrue(all('paper_batch' in r['attempts'][0]['metadata'] for r in records))
            cached, summary = self.run_batch(d, fake)
            self.assertEqual(results, cached)
            self.assertEqual(summary['cached_papers'], 4)
            self.assertEqual(len(fake.calls), 1)

    def test_only_missing_invalid_or_duplicate_members_retry(self):
        for error in ['missing', 'duplicate', 'source', 'quote', 'tail']:
            with self.subTest(error=error), tempfile.TemporaryDirectory() as d:
                fake = Fake(error)
                results, summary = self.run_batch(d, fake)
                self.assertEqual(len(fake.calls), 2)
                self.assertEqual(summary['missing_or_invalid_members'], 1)
                self.assertTrue(all(a['assessment_status'] == 'complete' for a, _ in results))
                trace = json.loads(next((Path(d)/'paper_batches').glob('*.json')).read_text())
                self.assertIn('output', trace)

    def test_transport_failure_falls_back_without_discarding_events(self):
        with tempfile.TemporaryDirectory() as d:
            fake = Fake('transport'); results, summary = self.run_batch(d, fake)
        self.assertEqual(len(fake.calls), 5)
        self.assertEqual(len(results), 4)
        self.assertTrue(all(a['score'] == 75 for a, _ in results))

    def test_interrupted_batch_reuses_saved_response_without_a_second_charge(self):
        with tempfile.TemporaryDirectory() as d:
            fake = Fake()
            self.run_batch(d, fake)
            for path in (Path(d)/'assessments').glob('*.json'): path.unlink()
            results, summary = self.run_batch(d, fake)
        self.assertEqual(len(fake.calls), 1)
        self.assertEqual(summary['cached_batch_responses'], 1)
        self.assertEqual(summary['batch_requests'], 0)
        self.assertEqual(len(results), 4)

    def test_exceptional_score_still_requires_separate_review(self):
        with tempfile.TemporaryDirectory() as d:
            fake = Fake('high'); results, _ = self.run_batch(d, fake)
        self.assertEqual(len(fake.calls), 2)
        self.assertEqual(results[0][0]['second_review']['previous_score'], 100)

    def test_disabled_batches_keep_individual_path_and_small_tail_is_not_lost(self):
        for count, size, calls in [(5, 4, 2), (4, 1, 4)]:
            with self.subTest(count=count, size=size), tempfile.TemporaryDirectory() as d:
                fake = Fake(); results, _ = self.run_batch(d, fake, count, {'paper_batch_size': size})
                self.assertEqual(len(results), count); self.assertEqual(len(fake.calls), calls)
        for bad in [0, 5, True]:
            with self.assertRaises(EditorialError): checked_policy({'paper_batch_size': bad})

    def test_only_single_readable_arxiv_abstracts_are_batched(self):
        events, materials = fixtures(1)
        packet = event_packet(events[0], materials, checked_policy({}))
        self.assertTrue(eligible(packet))
        for url, status in [('https://example.org/abs/2610.10000','read'),
                            ('https://arxiv.org/html/2610.10000','read'),
                            ('https://arxiv.org/abs/2610.10000','discovery_only')]:
            changed = deepcopy(packet);changed['sources'][0].update(url=url,read_status=status)
            self.assertFalse(eligible(changed))
        packet['sources'] *= 2; self.assertFalse(eligible(packet))

    def test_source_identity_error_names_bad_field_and_allowed_id(self):
        events, materials = fixtures(1);packet=event_packet(events[0],materials,checked_policy({}))
        raw=fixture_assessment(packet);raw['evidence'][0]['source_id']='mistyped'
        with self.assertRaisesRegex(EditorialError, r'evidence\[0\].source_id=.*mistyped.*long-source-identity-0'):
            validate_assessment(raw,packet,checked_policy({}))

    def test_batch_usage_is_charged_once_with_sufficient_output_budget(self):
        from test_news_prose_recovery import runtime
        from editorial_runtime_support import RuntimeBudget
        policy=checked_policy({});budget=RuntimeBudget(policy)
        with patch('event_editorial_runtime.load_runtime_config',return_value={}), patch('event_editorial_runtime.resolve_provider_fallbacks',return_value=(runtime(),)):
            evaluator=Evaluator(budget=budget)
        events, materials=fixtures();packets=[event_packet(e,materials,policy) for e in events];request,_=batch_request(packets)
        with patch('event_editorial_runtime.chat_json',return_value=({'assessments':[]},{'usage':{'prompt_tokens':100,'completion_tokens':40}})) as chat:
            evaluator('rubric',request)
        self.assertEqual(chat.call_args.kwargs['request_overrides']['max_tokens'],12000)
        self.assertEqual(budget.summary()['api_requests'],1)
        self.assertEqual(budget.summary()['reported_input_tokens'],100)

    def test_production_candidate_pipeline_uses_batches_and_preserves_selection_inputs(self):
        _, materials = fixtures()
        candidates = [{'primary_url': m['url'], 'title': m['title'], 'published_at': m['published_at']}
                      for m in materials.values()]
        by_url = {m['url']: m for m in materials.values()}
        class PipelineFake(Fake):
            def __call__(self, prompt, packet):
                if isinstance(packet, list): return {'groups': []}, {}
                return super().__call__(prompt, packet)
        def fetch(c, **kwargs):
            m = by_url[c['primary_url']]
            return {'fetch_status': 'fetched', 'source_text': m['text'], 'page_title': m['title'], 'resolved_url': m['url']}
        with tempfile.TemporaryDirectory() as d, patch('event_editorial_runtime.source_material', side_effect=fetch):
            fake = PipelineFake()
            result, summary = evaluate_candidates(candidates, output_dir=Path(d), evaluator=fake,
                policy=checked_policy({'shared_material_cache': str(Path(d)/'shared')}))
        self.assertEqual(summary['paper_assessment_batches']['batch_requests'], 1)
        self.assertEqual(summary['assessment_completed'], 4)
        self.assertEqual(len(fake.calls), 1)
        self.assertEqual({c['primary_url'] for c in result}, set(by_url))
        self.assertTrue(all(c['editorial_assessment']['score'] == 75 for c in result))


if __name__ == '__main__': unittest.main()
