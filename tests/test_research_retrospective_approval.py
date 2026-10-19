import copy
import sys
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from event_editorial import digest,validate_final_selection,checked_policy

class RetrospectiveTests(unittest.TestCase):
 def setUp(self):
  self.s={'event_editorial':{'policy':checked_policy({})},'selected_news':[],'selected_research':[]}
  self.item={'title':'【研究回顾】历史研究','source_date':'2026-09-14','boundary':'原论文日期 2026-09-14；受限实验。','links':[{'url':'https://arxiv.org/abs/2609.15011'}]}
  self.c={'presentation':{'date':'2026-09-21'},'paper':self.item,'news':[]}
  self.a={'kind':'user_approved_research_retrospectives','user_request':'发送日报','reason':'用户先要求补充过去研究，再批准发送已查看版本','content_sha256':digest(self.c),'items':[{'item_sha256':digest(self.item),'primary_url':self.item['links'][0]['url'],'source_review':'已阅读原论文，核对方法、数字与实验边界。'}]}
 def test_only_explicit_exact_edition_approval_passes(self):
  self.assertTrue(validate_final_selection(self.s,self.c))
  self.assertEqual(validate_final_selection(self.s,self.c,self.a),[])
 def test_edited_edition_is_rejected(self):
  self.c['paper']['boundary']+='改动'
  self.assertTrue(validate_final_selection(self.s,self.c,self.a))
 def test_other_unscored_news_cannot_piggyback(self):
  self.c['news']=[{'title':'普通新闻','url':'https://example.com/new'}]
  self.a['content_sha256']=digest(self.c)
  self.assertTrue(validate_final_selection(self.s,self.c,self.a))
 def test_missing_date_disclosure_and_new_research_rejected(self):
  for field,value in [('boundary','无日期'),('source_date','2026-09-21'),('title','普通新闻')]:
   c=copy.deepcopy(self.c);a=copy.deepcopy(self.a);c['paper'][field]=value
   a['content_sha256']=digest(c);a['items'][0]['item_sha256']=digest(c['paper'])
   self.assertTrue(validate_final_selection(self.s,c,a))
 def test_wrong_source_or_missing_review_rejected(self):
  for field,value in [('primary_url','https://example.org/unrelated'),('source_review','')]:
   a=copy.deepcopy(self.a);a['items'][0][field]=value
   self.assertTrue(validate_final_selection(self.s,self.c,a))
 def test_duplicate_retro_rejected(self):
  self.c['research_items']=[self.item,copy.deepcopy(self.item)];self.a['content_sha256']=digest(self.c)
  self.assertTrue(validate_final_selection(self.s,self.c,self.a))

if __name__=='__main__':unittest.main()
