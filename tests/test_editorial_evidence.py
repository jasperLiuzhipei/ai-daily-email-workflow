import hashlib
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from editorial_evidence import evidence_for_item, supporting_quotes, EVIDENCE_GUIDANCE, REVIEW_EVIDENCE_GUIDANCE
from generate_daily_content_api import news_card_user_prompt, news_card_system_prompt
from quality_review import compact_source_packet
from email_quality_control import REVIEW_SYSTEM


class EditorialEvidenceTests(unittest.TestCase):
    def source(self, **changes):
        return {"title": "公司申请破产", "primary_url": "https://publisher.example/report",
            "discovery_url": "https://feed.example/summary", "fetch_status": "fetched",
            "source_text": "The company entered a court-supervised sale. Operations continue.",
            "fallback_excerpt": "据法院陈述，公司每月亏损约800万至900万美元。", **changes}

    def test_summary_only_number_and_provenance_reach_both_stages(self):
        item = self.source()
        prompt = news_card_user_prompt(item)
        generation = json.loads(prompt.split('<news_material>\n')[1].split('\n</news_material>')[0])
        review = compact_source_packet({"news": [item]})["news"][0]
        self.assertEqual(generation['source_evidence'], review['source_text'])
        self.assertEqual(generation['material_excerpt'], review['material_excerpt'])
        self.assertEqual(generation['source_evidence_provenance'], review['source_evidence_provenance'])
        self.assertIn('800万至900万', review['material_excerpt'])
        self.assertNotIn('800万', review['source_text'])
        provenance = review['source_evidence_provenance']
        self.assertEqual(provenance['summary']['url'], item['discovery_url'])
        self.assertFalse(provenance['summary']['independently_verified'])
        self.assertNotEqual(provenance['document']['id'], provenance['summary']['id'])

    def test_missing_summary_origin_is_not_assigned_to_primary_article(self):
        evidence = evidence_for_item(self.source(discovery_url=''))
        self.assertIsNone(evidence['source_evidence_provenance']['summary']['url'])

    def test_fetch_failure_is_not_labeled_as_verified_document(self):
        evidence = evidence_for_item(self.source(fetch_status='fallback_after_fetch_error'))
        self.assertEqual(evidence['source_evidence_provenance']['document']['role'], 'unverified_excerpt')

    def test_truncation_and_content_identity_are_identical_on_both_sides(self):
        item = self.source(source_text='x' * 11900 + 'late critical boundary' + 'z' * 300)
        evidence = evidence_for_item(item)
        document = evidence['source_evidence_provenance']['document']
        self.assertIn('late critical boundary', evidence['source_text'])
        self.assertTrue(document['truncated'])
        self.assertEqual(document['sha256'], hashlib.sha256(evidence['source_text'].encode()).hexdigest())
        changed = evidence_for_item({**item, 'source_text': 'changed'})
        self.assertNotEqual(document['id'], changed['source_evidence_provenance']['document']['id'])

    def test_both_prompts_distinguish_absence_from_contradiction(self):
        self.assertIn(EVIDENCE_GUIDANCE, news_card_system_prompt(labels=['行业视角']))
        self.assertIn(REVIEW_EVIDENCE_GUIDANCE, REVIEW_SYSTEM)
        self.assertIn(EVIDENCE_GUIDANCE.partition('\n')[2], REVIEW_SYSTEM)

    def test_secondary_source_quote_is_passed_with_its_own_url(self):
        from event_editorial import digest
        quote = 'As of August it was losing approximately $8-9 million per month.'
        packet = {'sources': [{'source_id':'secondary','url':'https://other.example/court',
            'text':'The filing says: '+quote, 'read_status':'read'}]}
        item = self.source(editorial_packet=packet, editorial_assessment={
            'input_sha256':digest(packet), 'what_changed':'每月亏损约800万至900万美元。',
            'evidence':[{'source_id':'secondary','quote':quote}]})
        item['supporting_evidence'] = supporting_quotes(item)
        review = compact_source_packet({'news':[item]})['news'][0]
        prompt = news_card_user_prompt(item)
        generation = json.loads(prompt.split('<news_material>\n')[1].split('\n</news_material>')[0])
        self.assertEqual(generation['supporting_evidence'], review['supporting_evidence'])
        self.assertEqual(review['supporting_evidence'][0]['url'], 'https://other.example/court')
        self.assertEqual(review['supporting_evidence'][0]['quote'], quote)
        self.assertEqual(review['source_evidence_provenance']['summary']['role'], 'editorial_summary')
        self.assertIsNone(review['source_evidence_provenance']['summary']['url'])
        item['editorial_packet']['sources'][0]['text'] = 'Different source.'
        with self.assertRaisesRegex(ValueError, 'packet changed'):
            supporting_quotes(item)
        item['editorial_assessment']['input_sha256'] = digest(packet)
        with self.assertRaisesRegex(ValueError, 'quotation is absent'):
            supporting_quotes(item)


if __name__ == '__main__':
    unittest.main()
