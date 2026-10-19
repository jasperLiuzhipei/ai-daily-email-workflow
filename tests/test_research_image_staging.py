import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from stage_research_image import StageError, main, maybe_compress_image, validate_generation_prompt


class ResearchImageStagingTests(unittest.TestCase):
    def test_api_prompt_hash_uses_generator_payload_normalization(self):
        with tempfile.TemporaryDirectory() as directory:
            prompt = Path(directory) / 'prompt.txt'
            prompt.write_text('\n  研究图解：状态检查\n\n', encoding='utf-8')
            manifest = {'prompt_sha256': hashlib.sha256('研究图解：状态检查'.encode()).hexdigest()}
            validate_generation_prompt(manifest, prompt)

    def test_api_prompt_hash_cannot_be_missing(self):
        with tempfile.TemporaryDirectory() as directory:
            prompt = Path(directory) / 'prompt.txt'
            prompt.write_text('研究图解', encoding='utf-8')
            with self.assertRaisesRegex(StageError, 'prompt_sha256 is required'):
                validate_generation_prompt({}, prompt)

    def test_api_staging_rejects_changed_prompt_even_with_age_override(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'generated.png'
            Image.new('RGB', (2, 1), 'white').save(source)
            prompt = root / 'prompt.txt'
            prompt.write_text('替换后的新提示词', encoding='utf-8')
            content = root / 'content.json'
            original_content = json.dumps({'paper': {'title': '测试研究'}})
            content.write_text(original_content, encoding='utf-8')
            generation = root / 'generation.json'
            generation.write_text(json.dumps({
                'schema_version': 1,
                'provider': {'id': 'gpt_image_2'},
                'prompt_sha256': hashlib.sha256('旧提示词'.encode()).hexdigest(),
                'output': {'sha256': hashlib.sha256(source.read_bytes()).hexdigest()},
            }), encoding='utf-8')
            staged_manifest = root / 'staged.json'
            with mock.patch.object(sys, 'argv', [
                'stage_research_image.py', '--content', str(content),
                '--image-source', str(source), '--date', '2026-10-08',
                '--provider-id', 'gpt_image_2', '--quality-decision', 'pending',
                '--prompt-file', str(prompt), '--generation-manifest', str(generation),
                '--assets-root', str(root / 'assets'), '--manifest', str(staged_manifest),
                '--allow-image-before-prompt',
            ]):
                with self.assertRaisesRegex(SystemExit, 'prompt hash does not match'):
                    main()
            self.assertEqual(content.read_text(encoding='utf-8'), original_content)
            self.assertFalse(staged_manifest.exists())
            self.assertFalse((root / 'assets').exists())

    def test_transparent_image_has_white_email_background(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'generated.png'
            target = root / 'staged.png'
            image = Image.new('RGBA', (2, 1), (0, 0, 0, 0))
            image.putpixel((1, 0), (0, 0, 0, 255))
            image.save(source)
            staged = maybe_compress_image(source, target, 10000)
            with Image.open(staged) as actual:
                self.assertEqual(actual.convert('RGB').getpixel((0, 0)), (255, 255, 255))
                self.assertEqual(actual.convert('RGB').getpixel((1, 0)), (0, 0, 0))


if __name__ == '__main__':
    unittest.main()
