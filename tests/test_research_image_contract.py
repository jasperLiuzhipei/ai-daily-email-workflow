from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from research_image_contract import FIELDS, HandoffError, compile_prompt, compilation_record, normalize_handoff, directed_relations
from parse_ai_frontier_brief import make_image_prompt, parse_fields
from generate_research_brief_api import visual_handoff, visual_raw


def handoff():
    return {"subject": "电商 Agent", "takeaway": "共享状态减少交接", "mechanism": ["上下文", "Skills 调用规则", "Tools 调用系统"], "evidence": "公司部署经验，无统一基准", "scope": "自包含任务仍可用子智能体", "labels": ["共享状态", "Skills", "Tools", "范围提示"]}


class ResearchImageContractTests(unittest.TestCase):
    def test_relations_keep_request_and_response_directions(self):
        raw = handoff()
        raw["mechanism"] = ["关系：智能体｜发起请求｜代理浏览器", "关系：代理浏览器｜返回截图｜智能体", "关系：代理浏览器｜返回截图｜智能体"]
        edges = directed_relations(normalize_handoff(raw)["mechanism"])
        self.assertEqual(len(edges), 2)
        self.assertEqual((edges[1]["source"], edges[1]["target"]), ("代理浏览器", "智能体"))
        self.assertIn("关系：代理浏览器｜返回截图｜智能体", compile_prompt(raw))
        raw["mechanism"] = "关系：代理浏览器｜返回截图｜"
        with self.assertRaises(HandoffError):
            compile_prompt(raw)

    def test_native_route_does_not_depend_on_chinese_label_count(self):
        raw = handoff()
        raw["labels"] = [f"关键标签{i}" for i in range(24)]
        prompt = compile_prompt(raw)
        self.assertIn("关键标签23", prompt)
        self.assertIn("由图像模型完成图形、标题和中文标签", prompt)
        self.assertNotIn("无文字底图", prompt)
        self.assertEqual(prompt.count("绘制要求："), 1)

    def test_missing_evidence_or_wrong_types_do_not_get_invented(self):
        for key, value in (("evidence", ""), ("scope", None), ("labels", [1])):
            raw = handoff()
            raw[key] = value
            with self.assertRaises(HandoffError):
                normalize_handoff(raw)

    def test_long_scope_is_not_silently_truncated(self):
        raw = handoff()
        raw["scope"] = "需要保留的限定。" * 400 + "末尾关键例外"
        self.assertTrue(normalize_handoff(raw)["scope"].endswith("末尾关键例外"))
        self.assertIn(raw["scope"], compile_prompt(raw))

    def test_legacy_facts_preserved_without_legacy_palette(self):
        raw = {"Display Title": "标题", "Core Idea": "判断", "Must Preserve": "核心机制", "Distinctive Detail": "来源特有细节", "Primary Visual Scene": "THREE_LAYER_CARD_LAYOUT_SENTINEL", "Main Evidence Anchor": "部署观察", "Supporting Evidence Details": "无定量基准", "Boundary Cue": "企业范围", "Exception Cue": "窄任务例外", "Do Not Imply": "不要普遍化", "Exact Labels": ["规则", "工具"], "Visual Tone": "BLACK_BACKGROUND_LEGACY_SENTINEL", "Complexity Budget": "exactly three labels"}
        prompt = compile_prompt(raw)
        for item in ("核心机制", "来源特有细节", "部署观察", "无定量基准", "窄任务例外", "不要普遍化", "规则", "工具"):
            self.assertIn(item, prompt)
        self.assertNotIn("BLACK_BACKGROUND_LEGACY_SENTINEL", prompt)
        self.assertNotIn("THREE_LAYER_CARD_LAYOUT_SENTINEL", prompt)
        self.assertNotIn("exactly three", prompt)
        self.assertTrue(compilation_record(raw, prompt)["legacy_input"])

    def test_api_and_markdown_compilers_agree(self):
        raw = handoff()
        api_visual = visual_handoff(raw, raw["subject"])
        markdown = visual_raw(api_visual)
        parsed = parse_fields(markdown, list(FIELDS))
        self.assertEqual(compile_prompt(raw), make_image_prompt(markdown, parsed, "unused.png"))

    def test_legacy_scene_cannot_fill_missing_mechanism(self):
        raw = {"Display Title": "标题", "Core Idea": "判断",
               "Primary Visual Scene": "强制三层布局、固定七栏",
               "Main Evidence Anchor": "证据", "Boundary Cue": "范围",
               "Exact Labels": ["标签"]}
        with self.assertRaisesRegex(HandoffError, "mechanism"):
            compile_prompt(raw)

    def test_receipt_detects_post_compile_prompt_edits(self):
        raw = handoff()
        with self.assertRaises(HandoffError):
            compilation_record(raw, compile_prompt(raw) + "新增黑底限制")
        record = compilation_record(raw, compile_prompt(raw))
        changed = copy.deepcopy(raw)
        changed["evidence"] = "不同证据"
        other = compilation_record(changed, compile_prompt(changed))
        self.assertNotEqual(record["prompt_sha256"], other["prompt_sha256"])
        self.assertTrue(record["not_a_generation_or_approval_receipt"])


if __name__ == "__main__":
    unittest.main()
