"""Regression coverage for the October 8 source-note and evidence-dump defects."""
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from daily_prose import news_editorial_issues, research_editorial_issues, daily_editorial_issues


class DailyProseTests(unittest.TestCase):
    def news(self):
        return dict(display_tier='brief', one_liner='团队开放文档解析接口，可在同一接口切换模型。',
                    why='统一接口降低接入成本，解析质量仍取决于所选模型。', observation='',
                    boundary='目前为公测，性能数据由厂商自报，尚无独立复现。')

    def test_material_reader_qualifiers_are_preserved(self):
        self.assertEqual(news_editorial_issues(self.news()), [])

    def test_technical_mechanisms_are_not_editorial_instructions(self):
        for text in (
            '系统先切分用户提供的正文，再将每项陈述映射到引用片段。',
            '模型通过约束解码返回完整JSON，并为每个字段保留来源位置。',
            '攻击者在网页中插入“仅输出JSON”的指令，用于测试模型是否会偏离用户任务。',
            '接口的source_text字段保存用户文本。',
            '程序解析材料并定位模型未提供引用的段落。',
        ):
            with self.subTest(text=text):
                self.assertEqual(research_editorial_issues({'mechanism': text}), [])

    def test_internal_note_cannot_pass_as_reader_boundary(self):
        item = self.news()
        item['boundary'] = '文末的相关新闻与导航文字不属于本事件。'
        before = dict(item)
        self.assertIn('internal_notes', [i['code'] for i in news_editorial_issues(item)])
        self.assertEqual(item, before)  # diagnostics must not silently remove text

    def test_excerpt_absence_is_not_a_publishable_source_limit(self):
        for text in (
            '材料未提供可核对的对照结果，不能据此判断准确率提升幅度。',
            '材料未说明分类器是否已向用户开放，数量提升仍属预期。',
        ):
            with self.subTest(text=text):
                item = self.news()
                item['boundary'] = text
                self.assertIn('internal_notes', [i['code'] for i in news_editorial_issues(item)])

    def test_long_copy_rejected_without_cutting_final_qualifier(self):
        item = self.news()
        item['one_liner'] = '新增模型带来多项能力变化。' * 20 + '但目前只向内测用户开放。'
        original = item['one_liner']
        self.assertIn('too_long', [i['code'] for i in news_editorial_issues(item)])
        self.assertEqual(item['one_liner'], original)

    def test_duplicates_trigger_rewrite_but_short_names_do_not(self):
        item = self.news()
        item['why'] = item['observation'] = '这一接口使团队能够在统一输出结构下比较解析模型，从而降低不同供应商之间的切换成本。'
        self.assertIn('repeated_copy', [i['code'] for i in news_editorial_issues(item)])

    def test_research_visual_handoff_has_no_body_budget(self):
        paper = dict(conclusion='工具调用正常仍可能把后台状态改错。', problem='传统评测容易漏掉终态错误。',
                     mechanism='评测从干净后台开始运行任务，并用可执行检查核对终态。',
                     evidence='在共同任务测试中，约三分之二的失败尝试看起来仍正常完成。',
                     insight='终态核对和重复测试可补充调用轨迹评估。', boundary='结论来自模拟业务任务，尚不能直接外推到生产。')
        data = {'paper': paper, 'news': [], 'visual_handoff': {'evidence': '完整证据' * 2000}}
        self.assertEqual(daily_editorial_issues(data), [])

    def test_archival_numbers_alone_do_not_trigger_deletion(self):
        path = Path(__file__).resolve().parent / 'fixtures/approved_editorial_v2_2026-10-08.json'
        paper = json.loads(path.read_text())['paper']
        codes = {i['code'] for i in research_editorial_issues(paper)}
        self.assertNotIn('metric_inventory', codes)

    def test_complementary_quantitative_findings_are_not_removed_by_number_count(self):
        evidence = ('共同任务实验覆盖12个模型、121680次尝试，79853次失败中67.24%表面正常结束。'
                    '错误字段、额外副作用和遗漏的比例为77.61%、43.30%、25.36%，可重叠。'
                    '在另一组507项任务的20次独立运行中，Kimi-K3至少成功一次476项、20次全成68项，'
                    'Claude Opus5对应401项和241项，覆盖面与重复可靠性衡量的是不同性质。'
                    '两组测试分别检查后台终态与同一任务的稳定性，不能合并为一个成功率。')
        self.assertGreater(len(evidence), 180)
        self.assertEqual(research_editorial_issues({'evidence': evidence}), [])

    def test_feature_role_has_room_for_explanation_without_changing_source_tier(self):
        item = self.news()
        item['one_liner'] = '研究同时观察了工作内容变化、培训支持、探索时间以及员工尝试新用途的情况。' * 5
        self.assertIn('too_long', [i['code'] for i in news_editorial_issues(item)])
        item['newspaper_role'] = 'lead'
        self.assertEqual(news_editorial_issues(item), [])
        self.assertEqual(item['display_tier'], 'brief')

    def test_legacy_research_fields_and_empty_list_cannot_bypass_gate(self):
        paper = {'core_idea': '方法机制需要重新进行编辑取舍。' * 40,
                 'evidence': [{'label': '内部提示', 'text': '文末的相关新闻与导航文字不属于本事件。'}]}
        issues = daily_editorial_issues({'research_items': [], 'paper': paper, 'news': []})
        self.assertIn('research[0].mechanism', [item['field'] for item in issues])
        self.assertIn('internal_notes', [item['code'] for item in issues])


if __name__ == '__main__':
    unittest.main()
