from copy import deepcopy
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from email_copy_repair import apply_edits, editable_fields, repair_reviewed_copy, repair_news_prose
import email_quality_control as quality
import run_ai_daily as runner


class CopyRepairTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        self.paths = SimpleNamespace(run_dir=root, run_date='2026-10-13')
        for key in ('content', 'normalized_content', 'html', 'source_selection', 'source_materials'):
            path = root/(key+'.json')
            path.write_text('{}')
            setattr(self.paths, key, path)
        self.content = {'news': [{'title': '事件报道', 'one_liner': '任务明确禁止提交表单。',
                                 'url': 'https://example.org/source', 'event_id': 'unchanged'},
                                {'title': '无关新闻', 'one_liner': '不改写此条。'}],
                        'generation': {'selection_sha256': 'unchanged'}}
        self.paths.content.write_text(json.dumps(self.content))
        self.edit = {'path': '/news/0/one_liner', 'before': self.content['news'][0]['one_liner'],
                     'after': '这次任务的具体指令未明确禁止提交表单。', 'reason': '与来源说明相反'}
        self.args = SimpleNamespace(action='prod', image_source='old-preview.png', skip_build=True)
        self.audit = {'status': 'failed', 'hashes': quality.artifact_hashes(self.paths),
                      'checks': {'editorial': {'result': {'decision': 'fail', 'issues': ['错误归因'], 'repairs': [self.edit]}}}}
        (root/'email_self_review.json').write_text(json.dumps(self.audit))

    def test_exact_copy_patch_preserves_other_stories_urls_and_metadata(self):
        expected = deepcopy(self.content)
        expected['news'][0]['one_liner'] = self.edit['after']
        self.assertEqual(apply_edits(self.content, [self.edit]), expected)
        self.assertEqual(self.content['news'][0]['one_liner'], self.edit['before'])

    def test_immutable_stale_duplicate_empty_and_invalid_edits_are_rejected(self):
        for edits in ([{**self.edit, 'path': '/news/0/url'}], [{**self.edit, 'before': 'stale'}],
                      [self.edit, self.edit], [{**self.edit, 'after': ''}], [], 'not-a-list'):
            with self.subTest(edits=edits), self.assertRaises(ValueError):
                apply_edits(self.content, edits)
        self.assertNotIn('/generation/selection_sha256', editable_fields(self.content))

    def test_copy_failure_repairs_only_copy_and_rebuilds_existing_image(self):
        with patch.object(runner, 'run_build') as build, patch.object(runner, 'run_prepare') as prepare, \
             patch.object(runner, 'run_api_image') as image:
            runner.repair_email(self.paths, self.args, 'Final email editorial QA failed: factual error')
        build.assert_called_once()
        self.assertIsNone(build.call_args.args[1].image_source)
        self.assertTrue(build.call_args.args[1].allow_candidate_image_provider)
        prepare.assert_not_called()
        image.assert_not_called()
        updated = json.loads(self.paths.content.read_text())
        self.assertEqual(updated['news'][0]['one_liner'], self.edit['after'])
        self.assertEqual(updated['news'][1], self.content['news'][1])

    def test_build_failure_rolls_back_without_whole_issue_regeneration(self):
        before = {p: p.read_bytes() for p in (self.paths.content, self.paths.normalized_content, self.paths.html)}
        def fail(*args, **kwargs):
            self.paths.html.write_text('partial build')
            raise ValueError('Corrected text exceeds field limit')
        with patch.object(runner, 'run_build', side_effect=fail), patch.object(runner, 'run_prepare') as prepare:
            result = repair_reviewed_copy(self.paths, self.args)
        self.assertEqual(result['status'], 'review_again')
        self.assertEqual(before, {p: p.read_bytes() for p in before})
        prepare.assert_not_called()
        audit = json.loads((self.paths.run_dir/'email_self_review.json').read_text())
        self.assertIn('field limit', audit['copy_repair_error'])

    def test_stale_review_cannot_mutate_current_content(self):
        self.paths.content.write_text(json.dumps({**self.content, 'summary': 'new'}))
        before = self.paths.content.read_bytes()
        with patch.object(runner, 'run_build') as build:
            self.assertEqual(repair_reviewed_copy(self.paths, self.args)['status'], 'review_again')
        build.assert_not_called()
        self.assertEqual(before, self.paths.content.read_bytes())

    def test_visual_failure_does_not_regenerate_copy(self):
        with patch.object(runner, 'run_prepare') as prepare, patch.object(runner, 'run_api_image') as image, \
             patch.object(runner, 'run_api_preview') as preview:
            runner.repair_email(self.paths, self.args, 'Visual QA did not pass: reversed arrow')
        prepare.assert_not_called()
        image.assert_called_once()
        preview.assert_called_once()
        self.assertFalse(image.call_args.args[1].refresh_api_content)
        self.assertTrue(image.call_args.args[1].refresh_api_image)

    def test_blog_and_paper_links_use_accurate_generic_source_label(self):
        from generate_research_brief_api import source_links
        for url in ['https://developer.nvidia.com/blog/example', 'https://arxiv.org/abs/2610.12345']:
            self.assertEqual(source_links({'primary_url': url}), [{'label': '来源原文', 'url': url}])

    def test_overlength_repair_rebuilds_only_text_without_new_image(self):
        import quality_review as gate
        self.content['news'][0].update(display_tier='brief', one_liner='具体事实。' * 40)
        self.paths.content.write_text(json.dumps(self.content))
        edit = {**self.edit, 'before': self.content['news'][0]['one_liner'], 'after': '保留有来源的主要事实。'}
        with patch.object(gate, 'qa_runtime', return_value=None), \
             patch.object(gate, 'call_quality_model', return_value=({'repairs':[edit]},{})), \
             patch.object(runner, 'run_build') as build, patch.object(runner, 'run_prepare') as prepare, \
             patch.object(runner, 'run_api_image') as image:
            runner.repair_email(self.paths, self.args, 'Daily editorial rewrite required: news[0].one_liner')
        build.assert_called_once()
        self.assertIsNone(build.call_args.args[1].image_source)
        prepare.assert_not_called(); image.assert_not_called()
        result=json.loads(self.paths.content.read_text())
        self.assertEqual(result['news'][1],self.content['news'][1])
        self.assertEqual(result['news'][0]['url'],self.content['news'][0]['url'])

    def test_invalid_targeted_repair_preserves_draft_for_existing_recovery(self):
        import quality_review as gate
        self.content['news'][0].update(display_tier='brief', one_liner='具体事实。' * 40)
        self.paths.content.write_text(json.dumps(self.content)); before=self.paths.content.read_bytes()
        with patch.object(gate, 'qa_runtime', return_value=None), \
             patch.object(gate, 'call_quality_model', side_effect=ValueError('gateway unavailable')):
            self.assertFalse(repair_news_prose(self.paths,self.args))
        self.assertEqual(before,self.paths.content.read_bytes())

    def test_catchup_date_counts_inside_card_validation_before_image_generation(self):
        from generate_daily_content_api import generated_news_copy, ContentApiError
        source={'primary_url':'https://example.org/source', 'display_tier':'brief','newspaper_role':'brief',
                'quiet_day_admission':{'label':'近期补漏','original_published_at':'2026-10-14T08:00:00+08:00'}}
        card={'one_liner':'甲'*115,'why':'乙'*65,'observation':'丙'*60,'boundary':'丁'*40}
        with self.assertRaisesRegex(ContentApiError,'300'):
            generated_news_copy(card,source)
        card.update(one_liner='团队已公布新的推理方法，并给出了可复现的实验流程。',why='这一方法为开发团队评估实际部署效果提供了新的参考。',observation='')
        result=generated_news_copy(card,source)
        self.assertIn(source['quiet_day_admission']['original_published_at'],result['boundary'])


if __name__ == '__main__':
    unittest.main()
