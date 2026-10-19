import copy
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))

from event_editorial import DEFAULT_POLICY, digest, validate_final_selection


class ManualNewsEditorialApprovalTests(unittest.TestCase):
    def setUp(self):
        self.selection = {
            'event_editorial': {'policy': DEFAULT_POLICY},
            'window': {'start_at': '2026-09-24T10:30:00+08:00', 'end_at': '2026-09-28T08:30:00+08:00'},
            'selected_news': [], 'selected_research': [],
        }
        self.item = {'title': '官方工程更新', 'url': 'https://example.org/release'}
        self.content = {'date': '2026-09-28', 'news': [self.item]}
        self.approval = {
            'schema_version': 1, 'kind': 'manual_news_editorial_approval',
            'selection_sha256': digest(self.selection), 'date': '2026-09-28',
            'user_request': '用户要求复核遗漏后发布当期日报',
            'reason': '自动选源漏掉这篇具有新增工程机制的官方文章。',
            'editor': 'Codex editorial review',
            'items': [{
                'item_sha256': digest(self.item), 'primary_url': self.item['url'],
                'event_key': 'official-engineering-update',
                'event_date': '2026-09-25',
                'source_review': '已阅读官方原文，确认新增能力和分阶段开放范围。',
                'editorial_reason': '这项变化直接影响企业应用构建和代理监督的评估，值得本期补入。',
            }],
        }

    def test_exact_record_can_admit_manually_reviewed_news(self):
        self.assertTrue(validate_final_selection(self.selection, self.content))
        self.assertEqual(validate_final_selection(self.selection, self.content, None, self.approval), [])

    def test_stale_or_out_of_window_approval_fails_closed(self):
        cases = []
        stale = copy.deepcopy(self.approval); stale['selection_sha256'] = 'stale'; cases.append(stale)
        wrong_item = copy.deepcopy(self.approval); wrong_item['items'][0]['item_sha256'] = 'stale'; cases.append(wrong_item)
        wrong_url = copy.deepcopy(self.approval); wrong_url['items'][0]['primary_url'] = 'https://example.org/other'; cases.append(wrong_url)
        old_event = copy.deepcopy(self.approval); old_event['items'][0]['event_date'] = '2026-09-23'; cases.append(old_event)
        for case in cases:
            with self.subTest(case=case):
                self.assertTrue(validate_final_selection(self.selection, self.content, None, case))

    def test_date_only_release_on_window_start_requires_timing_record(self):
        selection = copy.deepcopy(self.selection)
        selection['window'] = {
            'start_at': '2026-09-28T08:30:00+08:00',
            'end_at': '2026-09-29T08:30:00+08:00',
        }
        approval = copy.deepcopy(self.approval)
        approval['date'] = '2026-09-29'
        approval['selection_sha256'] = digest(selection)
        approval['items'][0]['event_date'] = '2026-09-28'
        content = {'date': '2026-09-29', 'news': [self.item]}

        self.assertTrue(validate_final_selection(selection, content, None, approval))

        approval['items'][0]['time_precision'] = 'date_only'
        approval['items'][0]['timing_note'] = (
            '官方原文只列 9 月 28 日；聚合源的 08:00 时间不能证明实际发布小时，且前一期没有刊登。'
        )
        self.assertEqual(validate_final_selection(selection, content, None, approval), [])

        approval['items'][0]['timing_note'] = ''
        self.assertTrue(validate_final_selection(selection, content, None, approval))

    def test_precise_end_day_publication_is_checked_against_cutoff(self):
        approval = copy.deepcopy(self.approval)
        for timestamp, admitted in [
            ('2026-09-28T07:25:11+08:00', True),
            ('2026-09-28T08:31:00+08:00', False),
            ('2026-09-27T23:25:11+00:00', True),
            ('2026-09-28', False),
        ]:
            with self.subTest(timestamp=timestamp):
                approval['items'][0]['event_date'] = timestamp
                errors = validate_final_selection(self.selection, self.content, None, approval)
                self.assertEqual(not errors, admitted)


if __name__ == '__main__':
    unittest.main()
