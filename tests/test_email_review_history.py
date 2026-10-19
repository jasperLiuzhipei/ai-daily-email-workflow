from copy import deepcopy
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from email_review_history import start_review, checkpoint, save_input, bind_approval, history_dir, write_once


class ReviewHistoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.paths = SimpleNamespace(run_dir=Path(self.temp.name))

    def receipt(self):
        return {'status': 'reviewing', 'hashes': {'image_manifest': 'before'}, 'checks': {}}

    def test_interrupted_process_and_new_budget_keep_prior_checkpoint(self):
        old = self.receipt()
        start_review(self.paths, old, {})
        old['checks']['editorial'] = {'metadata': {'request_id': 'completed-before-crash'}}
        checkpoint(self.paths, old, 'editorial')
        before = (history_dir(self.paths, old) / 'editorial.json').read_bytes()
        new = self.receipt()
        start_review(self.paths, new, old)
        self.assertNotEqual(old['review_id'], new['review_id'])
        self.assertEqual((history_dir(self.paths, old) / 'editorial.json').read_bytes(), before)
        self.assertEqual(json.loads((history_dir(self.paths, old)/'interrupted.json').read_text())['status'], 'interrupted')
        self.assertEqual(new['previous_review_id'], old['review_id'])

    def test_approval_binding_does_not_rewrite_original_review(self):
        receipt = self.receipt()
        start_review(self.paths, receipt, {})
        save_input(self.paths, receipt, {'facts': 'original'}, 'review instructions')
        receipt['status'] = 'passed'
        checkpoint(self.paths, receipt, 'result')
        original = (history_dir(self.paths, receipt) / 'result.json').read_bytes()
        receipt['hashes']['image_manifest'] = 'after'
        bind_approval(self.paths, receipt)
        self.assertEqual((history_dir(self.paths, receipt) / 'result.json').read_bytes(), original)
        self.assertTrue((history_dir(self.paths, receipt) / 'approval_after.json').exists())
        self.assertEqual(json.loads((self.paths.run_dir/'email_self_review.json').read_text())['hashes']['image_manifest'], 'after')

    def test_legacy_receipt_is_preserved_and_new_attempt_is_linked_to_budget(self):
        legacy = {'status': 'failed', 'reason': 'previous factual error'}
        (self.paths.run_dir/'publish_recovery.json').write_text(json.dumps({'status': 'running',
            'attempts': [{'number': 2, 'started_at': '2026-10-14T00:43:00+00:00'}]}))
        receipt = self.receipt()
        start_review(self.paths, receipt, legacy)
        archived = list((self.paths.run_dir/'email_review_history/previous').glob('*.json'))
        self.assertEqual(json.loads(archived[0].read_text()), legacy)
        self.assertEqual(receipt['recovery']['attempt'], 2)
        self.assertEqual(receipt['recovery']['scope'], 'publish')

    def test_receipts_are_private_and_cannot_be_overwritten(self):
        path = self.paths.run_dir/'result.json'
        write_once(path, {'request_id': 'first'})
        write_once(path, {'request_id': 'first'})
        with self.assertRaises(ValueError):
            write_once(path, {'request_id': 'second'})
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)


if __name__ == '__main__':
    unittest.main()
