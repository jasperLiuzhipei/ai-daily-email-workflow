import sys,json,hashlib,tempfile,unittest
import base64
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import newspaper_text as n
import run_ai_daily as runner
from validate_ai_daily_content import ValidationError, validate_paper
from build_ai_daily_email import enrich_content,DEFAULT_FOOTER
from datetime import date
class IllustratedTests(unittest.TestCase):
 def setUp(self):
  temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
  self.image=Path(temp.name)/'research-fixture.png'
  self.image.write_bytes(base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aT4sAAAAASUVORK5CYII='))
 def data(self):
  d=json.loads((Path(__file__).resolve().parent / 'fixtures/newspaper_issue.json').read_text())
  d['paper']['image']=str(self.image)
  return n.configure_content(enrich_content(d,date(2026,9,15),45,'trial',DEFAULT_FOOTER,{}),'2026-09-15')
 def test_historical_date_and_future(self):
  self.assertTrue(n.text_only('2026-09-14'));self.assertTrue(n.illustrated('2026-09-15'));self.assertFalse(n.illustrated('2026-09-11'))
 def test_prepare_uses_image_pipeline(self):
  with patch.object(runner,'run_api_compose') as compose,patch.object(runner,'run_api_image') as image,patch.object(runner,'selected_research_exists',return_value=False):
   runner.run_prepare(SimpleNamespace(run_date='2026-09-16',content='content'),SimpleNamespace())
   compose.assert_called_once();image.assert_called_once()
 def test_image_preserved_and_missing_image_rejected(self):
  d=self.data();self.assertEqual(d['presentation']['mode'],'newspaper_illustrated');self.assertIn('image',d['paper'])
  self.assertIn('<img',n.render_email(d));d['paper'].pop('image')
  with self.assertRaises(ValueError):n.render_email(d)
 def test_send_rejects_swapped_image_even_with_rehashed_html(self):
  d=self.data();html=n.render_email(d)
  with tempfile.TemporaryDirectory(dir=Path.cwd()) as temp:
   p=Path(temp)/'mail.html'
   html=html.replace('src="assets/','src="../assets/')
   p.write_text(html);d['presentation']['content_sha256']=n.build_content_signature(d);d['presentation']['html_sha256']=hashlib.sha256(html.encode()).hexdigest();n.assert_text_send(p,d,'2026-09-15')
   html=html.replace('research-fixture.png','other.png');p.write_text(html);d['presentation']['content_sha256']=n.build_content_signature(d);d['presentation']['html_sha256']=hashlib.sha256(html.encode()).hexdigest()
   with self.assertRaises(ValueError):n.assert_text_send(p,d,'2026-09-15')
 def test_news_only_renders_without_image(self):
  d=self.data();d.pop('paper');d=n.configure_content(d,'2026-09-15');self.assertEqual(d['presentation']['mode'],'newspaper_text');self.assertNotIn('<img',n.render_email(d))
 def test_illustrated_research_keeps_analysis_and_rejects_visual_relation_leak(self):
  d=self.data();d['presentation']['date']='2026-09-29'
  html=n.render_email(d)
  research=html.split('id="np-research"',1)[1].split('id="np-supplement"',1)[0]
  for section in ('研究问题','方法机制','关键证据','部门能力启发'):
   self.assertIn(section,research)
  self.assertIn(d['paper']['mechanism'],research)
  d['paper']['mechanism'] += '\n关系：模型｜请求｜网关'
  with self.assertRaisesRegex(ValidationError,'visual handoff relation syntax'):
   validate_paper(d['paper'])
 def test_send_rejects_relation_fragment_even_when_html_hash_matches(self):
  d=self.data();d['paper']['mechanism'] += '\nDNS 响应｜SPACE 网关'
  html=n.render_email(d)
  with tempfile.TemporaryDirectory(dir=Path.cwd()) as temp:
   p=Path(temp)/'mail.html';p.write_text(html)
   d['presentation']['content_sha256']=n.build_content_signature(d);d['presentation']['html_sha256']=hashlib.sha256(html.encode()).hexdigest()
   with self.assertRaisesRegex(ValueError,'visual handoff relation syntax'):
    n.assert_text_send(p,d,'2026-09-15')
 def test_independent_research_findings_keep_their_paragraphs(self):
  from bs4 import BeautifulSoup
  d=self.data();d['paper']['evidence']='第一组实验发现终态错误，调用正常不能替代结果检查。\n\n第二组重复实验区分任务覆盖与稳定性。'
  soup=BeautifulSoup(n.render_email(d),'html.parser')
  paragraphs=soup.select('.rd-support')[0].select('p')
  self.assertEqual([p.get_text() for p in paragraphs],d['paper']['evidence'].split('\n\n'))
 def test_legacy_aliases_and_evidence_lists_cannot_hide_visual_relations(self):
  for alias in ('core_idea','evidence'):
   d=self.data()
   if alias=='core_idea':
    d['paper'].pop('mechanism');d['paper']['core_idea']='关系：请求｜经过｜网关'
   else:
    d['paper']['evidence']=[{'label':'机制记录','text':'请求｜经过｜网关'}]
   with self.subTest(field=alias),self.assertRaisesRegex(ValidationError,'visual handoff relation syntax'):
    validate_paper(d['paper'])
if __name__=='__main__':unittest.main()
