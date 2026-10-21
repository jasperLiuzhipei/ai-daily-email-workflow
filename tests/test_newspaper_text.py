"""Regression checks for image-free preparation and guarded text email sending."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import newspaper_text as n
import run_ai_daily as runner
from build_ai_daily_email import enrich_content, DEFAULT_FOOTER
from validate_ai_daily_content import validate_content
from types import SimpleNamespace

class NewspaperTests(unittest.TestCase):
    def setUp(self):
        legacy={'mode':'newspaper_text','version':'newspaper-text-v1','effective_date':'2026-09-14'}
        config_patch=patch.object(n,'config',return_value=legacy)
        config_patch.start();self.addCleanup(config_patch.stop)

    def test_effective_date(self):
        self.assertFalse(n.enabled('2026-09-11'))
        self.assertTrue(n.enabled('2026-09-15'))
    def test_prepare_skips_image(self):
        with patch.object(runner,'run_api_compose') as compose, patch.object(runner,'run_api_image') as image:
            runner.run_prepare(SimpleNamespace(run_date='2026-09-15'),SimpleNamespace())
            compose.assert_called_once();image.assert_not_called()
    def test_preview_builds_without_candidate(self):
        with patch.object(runner,'run_build') as build:
            runner.run_api_preview(SimpleNamespace(run_date='2026-09-15'),SimpleNamespace())
            build.assert_called_once()
    def test_week_subject(self):
        args=runner.weekly_conversation_args({'conversation':{'mode':'weekly','subject_template':'AI 情报雷达｜{year}年第{week}周'}},'2026-09-14')
        self.assertEqual(args,['--subject','AI 情报雷达｜2026年第38周'])
    def test_future_without_images_and_tamper_guard(self):
        data=json.loads((Path(__file__).resolve().parent / 'fixtures/newspaper_issue.json').read_text())
        from datetime import date
        data=enrich_content(data,date(2026,9,15),45,'trial',DEFAULT_FOOTER,{})
        data=n.configure_content(data,'2026-09-15')
        self.assertNotIn('image',data['paper']);validate_content(data)
        html=n.render_email(data)
        self.assertNotIn('<img',html)
        self.assertIn('本期其余新闻与简讯',html)
        self.assertNotIn('应用、安全与研究进展',html)
        self.assertNotIn('PlannerForge：连接场景测试模块',html)  # prior issue's reviewed TOC is not reused
        import hashlib
        data['presentation']['html_sha256']=hashlib.sha256(html.encode()).hexdigest()
        data['presentation']['content_sha256']=n.build_content_signature(data)
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'daily.html';p.write_text(html);n.assert_text_send(p,data,'2026-09-15')
            p.write_text(html+'changed')
            with self.assertRaises(ValueError):n.assert_text_send(p,data,'2026-09-15')
    def test_news_only_has_no_stale_research(self):
        data=json.loads((Path(__file__).resolve().parent / 'fixtures/newspaper_issue.json').read_text())
        data.pop('paper');data['news']=data['news'][:1];data['issue']=45
        data=n.configure_content(data,'2026-09-15');html=n.render_email(data)
        self.assertNotIn('id="np-research"',html)
        self.assertNotIn('id="np-supplement"',html)
        self.assertNotIn('id="np-quick"',html)

    def test_all_brief_selection_preserves_feature_sections_and_rest_are_concise(self):
        from copy import deepcopy
        data=json.loads((Path(__file__).resolve().parent / 'fixtures/newspaper_issue.json').read_text())
        data.pop('paper');data['news']=[deepcopy(data['news'][0]) for _ in range(3)];data['issue']=45
        for item in data['news']:
            item.pop('event_id',None)
            item['display_tier']='brief'
            item['observation']='重点栏目需要保留独立分析'
            item['boundary']='目前仅为测试版。'
        data['news'][2]['observation']='这段长实践不应出现在简讯中'
        data=n.configure_content(data,'2026-09-15');html=n.render_email(data)
        self.assertEqual(data['presentation']['order_policy'],'editor_order')
        self.assertNotIn('这段长实践不应出现在简讯中',html)
        self.assertIn('目前仅为测试版。',html)
        self.assertIn('id="np-lead"',html)
        self.assertIn('id="np-supplement"',html)
        self.assertIn('重点栏目需要保留独立分析',html)

    def test_historical_roles_preserve_original_topics_with_source_metadata(self):
        from bs4 import BeautifulSoup
        data=json.loads((Path(__file__).resolve().parent / 'fixtures/newspaper_issue.json').read_text())
        data.pop('paper');data['issue']=45;data['news']=data['news'][:3]
        for i,item in enumerate(data['news']):
            item.update(display_tier='brief',event_id=f'e{i}',daily_rank=i+1,
                        newspaper_role='lead' if i==2 else 'supplement' if i==0 else 'brief')
        data=n.configure_content(data,'2026-09-15')
        soup=BeautifulSoup(n.render_email(data),'html.parser')
        self.assertEqual(soup.select_one('#np-lead .np-title a')['href'],data['news'][2]['url'])
        self.assertEqual(soup.select_one('#np-supplement .np-title a')['href'],data['news'][0]['url'])
        self.assertEqual(len(soup.select('.np-brief')),1)

    def test_send_rejects_missing_sections_even_with_current_hashes(self):
        from bs4 import BeautifulSoup
        import hashlib
        data=json.loads((Path(__file__).resolve().parent / 'fixtures/newspaper_issue.json').read_text())
        data.pop('paper');data['issue']=45
        for item in data['news']:
            item['display_tier']='brief'
        data=n.configure_content(data,'2026-09-15')
        soup=BeautifulSoup(n.render_email(data),'html.parser')
        soup.select_one('#np-lead').decompose()
        html=str(soup)
        data['presentation']['html_sha256']=hashlib.sha256(html.encode()).hexdigest()
        data['presentation']['content_sha256']=n.build_content_signature(data)
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'daily.html';path.write_text(html)
            with self.assertRaisesRegex(ValueError,'feature section'):
                n.assert_text_send(path,data,'2026-09-15')

    def test_send_rejects_duplicated_quick_topic_even_with_current_hashes(self):
        from bs4 import BeautifulSoup
        import hashlib
        data=json.loads((Path(__file__).resolve().parent / 'fixtures/newspaper_issue.json').read_text())
        data=n.configure_content(data,'2026-09-15')
        soup=BeautifulSoup(n.render_email(data),'html.parser')
        links=soup.select('.np-brief h3 a')
        self.assertEqual(len(links),2)
        links[1]['href']=links[0]['href']
        html=str(soup)
        data['presentation']['html_sha256']=hashlib.sha256(html.encode()).hexdigest()
        data['presentation']['content_sha256']=n.build_content_signature(data)
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'daily.html';path.write_text(html)
            with self.assertRaisesRegex(ValueError,'section coverage'):
                n.assert_text_send(path,data,'2026-09-15')

    def test_send_rejects_missing_research_even_with_current_hashes(self):
        from bs4 import BeautifulSoup
        import hashlib
        data=json.loads((Path(__file__).resolve().parent / 'fixtures/newspaper_issue.json').read_text())
        data=n.configure_content(data,'2026-09-15')
        soup=BeautifulSoup(n.render_email(data),'html.parser')
        soup.select_one('#np-research').decompose()
        html=str(soup)
        data['presentation']['html_sha256']=hashlib.sha256(html.encode()).hexdigest()
        data['presentation']['content_sha256']=n.build_content_signature(data)
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'daily.html';path.write_text(html)
            with self.assertRaisesRegex(ValueError,'research coverage'):
                n.assert_text_send(path,data,'2026-09-15')

    def test_final_validation_requires_explanation_for_feature_even_with_brief_score(self):
        from datetime import date
        from validate_ai_daily_content import ValidationError
        data=json.loads((Path(__file__).resolve().parent / 'fixtures/newspaper_issue.json').read_text())
        data.pop('paper');data['news']=data['news'][:2]
        for item in data['news']:
            item.update(display_tier='brief',observation='')
        data=n.configure_content(enrich_content(data,date(2026,9,15),45,'trial',DEFAULT_FOOTER,{}),'2026-09-15')
        for explicit in (False,True):
            if explicit:
                data['news'][0]['newspaper_role']='lead';data['news'][1]['newspaper_role']='supplement'
            with self.subTest(explicit=explicit),self.assertRaisesRegex(ValidationError,'observation'):
                validate_content(data)

    def test_legacy_tier_cannot_hide_a_visible_qualifier(self):
        from copy import deepcopy
        data=json.loads((Path(__file__).resolve().parent / 'fixtures/newspaper_issue.json').read_text())
        data.pop('paper');seed=data['news'][0];data['issue']=45
        data['news']=[deepcopy(seed) for _ in range(3)]
        for index,item in enumerate(data['news']):
            item['event_id']=f'legacy-{index}'
            item['display_tier']='deep' if index<2 else 'brief'
        data['news'][2].pop('display_tier')
        data['news'][2]['observation']='该结果只适用于企业内测用户。'
        data=n.configure_content(data,'2026-09-15')
        html=n.render_email(data)
        self.assertIn('该结果只适用于企业内测用户。',html)

if __name__=='__main__':unittest.main()
