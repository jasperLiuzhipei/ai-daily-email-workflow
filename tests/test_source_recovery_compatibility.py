"""Document identity, conservative URL repair and bounded recovery behavior."""
from copy import deepcopy
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from event_editorial import checked_policy
from fetch_selected_source_material import normalize_source_url, source_material
from source_identity import first_primary_url
from source_document import read_document
from source_verification import material_readability
from quiet_day_recovery import recover_pending, recovery_plan, recovery_urls

PAPER = 'https://arxiv.org/abs/2610.12345v1'
ABSTRACT = 'We evaluate how independent agents share evidence across reasoning steps. ' * 6
BODY = 'Runtime migration supports independent permissions and durable execution. ' * 8


def paper_material():
    return {'fetch_status': 'fetched', 'resolved_url': PAPER, 'http_status': 200,
        'source_text': 'arXiv:2610.12345v1 (cs) Title: Agent reasoning Abstract: ' + ABSTRACT + ' Subjects: cs.AI',
        'document_identity': {'declared_urls': [PAPER.removesuffix('v1'), PAPER],
                              'og_type': 'website', 'body_kind': 'main'}}


class DocumentCompatibilityTests(unittest.TestCase):
    def test_translated_arxiv_abstract_accepts_matching_versioned_identity(self):
        candidate = {'primary_url': PAPER, 'title': '论文研究证据共享的边界'}
        self.assertEqual(material_readability(candidate, paper_material()), (True, 'matching_arxiv_identity'))
        candidate['primary_url'] = PAPER.removesuffix('v1')
        self.assertTrue(material_readability(candidate, paper_material())[0])

    def test_arxiv_different_paper_version_host_and_ambiguous_declarations_rejected(self):
        for edits in [
            {'resolved_url': PAPER.replace('12345', '99999')},
            {'resolved_url': PAPER.replace('v1', 'v2')},
            {'resolved_url': PAPER.removesuffix('v1')},
            {'resolved_url': PAPER.replace('arxiv.org', 'arxiv.org.example.com')},
            {'declared_urls': [PAPER, PAPER.replace('v1', 'v2')]},
            {'declared_urls': [PAPER.replace('12345', '99999')]},
        ]:
            m = paper_material()
            if 'declared_urls' in edits:
                m['document_identity'].update(edits)
            else:
                m.update(edits)
            with self.subTest(edits=edits):
                self.assertFalse(material_readability({'primary_url': PAPER, 'title': '论文研究证据共享'}, m)[0])

    def test_arxiv_requires_abstract_and_matching_body_identifier(self):
        for text in [BODY, 'arXiv:2610.99999v1 Abstract: ' + ABSTRACT,
                     'arXiv:2610.12345v1 Abstract: Too short. Subjects: cs.AI ' + BODY,
                     'Access denied arXiv:2610.12345v1 Abstract: ' + ABSTRACT]:
            m = paper_material(); m['source_text'] = text
            self.assertFalse(material_readability({'primary_url': PAPER, 'title': '论文研究证据共享'}, m)[0])
        m = paper_material(); m['fetch_status'] = 'fallback_after_fetch_error'
        self.assertFalse(material_readability({'primary_url': PAPER}, m)[0])

    def test_overlapping_english_title_cannot_override_conflicting_paper_identity(self):
        m = paper_material(); m['resolved_url'] = PAPER.replace('v1', 'v2')
        self.assertEqual(material_readability({'primary_url': PAPER, 'title': 'Agent reasoning'}, m),
                         (False, 'conflicting_document_identity'))
        m = paper_material(); m['document_identity']['declared_urls'] = [PAPER.replace('12345', '99999')]
        self.assertFalse(material_readability({'primary_url': PAPER, 'title': 'Agent reasoning'}, m)[0])

    def test_x_redirects_and_twitter_alias_share_post_identity_without_replies(self):
        for requested in ['https://x.com/i/web/status/12345', 'https://twitter.com/old_handle/status/12345']:
            raw = ('<link rel="canonical" href="https://x.com/i/web/status/12345">'
                '<meta property="og:type" content="article"><main>'
                '<article>' + BODY + '<a href="https://twitter.com/new_handle/status/12345">Post</a></article>'
                '<article>' + BODY * 5 + '<a href="/reply/status/99999">Reply</a></article></main>')
            title, text, _, identity = read_document(raw)
            self.assertEqual(text.count('Runtime migration'), 8)
            m = {'fetch_status': 'fetched', 'resolved_url': 'https://x.com/new_handle/status/12345',
                 'source_text': text, 'page_title': title, 'document_identity': identity}
            c = {'primary_url': requested, 'title': '工具接口升级公告'}
            self.assertEqual(material_readability(c, m), (True, 'matching_social_post_identity'))
            for final in ['https://x.com/new_handle/status/99999', 'https://evil.test/new_handle/status/12345']:
                self.assertFalse(material_readability(c, {**m, 'resolved_url': final})[0])
            self.assertFalse(material_readability(c, {**m, 'source_text': 'Short post.'})[0])

    def test_social_body_must_be_the_requested_post(self):
        m = {'fetch_status': 'fetched', 'resolved_url': 'https://x.com/account/status/12345',
             'source_text': BODY, 'document_identity': {'declared_urls': ['https://x.com/account/status/12345'],
             'og_type': 'article', 'body_kind': 'main'}}
        self.assertFalse(material_readability({'primary_url': m['resolved_url'], 'title': '工具接口升级公告'}, m)[0])

    def test_observed_alternate_post_can_supply_topic_evidence_without_claiming_same_identity(self):
        original = {'primary_url': 'https://x.com/one/status/123', 'title': 'Runtime migration'}
        alternate = 'https://x.com/two/status/456'
        m = {'fetch_status': 'fetched', 'primary_url': alternate, 'resolved_url': alternate,
             'source_text': BODY, 'document_identity': {'declared_urls': [alternate],
             'og_type': 'article', 'body_kind': 'social_post'}}
        self.assertEqual(material_readability(original, m), (True, 'title_overlap'))
        m['resolved_url'] = 'https://x.com/other/status/789'
        self.assertFalse(material_readability(original, m)[0])


class LinkCleanupTests(unittest.TestCase):
    def test_feed_prose_delimiter_is_removed_before_fetch_with_original_recorded(self):
        bad = 'https://clau.de/reset）等着你'
        self.assertEqual(first_primary_url('详情（' + bad, ''), 'https://clau.de/reset')
        with patch('fetch_selected_source_material.fetch_html', return_value=(f'<article>{BODY}</article>',
                'https://example.com/reset', 200)) as fetch:
            m = source_material({'primary_url': bad}, max_chars=1000, timeout_seconds=1, max_bytes=10000)
        self.assertEqual(fetch.call_args.args[0], 'https://clau.de/reset')
        self.assertEqual(m['url_normalization']['original_url'], bad)
        self.assertEqual(m['fetch_status'], 'fetched')

    def test_entities_encoded_delimiters_and_meaningful_queries_preserved(self):
        self.assertEqual(normalize_source_url('https://example.com/a?x=1&amp;y=2'), 'https://example.com/a?x=1&y=2')
        for url in ['https://example.com/a?x=%EF%BC%89&sig=A%2FB%3D',
                    'https://example.com/a_(b)?q=value', 'https://example.com/中文路径',
                    'https://example.com/a?x=1&amplitude=3']:
            self.assertEqual(normalize_source_url(url), url)

    def test_cleanup_cannot_make_private_or_ambiguous_unicode_url_fetchable(self):
        for url in ['http://127.0.0.1/admin）尾文', 'https://example.com/没有分隔符的路径']:
            with patch('fetch_selected_source_material.fetch_html', side_effect=AssertionError('must not fetch')):
                m = source_material({'primary_url': url}, max_chars=1000, timeout_seconds=1, max_bytes=10000)
            self.assertEqual(m['fetch_status'], 'skipped_unsafe_or_missing_url')

    def test_recovery_considers_all_observed_fields_and_deduplicates_normalized_urls(self):
        urls = recovery_urls({'primary_url': 'https://clau.de/reset）尾文',
            'coverage_source_urls': ['https://clau.de/reset', 'https://aihot.news/items/1'],
            'discovery_excerpt': 'https://example.com/one', 'description': 'https://example.com/two',
            'source_excerpt': 'https://example.com/three'})
        self.assertEqual(urls, ['https://clau.de/reset', 'https://example.com/one',
                               'https://example.com/two', 'https://example.com/three'])


class RecoverySchedulingTests(unittest.TestCase):
    def event(self, eid, error='HTTP 403', alternate=None):
        c = {'editorial_source_id': eid, 'primary_url': f'https://example.com/{eid}', 'title': 'Runtime migration'}
        if alternate:
            c['coverage_source_urls'] = [alternate]
        event = {'event_id': eid, 'sources': [c], 'merge_reason': ''}
        material = {'source_id': eid, 'url': c['primary_url'], 'read_status': 'discovery_only',
            'text': '', 'fetch_status': 'fallback_after_fetch_error', 'fetch_error': error}
        return event, material

    def recover(self, events, materials, fetch, max_events=12, assessor=None, remaining=lambda: 100):
        class Evaluator:
            identity = ['offline-fixture']
        if assessor is None:
            def assessor(event, material, *args):
                return {'assessment_status': 'pending'}, {'sources': [deepcopy(material[c['editorial_source_id']]) for c in event['sources']]}
        assessed = [({'assessment_status': 'pending'}, {'sources': [deepcopy(materials[c['editorial_source_id']])
            for c in e['sources']]}) for e in events]
        with tempfile.TemporaryDirectory() as folder, patch('quiet_day_recovery.source_material', side_effect=fetch):
            return recover_pending(events, assessed, materials, policy=checked_policy({'admission_contract': 'value-evidence-v3', 'brief_value_floor': 60, 'quiet_day': {
                'recovery_max_events': max_events}}), evaluator=Evaluator(), output_dir=folder,
                prompt='', remaining=remaining, assessor=assessor)

    def success(self, candidate, **kwargs):
        return {'fetch_status': 'fetched', 'resolved_url': candidate['primary_url'],
                'source_text': BODY, 'page_title': 'Runtime migration'}

    def test_known_denials_without_routes_do_not_consume_event_limit(self):
        e1, m1 = self.event('blocked')
        e2, m2 = self.event('recoverable', alternate='https://alternate.test/story')
        calls = []
        def fetch(c, **kwargs):
            calls.append(c['primary_url']); return self.success(c)
        result, audit = self.recover([e1, e2], {'blocked': m1, 'recoverable': m2}, fetch, max_events=1)
        self.assertEqual(calls, ['https://alternate.test/story'])
        self.assertEqual(audit['events_attempted'], 1)
        self.assertEqual(audit['events_skipped_no_recovery_route'], 1)
        self.assertEqual(audit['status'], 'completed')
        self.assertEqual(audit['sources_recovered'], 1)
        self.assertEqual(result[1][0]['assessment_status'], 'pending')  # Retrieval isn't publication approval.

    def test_403_and_404_skipped_but_transient_failure_retried(self):
        for error, expected in [('HTTP 403', False), ('HTTP 404', False), ('HTTP 429', True),
                                ('HTTP 503', True), ('network error: timed out', True)]:
            e, m = self.event('item', error)
            urls, skipped = recovery_plan(e['sources'][0], m)
            self.assertEqual(bool(urls), expected)
            self.assertEqual(bool(skipped), not expected)
        e, m = self.event('temporary', 'HTTP 503')
        _, audit = self.recover([e], {'temporary': m}, self.success)
        self.assertEqual(audit['http_requests'], 1)
        self.assertEqual(audit['sources_recovered'], 1)

    def test_short_and_captcha_responses_not_immediately_refetched(self):
        for reason in ['short_body', 'blocked_page', 'unmatched_document']:
            e, m = self.event('item', '')
            m.update(fetch_status='fetched', readability_reason=reason)
            urls, skipped = recovery_plan(e['sources'][0], m)
            self.assertEqual(urls, [])
            self.assertEqual(skipped[0]['reason'], 'same_run_' + reason)

    def test_observed_alternate_before_transient_primary_retry(self):
        e, m = self.event('item', 'HTTP 503', 'https://other.test/story')
        urls, _ = recovery_plan(e['sources'][0], m)
        self.assertEqual(urls, ['https://other.test/story', 'https://example.com/item'])

    def test_shared_alternate_fetched_once_and_revalidated_for_each_candidate(self):
        e1, m1 = self.event('one', alternate='https://alternate.test/story')
        e2, m2 = self.event('two', alternate='https://alternate.test/story')
        e2['sources'][0]['title'] = '完全不相干的另一篇文章'
        calls = []
        def fetch(c, **kwargs):
            calls.append(c['primary_url']); return self.success(c)
        _, audit = self.recover([e1, e2], {'one': m1, 'two': m2}, fetch)
        self.assertEqual(len(calls), 1)
        self.assertEqual(audit['http_requests'], 1)
        self.assertEqual(audit['sources_recovered'], 1)
        self.assertTrue(audit['attempts'][1]['response_reused'])
        self.assertFalse(audit['attempts'][1]['read'])

    def test_failed_retrieval_does_not_call_assessor_without_new_evidence(self):
        e, m = self.event('temporary', 'HTTP 503')
        def assessor(*args):
            self.fail('No readable evidence, so no new model assessment')
        _, audit = self.recover([e], {'temporary': m}, lambda *a, **k: {
            'fetch_status': 'fallback_after_fetch_error', 'fetch_error': 'HTTP 503'}, assessor=assessor)
        self.assertEqual(audit['sources_recovered'], 0)
        self.assertEqual(audit['remaining_pending'], 1)

    def test_same_host_other_observed_article_not_blocked_and_time_budget_respected(self):
        e, m = self.event('one', alternate='https://example.com/different')
        self.assertEqual(recovery_plan(e['sources'][0], m)[0], ['https://example.com/different'])
        def no_fetch(*args, **kwargs):
            self.fail('Budget already exhausted')
        _, audit = self.recover([e], {'one': m}, no_fetch, remaining=lambda: 0)
        self.assertEqual(audit['events_attempted'], 0)
        self.assertEqual(audit['status'], 'exhausted')

    def test_existing_readable_response_reused_without_http_or_score_promotion(self):
        e1, m1 = self.event('blocked', alternate='https://example.com/readable')
        e2, m2 = self.event('readable', '')
        m2.update(read_status='read', fetch_status='fetched', text=BODY)
        def no_fetch(*a, **k):
            self.fail('Already fetched this exact URL')
        result, audit = self.recover([e1, e2], {'blocked': m1, 'readable': m2}, no_fetch)
        self.assertEqual(audit['http_requests'], 0)
        self.assertEqual(audit['sources_recovered'], 1)
        self.assertEqual(result[0][0]['assessment_status'], 'pending')

    def test_failed_url_seen_on_another_source_not_retried_as_an_alternate(self):
        e1, m1 = self.event('one', alternate='https://example.com/two')
        e2, m2 = self.event('two')
        def no_fetch(*a, **k):
            self.fail('Both observed URLs already returned 403 in this run')
        _, audit = self.recover([e1, e2], {'one': m1, 'two': m2}, no_fetch)
        self.assertEqual(audit['http_requests'], 0)
        self.assertEqual(audit['status'], 'no_recovery_routes')

    def test_canonicalized_html_query_duplicate_uses_original_clean_link(self):
        from event_editorial import canonical_url
        raw = 'https://example.com/a?first=1&amp;second=2'
        urls = recovery_urls({'primary_url': raw, 'coverage_source_urls': [canonical_url(raw)]})
        self.assertEqual(urls, ['https://example.com/a?first=1&second=2'])


if __name__ == '__main__':
    unittest.main()
