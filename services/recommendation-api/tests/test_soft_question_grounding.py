import unittest
from unittest.mock import patch

from recommendation.llm_explanation import explain_candidates, template_card
from recommendation.llm_runtime import LLMConfig
from recommendation.question_explanation import finish_question_card


class SoftQuestionGroundingTests(unittest.TestCase):
    def setUp(self):
        self.candidate = {'candidate_id': 'a', 'fit_tier': '조건부 검토',
                          'industry_code': 'CS100010',
                          'reasons': [], 'counter_evidence': [], 'missing_features': [],
                          'context_notes': ['지하철역까지 거리는 100m입니다.'], 'evidence': []}
        self.contract = {'topic_ids': ['jobs'], 'unsupported': []}
        self.source = {'context_notes:0': {'bucket': 'context_notes', 'text': self.candidate['context_notes'][0]}}

    def test_selected_unclassified_verbatim_survives_postprocessing(self):
        final, diagnostic = finish_question_card(template_card(self.candidate), self.candidate,
                                                  self.source, [], self.contract)
        self.assertEqual(final['context_notes'], self.candidate['context_notes'])
        self.assertEqual(diagnostic['removed_optional_claim_count'], 0)

    def test_verified_rewrite_survives_unknown_topic_but_unsupported_number_does_not(self):
        for value in (100, 900):
            with self.subTest(value=value):
                draft = template_card(self.candidate)
                draft['context_notes'] = [f'지하철역은 {value}m 떨어져 있습니다.']
                draft['citations'] = {'context_notes:0': ['context_notes:0']}
                replies = [draft]
                if value == 100:
                    replies.append({'verdicts': [{'claim_id': 'context_notes:0', 'supported': True}]})
                with patch('recommendation.llm_explanation.LLMConfig.from_env', return_value=LLMConfig(
                        endpoint='https://example.invalid', api_key='test', model='test')), \
                     patch('recommendation.llm_explanation.OpenAICompatibleJsonClient') as factory, \
                     patch('recommendation.llm_explanation.select_sources', return_value=(self.source, {})):
                    factory.return_value.generate_json.side_effect = replies
                    result = explain_candidates([self.candidate], query_context={
                        'normalized_text': '직장인 수요와 지하철 접근성을 비교', 'question_contract': self.contract})
                card = result['cards'][0]
                diagnostic = result['verification_by_candidate']['a']
                if value == 100:
                    self.assertIn(draft['context_notes'][0], card['context_notes'])
                    self.assertEqual(diagnostic['verified_claim_count'], 1)
                else:
                    self.assertNotIn(draft['context_notes'][0], card['context_notes'])
                    self.assertEqual(diagnostic['verification_counts']['unsupported_number'], 1)

    def test_unselected_template_prose_does_not_flood_fallback(self):
        final, diagnostic = finish_question_card(template_card(self.candidate), self.candidate, {}, [], self.contract)
        self.assertEqual(final['context_notes'], [])
        self.assertEqual(diagnostic['removed_optional_claim_count'], 1)

    def test_unknown_topic_does_not_send_another_candidates_sql_to_model(self):
        self.candidate['location'] = {'host_commercial_area': {'code': '3120103'}}
        row = {'spatial_unit_type':'commercial_area', 'spatial_unit_code':'9999999',
               'spatial_unit_name':'다른 상권', 'sigungu_name':'마포구', 'period':'20261',
               'industry_code':'CS100010', 'dimension':'sales', 'value':100,
               'source_table':'location.sales_quarter'}
        result = explain_candidates([self.candidate], llm_mode='offline',
            query_context={'normalized_text':'지하철 접근성을 알려줘'},
            retrieval_context={'results':[{'rows':[row]}]})
        sources = result['sources_by_candidate']['a']
        self.assertTrue(any(ref.startswith('retrieval-') for ref in sources))
        self.assertFalse(any(ref.startswith('retrieval-') for ref in result['relevance_by_candidate']['a']['selected_ids']))
