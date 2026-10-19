import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from event_editorial import (EditorialError, checked_policy, digest, eligibility, group_sources,
    event_family_key, news_window_eligible, select_events, unique_sources, validate_assessment, validate_final_selection)
from event_editorial_runtime import assess_event, prepare_material
from fetch_selected_source_material import source_material, SourceMaterialError, fetch_group
from coverage_review import assert_ready, CoverageReviewError

POLICY = checked_policy({})
TEXT = 'Runtime migration preserved behavior through replayable end-to-end tests and staged rollout.'


def packet(eid='e1'):
    return {'event_id': eid, 'audience': POLICY['audience'], 'sources': [
        {'source_id': 's1', 'url': 'https://example.com/runtime', 'text': TEXT,
         'read_status': 'read'}]}


def assessment(eid='e1', level=3):
    return {'event_id': eid, 'event_title': '运行时迁移中的行为一致性验证', 'primary_source_id': 's1',
            'content_kind': 'engineering', 'what_changed': '迁移采用行为回放测试验证与旧版本一致。',
            'reader_takeaway': '研发团队可借鉴端到端回放测试方法。', 'limitations': '结果限于这一运行时迁移工程案例。',
            'decision_reason': '提供具体的渐进迁移及验证方法，研发团队可以借鉴。',
            'evidence_status': 'supported', 'novelty': 'new', 'urgency': 'routine', 'urgency_reason': '',
            'evidence': [{'source_id': 's1', 'quote': TEXT}],
            'dimensions': {k: {'level': level, 'reason': '该工程案例展示了具体行为回放与渐进验证方法。',
                               'evidence_refs': [0]} for k in POLICY['weights']},
            'illustration': {'eligible': False, 'reason': '本案例采用文字说明已经足以理解核心方法。'}}


def candidate(eid='e1', level=3):
    p = packet(eid)
    a = validate_assessment(assessment(eid, level), p, POLICY)
    return {'event_id': eid, 'title': a['event_title'], 'primary_url': 'https://example.com/runtime',
            'editorial_assessment': a, 'editorial_packet': p,
            'editorial_approved_urls': ['https://example.com/runtime'],
            'source_text': TEXT, 'editorial_source_text_sha256': digest(TEXT)}


class EventEditorialTests(unittest.TestCase):
    def test_long_authentic_quote_does_not_discard_an_assessment(self):
        a, p = assessment(), packet()
        quote = 'A controlled evaluation found a specific failure under stated conditions. ' * 8
        p['sources'][0]['text'] = quote
        a['evidence'][0]['quote'] = quote
        checked = validate_assessment(a, p, POLICY)
        self.assertEqual(checked['evidence'][0]['quote'], quote)
        self.assertEqual(checked['score'], 75)
        a['evidence'][0]['quote'] = quote + ' Invented outcome.'
        with self.assertRaises(EditorialError): validate_assessment(a, p, POLICY)
        oversized = quote * 4
        p['sources'][0]['text'] = oversized
        a['evidence'][0]['quote'] = oversized
        with self.assertRaises(EditorialError): validate_assessment(a, p, POLICY)

    def test_quotes_and_evidence_identity_are_required(self):
        for change in ('invented_quote', 'unknown_source', 'wrong_event'):
            a = assessment()
            if change == 'invented_quote': a['evidence'][0]['quote'] = 'This quote does not exist in the article.'
            if change == 'unknown_source': a['primary_source_id'] = 'not-given'
            if change == 'wrong_event': a['event_id'] = 'other'
            with self.assertRaises(EditorialError): validate_assessment(a, packet(), POLICY)

    def test_brand_heat_and_old_scores_do_not_affect_selection(self):
        c = candidate(); c.update(scores={'final_news': 100}, brand_bonus=999, bestblogs_score=100)
        _, news, _ = select_events([c], POLICY)
        self.assertEqual(news[0]['editorial_assessment']['score'], 75)
        self.assertEqual(news[0]['display_tier'], 'brief')

    def test_quiet_day_does_not_fill_and_research_can_be_empty(self):
        research, news, rejected = select_events([candidate(level=2)], POLICY, research_count=1)
        self.assertEqual((research, news), ([], []))
        self.assertEqual(rejected[0]['editorial_decision']['action'], 'exclude')

    def test_unknown_score_is_deferred_not_zero(self):
        a = assessment(); a['dimensions']['change']['level'] = None
        checked = validate_assessment(a, packet(), POLICY)
        self.assertIsNone(checked['score'])
        self.assertEqual(eligibility(checked, POLICY)[0], 'defer')

    def test_discovery_excerpt_cannot_be_verified_by_model(self):
        p = packet();p['sources'][0]['read_status'] = 'discovery_only'
        with self.assertRaises(EditorialError):validate_assessment(assessment(), p, POLICY)

    def test_no_category_quota_or_minmax_scaling(self):
        items = [candidate(str(i)) for i in range(20)]
        _, news, _ = select_events(items, POLICY)
        self.assertEqual(len(news), 20)
        self.assertEqual({x['editorial_assessment']['score'] for x in news}, {75})

    def test_same_model_release_is_one_news_event_but_other_versions_survive(self):
        titles = [
            'Claude Sonnet 5.5发布：同价提速并带来API迁移变更',
            'Anthropic 发布 Claude Sonnet 5.5，主打更快推理',
            'Anthropic 发布 Claude Opus 5.5，增加新能力',
        ]
        items = []
        for index, title in enumerate(titles):
            item = candidate(str(index))
            item['title'] = title
            items.append(item)
        self.assertEqual(event_family_key(items[0]), event_family_key(items[1]))
        self.assertEqual(event_family_key(items[0]), event_family_key({'title': 'Sonnet 5.5 正式发布'}))
        self.assertNotEqual(event_family_key(items[1]), event_family_key(items[2]))
        research, news, rejected = select_events(items, POLICY)
        self.assertEqual(len(research), 0)
        self.assertEqual(len(news), 2)
        self.assertEqual(len(rejected), 1)
        self.assertTrue(rejected[0]['event_duplicate_check']['is_duplicate'])

    def test_research_redteam_event_is_not_repeated_as_news(self):
        research = candidate('space-research')
        research['title'] = 'Perplexity 红队测试揭示：代理沙箱网络绕过路径'
        research['source_excerpt'] = 'Perplexity 披露对 SPACE 沙箱开展的红队测试。'
        research['editorial_assessment']['illustration']['eligible'] = True
        news = candidate('space-news')
        news['title'] = 'Perplexity披露SPACE智能体沙箱红队测试'
        selected_research, selected_news, rejected = select_events([research, news], POLICY)
        self.assertEqual(len(selected_research), 1)
        self.assertEqual(selected_news, [])
        self.assertEqual(len(rejected), 1)

    def test_high_score_requires_second_pass(self):
        c = candidate(level=4)
        self.assertEqual(select_events([c], POLICY)[1], [])
        c['editorial_assessment']['second_review'] = {'kind': 'same_model_second_pass_not_independent_verification'}
        self.assertEqual(len(select_events([c], POLICY)[1]), 1)

    def test_source_dedupe_does_not_merge_different_releases(self):
        sources = unique_sources([{'primary_url': 'http://www.example.com/a/?utm_source=one',
                                   'coverage_source_urls': ['https://aihot.news/items/originalalias']},
                                  {'primary_url': 'https://example.com/a'},
                                  {'primary_url': 'https://example.com/b'}])
        self.assertEqual(len(sources), 2)
        self.assertTrue(any('https://aihot.news/items/originalalias' in c['coverage_source_urls'] for c in sources))
        self.assertEqual(len(group_sources(sources, [])), 2)
        ids = [s['editorial_source_id'] for s in sources]
        with self.assertRaises(EditorialError):
            group_sources(sources, [{'source_ids': [ids[0], 'invented'], 'reason': '这里说明一个充分具体的合并理由'}])

    def test_overlap_group_is_rejected(self):
        sources = unique_sources([{'primary_url': 'https://example.com/' + str(i)} for i in range(3)])
        ids = [s['editorial_source_id'] for s in sources]
        groups = [{'source_ids': ids[:2], 'reason': '这是同一个发布事件的重复报道材料'},
                  {'source_ids': ids[1:], 'reason': '这是同一个发布事件的重复报道材料'}]
        with self.assertRaises(EditorialError):group_sources(sources, groups)

    def test_final_gate_rejects_alias_laundering_low_score_and_duplicates(self):
        c = candidate(); s = {'event_editorial': {'policy': POLICY}, 'selected_news': [c]}
        good = {'news': [{'title': 'selected', 'url': c['primary_url']}]}
        self.assertEqual(validate_final_selection(s, good), [])
        bad = {'news': [{'title': 'unselected', 'url': 'https://example.com/unselected',
                         'coverage_source_urls': [c['primary_url']]}]}
        self.assertTrue(validate_final_selection(s, bad))
        self.assertTrue(validate_final_selection(s, {'news': good['news'] * 2}))
        s['selected_news'][0]['editorial_assessment']['score'] = 100
        self.assertTrue(validate_final_selection(s, good))

    def test_changed_evidence_invalidates_send_and_composition(self):
        c = candidate(); c['editorial_packet']['sources'][0]['text'] += ' Modified evidence.'
        self.assertTrue(validate_final_selection({'event_editorial': {'policy': POLICY}, 'selected_news': [c]},
                                                 {'news': [{'url': c['primary_url']}]}))
        with self.assertRaises(SourceMaterialError):
            source_material(c, max_chars=1000, timeout_seconds=1, max_bytes=1000)

    def test_changed_body_and_self_hash_cannot_replace_evaluated_primary(self):
        for field in ('body', 'url', 'allowlist'):
            c = candidate()
            url = c['primary_url']
            if field == 'body':
                c['source_text'] = 'A different body'; c['editorial_source_text_sha256'] = digest(c['source_text'])
            elif field == 'url':
                c['primary_url'] = 'https://example.com/different'
            else:
                url = 'https://example.com/different';c['editorial_approved_urls'].append(url)
            self.assertTrue(validate_final_selection({'event_editorial': {'policy': POLICY}, 'selected_news': [c]},
                                                     {'news': [{'url': url}]}))
            if field != 'allowlist':
                with self.assertRaises(SourceMaterialError):
                    source_material(c, max_chars=1000, timeout_seconds=1, max_bytes=1000)
                with self.assertRaises(SourceMaterialError):
                    fetch_group([c], max_chars=1000, timeout_seconds=1, max_bytes=1000, workers=1)

    def test_unambiguous_one_based_evidence_refs_are_normalized(self):
        a = assessment()
        for d in a['dimensions'].values():d['evidence_refs'] = [1]
        checked = validate_assessment(a, packet(), POLICY)
        self.assertEqual(checked['score'], 75)
        self.assertEqual(checked['dimensions']['impact']['evidence_refs'], [0])
        self.assertEqual(validate_assessment(checked, packet(), POLICY), checked)
        a['dimensions']['impact']['evidence_refs'] = [0, 1]
        with self.assertRaises(EditorialError):validate_assessment(a, packet(), POLICY)

    def test_backlog_keeps_original_news_window(self):
        window = {'news_start_at': '2026-09-17T10:00:00+08:00', 'end_at': '2026-09-18T10:00:00+08:00'}
        c = {'published_at': '2026-09-12T10:00:00+08:00', 'research_window_strategy': 'unpublished_backlog'}
        self.assertFalse(news_window_eligible(c, window))
        c.update(published_at='2026-09-16T10:00:00+08:00', research_window_strategy='official_release_catchup')
        self.assertTrue(news_window_eligible(c, window))
        c['published_at'] = '2026-09-20T10:00:00+08:00'
        self.assertFalse(news_window_eligible(c, window))

    def test_api_failure_is_pending_not_legacy_score(self):
        class Failing:
            identity = ['test']
            def __call__(self, *args): raise RuntimeError('unavailable')
        event = {'event_id': 'e1', 'merge_reason': '', 'sources': [{'editorial_source_id': 's1'}]}
        materials = {'s1': packet()['sources'][0]}
        with tempfile.TemporaryDirectory() as d:
            a, _ = assess_event(event, materials, POLICY, Failing(), Path(d), 'test prompt')
        self.assertEqual(a['assessment_status'], 'pending')
        self.assertIsNone(a['score'])

    def test_pending_backlog_can_retry_retrieval(self):
        c = {'primary_url': 'https://example.com/runtime', 'title': 'Runtime migration',
             'editorial_assessment': {'assessment_status': 'pending', 'score': None}}
        def fetch(new_candidate, **kwargs):
            self.assertNotIn('editorial_assessment', new_candidate)
            return {'fetch_status': 'fetched', 'source_text': TEXT * 8, 'page_title': 'Runtime migration'}
        with tempfile.TemporaryDirectory() as d, patch('event_editorial_runtime.source_material', side_effect=fetch):
            result = prepare_material(c, folder=Path(d), policy=POLICY)
        self.assertEqual(result['read_status'], 'read')

    def test_material_links_to_resolved_primary_not_discovery_page(self):
        c = {'primary_url': 'https://huggingface.co/papers/2609.12345', 'source_type': 'paper', 'title': 'Runtime migration'}
        with tempfile.TemporaryDirectory() as d, patch('event_editorial_runtime.source_material', return_value={
                'fetch_status': 'fetched', 'source_text': TEXT * 8, 'page_title': 'Runtime migration',
                'resolved_url': 'https://arxiv.org/abs/2609.12345'}):
            result = prepare_material(c, folder=Path(d), policy=POLICY)
        self.assertEqual(result['url'], 'https://arxiv.org/abs/2609.12345')

    def test_bad_weights_and_levels_rejected(self):
        with self.assertRaises(EditorialError):checked_policy({'weights': {'change': 100}})
        for value in (True, 4.1, 5, -1):
            a = assessment(); a['dimensions']['change']['level'] = value
            with self.assertRaises(EditorialError):validate_assessment(a, packet(), POLICY)

    def test_coverage_cannot_approve_unselected_content(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); c = candidate()
            s = {'event_editorial': {'policy': POLICY}, 'selected_news': [c], 'all_candidates_scored': [c]}
            (root/'s.json').write_text(json.dumps(s)); (root/'c.json').write_text(json.dumps({'news': [{'url': 'https://example.com/low'}]}))
            with self.assertRaises(CoverageReviewError):assert_ready(root/'s.json', root/'c.json', root/'r.json')


if __name__ == '__main__':
    unittest.main()
