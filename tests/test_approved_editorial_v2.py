"""The user-approved October 8 reading is a portable editorial/rendering fixture.

Its 54 news items and exact prose describe this issue, not quotas for future issues.
No API, image generation, ledger writes, or mail are used by these tests.
"""
import copy
from datetime import date
import json
from pathlib import Path
import re
import sys
import unittest

from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from build_ai_daily_email import enrich_content, DEFAULT_FOOTER
from daily_prose import daily_editorial_issues
from newspaper_sections import section_indices
import newspaper_text
from validate_ai_daily_content import validate_content, ValidationError

FIXTURE = Path(__file__).resolve().parent / 'fixtures/approved_editorial_v2_2026-10-08.json'


class ApprovedEditorialV2Tests(unittest.TestCase):
    def data(self):
        raw = json.loads(FIXTURE.read_text(encoding='utf-8'))
        return newspaper_text.configure_content(
            enrich_content(raw, date(2026, 10, 8), 61, 'trial', DEFAULT_FOOTER, {}), '2026-10-08')

    def test_approved_reading_passes_without_changing_source_tiers_or_roles(self):
        data = self.data()
        before = copy.deepcopy(data)
        validate_content(data)
        self.assertEqual(daily_editorial_issues(data), [])
        self.assertEqual(section_indices(data['news']), (52, 1))
        self.assertTrue(all(item['display_tier'] == 'brief' for item in data['news']))
        self.assertEqual(data, before)

    def test_all_sections_topics_and_briefs_survive_rendering(self):
        data = self.data()
        soup = BeautifulSoup(newspaper_text.render_email(data, editorial_override={}), 'html.parser')
        for section in ('np-lead', 'np-research', 'np-supplement', 'np-quick'):
            self.assertIsNotNone(soup.select_one('#' + section), section)
        for section, index in (('np-lead', 52), ('np-supplement', 1)):
            node = soup.select_one('#' + section)
            self.assertIn(data['news'][index]['url'], [a.get('href') for a in node.select('a')])
            self.assertIn(data['news'][index]['observation'], node.get_text())
        self.assertEqual(len(soup.select('.np-brief')), 52)
        self.assertEqual(len(soup.select('#np-research img')), 1)

    def test_full_explanation_and_two_findings_are_visible(self):
        data = self.data()
        soup = BeautifulSoup(newspaper_text.render_email(data, editorial_override={}), 'html.parser')
        visible = re.sub(r'\s+', '', soup.select_one('#np-research').get_text())
        for field in ('conclusion', 'problem', 'mechanism', 'evidence', 'insight', 'boundary'):
            self.assertIn(re.sub(r'\s+', '', data['paper'][field]), visible, field)
        # These anchors preserve both central findings and the observed-vs-guaranteed
        # boundary in this accepted example; they are not universal numeric budgets.
        for anchor in ('67.24%', '476', '68', '241', '不是未来永不出错的保证'):
            self.assertIn(anchor, visible)
        evidence = soup.select_one('.rd-support')
        self.assertEqual([p.get_text() for p in evidence.select('p')], data['paper']['evidence'].split('\n\n'))

    def test_incomplete_explicit_roles_cannot_silently_drop_a_feature(self):
        data = self.data()
        data['news'][1]['newspaper_role'] = 'brief'
        with self.assertRaises(ValidationError):
            validate_content(data)


if __name__ == '__main__':
    unittest.main()
