"""Translated leads require an actual article bound to the requested URL."""
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from source_document import read_document
from source_verification import material_readability, relevant, verify_candidate
from fetch_selected_source_material import source_material
from event_editorial_runtime import prepare_material
from event_editorial import checked_policy
from quiet_day_recovery import recover_pending

URL = 'https://example.org/posts/new-tool'
TEXT = 'The new tool combines document search with existing permission controls. ' * 12
CANDIDATE = {'primary_url': URL, 'title': '企业知识检索工具开放预览'}


def document(body=None, canonical=URL, kind='article'):
    return (f'<html><head><title>New search tool</title><link rel="canonical" href="{canonical}">'
            f'<meta property="og:type" content="{kind}"><meta property="og:url" content="{canonical}">'
            '</head><body>' + (body if body is not None else f'<article>{TEXT}</article>') + '</body></html>')


def material(raw, final_url=URL):
    with patch('fetch_selected_source_material.fetch_html', return_value=(raw, final_url, 200)):
        return source_material(CANDIDATE, max_chars=12000, timeout_seconds=1, max_bytes=1500000)


class SourceDocumentIdentityTests(unittest.TestCase):
    def test_translated_title_requires_matching_article_identity(self):
        m = material(document())
        self.assertFalse(relevant(CANDIDATE, m['page_title'], m['source_text']))
        self.assertEqual(material_readability(CANDIDATE, m), (True, 'matching_article_identity'))
        self.assertEqual(len(m['document_identity']['response_sha256']), 64)
        with tempfile.TemporaryDirectory() as folder, patch('event_editorial_runtime.source_material', return_value=m):
            record = prepare_material(CANDIDATE, folder=Path(folder), policy=checked_policy({}))
        self.assertEqual(record['read_status'], 'read')
        self.assertEqual(record['readability_reason'], 'matching_article_identity')
        self.assertEqual(record['document_identity'], m['document_identity'])
        self.assertNotIn('source_authority', record)

    def test_official_verification_uses_same_identity_gate(self):
        with tempfile.TemporaryDirectory() as folder:
            c = verify_candidate(CANDIDATE, policy={'official_hosts': ['example.org']},
                output_dir=folder, fetcher=lambda *a, **k: (document(), URL, 200))
        self.assertEqual(c['primary_verification']['status'], 'verified')
        self.assertEqual(c['primary_verification']['readability_reason'], 'matching_article_identity')

    def test_recovery_sends_restored_evidence_through_assessment(self):
        c = {**CANDIDATE, 'editorial_source_id': 's1'}
        event = {'event_id': 'e1', 'sources': [c], 'merge_reason': ''}
        materials = {'s1': {'source_id': 's1', 'url': URL, 'read_status': 'discovery_only', 'text': ''}}
        class Evaluator:
            identity = ['test']
        def assessor(event, restored, *args):
            self.assertEqual(restored['s1']['read_status'], 'read')
            # Retrieval is not approval: an assessor may still defer it.
            return {'assessment_status': 'pending'}, {'sources': list(restored.values())}
        with tempfile.TemporaryDirectory() as folder, patch('quiet_day_recovery.source_material', return_value=material(document())):
            assessed, audit = recover_pending([event], [({'assessment_status': 'pending'}, {})], materials,
                policy=checked_policy({'quiet_day': {'enabled': True}}), evaluator=Evaluator(),
                output_dir=Path(folder), prompt='', remaining=lambda: 100, assessor=assessor)
        self.assertEqual(audit['sources_recovered'], 1)
        self.assertEqual(audit['remaining_material_gaps'], 0)
        self.assertEqual(audit['remaining_pending'], 1)
        self.assertEqual(assessed[0][0]['assessment_status'], 'pending')

    def test_redirect_unrelated_canonical_shell_challenge_and_short_body_stay_unread(self):
        cases = [
            (document(), 'https://example.org/login'),
            (document(canonical='https://example.org/posts/unrelated'), URL),
            (document().replace('</head>', '<link rel="canonical" href="/other"></head>'), URL),
            (document(body=f'<main>Log in Sign up {TEXT}</main>'), URL),
            (document(body='<article>Access denied ' + TEXT + '</article>'), URL),
            (document(body='<article>Video clip only</article>'), URL),
            (document(kind='website'), URL),
        ]
        for raw, final in cases:
            with self.subTest(final=final, raw=raw[:100]):
                self.assertFalse(material_readability(CANDIDATE, material(raw, final))[0])
        m = material(document())
        m.update(fetch_status='fallback_after_fetch_error', fetch_error='HTTP 403')
        self.assertEqual(material_readability(CANDIDATE, m), (False, 'fetch_unavailable'))

    def test_discovery_excerpt_and_alternate_url_cannot_supply_identity(self):
        m = material(document())
        self.assertFalse(material_readability({**CANDIDATE, 'primary_url': 'https://example.org/other'}, m)[0])
        with tempfile.TemporaryDirectory() as folder:
            c = {**CANDIDATE, 'source_text': TEXT, 'document_identity': m['document_identity']}
            record = prepare_material(c, folder=Path(folder), policy=checked_policy({}), offline=True)
        self.assertEqual(record['read_status'], 'discovery_only')

    def test_dev_article_body_wins_over_embedded_readme(self):
        raw = document(body='<article><div class="crayons-article__body">Real article introduction. '
            + TEXT + '<article class="entry-content">Embedded README only.</article>'
            '</div></article>')
        _, text, _, identity = read_document(raw)
        self.assertIn('Real article introduction.', text)
        self.assertGreater(len(text), len(TEXT))
        self.assertEqual(identity['body_kind'], 'explicit_article')

    def test_social_post_identity_excludes_title_duplication_and_longer_replies(self):
        url = 'https://x.com/example/status/123456'
        def raw(body):
            return document(canonical=url, body='<main>Log in Sign up '
                + '<article>' + body + '<a href="/example/status/123456">Today</a></article>'
                + '<article>' + TEXT * 4 + '<a href="/reply/status/999">Reply</a></article></main>')
        title, text, _, identity = read_document(raw(TEXT))
        self.assertEqual(identity['body_kind'], 'social_post')
        self.assertNotIn('Log in', text)
        self.assertEqual(text.count('The new tool'), 12)
        c = {**CANDIDATE, 'primary_url': url}
        m = {'fetch_status': 'fetched', 'page_title': title, 'source_text': text,
             'resolved_url': url, 'document_identity': identity}
        self.assertTrue(material_readability(c, m)[0])
        title, text, _, identity = read_document(raw('Short post.'))
        m.update(page_title=title, source_text=text, document_identity=identity)
        self.assertEqual(material_readability(c, m), (False, 'short_body'))

    def test_unsafe_final_response_remains_a_fetch_failure(self):
        m = material(document(), 'http://127.0.0.1/private')
        self.assertEqual(m['fetch_status'], 'fallback_after_fetch_error')
        self.assertNotIn('document_identity', m)


if __name__ == '__main__':
    unittest.main()
