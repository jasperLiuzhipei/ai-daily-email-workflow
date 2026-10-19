"""Regression cases for resolved short links and continued daily delivery."""
from copy import deepcopy
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import publication_dedup as dedup
from event_editorial import digest, select_events, DEFAULT_POLICY
from ai_daily_common import file_sha256
from workflow_context import assert_content_lineage


URL = 'https://blog.google/model-release'
QUOTE = 'Gemini 4 Argon is now globally available via the API and for Pro subscribers in the Gemini app.'


def announcement(title='Gemini 4 Argon 开始在 Gemini 应用中开放使用', day='Oct 13, 2026'):
    packet = {'sources': [{'source_id': 's', 'url': 'https://x.com/GeminiApp/status/123',
                          'read_status': 'read', 'text': f'Our model is rolling out. 4:05 PM · {day} · Views'}]}
    return {'event_id': 'app', 'title': title, 'primary_url': packet['sources'][0]['url'],
            'editorial_packet': packet, 'editorial_assessment': {'primary_source_id': 's', 'input_sha256': digest(packet)}}


class DuplicateTests(unittest.TestCase):
    def setUp(self):
        self.previous = [{'published_on': '2026-10-14', 'title': 'Google开放Gemini 4 Argon',
                          'urls': [URL], 'evidence': [QUOTE]}]

    def test_short_link_resolved_to_published_article_is_detected(self):
        c = {'title': 'Google 分享更多 AI 学习资源', 'primary_url': 'https://goo.gle/short'}
        self.assertFalse(dedup.duplicate_check(c, self.previous)['is_duplicate'])
        c['primary_url'] = URL + '/?utm_source=social'
        check = dedup.duplicate_check(c, self.previous)
        self.assertEqual(check['reason'], 'resolved_same_url')
        self.assertEqual(check['published_on'], '2026-10-14')

    def test_old_app_announcement_already_in_published_release_is_removed(self):
        self.assertEqual(dedup.duplicate_check(announcement(), self.previous)['reason'],
                         'already_published_availability')

    def test_new_developments_variants_and_unproven_dates_are_retained(self):
        cases = [announcement('Gemini 4 Argon 向免费用户开放'),
                 announcement('Gemini 4 Pro 开始在 Gemini 应用中开放使用'),
                 announcement(day='Oct 14, 2026'), announcement(day='Oct 15, 2026'),
                 {'title': '40名总检察长介入Character.AI破产案', 'primary_url': 'https://news.test/new'}]
        changed = announcement(); changed['editorial_assessment']['what_changed'] = '向新地区扩大开放范围'
        cases.append(changed)
        stale = announcement(); stale['editorial_packet']['sources'][0]['text'] = 'modified'
        cases.append(stale)
        for c in cases:
            with self.subTest(c=c['title']):
                self.assertFalse(dedup.duplicate_check(c, self.previous)['is_duplicate'])

    def test_unknown_history_is_a_visible_warning_not_failure(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); (root/'send_ledger.json').write_text('invalid json')
            items, warnings = dedup.history('2026-10-15', root)
            self.assertEqual(items, [])
            self.assertTrue(warnings)

    def test_drafts_and_current_day_are_not_publication_history(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root/'send_ledger.json').write_text(json.dumps({'prod': {
                '2026-10-13': {'published_items': [{'title': 'unsent'}]},
                '2026-10-14': {'sent_at': 'yes', 'published_items': [{'title': 'sent', 'url': URL}]},
                '2026-10-15': {'sent_at': 'yes', 'published_items': [{'title': 'same day'}]}}}))
            items, warnings = dedup.history('2026-10-15', root)
            self.assertEqual([i['title'] for i in items], ['sent'])
            self.assertEqual(warnings, [])

    def test_confirmed_repeat_is_not_readmitted_by_score(self):
        c = {'title': 'old', 'event_id': 'old', 'publication_duplicate_check': {
             'is_duplicate': True, 'published_on': '2026-10-14', 'matched_previous': 'old'}}
        research, news, rejected = select_events([c], DEFAULT_POLICY)
        self.assertEqual((research, news), ([], []))
        self.assertEqual(rejected[0]['editorial_decision']['action'], 'exclude')


class ReconciliationTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        self.paths = SimpleNamespace(run_date='2026-10-15', run_dir=root)
        for name in ['content', 'source_selection', 'source_materials']:
            setattr(self.paths, name, root/(name+'.json'))
        self.old = {'event_id': 'old', 'title': '重复报道', 'url': URL, 'one_liner': '原文'}
        self.new = {'event_id': 'new', 'title': '新进展', 'url': 'https://news.test/new', 'one_liner': '新的事实'}
        self.paper = {'title': '研究解读', 'links': [{'url': 'https://paper.test/new'}], 'image': 'unchanged.jpg'}
        self.selection = {'selected_news': [self.old, self.new], 'selected_research': [], 'quiet_day_selection': {'original': True}}
        self.paths.source_selection.write_text(json.dumps(self.selection))
        material = {'selection_sha256': file_sha256(self.paths.source_selection), 'news': [self.old,self.new], 'research': []}
        self.paths.source_materials.write_text(json.dumps(material))
        self.original = {'news': [self.old,self.new], 'paper':self.paper, 'generation': {
            'selection_sha256':file_sha256(self.paths.source_selection),
            'source_material_sha256':file_sha256(self.paths.source_materials)}}
        self.paths.content.write_text(json.dumps(self.original))
        self.previous = [{'published_on':'2026-10-14','title':'重复报道','urls':[URL],'evidence':[]}]
        mock = patch.object(dedup,'history',return_value=(self.previous,[]));mock.start();self.addCleanup(mock.stop)

    def test_reconcile_continues_with_remaining_cards_and_preserves_image_lineage(self):
        self.assertTrue(dedup.reconcile(self.paths))
        result = json.loads(self.paths.content.read_text())
        self.assertEqual([c['event_id'] for c in result['news']], ['new'])
        self.assertEqual(result['paper'], self.paper)
        self.assertEqual(result['news'][0]['one_liner'], self.new['one_liner'])
        assert_content_lineage(self.paths)
        self.assertEqual(json.loads(self.paths.source_selection.read_text())['quiet_day_selection'], {'original': True})
        self.assertEqual(len(list((self.paths.run_dir/'publication_dedup_history').glob('*/result.json'))),1)
        before = self.paths.content.read_bytes()
        self.assertFalse(dedup.reconcile(self.paths))
        self.assertEqual(before,self.paths.content.read_bytes())

    def test_research_only_issue_can_continue(self):
        self.previous.append({'published_on':'2026-10-14','title':'new','urls':['https://news.test/new'],'evidence':[]})
        self.assertTrue(dedup.reconcile(self.paths))
        self.assertEqual(json.loads(self.paths.content.read_text())['news'], [])

    def test_partial_send_cannot_be_modified(self):
        (self.paths.run_dir/'smtp_prod_delivery.json').write_text('{"sent":["redacted"]}')
        before=self.paths.content.read_bytes()
        with self.assertRaisesRegex(ValueError,'delivery has started'):
            dedup.reconcile(self.paths)
        self.assertEqual(before,self.paths.content.read_bytes())

    def test_failed_write_rolls_back_lineage_files(self):
        from send_email_package_smtp import write_json_atomic
        paths=[self.paths.content,self.paths.source_selection,self.paths.source_materials]
        before={p:p.read_bytes() for p in paths}
        def fail(path,value):
            if path==self.paths.source_materials:raise OSError('simulated')
            write_json_atomic(path,value)
        with patch('send_email_package_smtp.write_json_atomic',side_effect=fail),self.assertRaises(OSError):
            dedup.reconcile(self.paths)
        self.assertEqual(before,{p:p.read_bytes() for p in paths})


if __name__ == '__main__':
    unittest.main()
