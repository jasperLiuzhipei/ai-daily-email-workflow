"""One valid image review cannot approve other rendered research images."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import build_ai_daily_email as builder


class ResearchManifestCoverageTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.data = {'research_items': [{'image': 'one.png'}, {'image': 'two.png'}]}
        self.manifests = []
        for index in range(2):
            path = self.root / f'manifest-{index}.json'
            path.write_text(json.dumps({'research_index': index}))
            self.manifests.append(path)

    def test_one_manifest_cannot_approve_two_research_items(self):
        # Cryptographic/image-review checks have their own tests. Keep their
        # success explicit here to prove the additional coverage requirement.
        with patch.object(builder, 'verify_lineage') as verify:
            with self.assertRaisesRegex(builder.LineageError, 'every rendered research item'):
                builder.verify_research_image_manifests(self.data, self.manifests[:1], self.root)
            verify.assert_called_once_with(self.data, self.manifests[0], self.root)

    def test_distinct_manifests_cover_all_research_items(self):
        with patch.object(builder, 'verify_lineage') as verify:
            builder.verify_research_image_manifests(self.data, self.manifests, self.root)
            self.assertEqual(verify.call_count, 2)

    def test_repeating_one_manifest_does_not_fake_full_coverage(self):
        with patch.object(builder, 'verify_lineage') as verify:
            with self.assertRaisesRegex(builder.LineageError, 'every rendered research item'):
                builder.verify_research_image_manifests(self.data, self.manifests[:1] * 2, self.root)
            self.assertEqual(verify.call_count, 2)

    def test_legacy_single_paper_remains_supported(self):
        with patch.object(builder, 'verify_lineage') as verify:
            data = {'paper': {'image': 'one.png'}}
            builder.verify_research_image_manifests(data, self.manifests[:1], self.root)
            verify.assert_called_once()


if __name__ == '__main__':
    unittest.main()
