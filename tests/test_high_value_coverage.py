import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import select_aihot_sources as selector
from coverage_review import CoverageReviewError, assert_ready, refresh_review
from daily_config import load_selection_policy
from source_verification import verify_candidate, enrich_high_value_candidates, readable, high_value_reasons


POLICY = load_selection_policy()
# Preserve regression coverage for the opt-in legacy blocking policy.
POLICY['coverage_review']['omission_policy'] = 'blocking'


def model():
    return {'title': 'Qwen 发布原生全模态模型 Qwen3.8-Omni-Flash',
            'primary_url': 'https://qwen.ai/blog?id=qwen3.8-omni-flash',
            'description': '下一代原生全模态模型，音频价格降低98%，1M上下文。',
            'source_type': 'model_release', 'source_quality_tier': 'discovery',
            'aihot_url': '', 'categories': ['AI 模型'], 'published_at': '2026-09-18T01:18:08+08:00',
            'aihot_daily_featured': True, 'aihot_daily_rank': 1}


class HighValueCoverageTests(unittest.TestCase):
    def test_official_blog_author_article_is_not_the_story(self):
        text = 'Copilot runtime migration to Rust: end-to-end tests preserve behavioral contracts. ' * 30
        raw = '<title>Copilot runtime migration</title><main><section class="post__content">' + text + '</section><article>Author bio</article></main>'
        self.assertEqual(readable(raw)[1], text.strip())

    def test_old_research_backlog_does_not_become_fresh_news(self):
        self.assertFalse(selector.news_window_eligible({'source_type':'paper','research_window_strategy':'unpublished_backlog'}))
        self.assertFalse(selector.news_window_eligible({'source_type':'methodology','source_pool':'research','research_window_strategy':'unpublished_backlog'}))
        self.assertTrue(selector.news_window_eligible({'source_type':'industry_news','research_window_strategy':'unpublished_backlog'}))

    def test_bestblogs_featured_item_requires_review(self):
        item = {'title':'Independent engineering article', 'bestblogs_score':92}
        self.assertIn('aggregator_featured', high_value_reasons(item, POLICY['source_verification'], POLICY['model_update_priority']))

    def test_generic_score_only_reason_does_not_close_review(self):
        from coverage_review import specific_reason
        self.assertFalse(specific_reason('分数低。'))
        self.assertFalse(specific_reason('low score'))
        self.assertTrue(specific_reason('与昨天已刊条目同一版本、同一能力，原文未提供新增结果。'))

    def test_featured_leads_missing_before_scoring_still_block(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);s=root/'s.json';c=root/'c.json';r=root/'r.json'
            s.write_text(json.dumps({'all_candidates_scored':[], 'source':{'supplemental_news_sources':[
                {'featured_entries':[{'item_id':'abc123','rank':1,'title':'An important release'}]},
                {'primary_url_resolution_errors':[{'title':'Engineering case','url':'https://bestblogs.dev/article/abc','reason':'primary_url_unresolved'}]}
            ]}}));c.write_text(json.dumps({'news':[]}))
            report=refresh_review(s,c,r,POLICY)
            self.assertEqual(report['summary']['pending'],2)
            with self.assertRaises(CoverageReviewError):assert_ready(s,c,r,POLICY)

    def test_more_than_twelve_same_topic_independent_news_survive_reading_budget(self):
        candidates = []
        for i in range(17):
            c, _ = selector.score_candidate({'title': f'工程实践 {i}: agent evaluation',
                                            'description': 'AI 工程评估方法', 'published_at': '2026-09-18T08:00:00+08:00', 'categories': [], 'aihot_url': '',
                                            'primary_url': f'https://github.blog/engineering/case-{i}'}, [])
            c['selection_group'] = 'methodology'
            c['source_type'] = 'industry_news'
            c['research_eligible'] = False
            c['scores'].update(final_news=77, final_research=0)
            c['score_breakdown'] = {}
            candidates.append(c)
        _, selected, rejected = selector.select_items(candidates, research_count=0, min_news_count=5)
        self.assertEqual(len(selected), 17)
        self.assertEqual(rejected, [])
        self.assertGreater(selector.selected_reading_estimate([], selected)['total_minutes'], 10)
        _, capped, _ = selector.select_items(candidates, research_count=0, min_news_count=5, max_news_count=8)
        self.assertEqual(len(capped), 8)

    def test_qwen_dynamic_shell_is_not_primary_but_official_api_is(self):
        calls = []
        body = '<article><h1>Qwen3.8-Omni-Flash</h1>' + ('Our next-generation model is now available via API. Audio price decreases by 98%. ' * 8) + '</article>'
        def fetch(url, **kwargs):
            calls.append(url)
            if '/api/v2/article/' in url:
                return json.dumps({'success': True, 'data': {'path': 'qwen3.8-omni-flash', 'content': body}}), url, 200
            return '<html><title>Qwen</title><div id="root"></div></html>', url, 200
        with tempfile.TemporaryDirectory() as d:
            c = verify_candidate(model(), policy=POLICY['source_verification'], output_dir=Path(d), fetcher=fetch)
            self.assertEqual(c['source_quality_tier'], 'primary')
            self.assertEqual(c['primary_verification']['status'], 'verified')
            self.assertEqual(len(calls), 2)
            self.assertIn('path=qwen3.8-omni-flash', calls[1])
            scored, _ = selector.score_candidate(c, [])
            self.assertTrue(scored['model_update_priority']['lead_eligible'])

    def test_failed_or_unrelated_official_page_does_not_promote(self):
        def fetch(url, **kwargs):
            return '<title>Other product</title>' + 'unrelated content ' * 60, url, 200
        with tempfile.TemporaryDirectory() as d:
            c = verify_candidate(model(), policy=POLICY['source_verification'], output_dir=Path(d), fetcher=fetch)
            self.assertEqual(c['primary_verification']['status'], 'unresolved')
            self.assertEqual(c['source_quality_tier'], 'discovery')
            self.assertTrue(c['primary_verification']['attempts'])

    def test_budget_skipped_candidates_are_explicit(self):
        with tempfile.TemporaryDirectory() as d:
            result = enrich_high_value_candidates([model()], policy={**POLICY['source_verification'], 'max_candidates': 0},
                        model_policy=POLICY['model_update_priority'], output_dir=Path(d), rescore=lambda c: c)
            self.assertEqual(result[0]['primary_verification']['status'], 'not_attempted_budget')

    def test_pending_omission_blocks_send_and_stale_reason_is_invalidated(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d);selection = root/'selection.json'; content = root/'content.json';review=root/'review.json'
            c = model();c['source_excerpt'] = c.pop('description')
            selection.write_text(json.dumps({'all_candidates_scored':[c]}));content.write_text(json.dumps({'news':[]}))
            report = refresh_review(selection, content, review, POLICY)
            self.assertEqual(report['summary']['pending'], 1)
            with self.assertRaises(CoverageReviewError): assert_ready(selection, content, review, POLICY)
            row=report['items'][0];row.update(disposition='defer', editor='test-editor', reason='Official body retrieval failed after attempts; retain for next issue and explain source limitation.')
            review.write_text(json.dumps(report));self.assertEqual(assert_ready(selection,content,review,POLICY)['summary']['pending'],0)
            c['primary_verification']={'status':'verified'};selection.write_text(json.dumps({'all_candidates_scored':[c]}))
            with self.assertRaises(CoverageReviewError):assert_ready(selection,content,review,POLICY)

    def test_covered_item_resolves_and_generation_drop_reappears(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);s=root/'s.json';c=root/'c.json';r=root/'r.json';item=model()
            s.write_text(json.dumps({'all_candidates_scored':[item], 'selected_news':[item]}))
            c.write_text(json.dumps({'news':[{'title':'千问新模型', 'url':item['primary_url']}]}))
            self.assertEqual(assert_ready(s,c,r,POLICY)['summary']['pending'],0)
            c.write_text(json.dumps({'news':[]}))
            with self.assertRaises(CoverageReviewError):assert_ready(s,c,r,POLICY)
            self.assertIn('missing from final',json.loads(r.read_text())['items'][0]['machine_reason'])

    def test_prod_skip_build_still_checks_selected_content_before_packaging(self):
        import argparse
        import run_ai_daily as runner
        from email_quality_control import selected_content_checks, EmailQualityError
        with tempfile.TemporaryDirectory() as d:
            paths=runner.default_paths('2026-09-19')
            paths.run_dir=Path(d);paths.source_selection=Path(d)/'s.json';paths.content=Path(d)/'c.json';paths.normalized_content=Path(d)/'n.json'
            paths.source_selection.write_text(json.dumps({'all_candidates_scored':[model()], 'selected_news':[model()]}));paths.content.write_text(json.dumps({'news':[]}))
            paths.normalized_content.write_text(json.dumps({'date': '9月19日', 'news': [], 'presentation': {'source_content_sha256': runner.file_sha256(paths.content)}}))
            args=argparse.Namespace(send=True,confirm_prod=True,allow_candidate_image_provider=False,allow_resend_prod=False,skip_build=True)
            # Exercise completeness through the final-copy review path without
            # invoking a model or the retired candidate-selection review.
            with patch.object(runner,'review_current_email', side_effect=lambda p,a: selected_content_checks(p,json.loads(p.normalized_content.read_text()))), patch.object(runner,'run_coverage_review',side_effect=AssertionError('Selection review must not run')), patch.object(runner,'validate_content') as prose, patch.object(runner,'assert_content_lineage') as lineage, patch.object(runner.newspaper_text,'enabled',return_value=False), patch.object(runner,'run_package') as package:
                with self.assertRaisesRegex(EmailQualityError, 'Selected content missing'):runner.check_production_email(paths,args)
                prose.assert_called_once()
                lineage.assert_called_once_with(paths)
                package.assert_not_called()
                self.assertFalse((Path(d)/'high_value_coverage_review.json').exists())


if __name__ == '__main__':
    unittest.main()
