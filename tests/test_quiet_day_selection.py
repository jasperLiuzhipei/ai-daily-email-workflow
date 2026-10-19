import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from event_editorial import checked_policy, validate_assessment, digest, validate_final_selection
from event_editorial_runtime import apply_selection, assess_event
from quiet_day_selection import select_quiet_day, apply_quiet_labels
from quiet_day_recovery import recover_pending
from test_event_editorial import assessment, packet, TEXT

POLICY=checked_policy({'quiet_day':{'enabled':True}})
WINDOW={'end_at':'2026-09-21T08:30:00+08:00'}
RECOVERY={'status':'completed'}

def fixture(eid,levels=(3,2,3,3),**kwargs):
 p=packet(eid);p['sources'][0]['url']='https://example.com/'+eid
 a=assessment(eid)
 for k,v in zip(POLICY['weights'],levels):a['dimensions'][k]['level']=v
 a=validate_assessment(a,p,POLICY)
 return {'event_id':eid,'title':a['event_title'],'primary_url':p['sources'][0]['url'],
  'editorial_assessment':a,'editorial_packet':p,'source_text':TEXT,'editorial_source_text_sha256':digest(TEXT),
  'published_at':'2026-09-21T01:00:00+08:00','editorial_news_window_eligible':True,**kwargs}

class QuietDayTests(unittest.TestCase):
 def test_recovery_required_and_scores_immutable(self):
  cs=[fixture('a')];old=copy.deepcopy(cs)
  self.assertEqual(select_quiet_day(cs,POLICY,WINDOW,None)[1],[])
  _,n,_,r=select_quiet_day(cs,POLICY,WINDOW,RECOVERY)
  self.assertEqual(n[0]['editorial_assessment']['score'],69)
  self.assertEqual(cs,old)
  self.assertTrue(r['below_target'])

 def test_high_volume_keeps_normal_threshold(self):
  cs=[fixture(str(i),(3,3,3,3)) for i in range(3)]+[fixture('extra')]
  _,n,_,r=select_quiet_day(cs,POLICY,WINDOW,None)
  self.assertEqual(len(n),3);self.assertFalse(r['triggered'])

 def test_step_down_and_cap(self):
  _,n,_,r=select_quiet_day([fixture(str(i)) for i in range(8)],POLICY,WINDOW,RECOVERY)
  self.assertEqual(len(n),4);self.assertEqual(r['effective_threshold'],65)
  cs=[fixture('a')]+[fixture(str(i),(2,2,3,3)) for i in range(4)]
  _,n,_,r=select_quiet_day(cs,POLICY,WINDOW,RECOVERY)
  self.assertEqual(r['effective_threshold'],60);self.assertEqual(len(n),4)

 def test_no_pending_conflicting_repeat_duplicate_or_low_value(self):
  cs=[fixture(str(i)) for i in range(6)]
  cs[0]['editorial_assessment'].update(assessment_status='pending',score=None)
  cs[1]['editorial_assessment']['evidence_status']='conflicting'
  cs[2]['editorial_assessment']['novelty']='repeat'
  cs[3]['content_duplicate_check']={'is_duplicate':True}
  cs[4]['backlog_status']='published'
  cs[5]=fixture('low',(2,2,2,2))
  self.assertEqual(select_quiet_day(cs,POLICY,WINDOW,RECOVERY)[1],[])

 def test_catchup_has_original_date_and_expiry(self):
  c=fixture('a',editorial_news_window_eligible=False,published_at='2026-09-19T08:00:00+08:00')
  _,n,_,_=select_quiet_day([c],POLICY,WINDOW,RECOVERY)
  self.assertEqual(n[0]['quiet_day_admission']['label'],'近期补漏')
  for day in ['2026-09-10T08:00:00+08:00','2026-09-22T08:00:00+08:00','unknown']:
   c['published_at']=day
   self.assertEqual(select_quiet_day([c],POLICY,WINDOW,RECOVERY)[1],[])

 def test_final_gate_and_labels_replay_exact_admission(self):
  s=apply_selection({'window':WINDOW},[fixture('a')],{'policy':POLICY,'quiet_day_recovery':RECOVERY})
  c=s['selected_news'][0]
  news=apply_quiet_labels([{'title':'标题','url':c['primary_url']}],[c])
  self.assertEqual(validate_final_selection(s,{'news':news}),[])
  bad=copy.deepcopy(s);bad['quiet_day_selection']['effective_threshold']=1
  self.assertTrue(validate_final_selection(bad,{'news':news}))
  news[0]['title']='未标注'
  self.assertTrue(validate_final_selection(s,{'news':news}))

 def test_floor_and_explicit_news_limit(self):
  with self.assertRaises(ValueError):checked_policy({'quiet_day':{'enabled':True,'thresholds':[55]}})
  _,n,_,_=select_quiet_day([fixture('a'),fixture('b')],POLICY,WINDOW,RECOVERY,max_news_count=1)
  self.assertEqual(len(n),1)

 def test_recovery_403_uses_observed_alternate_and_keeps_trace(self):
  c={'editorial_source_id':'s1','primary_url':'https://example.com/blocked','title':'Runtime migration',
     'coverage_source_urls':['https://example.com/blocked','https://example.org/alternate']}
  event={'event_id':'e1','sources':[c],'merge_reason':''}
  pending={'assessment_status':'pending','score':None}
  materials={'s1':{'source_id':'s1','url':c['primary_url'],'read_status':'discovery_only','text':'short'}}
  calls=[]
  def fetch(c,**kw):
   calls.append(c['primary_url'])
   if len(calls)==1:return {'fetch_status':'fallback_after_fetch_error','fetch_error':'HTTP 403','source_text':''}
   return {'fetch_status':'fetched','source_text':TEXT*8,'page_title':'Runtime migration','resolved_url':c['primary_url']}
  def assessor(e,m,*args):return ({'assessment_status':'complete','score':69}, {'sources':list(m.values())})
  class E:identity=['test']
  with tempfile.TemporaryDirectory() as d,patch('quiet_day_recovery.source_material',side_effect=fetch):
   result,audit=recover_pending([event],[(pending,{})],materials,policy=POLICY,evaluator=E(),
      output_dir=Path(d),prompt='',remaining=lambda:100,assessor=assessor)
  self.assertEqual(calls,[c['primary_url'],'https://example.org/alternate'])
  self.assertEqual(audit['sources_recovered'],1)
  self.assertEqual(audit['attempts'][0]['error'],'HTTP 403')
  self.assertEqual(result[0][0]['score'],69)

 def test_exhausted_recovery_does_not_fake_success(self):
  class E:identity=['test']
  e={'event_id':'e','sources':[{'editorial_source_id':'s','primary_url':'https://example.com/a'}]}
  with tempfile.TemporaryDirectory() as d,patch('quiet_day_recovery.source_material',side_effect=AssertionError('unexpected fetch')):
   result,audit=recover_pending([e],[({'assessment_status':'pending'}, {})],{},policy=POLICY,evaluator=E(),
     output_dir=Path(d),prompt='',remaining=lambda:0,assessor=lambda *a:None)
  self.assertEqual(audit['status'],'exhausted');self.assertEqual(audit['events_attempted'],0)
  self.assertEqual(result[0][0]['assessment_status'],'pending')

class RecoveryIntegrationTests(unittest.TestCase):
 def test_live_evaluation_recovers_then_selects_and_preserves_evidence(self):
  from event_editorial_runtime import evaluate_candidates
  from fetch_selected_source_material import source_material
  from test_source_selection_refactor import fixture_assessment
  class E:
   identity=['recovery-integration-fixture']
   def __call__(self,system,packet):
    a=fixture_assessment(packet);a['dimensions']['impact']['level']=2
    return a,{}
  c={'primary_url':'https://example.com/blocked','title':'Runtime migration',
     'coverage_source_urls':['https://example.org/recovered'],'published_at':'2026-09-21T01:00:00+08:00'}
  def fetch(c,**kwargs):
   if c['primary_url'].endswith('blocked'):
    return {'fetch_status':'fallback_after_fetch_error','fetch_error':'HTTP 403','source_text':''}
   return {'fetch_status':'fetched','source_text':TEXT*8,'page_title':'Runtime migration','resolved_url':c['primary_url']}
  with tempfile.TemporaryDirectory() as d:
   p=checked_policy({**POLICY,'shared_material_cache':str(Path(d)/'shared')})
   with patch('event_editorial_runtime.source_material',side_effect=fetch),patch('quiet_day_recovery.source_material',side_effect=fetch):
    cs,summary=evaluate_candidates([c],output_dir=Path(d),policy=p,evaluator=E())
   self.assertEqual(summary['quiet_day_recovery']['sources_recovered'],1)
   s=apply_selection({'window':WINDOW},cs,summary)
   self.assertEqual(len(s['selected_news']),1)
   selected=s['selected_news'][0]
   self.assertEqual(selected['primary_url'],'https://example.org/recovered')
   m=source_material(selected,max_chars=12000,timeout_seconds=1,max_bytes=10000)
   self.assertIn('quiet_day_admission',m)
   news=apply_quiet_labels([{'title':'运行时迁移','url':selected['primary_url']}],[m])
   self.assertEqual(validate_final_selection(s,{'news':news}),[])

if __name__=='__main__':unittest.main()
