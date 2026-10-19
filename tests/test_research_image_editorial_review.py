from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image


WORKFLOW_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WORKFLOW_DIR / "scripts"))

from ai_daily_common import write_json_object  # noqa: E402
from review_research_image_editorial import (  # noqa: E402
    EditorialReviewError,
    approve_audit,
    create_audit,
    validate_editorial_review_for_approval,
)


def write_brief(path: Path, handoff: dict[str, str]) -> None:
    write_json_object(path, {"visual_handoff": handoff})


class ResearchImageEditorialReviewTests(unittest.TestCase):
    def test_heuristics_warn_without_automatically_rejecting_complex_images(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "candidate.png"
            Image.new("RGB", (1200, 675), "white").save(source)
            brief = root / "brief.json"
            write_brief(
                brief,
                {
                    "Core Idea": "通用模型用于蛋白设计和化学分析。",
                    "Primary Visual Scene": "同一中央推理核心连接蛋白和NMR、LC-MS谱图。",
                    "Must Preserve": "两项独立实验：蛋白设计、化学分析。",
                },
            )
            plan = root / "plan.json"
            write_json_object(
                plan,
                {"labels": [{"text": "通用推理核心"}, {"text": "论文报告：15 个靶点中 14 个有结合物"}] * 4},
            )
            derivation = root / "derivation.json"
            write_json_object(
                derivation,
                {"overlay_plan": {"path": str(plan)}},
            )

            audit = create_audit(
                brief_json=brief,
                image_source=source,
                layout="mechanism_hub",
                render_mode="hybrid_overlay",
                derivation_manifest=derivation,
            )

            self.assertEqual(audit["decision"], "needs_review")
            self.assertEqual(audit["structural_decision"], "pass")
            warnings = {item["id"] for item in audit["structural_checks"] if item["decision"] == "warn"}
            self.assertEqual(warnings, {"route_fit", "source_specificity", "overlay_density"})
            with self.assertRaisesRegex(EditorialReviewError, "complete explicit visual pass"):
                approve_audit(audit, reviewer="codex", notes="should not approve")

    def test_passes_structural_checks_then_requires_explicit_visual_review(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "candidate.png"
            Image.new("RGB", (1200, 675), "white").save(source)
            brief = root / "brief.json"
            write_brief(
                brief,
                {
                    "Core Idea": "模型把结构约束映射为候选序列，再由湿实验复核。",
                    "Primary Visual Scene": "目标结构、候选序列、湿实验复核构成单一路径。",
                    "Must Preserve": "结果仍需湿实验复核。",
                },
            )
            audit = create_audit(
                brief_json=brief,
                image_source=source,
                layout="mechanism_hub",
                render_mode="model_native",
                derivation_manifest=None,
            )
            self.assertEqual(audit["structural_decision"], "pass")
            self.assertEqual(audit["decision"], "needs_review")
            self.assertEqual({check["id"] for check in audit["manual_checks"]},
                             {"main_story", "source_specificity", "evidence_boundary"})
            review_path = root / "review.json"
            write_json_object(review_path, audit)
            with self.assertRaisesRegex(EditorialReviewError, "complete explicit visual pass"):
                validate_editorial_review_for_approval(review_path, image_source=source, brief_json=brief)

            with self.assertRaisesRegex(EditorialReviewError, "complete explicit visual pass"):
                approve_audit(audit, reviewer="codex", notes="Must not auto-approve pending checks.")
            for check in audit["manual_checks"]:
                check["decision"] = "pass"
                check["notes"] = "Explicit fixture review."
            approved = approve_audit(audit, reviewer="codex", notes="Reviewed at email scale.")
            write_json_object(review_path, approved)
            validated = validate_editorial_review_for_approval(review_path, image_source=source, brief_json=brief)
            self.assertEqual(validated["decision"], "pass")
            brief.write_text('{}', encoding="utf-8")
            with self.assertRaisesRegex(EditorialReviewError, "brief-json"):
                validate_editorial_review_for_approval(review_path, image_source=source, brief_json=brief)

    def test_rejects_hashable_non_image(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "fake.png"
            source.write_bytes(b"not actually an image")
            brief = root / "brief.json"
            write_brief(brief, {"Core Idea": "test"})
            with self.assertRaisesRegex(EditorialReviewError, "cannot be decoded"):
                create_audit(brief_json=brief, image_source=source, layout="mechanism_hub", render_mode="model_native", derivation_manifest=None)


if __name__ == "__main__":
    unittest.main()
