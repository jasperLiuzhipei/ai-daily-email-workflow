import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from verify_research_image_lineage import LineageError, verify_editorial_review, verify_lineage


class LineagePolicyTests(unittest.TestCase):
    def test_old_policy_name_cannot_bypass_review_for_new_approval(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for policy in (None, "baseline_locked_v1", "baseline_locked_editorial_v2"):
                with self.subTest(policy=policy), self.assertRaisesRegex(LineageError, "missing editorial_review"):
                    verify_editorial_review({"quality_gate": {"policy": policy}}, workspace=root, image_source=root / "image.png", quality_decision="approved")

    def test_legacy_schema_cannot_auto_approve_current_publication(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = root / "manifest.json"
            manifest.write_text(json.dumps({"schema_version": 1, "source_kind": "imagegen"}))
            with self.assertRaisesRegex(LineageError, "historical-read-only"):
                verify_lineage({}, manifest, root)


if __name__ == "__main__":
    unittest.main()
