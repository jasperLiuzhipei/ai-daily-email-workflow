import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import automatic_delivery as auto
import quality_review as gate
import run_ai_daily as runner
from ai_daily_common import file_sha256
from workflow_run_state import load_workflow_state, record_run_event


class ReviewTraceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.paths = SimpleNamespace(run_dir=root, run_date='2026-10-14', workflow_state=root/'workflow_run.json',
            html=root/'daily.html', image_manifest=root/'manifest.json')
        self.paths.html.write_text('<p>First candidate</p>')
        self.paths.image_manifest.write_text('{"quality_gate":{"decision":"pending"}}')
        (root/'email_self_review.json').write_text('{"status":"passed"}')

    def test_candidate_builds_preserve_html_revisions_without_false_approval(self):
        runner.record_built_html(self.paths, has_research=True, text_only=False)
        state = load_workflow_state(self.paths.workflow_state)
        first = state['artifacts']['comparison_html']
        self.assertEqual(state['phase'], 'CONTENT_READY')
        self.assertNotIn('image_approved', [e['action'] for e in state['events']])
        self.paths.html.write_text('<p>Copy repaired</p>')
        runner.record_built_html(self.paths, has_research=True, text_only=False)
        state = load_workflow_state(self.paths.workflow_state)
        self.assertNotEqual(first['path'], state['artifacts']['comparison_html']['path'])
        self.assertEqual(file_sha256(Path(first['path'])), first['sha256'])
        self.assertEqual(state['artifacts']['html']['sha256'], file_sha256(self.paths.html))

    def test_approval_updates_current_manifest_and_is_idempotent_after_restart(self):
        runner.record_built_html(self.paths, has_research=True, text_only=False)
        before = file_sha256(self.paths.image_manifest)
        self.paths.image_manifest.write_text('{"quality_gate":{"decision":"approved"}}')
        auto.record_image_approval(self.paths)
        state = load_workflow_state(self.paths.workflow_state)
        self.assertEqual(state['phase'], 'HTML_READY')
        self.assertEqual(state['artifacts']['research_image_manifest']['sha256'], file_sha256(self.paths.image_manifest))
        candidate = next(e for e in state['events'] if e['action']=='stage_image_candidate')
        self.assertEqual(candidate['artifact_snapshots']['research_image_manifest']['sha256'], before)
        auto.record_image_approval(self.paths)
        self.assertEqual(load_workflow_state(self.paths.workflow_state), state)

    def test_sent_phase_is_not_regressed_when_artifact_event_is_added(self):
        record_run_event(self.paths.workflow_state, self.paths.run_date, action='smtp_prod', phase='SENT')
        auto.record_image_approval(self.paths)
        self.assertEqual(load_workflow_state(self.paths.workflow_state)['phase'], 'SENT')

    def test_visual_failure_retains_actual_request_metadata_before_raising(self):
        image = self.paths.run_dir/'candidate.png'
        image.write_bytes(b'\x89PNG\r\n\x1a\n'+b'x'*25000)
        result = {'decision':'fail','issues':['reversed mechanism']}
        responses, inputs = [], []
        with patch.object(gate, 'call_quality_model', return_value=(result, {'request_id':'failed-visual-call'})):
            with self.assertRaises(gate.AutoPublishBlocked):
                gate.visual_review(None, workspace=self.paths.run_dir, content={'paper':{'image':str(image)}},
                    manifest={'staged_image_sha256':file_sha256(image)},
                    on_result=lambda *r: responses.append(r), on_input=lambda *r: inputs.append(r))
        self.assertEqual(responses[0][1]['request_id'], 'failed-visual-call')
        self.assertTrue(inputs[0][0][1]['image_url']['url'].startswith('data:image/png;base64,'))


if __name__ == '__main__':
    unittest.main()
