"""Regressions for loss at discovery, recall, and publication boundaries."""
from copy import deepcopy
from datetime import datetime
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import source_selection_context as context
from aihot_discovery import fetch_window, candidates_from_items, fetch_hot_topics, coverage_receipts, recovery_query
from source_candidates import merge_candidates
from source_feed_adapters import parse_aihot_daily_entries
from editorial_backlog import recall_candidates, sync_backlog
from news_budget import apply_budget

START = datetime.fromisoformat('2026-10-22T08:30:00+08:00')
END = datetime.fromisoformat('2026-10-23T08:30:00+08:00')


def row(i, stamp='2026-10-22T09:00:00+08:00'):
    return {'id': str(i), 'title': '示例推理框架开放', 'publishedAt': stamp,
            'discoveredAt': '2026-10-22T09:05:00+08:00', 'summary': '框架开放代码并披露部署方法。',
            'category': 'ai-products', 'source': {'name': 'Example'}, 'selected': True,
            'links': {'original': 'https://example.com/'+str(i), 'aihot': 'https://aihot.news/items/'+str(i)}}


def page(rows, more=False, cursor=None):
    return {'schemaVersion': 1, 'query': {'ordering': 'publishedAtDesc'},
            'items': rows, 'page': {'hasMore': more, 'nextCursor': cursor}}


class DiscoveryCoverageTests(unittest.TestCase):
    def setUp(self):
        self.state = {k: deepcopy(v) for k, v in vars(context).items() if k.isupper()}
        context.DISCOVERY_ONLY = True
    def tearDown(self):
        for k, v in self.state.items():
            setattr(context, k, v)

    def test_pagination_reads_past_fifty_until_cutoff_without_future_leak(self):
        pages = [page([row(i, '2026-10-23T09:00:00+08:00') for i in range(100)], True, 'p2'),
                 page([row(i) for i in range(100, 200)], True, 'p3'),
                 page([row(200, '2026-10-21T00:00:00+08:00')], True, 'p4')]
        with tempfile.TemporaryDirectory() as d, patch('aihot_discovery.get_json', side_effect=pages) as fetch:
            rows, audit = fetch_window(START, END, d, None)
        self.assertEqual(len(rows), 100)
        self.assertEqual(fetch.call_count, 3)
        self.assertTrue(audit['complete'])
        self.assertEqual(audit['stop_reason'], 'window_boundary')

    def test_partial_and_stalled_traversal_never_claim_complete(self):
        for pages, limit in [([page([row(1)], True, 'x')], 1),
                             ([page([row(1)], True, 'x'), page([row(1)], True, 'x')], 3)]:
            with tempfile.TemporaryDirectory() as d, patch('aihot_discovery.get_json', side_effect=pages):
                rows, audit = fetch_window(START, END, d, None, max_pages=limit)
            self.assertEqual(len(rows), 1)
            self.assertFalse(audit['complete'])
            self.assertTrue(audit['error'])

    def test_unknown_publication_keeps_discovery_separate(self):
        rows, _, excluded = candidates_from_items([row(1, None)], START, END, [])
        self.assertEqual(rows, [])
        self.assertEqual(excluded[0]['reason'], 'publication_unknown')

    def test_daily_html_attribute_order_single_quotes_and_nested_title(self):
        html = '<a class="x" href="/items/a"><b>新闻甲</b></a><a data-id="2" href=\'/items/b\'>新闻乙</a>'
        self.assertEqual([e['item_id'] for e in parse_aihot_daily_entries(html)], ['a', 'b'])

    def test_preferred_paper_variant_preserves_other_timestamps_and_salience(self):
        a, _, _ = candidates_from_items([row(1)], START, END, [])
        a[0].update(aihot_hot_topics=['hot'], backlog_recall_lane='pending_evidence')
        b = deepcopy(a[0]); b.update(feed_source='paper', published_at='2026-10-22T00:00:00+08:00')
        b.pop('source_time_variants'); b.pop('aihot_hot_topics'); b.pop('backlog_recall_lane')
        old = deepcopy(a)
        result = merge_candidates(a + [b])[0]
        self.assertEqual(a, old)
        self.assertEqual(result['published_at'], b['published_at'])
        self.assertEqual(len(result['source_time_variants']), 2)
        self.assertTrue(result['timing_review_required'])
        self.assertEqual(result['aihot_hot_topics'], ['hot'])
        self.assertEqual(result['backlog_recall_lane'], 'pending_evidence')

    def test_hot_topics_supply_report_links_but_not_scores(self):
        topic = {'id': 'hot', 'rank': 1, 'title': '热门新框架', 'links': {'story': 'https://aihot.news/story/abc'}}
        with tempfile.TemporaryDirectory() as d, patch('aihot_discovery.get_json', side_effect=[
                {'items': [topic]}, {'story': {'reports': [row(1)]}}]):
            candidates, _, audit = fetch_hot_topics(START, END, d, [], None)
        self.assertEqual(candidates[0]['scores'], {})
        self.assertEqual(candidates[0]['aihot_hot_topics'], ['hot'])
        receipts = coverage_receipts(audit, {'all_candidates_scored': candidates}, [])
        self.assertEqual(receipts[0]['status'], 'reviewed_not_selected')
        self.assertEqual(recovery_query({'title': '某公司全面开源推理框架 SampleInfer'}), 'SampleInfer')
        self.assertEqual(recovery_query({'title': 'Acme models are coming to Example Cloud'}), 'Example Cloud')

    def test_pending_search_is_bounded_and_reports_truncated_results(self):
        from aihot_discovery import discover_pending
        candidate = {'title': '示例框架 SampleInfer', 'backlog_id': 'pending',
                     'backlog_recall_lane': 'pending_evidence'}
        def fetch(url, *args, **kwargs):
            self.assertIn('limit=20', url)
            return json.dumps(page([row(i) for i in range(20)], True, 'more'))
        with tempfile.TemporaryDirectory() as d:
            found, _, audit = discover_pending([candidate], START, END, d, [], fetch)
        self.assertEqual(len(found), 20)
        self.assertEqual(audit[0]['pages'], 1)
        self.assertFalse(audit[0]['complete'])

    def test_backlog_reserves_news_and_pending_without_losing_discovery_text(self):
        b = {'policy': {'max_recalled_candidates': 4, 'reserved_budget_deferred': 2,
                       'reserved_product_news': 1, 'reserved_pending_evidence': 1}, 'items': {}}
        for i, (kind, score, pending) in enumerate([('research', 95, False), ('research', 90, False),
                                                 ('model_product', 64, False), ('tool', 0, True)]):
            b['items'][str(i)] = {'id': str(i), 'status': 'pending_review', 'raw_news_score': score,
                'first_seen': '2026-10-22', 'candidate': {'title': kind, 'published_at': START.isoformat(),
                'description': '保留的发现摘要 https://example.com/report', 'categories': ['Product'],
                'source_excerpt': '', 'source_categories': [], 'source_type': kind,
                'editorial_assessment': {'assessment_status': 'pending' if pending else 'complete', 'content_kind': kind},
                'editorial_decision': {'code': 'pending' if pending else 'publication_volume_budget'}}}
        selected = recall_candidates(b, '2026-10-23', start_at=START, end_at=END)
        self.assertEqual([c['backlog_id'] for c in selected[:3]], ['2', '0', '3'])
        self.assertTrue(selected[2]['description'])
        self.assertEqual(selected[2]['categories'], ['Product'])

    def test_reconciliation_preserves_selection_policy(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root/'editorial_backlog.json').write_text(json.dumps({'items': {}, 'policy': {
                'max_recalled_candidates': 24, 'news_min_score': 60, 'reserved_product_news': 4}}))
            ledger = sync_backlog(root, '2026-10-23')
            self.assertEqual(ledger['policy']['max_recalled_candidates'], 24)
            self.assertEqual(ledger['policy']['news_min_score'], 60)
            self.assertEqual(ledger['policy']['reserved_product_news'], 4)

    def test_high_value_catchup_replaces_weaker_fresh_within_cap(self):
        settings = {'enabled': True, 'normal_min': 12, 'normal_max': 15, 'hard_max': 20,
                    'max_catchup': 2, 'expansion_score': 85, 'catchup_competes': True}
        def item(i, score, fresh):
            return {'event_id': str(i), 'title': str(i), 'editorial_news_window_eligible': fresh,
                    'editorial_assessment': {'score': score, 'urgency': 'routine'}}
        selected, rejected, audit = apply_budget([item(i, 64, True) for i in range(15)] +
                                                 [item('old', 83, False)], settings)
        self.assertEqual(len(selected), 15)
        self.assertEqual(selected[0]['event_id'], 'old')
        self.assertEqual(len(audit['catchup_replacements']), 1)
        self.assertEqual(rejected[0]['editorial_decision']['code'], 'publication_volume_budget')
        selected, _, audit = apply_budget([item(i, 90, True) for i in range(15)] +
                                          [item('old', 83, False)], settings)
        self.assertEqual(audit['selected_catchup_count'], 0)

    def test_research_catchup_keeps_current_product_coverage(self):
        settings = {'enabled': True, 'normal_min': 2, 'normal_max': 2, 'hard_max': 2,
                    'max_catchup': 1, 'expansion_score': 85, 'catchup_competes': True,
                    'catchup_preserve_mix': True}
        def item(eid, score, kind, fresh):
            return {'event_id': eid, 'title': eid, 'editorial_news_window_eligible': fresh,
                    'editorial_assessment': {'score': score, 'content_kind': kind}}
        selected, _, _ = apply_budget([item('product', 64, 'model_product', True),
            item('method', 69, 'research', True), item('old_method', 83, 'research', False)], settings)
        self.assertEqual({c['event_id'] for c in selected}, {'product', 'old_method'})

    def test_legacy_budget_receipt_stays_byte_equivalent(self):
        settings = {'enabled': True, 'normal_min': 12, 'normal_max': 15, 'hard_max': 20,
                    'max_catchup': 2, 'expansion_score': 85}
        _, _, audit = apply_budget([], settings)
        self.assertNotIn('catchup_replacements', audit)

    def test_real_cli_uses_paginated_discovery_without_rss_fallback(self):
        import source_selection_cli as cli
        from daily_config import load_selection_policy
        from event_editorial import checked_policy
        from test_source_selection_refactor import FakeEvaluator, TEXT
        xml = '<rss><channel></channel></rss>'
        def fetch(url, *args, **kwargs):
            if '/api/v1/items?' in url:
                return json.dumps(page([row(1)]))
            if '/api/v1/hot-topics' in url:
                return json.dumps({'schemaVersion': 1, 'items': []})
            if url.endswith('/feed/all.xml'):
                raise AssertionError('Complete API traversal must not use truncated RSS')
            return xml
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); out=root/'selection.json'; policy=load_selection_policy()
            policy['backlog']['enabled']=False; policy['source_verification']['enabled']=False
            p=root/'policy.json';p.write_text(json.dumps(policy))
            ep=root/'event.json';ep.write_text(json.dumps(checked_policy({'shared_material_cache':str(root/'cache')})))
            argv=['select','--date','2026-10-23','--start-at',START.isoformat(),'--end-at',END.isoformat(),
                  '--no-auto-previous','--no-aihot-daily','--no-bestblogs','--no-official-sources',
                  '--selection-policy',str(p),'--editorial-policy',str(ep),'--output',str(out)]
            material={'fetch_status':'fetched','source_text':TEXT*8,'page_title':'示例推理框架开放',
                      'resolved_url':'https://example.com/1'}
            with patch.object(sys,'argv',argv), patch.object(cli,'run_curl',side_effect=fetch), \
                 patch.object(cli,'latest_previous_prod_send_at',return_value=None), \
                 patch('event_editorial_runtime.Evaluator',FakeEvaluator), \
                 patch('event_editorial_runtime.source_material',return_value=material):
                cli.main()
            result=json.loads(out.read_text())
            self.assertTrue(result['source']['aihot_api']['complete'])
            self.assertEqual(result['event_editorial']['event_count'],1)


if __name__ == '__main__':
    unittest.main()
