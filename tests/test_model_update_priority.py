import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from model_update_priority import assess_model_update, subject_prior
from select_aihot_sources import (ACTIVE_SELECTION_POLICY, score_candidate,
    news_selection_score, apply_aihot_daily_signal)

POLICY = ACTIVE_SELECTION_POLICY['model_update_priority']


def candidate(title, description='', **kwargs):
    return {'title':title, 'description':description, 'source_quality_tier':'primary',
            'published_at':'2026-09-16T00:00:00+00:00', **kwargs}


class ModelUpdatePriorityTests(unittest.TestCase):
    def test_release_count_and_superlatives_do_not_establish_importance(self):
        d = assess_model_update(candidate('阶跃星辰发布StepAudio 3系列五款模型，全球第一', '模型全部上线API。'), POLICY)
        self.assertEqual(d['priority_bonus'], 0)
        self.assertEqual(d['public_attention'], 'unknown')
        self.assertFalse(d['lead_eligible'])
        self.assertLess(d['score_cap'], 80)

    def test_large_brand_minor_patch_is_not_major(self):
        d = assess_model_update(candidate('GPT-6模型修复小版本', 'OpenAI开放API，重大升级。'), POLICY)
        self.assertEqual(d['priority_bonus'], 0)
        self.assertEqual(d['score_cap'], 69)

    def test_major_releases_have_equal_path_for_domestic_families(self):
        for brand in ['GPT-6', 'Qwen', '千问', '混元', 'DeepSeek', 'Claude']:
            with self.subTest(brand=brand):
                d = assess_model_update(candidate(brand+'发布新一代旗舰模型', '开放API和权重。'), POLICY)
                self.assertEqual(d['priority_bonus'], 6)
                self.assertTrue(d['lead_eligible'])

    def test_benchmark_comparison_does_not_borrow_gpt_reach(self):
        self.assertEqual(subject_prior('小众厂商发布模型，超越GPT-6', POLICY)[0], 'unestablished')

    def test_blocked_primary_cannot_qualify(self):
        d = assess_model_update(candidate('Qwen发布新一代旗舰模型', '开放API', resolved_url='https://mp.weixin.qq.com/mp/wappoc_appmsgcaptcha'), POLICY)
        self.assertFalse(d['lead_eligible'])
        self.assertEqual(d['score_cap'], 79)
        self.assertTrue(d['source_unusable'])

    def test_unknown_breakthrough_can_qualify_with_corroboration(self):
        evidence = [{'url':f'https://{host}/review', 'observed_at':'2026-09-15T18:00:00+00:00',
                     'reviewed':True, 'independent_origin':True,
                     'kind':'independent_coverage', 'finding':'Independent review documents the new capability.'}
                    for host in ['example.org', 'example.net']]
        d = assess_model_update(candidate('新厂商发布新一代模型', '开放API，首次支持实时视频。', model_attention_evidence=evidence), POLICY)
        self.assertEqual(d['public_attention'], 'corroborated')
        self.assertTrue(d['lead_eligible'])
        self.assertEqual(d['priority_bonus'], 6)
        for item in evidence: item['origin_id']='same-press-release'
        d = assess_model_update(candidate('新厂商发布新一代模型', '开放API', model_attention_evidence=evidence), POLICY)
        self.assertFalse(d['lead_eligible'])
        self.assertEqual(d['attention_bonus'], 0)

    def test_stale_and_unreviewed_heat_are_not_counted(self):
        evidence=[{'url':f'https://{host}/review','observed_at':'2026-08-15T00:00:00+00:00','reviewed':True,
                   'independent_origin':True,'kind':'search_growth','finding':'Old growth'} for host in ['example.org','example.net']]
        d=assess_model_update(candidate('Qwen发布模型',model_attention_evidence=evidence),POLICY)
        self.assertEqual(d['attention_bonus'],0)

    def test_aggregator_bonus_cannot_bypass_ceiling(self):
        c,_=score_candidate({'title':'阶跃星辰发布StepAudio 3系列语音大模型','description':'五款模型已在平台上线API，号称多款全球第一。',
            'primary_url':'https://example.org/release','aihot_url':'https://aihot.news/items/step-fixture','categories':['模型更新']},[])
        c['scores']['final_news']=72
        apply_aihot_daily_signal([c],[{'item_id':'step-fixture','rank':1,'title':c['title']}])
        self.assertLessEqual(c['scores']['final_news'],72)
        self.assertLessEqual(news_selection_score(c),72)
        c['score_breakdown']['secondary_news_score_cap']=79
        self.assertLessEqual(news_selection_score(c),72)

    def test_qualcomm_chip_is_not_implicitly_a_model_family(self):
        self.assertEqual(subject_prior('高通发布旗舰芯片', POLICY)[0], 'unestablished')


if __name__ == '__main__':
    unittest.main()
