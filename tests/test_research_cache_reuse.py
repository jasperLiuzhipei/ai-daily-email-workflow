"""Research retries may reuse only complete, current reviewed artifacts."""
from contextlib import ExitStack
import io
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from daily_prose import POLICY_VERSION
from generate_research_brief_api import DEFAULT_SYSTEM_PROMPT, load_system_prompt
from research_cache import build_cache_contract, cache_manifest_path, write_cache_contract
import run_ai_daily as runner


class ResearchCacheReuseTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.paths = SimpleNamespace(run_date='2026-10-11', run_dir=self.root)
        for name in ('source_selection', 'source_materials', 'brief_json', 'research_api_raw',
                     'prompt_file', 'brief_markdown', 'content', 'content_api_raw'):
            setattr(self.paths, name, self.root / f'{name}.json')
        self.paths.source_selection.write_text('{}')
        self.source = {'title': '合成研究来源', 'primary_url': 'https://example.com/research',
                       'source_text': 'This public synthetic source is only used in tests.', 'fetch_status': 'fetched'}
        self.paths.source_materials.write_text(json.dumps({
            'selection_sha256': runner.file_sha256(self.paths.source_selection), 'research': [self.source]}))
        self.paths.brief_json.write_text(json.dumps({'generator': {
            'reading_review': {'requirements': [{'id': 'mechanism', 'covered': True}]},
            'prose_recovery': {'mode': 'not_needed', 'policy_version': POLICY_VERSION},
        }}))
        for name in ('research_api_raw', 'prompt_file', 'brief_markdown', 'content', 'content_api_raw'):
            getattr(self.paths, name).write_text('{}')
        self.args = SimpleNamespace(refresh_source_materials=False, refresh_api_content=False,
                                    ai_runtime_config=None, content_sections=None, editorial_policy=None)
        self.write_contract()

    def write_contract(self):
        contract = build_cache_contract(
            source=self.source, system_prompt=load_system_prompt(DEFAULT_SYSTEM_PROMPT),
            artifacts={'brief_json': self.paths.brief_json, 'image_prompt': self.paths.prompt_file,
                       'raw_output': self.paths.research_api_raw, 'brief_markdown': self.paths.brief_markdown},
        )
        write_cache_contract(cache_manifest_path(self.paths.brief_json), contract)

    def compose_mocks(self, stack):
        stack.enter_context(patch.object(sys, 'stdout', io.StringIO()))
        stack.enter_context(patch.object(runner, 'run_coverage_review'))
        stack.enter_context(patch.object(runner, 'record_phase'))
        return stack.enter_context(patch.object(runner, 'run_cmd'))

    def assert_reuse_blocked(self):
        with ExitStack() as stack:
            command = self.compose_mocks(stack)
            with self.assertRaisesRegex(runner.WorkflowError, '--refresh-api-content'):
                runner.run_api_compose(self.paths, self.args)
            command.assert_not_called()

    def test_current_complete_cache_reuses_without_model_call(self):
        with ExitStack() as stack:
            command = self.compose_mocks(stack)
            runner.run_api_compose(self.paths, self.args)
            command.assert_not_called()

    def test_four_old_files_without_success_record_cannot_reuse(self):
        cache_manifest_path(self.paths.brief_json).unlink()
        self.assert_reuse_blocked()

    def test_changed_raw_from_failed_force_cannot_reuse_old_brief(self):
        self.paths.research_api_raw.write_text('{"failed_attempt":true}')
        self.assert_reuse_blocked()

    def test_changed_source_rejects_cache_without_calling_api(self):
        materials = json.loads(self.paths.source_materials.read_text())
        materials['research'][0]['source_text'] = 'The source changed after the old reading.'
        self.paths.source_materials.write_text(json.dumps(materials))
        self.assert_reuse_blocked()

    def test_changed_prompt_or_policy_rejects_cache(self):
        for field in ('system_prompt_sha256', 'generation_policy_sha256', 'policy_version'):
            self.write_contract()
            path = cache_manifest_path(self.paths.brief_json)
            contract = json.loads(path.read_text())
            contract[field] = 'old-contract'
            path.write_text(json.dumps(contract))
            with self.subTest(field=field):
                self.assert_reuse_blocked()

    def test_changed_any_successful_output_rejects_cache(self):
        for name in ('brief_json', 'prompt_file', 'brief_markdown'):
            path = getattr(self.paths, name)
            before = path.read_bytes()
            path.write_bytes(before + b'\nchanged')
            with self.subTest(output=name):
                self.assert_reuse_blocked()
            path.write_bytes(before)

    def test_success_record_does_not_replace_reading_review(self):
        self.paths.brief_json.write_text(json.dumps({'generator': {}}))
        self.write_contract()
        self.assert_reuse_blocked()

    def test_refresh_does_not_continue_to_daily_without_new_success_receipt(self):
        self.args.refresh_api_content = True
        cache_manifest_path(self.paths.brief_json).unlink()
        with ExitStack() as stack:
            command = self.compose_mocks(stack)
            with self.assertRaisesRegex(runner.WorkflowError, '--refresh-api-content'):
                runner.run_api_compose(self.paths, self.args)
            self.assertEqual(command.call_count, 1)
            self.assertIn('generate_research_brief_api.py', command.call_args.args[0][1])


if __name__ == '__main__':
    unittest.main()
