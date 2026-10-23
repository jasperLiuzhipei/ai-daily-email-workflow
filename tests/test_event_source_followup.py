"""Alternate-report discovery and article-to-source recovery regressions."""
from copy import deepcopy
from datetime import datetime
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from aihot_discovery import FetchBudget, discover_pending
from event_editorial import checked_policy, digest
from quiet_day_recovery import article_reference_links, recover_pending
from source_document import read_document
from source_identity import SourceSelectionError
import source_selection_context as context

TITLE = 'ExampleInfer 开源推理框架'
BODY = 'ExampleInfer opens its inference framework and documents the deployment constraints. ' * 7
START = datetime.fromisoformat('2026-10-20T08:30:00+08:00')
END = datetime.fromisoformat('2026-10-23T08:30:00+08:00')


def material(url, links=()):
    return {'fetch_status': 'fetched', 'resolved_url': url, 'page_title': TITLE,
            'source_text': BODY, 'document_identity': {'body_kind': 'article',
                'article_references': [{'href': u, 'label': label} for u, label in links]}}


class EventSourceFollowupTests(unittest.TestCase):
    def test_article_citations_exclude_navigation_and_resolve_relative_links(self):
        raw = '<nav><a href="https://ads.test/ExampleInfer">Advertisement</a></nav><article>' + BODY + '<a href="/releases/ExampleInfer">官方公告</a><a href="https://github.com/example/ExampleInfer">代码仓库</a><a href="https://unrelated.test/other">Other news</a></article>'
        _, _, _, identity = read_document(raw)
        refs = article_reference_links({'title': TITLE}, {'url': 'https://vendor.test/news/a', 'document_identity': identity})
        self.assertEqual({r['url'] for r in refs}, {'https://vendor.test/releases/ExampleInfer', 'https://github.com/example/ExampleInfer'})
        self.assertTrue(all(r['discovered_from'] == 'https://vendor.test/news/a' for r in refs))

    def test_reference_filter_rejects_private_urls_credentials_and_login(self):
        bad = ['http://127.0.0.1/ExampleInfer', 'https://user:password@vendor.test/ExampleInfer',
               'https://vendor.test/login/ExampleInfer', 'javascript:alert(1)', 'https://vendor.test/']
        refs = article_reference_links({'title': TITLE}, material('https://vendor.test/article', [(u, 'source') for u in bad]))
        self.assertEqual(refs, [])

    def test_repeated_citations_send_the_same_document_only_once(self):
        from event_editorial_runtime import event_packet
        materials={sid: {'source_id': sid, 'url': 'https://github.com/example/ExampleInfer',
                    'read_status': 'read', 'text': BODY, 'published_at': ''} for sid in ['a','b']}
        materials['b']['published_at']='2026-10-22T09:00:00+08:00'
        event={'event_id':'e','merge_reason':'同一发布的多个报道引用了同一份原文。',
               'sources':[{'editorial_source_id':sid} for sid in materials]}
        packet=event_packet(event, materials, checked_policy({}))
        self.assertEqual(len(packet['sources']),1)
        self.assertEqual(packet['sources'][0]['source_id'],'b')

    def test_http_budget_does_not_expire_during_unrelated_pipeline_work(self):
        calls = []
        def fetch(*args, **kwargs): calls.append(kwargs); return '{}'
        clock = iter([0, 2, 1000, 1003])
        budget = FetchBudget(fetch, 5, clock=lambda: next(clock))
        budget('https://aihot.news/one', None)
        budget('https://aihot.news/two', None)
        self.assertEqual([r['max_time_seconds'] for r in calls], [5, 3])
        with self.assertRaises(SourceSelectionError): budget('https://aihot.news/three', None)

    def test_today_unresolved_important_source_searches_without_backlog(self):
        row = {'id': 'source', 'title': TITLE, 'summary': BODY, 'source': {'name': 'Example'},
               'publishedAt': '2026-10-22T09:00:00+08:00',
               'links': {'original': 'https://vendor.test/ExampleInfer'}}
        response = {'schemaVersion': 1, 'query': {'ordering': 'publishedAtDesc'},
                    'items': [row], 'page': {'hasMore': False}}
        candidate = {'title': TITLE, 'high_value_reasons': ['aggregator_featured'],
                     'primary_verification': {'status': 'unresolved'}}
        with tempfile.TemporaryDirectory() as d, patch.object(context, 'DISCOVERY_ONLY', True):
            found, _, audit = discover_pending([candidate], START, END, d, [], lambda *a, **k: json.dumps(response))
        self.assertEqual(len(found), 1)
        self.assertEqual(audit[0]['trigger'], 'current_primary_unresolved')
        self.assertEqual(found[0]['scores'], {})

    def run_recovery(self, fetch, *, shared=False, complete_defer=False, complete_include=False):
        c = {'editorial_source_id': 'blocked', 'primary_url': 'https://media.test/blocked',
             'title': TITLE, 'coverage_source_urls': ['https://media.test/ExampleInfer']}
        materials = {'blocked': {'source_id': 'blocked', 'url': c['primary_url'],
            'read_status': 'discovery_only', 'text': '', 'fetch_status': 'fallback_after_fetch_error', 'fetch_error': 'HTTP 403'}}
        sources = [c]
        if shared or complete_defer or complete_include:
            m = material('https://vendor.test/ExampleInfer', [('https://github.com/example/ExampleInfer', '源码')])
            if shared:
                sources.append({'editorial_source_id': 'read', 'primary_url': m['resolved_url'], 'title': TITLE})
                sid = 'read'
            else:
                sid = 'blocked'
                sources[0].update(primary_url=m['resolved_url'], coverage_source_urls=[])
            materials[sid] = {'source_id': sid, 'url': m['resolved_url'], 'text': BODY,
                'read_status': 'read', 'fetch_status': 'fetched', 'document_identity': m['document_identity']}
        event = {'event_id': 'event', 'merge_reason': '同一次开源框架发布的已归并报道。', 'sources': sources}
        a = {'assessment_status': 'complete' if complete_defer else 'pending', 'editorial_recommendation': 'defer', 'score': None}
        if complete_include:
            a.update(assessment_status='complete', editorial_recommendation='include', score=75,
                     evidence_status='limited', novelty='new', decision_reason='值得报道但仍须补读重要一手资料。',
                     publication_priority={'level': 'lead'})
        packet = {'sources': list(materials.values())}
        seen = []
        def assess(e, ms, *args):
            seen.append(deepcopy(ms))
            return {'assessment_status': 'pending', 'score': None}, {'sources': [ms[x['editorial_source_id']] for x in e['sources']]}
        class E: identity = ['test']
        with tempfile.TemporaryDirectory() as d, patch('quiet_day_recovery.source_material', side_effect=fetch):
            result, audit = recover_pending([event], [(a, packet)], materials,
                policy=checked_policy({'admission_contract': 'value-evidence-v3', 'brief_value_floor': 60,
                    **({'priority_contract': 'publication-priority-v1'} if complete_include else {})}), evaluator=E(),
                output_dir=d, prompt='', remaining=lambda: 100, assessor=assess)
        return materials, result, audit, seen

    def test_two_hops_report_to_announcement_to_repository_then_reassess(self):
        urls = []
        def fetch(c, **kwargs):
            u = c['primary_url']; urls.append(u)
            if u.startswith('https://media.test/'):
                return material(u, [('https://vendor.test/ExampleInfer', '官方公告')])
            if u.startswith('https://vendor.test/'):
                return material(u, [('https://github.com/example/ExampleInfer', '源码')])
            return material(u, [('https://github.com/example/ExampleInfer/blob/main/examples.md', 'ExampleInfer examples')])
        ms, result, audit, seen = self.run_recovery(fetch)
        self.assertEqual(urls, ['https://media.test/ExampleInfer', 'https://vendor.test/ExampleInfer', 'https://github.com/example/ExampleInfer'])
        self.assertEqual(ms['blocked']['url'], urls[-1])
        self.assertEqual([a['reference_depth'] for a in audit['attempts']], [0, 1, 2])
        self.assertEqual(len(seen), 1)
        self.assertEqual(ms['blocked']['text_sha256'], digest(BODY))
        self.assertIsNone(result[0][0]['score'])  # Fetching is not publication approval.

    def test_unreadable_lead_can_use_citation_from_another_grouped_report(self):
        urls = []
        def fetch(c, **kw): urls.append(c['primary_url']); return material(c['primary_url'])
        ms, _, audit, _ = self.run_recovery(fetch, shared=True)
        self.assertEqual(urls, ['https://github.com/example/ExampleInfer'])
        self.assertEqual(audit['sources_recovered'], 1)
        self.assertEqual(audit['attempts'][0]['discovered_from'], 'https://vendor.test/ExampleInfer')
        self.assertEqual(ms['read']['url'], 'https://vendor.test/ExampleInfer')

    def test_failed_or_unrelated_citation_keeps_readable_report(self):
        for unrelated in [False, True]:
            def fetch(c, **kw):
                if c['primary_url'].startswith('https://media.test/'):
                    return material(c['primary_url'], [('https://vendor.test/ExampleInfer', '官方公告')])
                if unrelated:
                    m = material(c['primary_url']); m.update(page_title='Completely different', source_text='An unrelated product description. ' * 20); return m
                return {'fetch_status': 'fallback_after_fetch_error', 'fetch_error': 'HTTP 403'}
            ms, _, audit, _ = self.run_recovery(fetch)
            self.assertEqual(ms['blocked']['url'], 'https://media.test/ExampleInfer')
            self.assertFalse(audit['attempts'][-1]['read'])

    def test_readable_but_deferred_event_can_follow_reference(self):
        ms, _, audit, seen = self.run_recovery(lambda c, **kw: material(c['primary_url']), complete_defer=True)
        self.assertEqual(ms['blocked']['url'], 'https://vendor.test/ExampleInfer')
        self.assertIn('https://github.com/example/ExampleInfer', {m['url'] for m in ms.values()})
        self.assertEqual(audit['sources_enriched'], 1)
        self.assertEqual(len(seen), 1)

    def test_qualified_limited_major_event_still_reads_observed_primary_reference(self):
        ms, _, audit, seen = self.run_recovery(lambda c, **kw: material(c['primary_url']), complete_include=True)
        self.assertEqual(ms['blocked']['url'], 'https://vendor.test/ExampleInfer')
        self.assertIn('https://github.com/example/ExampleInfer', {m['url'] for m in ms.values()})
        self.assertEqual(audit['sources_enriched'], 1)
        self.assertEqual(audit['http_requests'], 1)
        self.assertEqual(len(seen), 1)

    def test_enriched_evidence_survives_evaluation_and_composition_contract(self):
        from event_editorial_runtime import evaluate_candidates
        from fetch_selected_source_material import source_material
        from event_editorial import validate_final_selection
        class E:
            identity = ['reference-integration-fixture']
            def __call__(self, prompt, packet):
                source = packet['sources'][-1]
                enriched = len(packet['sources']) == 2
                sid = source['source_id']
                return {'event_id': packet['event_id'], 'event_title': TITLE, 'primary_source_id': sid,
                    'content_kind': 'engineering', 'what_changed': '开放推理框架并提供具体部署约束。',
                    'reader_takeaway': '研发可以据此评估推理框架部署条件。', 'limitations': '仅依据发布方材料，效果仍需要验证。',
                    'decision_reason': '补齐部署原文后可以对这一发布作有限报道。',
                    'evidence_status': 'limited', 'novelty': 'new', 'urgency': 'routine', 'urgency_reason': '',
                    'novelty_basis': {'kind': 'release_announcement', 'reason': '正文明确描述此次开放框架的新增事实。', 'evidence_refs': [0]},
                    'editorial_recommendation': 'include' if enriched else 'defer',
                    'evidence': [{'source_id': sid, 'quote': BODY[:150]}],
                    'dimensions': {k: {'level': 3 if enriched else 2,
                        'reason': '具体的发布与部署边界为读者提供判断依据。', 'evidence_refs': [0]}
                        for k in ('change', 'impact', 'reader_value', 'learning_value')},
                    'illustration': {'eligible': False, 'reason': '文字足以解释这一发布及适用范围。'}}, {}
        def fetch(c, **kwargs):
            return material(c['primary_url'], [('https://github.com/example/ExampleInfer', '源码')])
        with tempfile.TemporaryDirectory() as d:
            policy=checked_policy({'admission_contract': 'value-evidence-v3', 'brief_value_floor': 60,
                                   'shared_material_cache': str(Path(d)/'cache')})
            c={'primary_url': 'https://vendor.test/ExampleInfer', 'title': TITLE,
               'published_at': '2026-10-22T09:00:00+08:00'}
            with patch('event_editorial_runtime.source_material', side_effect=fetch), patch('quiet_day_recovery.source_material', side_effect=fetch):
                rows, summary=evaluate_candidates([c],output_dir=Path(d),policy=policy,evaluator=E())
            self.assertEqual(summary['source_count'], 2)
            self.assertEqual(summary['discovered_source_count'], 1)
            self.assertEqual(rows[0]['primary_url'], 'https://github.com/example/ExampleInfer')
            self.assertEqual(len(rows[0]['editorial_packet']['sources']), 2)
            self.assertEqual(source_material(rows[0],max_chars=12000,timeout_seconds=1,max_bytes=1000)['source_text'], BODY)
            self.assertEqual(validate_final_selection({'event_editorial': summary, 'selected_news': rows},
                {'news': [{'url': rows[0]['primary_url']}]}), [])


if __name__ == '__main__': unittest.main()
