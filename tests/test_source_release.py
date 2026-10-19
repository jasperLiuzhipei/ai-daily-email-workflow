import hashlib,json,sys,tempfile,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from deploy_source_selection_release import deploy, release_path, VALIDATION_PATTERNS
from score_calibration import evaluate

class ReleaseTests(unittest.TestCase):
    def test_only_fixture_json_is_allowed_beyond_existing_release_scope(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            self.assertEqual(release_path(root, 'ai_daily_workflow/tests/fixtures/reading.json'),
                             root / 'ai_daily_workflow/tests/fixtures/reading.json')
            for rel in ('ai_daily_workflow/tests/reading.json', 'ai_daily_workflow/.env',
                        'ai_daily_workflow/config/recipients.json',
                        'ai_daily_workflow/tests/fixtures/../../config/recipients.json'):
                with self.subTest(rel=rel), self.assertRaises(ValueError):
                    release_path(root, rel)

    def test_release_default_includes_editorial_and_publication_gates(self):
        self.assertTrue({'test_approved_editorial_v2.py', 'test_prod_prose_gate.py',
                         'test_research_prose_recovery.py', 'test_news_prose_recovery.py'} <= set(VALIDATION_PATTERNS))

    def setup_dirs(self, root):
        stage=root/'stage';target=root/'target';rel='ai_daily_workflow/scripts/fixture.py'
        for d,body in [(stage,'value = 2\n'),(target,'value = 1\n')]:
            p=d/rel;p.parent.mkdir(parents=True);p.write_text(body)
        manifest={'version':'fixture','files':{rel:hashlib.sha256((stage/rel).read_bytes()).hexdigest()},
                  'base_sha256':{rel:hashlib.sha256((target/rel).read_bytes()).hexdigest()}}
        (stage/'manifest.json').write_text(json.dumps(manifest));return stage,target,rel
    def test_release_detects_concurrent_drift(self):
        with tempfile.TemporaryDirectory() as d:
            stage,target,rel=self.setup_dirs(Path(d));(target/rel).write_text('value = 3\n')
            with self.assertRaises(ValueError):deploy(stage,target)
            self.assertEqual((target/rel).read_text(),'value = 3\n')
    def test_failed_validation_rolls_back(self):
        with tempfile.TemporaryDirectory() as d:
            stage,target,rel=self.setup_dirs(Path(d))
            def reject(root):raise RuntimeError('fixture failure')
            with self.assertRaises(RuntimeError):deploy(stage,target,reject)
            self.assertEqual((target/rel).read_text(),'value = 1\n')
    def test_release_records_checksums(self):
        with tempfile.TemporaryDirectory() as d:
            stage,target,rel=self.setup_dirs(Path(d));result=deploy(stage,target)
            self.assertEqual(result['status'],'deployed');self.assertEqual((target/rel).read_text(),'value = 2\n')
    def test_unlabelled_events_do_not_produce_accuracy_claims(self):
        r=evaluate([{'event_id':'1','actual_action':'exclude'}]);self.assertIsNone(r['selection_precision']);self.assertEqual(r['status'],'human_labels_required')
    def test_missed_important_event_counts_in_recall(self):
        r=evaluate([{'event_id':str(i),'actual_action':a,'expected_action':'include','reviewed_by':'editor','reason':'Useful engineering evidence'} for i,a in enumerate(['include','defer'])])
        self.assertEqual(r['important_event_recall'],0.5)

if __name__=='__main__':unittest.main()
