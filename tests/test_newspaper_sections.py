import copy
from pathlib import Path
import sys
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from newspaper_sections import planned_news_sources, section_indices


class NewspaperSectionTests(unittest.TestCase):
    def sources(self):
        return [dict(event_id=f'e{i}',daily_rank=i+1,display_tier='brief',score=81-i) for i in range(54)]

    def test_all_brief_keeps_two_sections_and_all_source_metadata(self):
        sources=self.sources();before=copy.deepcopy(sources)
        result=planned_news_sources(sources)
        self.assertEqual(sources,before)
        self.assertEqual(section_indices(result),(0,1))
        self.assertEqual([x['newspaper_role'] for x in result].count('brief'),52)
        self.assertEqual([{k:v for k,v in x.items() if k!='newspaper_role'} for x in result],sources)

    def test_explicit_historical_placements_win_over_scores(self):
        sources=self.sources()
        for i,item in enumerate(sources):
            item['newspaper_role']='lead' if i==52 else 'supplement' if i==1 else 'brief'
        self.assertEqual(section_indices(planned_news_sources(sources)),(52,1))

    def test_deep_preferred_then_existing_order_without_reordering_list(self):
        sources=self.sources();sources[8]['display_tier']='deep'
        result=planned_news_sources(sources)
        self.assertEqual(section_indices(result),(8,0))
        self.assertEqual([n['event_id'] for n in result],[n['event_id'] for n in sources])

    def test_missing_or_conflicting_feature_requires_recomposition(self):
        sources=planned_news_sources(self.sources())
        with self.assertRaisesRegex(ValueError,'Incomplete'):section_indices(sources[1:])
        sources[2]['newspaper_role']='lead'
        with self.assertRaisesRegex(ValueError,'Duplicate'):section_indices(sources)

    def test_single_and_legacy_sections(self):
        self.assertEqual(section_indices([]),(None,None))
        self.assertEqual(section_indices(planned_news_sources(self.sources()[:1])),(0,None))
        self.assertEqual(section_indices([{'category':'methods_and_views'},{'category':'industry_signal'},{'category':'methods_and_views'}]),(2,0))

    def test_targeted_retry_preserves_existing_role_but_whole_issue_stays_strict(self):
        planned=planned_news_sources(self.sources())
        for index in (1,4):
            subset=[planned[index]]
            self.assertEqual(planned_news_sources(subset,allow_partial=True),subset)
            with self.assertRaisesRegex(ValueError,'Incomplete'):
                section_indices(subset)
