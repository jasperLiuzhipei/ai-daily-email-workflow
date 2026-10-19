from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace


WORKFLOW_DIR = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = WORKFLOW_DIR / "scripts"
WORKFLOW_PYTHON = WORKFLOW_DIR / ".venv" / "bin" / "python"

sys.path.insert(0, str(SCRIPTS_DIR))
from run_ai_daily import selected_research_exists  # noqa: E402
from validate_ai_daily_content import ValidationError, validate_news_item  # noqa: E402


def news_only_content() -> dict[str, object]:
    return {
        "date": "2026-07-30",
        "editor_note": "今日没有入选达到研究前沿门槛的内容，日报聚焦五条行业动态。",
        "news_section": {"title": "行业动态", "subtitle": "精选今日值得关注的 AI 行业变化"},
        "news": [
            {
                "category": "industry_signal",
                "title": "测试行业动态",
                "tags": ["通用大模型", "适合：产品 / 运营"],
                "one_liner": "这是一条用于验证纯新闻日报渲染的中文事实摘要。",
                "why": "它验证无研究前沿时，行业新闻仍可独立构成一封完整日报。",
                "observation_label": "行业视角",
                "observation": "研究是否入选不应影响已通过质量门槛的行业新闻正常交付。",
                "url": "https://example.com/news-only",
            }
        ],
        "summary_headline": "行业动态仍可独立成刊",
        "summary": "当天没有合格研究内容时，系统应保持研究门槛，同时生成纯行业新闻日报。",
    }


class NewsOnlyDailyTests(unittest.TestCase):
    def test_placeholder_news_title_is_blocked_before_render(self) -> None:
        content = news_only_content()
        news = content["news"]
        assert isinstance(news, list)
        news[0]["title"] = "AI 行业动态待核验"

        with self.assertRaisesRegex(ValidationError, "fallback placeholder"):
            validate_news_item(news[0], 0, {"行业视角", "工作启发"})

    def test_unfetched_research_downgrades_to_news_only(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source_materials = Path(temp_dir) / "source_material.json"
            source_materials.write_text(
                json.dumps(
                    {
                        "research": [
                            {
                                "title": "无法读取的研究源",
                                "fetch_status": "fallback_after_fetch_error",
                            }
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            self.assertFalse(selected_research_exists(SimpleNamespace(source_materials=source_materials)))

    def test_fetched_research_keeps_research_route(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source_materials = Path(temp_dir) / "source_material.json"
            source_materials.write_text(
                json.dumps(
                    {"research": [{"title": "可读取的研究源", "fetch_status": "fetched"}]},
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            self.assertTrue(selected_research_exists(SimpleNamespace(source_materials=source_materials)))

    def test_all_email_themes_render_without_research_or_image_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            content = temp / "content.json"
            content.write_text(json.dumps(news_only_content(), ensure_ascii=False), encoding="utf-8")

            for theme in ("digest_navy", "clay_orange", "editorial_mono", "newsroom_mono", "newsroom_navy"):
                output = temp / f"{theme}.html"
                normalized = temp / f"{theme}.normalized.json"
                result = subprocess.run(
                    [
                        str(WORKFLOW_PYTHON if WORKFLOW_PYTHON.exists() else Path(sys.executable)),
                        str(SCRIPTS_DIR / "build_ai_daily_email.py"),
                        "--content",
                        str(content),
                        "--date",
                        "2026-07-30",
                        "--output",
                        str(output),
                        "--normalized-output",
                        str(normalized),
                        "--theme",
                        theme,
                        "--series",
                        "none",
                        "--no-write-ledger",
                        "--issue-ledger",
                        str(temp / "issue_ledger.json"),
                        "--disable-feedback",
                    ],
                    cwd=WORKFLOW_DIR,
                    text=True,
                    capture_output=True,
                    check=False,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                html = output.read_text(encoding="utf-8")
                self.assertIn("测试行业动态", html)
                self.assertIn("industry", html.lower())
                self.assertNotIn("RESEARCH PICK", html)
                self.assertNotIn("RESEARCH FRONTIER", html)
                self.assertNotIn("digest-research", html)
                self.assertNotIn("paper", json.loads(normalized.read_text(encoding="utf-8")))


if __name__ == "__main__":
    unittest.main()
