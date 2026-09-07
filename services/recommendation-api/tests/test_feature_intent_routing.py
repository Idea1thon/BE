import json
import unittest

from recommendation.evidence_reranker import select_sources, source_topic_ids
from recommendation.question_contract import build_question_contract
from recommendation.llm_explanation import explain_candidates


class FeatureIntentRoutingTests(unittest.TestCase):
    def query(self, text='외국인이 많은 곳이면 좋겠어요', preferences=None):
        return {
            'original_text': text,
            'normalized_text': text,
            'preferences': preferences or {},
        }

    def test_planner_preference_and_catalog_map_without_foreign_topic_regex(self):
        query = self.query(preferences={
            'demand_preferences': [{
                'type': 'target_customer', 'value': 'foreigner',
                'source_text': '외국인이 많은 곳이면 좋겠어요',
            }],
        })
        contract = build_question_contract(query)
        self.assertEqual(contract['topic_ids'], [])
        self.assertEqual(contract['feature_ids'], ['FC-06a', 'FC-06b'])
        self.assertEqual(contract['feature_catalog_version'], '2026-09-07')
        self.assertEqual({row['match_method'] for row in contract['feature_matches']}, {'preference_metadata'})
        self.assertNotIn('unmapped_question', {row['id'] for row in contract['unsupported']})

    def test_catalog_alias_fallback_works_when_planner_has_no_preference(self):
        contract = build_question_contract(self.query(preferences={}))
        self.assertEqual(contract['feature_ids'], ['FC-06a', 'FC-06b'])
        self.assertTrue(all(row['match_method'] == 'catalog_semantic' for row in contract['feature_matches']))

    def test_negated_and_unrelated_text_do_not_create_feature_request(self):
        self.assertEqual(build_question_contract(self.query('외국인은 제외하고 싶어요'))['feature_ids'], [])
        contract = build_question_contract(self.query('반려견을 동반할 수 있나요?'))
        self.assertEqual(contract['feature_ids'], [])
        self.assertEqual(contract['unsupported'][0]['id'], 'unmapped_question')

    def test_reranker_uses_feature_identity_for_structured_evidence(self):
        record = {
            'feature_id': 'FC-06b', 'metric_name': '외국인_방문_근사비율',
            'value': 0.22, 'unit': '비율',
        }
        sources = {
            'foreign': {'bucket': 'evidence', 'text': json.dumps(record, ensure_ascii=False)},
            'unrelated': {'bucket': 'evidence', 'text': '{"metric_name":"계획철도","note":"외국인"}'},
        }
        query = {
            'normalized_text': '외국인이 많은 곳',
            'question_contract': {'topic_ids': [], 'feature_ids': ['FC-06b']},
        }
        self.assertEqual(source_topic_ids(sources['foreign']), {'foreign_customer_presence'})
        self.assertEqual(source_topic_ids(sources['unrelated']), set())
        selected, _ = select_sources(query, sources)
        self.assertIn('foreign', selected)
        self.assertNotIn('unrelated', selected)

    def test_feature_only_question_renders_structured_candidate_observations(self):
        candidate = {
            'candidate_id': 'foreign-site', 'fit_tier': '조건부 검토',
            'industry_code': 'CS100010',
            'location': {'host_commercial_area': {'code': '3120103'}},
            'reasons': [], 'counter_evidence': [], 'context_notes': [],
            'missing_features': [],
            'evidence': [
                {'feature_id': 'FC-06a', 'metric_name': '외국인_거주_근사비율',
                 'value': 0.1191, 'unit': '비율', 'period': '20262',
                 'observed_end_period': '20262', 'spatial_grain': '행정동',
                 'grain_is_proxy': False, 'limitation': '근사 신호이며 정밀 비율 아님'},
                {'feature_id': 'FC-06b', 'metric_name': '외국인_방문_근사비율',
                 'value': 0.2261, 'unit': '비율', 'period': '20262',
                 'observed_end_period': '20262', 'spatial_grain': '행정동',
                 'grain_is_proxy': False, 'limitation': '근사 신호이며 정밀 비율 아님'},
            ],
        }
        result = explain_candidates([candidate], llm_mode='offline', query_context=self.query())
        contract = result['query_context']['question_contract']
        self.assertEqual(contract['feature_ids'], ['FC-06a', 'FC-06b'])
        card = result['cards'][0]
        self.assertTrue(any('FC-06a' in text for text in card['context_notes']))
        self.assertTrue(any('FC-06b' in text for text in card['context_notes']))
        rendered = result['question_grounding_by_candidate']['foreign-site']['server_rendered_claims']
        self.assertEqual({item['feature_id'] for item in rendered}, {'FC-06a', 'FC-06b'})
        self.assertTrue(all(item['method'] == 'validated_candidate_evidence_template' for item in rendered))


if __name__ == '__main__':
    unittest.main()
