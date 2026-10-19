"""Contracts for the shared reviewer and historical publishing adapters."""
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPTS))
import quality_review as review
from ai_gateway_client import GatewayError, ProviderRuntime


class SharedReviewTests(unittest.TestCase):
    def runtime(self):
        return ProviderRuntime('text', 'https://example.invalid', 'test-only', 'fixed-reviewer',
                               '/chat/completions', 90, 0, {'extra_option': True})

    def test_transport_keeps_payload_and_trace_metadata(self):
        response = {'choices': [{'message': {'content': [
            {'type': 'text', 'text': '```json\n{"decision":"pass","issues":[]}\n```'}]}}],
            'usage': {'prompt_tokens': 17, 'completion_tokens': 8}}
        with patch.object(review, 'request_json', return_value=(response, {'x-request-id': 'trace-1'}, 200)) as request:
            result, meta = review.call_quality_model(self.runtime(), system_prompt='review',
                                                     user_content='fixture', max_output_tokens=123)
        request.assert_called_once()
        payload = request.call_args.args[1]
        self.assertEqual(payload['model'], 'fixed-reviewer')
        self.assertEqual(payload['messages'], [{'role': 'system', 'content': 'review'},
                                              {'role': 'user', 'content': 'fixture'}])
        self.assertEqual(payload['max_tokens'], 123)
        self.assertEqual(payload['response_format'], {'type': 'json_object'})
        self.assertTrue(payload['extra_option'])
        self.assertEqual(result, {'decision': 'pass', 'issues': []})
        self.assertEqual(meta['request_id'], 'trace-1')
        self.assertEqual(meta['usage'], response['usage'])
        self.assertNotIn('test-only', json.dumps(meta))

    def test_transport_failure_does_not_retry_or_return_approval(self):
        with patch.object(review, 'request_json', side_effect=GatewayError('HTTP 503')) as request:
            with self.assertRaisesRegex(review.AutoPublishBlocked, 'Quality model request failed'):
                review.call_quality_model(self.runtime(), system_prompt='review', user_content='fixture')
        request.assert_called_once()

    def test_malformed_model_responses_cannot_approve(self):
        for response in ({}, {'choices': []}, {'choices': [{'message': {'content': 'not-json'}}]},
                         {'choices': [{'message': {'content': '[]'}}]}):
            with self.subTest(response=response), self.assertRaises(review.AutoPublishBlocked):
                review.parse_response_json(response)

    def test_shared_modules_import_without_workflow_or_legacy_dispatch(self):
        source = """import sys
sys.path.insert(0, sys.argv[1])
import quality_review, workflow_context, workflow_cli
assert not {'run_ai_daily', 'holiday_auto_publish', 'auto_publish', 'automatic_delivery'} & set(sys.modules)
"""
        result = subprocess.run([sys.executable, '-c', source, str(SCRIPTS)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_historical_imports_keep_same_exception_and_approval_contract(self):
        import holiday_auto_publish as legacy
        import run_ai_daily as runner
        import workflow_context as context
        self.assertIs(legacy.AutoPublishBlocked, review.AutoPublishBlocked)
        self.assertIs(legacy.visual_review, review.visual_review)
        self.assertIs(legacy.approve_image_manifest, review.approve_image_manifest)
        self.assertIs(runner.WorkflowError, context.WorkflowError)
        self.assertIs(runner.assert_content_lineage, context.assert_content_lineage)


if __name__ == '__main__':
    unittest.main()
