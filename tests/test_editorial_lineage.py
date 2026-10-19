from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


WORKFLOW_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WORKFLOW_DIR / "scripts"))

from ai_daily_common import file_sha256  # noqa: E402
from workflow_context import WorkflowError, assert_content_lineage, default_paths  # noqa: E402


class EditorialLineageTests(unittest.TestCase):
    def test_build_rejects_content_when_selection_changed_after_material_fetch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = default_paths("2026-08-03")
            paths.source_selection = root / "selection.json"
            paths.source_materials = root / "materials.json"
            paths.content = root / "content.json"

            paths.source_selection.write_text(json.dumps({"selected_news": [{"rank": 1}]}), encoding="utf-8")
            selection_sha = file_sha256(paths.source_selection)
            paths.source_materials.write_text(
                json.dumps({"selection_sha256": selection_sha, "news": [{"rank": 1}]}), encoding="utf-8"
            )
            material_sha = file_sha256(paths.source_materials)
            paths.content.write_text(
                json.dumps(
                    {
                        "generation": {
                            "selection_sha256": selection_sha,
                            "source_material_sha256": material_sha,
                        }
                    }
                ),
                encoding="utf-8",
            )

            assert_content_lineage(paths)

            paths.source_selection.write_text(json.dumps({"selected_news": [{"rank": 1}, {"rank": 2}]}), encoding="utf-8")
            with self.assertRaisesRegex(WorkflowError, "Source selection changed"):
                assert_content_lineage(paths)


if __name__ == "__main__":
    unittest.main()
