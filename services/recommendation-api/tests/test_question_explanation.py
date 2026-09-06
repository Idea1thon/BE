import copy
import unittest
from unittest.mock import patch

from recommendation.llm_explanation import explain_candidates, template_card
from recommendation.llm_runtime import LLMConfig, LLMRuntimeError


class QuestionExplanationTests(unittest.TestCase):
    def setUp(self):
        self.candidate = {'candidate_id': 'a', 'fit_tier': '조건부 검토', 'industry_code': 'CS100010',
            'location': {'place_name': '후보A', 'sigungu': '마포구', 'host_commercial_area': {'code': '3120103'}},
            'reasons': ['유동인구가 많습니다.'], 'counter_evidence': ['접근성 주의'],
            'context_notes': ['아파트 배경 통계입니다.'], 'missing_features': ['현장 확인 필요'],
            'evidence': [{'metric_name': 'R-ONE_임대가격지수', 'value': 105.3, 'unit': '지수',
                          'period': '20262', 'spatial_grain': '권역', 'grain_is_proxy': True}]}
        self.row = {'spatial_unit_type': 'commercial_area', 'spatial_unit_code': '3120103',
            'spatial_unit_name': '홍대입구역', 'sigungu_name': '마포구', 'period': '20262',
            'dimension': 'rent', 'value': '105.3', 'source_table': 'context.rent_index',
            'source_region': '홍대/합정', 'grain_is_proxy': True,
            'limitation': '조사권역 대리지표이며 개별 매물 월세가 아님', 'period_policy': 'latest_available'}
        self.query = {'normalized_text': '임대료와 공실을 비교하고 개별 매물 월세는 추정하지 마세요.'}

    def run_explanation(self, rows=None, mode='offline'):
        return explain_candidates([self.candidate], llm_mode=mode,
            query_context=self.query, retrieval_context={'results': [{'rows': rows if rows is not None else [self.row]}]})

    def test_offline_render_has_exact_sql_citation_scope_and_limitations(self):
        before = copy.deepcopy(self.candidate)
        result = self.run_explanation()
        card = result['cards'][0]
        self.assertEqual(card['reasons'], [])
        self.assertEqual(card['counter_evidence'], before['counter_evidence'])
        self.assertTrue(set(before['missing_features']).issubset(card['missing_features']))
        self.assertEqual(len(card['context_notes']), 1)
        text = card['context_notes'][0]
        for value in ('105.3지수', '2026년 2분기', '홍대/합정', '개별 매물 월세가 아님'):
            self.assertIn(value, text)
        ref = card['citations']['context_notes:0'][0]
        self.assertTrue(ref.startswith('retrieval-'))
        self.assertIn(ref, result['sources_by_candidate']['a'])
        diagnostic = result['verification_by_candidate']['a']
        self.assertEqual(diagnostic['draft_retrieval_citation_count'], 0)
        self.assertEqual(diagnostic['final_retrieval_citation_count'], 1)
        self.assertEqual(diagnostic['verified_claim_count'], 0)
        self.assertEqual(result['question_grounding_by_candidate']['a']['server_rendered_claims'][0]['final_position'], 'context_notes:0')
        self.assertEqual(result['question_comparison']['rows'][0]['cells']['vacancy']['status'], 'missing')
        self.assertEqual(self.candidate, before)

    def test_wrong_region_unrelated_and_invalid_sql_do_not_become_claims(self):
        for updates in ({'spatial_unit_code': '999'},
                        {'value': 'NaN'}, {'source_table': 'untrusted'}, {'source_region': ''}):
            with self.subTest(updates=updates):
                result = self.run_explanation([{**self.row, **updates}])
                self.assertEqual(result['cards'][0]['context_notes'], [])
                self.assertEqual(result['verification_by_candidate']['a']['final_retrieval_citation_count'], 0)
        self.query = {'normalized_text': '직장인 경쟁 비교'}
        self.assertEqual(self.run_explanation()['cards'][0]['context_notes'], [])

    def test_llm_omission_and_generation_error_both_keep_server_sql(self):
        for reply in (template_card(self.candidate), LLMRuntimeError('unavailable')):
            with self.subTest(reply=type(reply).__name__):
                with patch('recommendation.llm_explanation.LLMConfig.from_env', return_value=LLMConfig(
                        endpoint='https://example.invalid', api_key='test', model='test')):
                    with patch('recommendation.llm_explanation.OpenAICompatibleJsonClient') as factory:
                        # First call is source ranking; second is generation.
                        factory.return_value.generate_json.side_effect = [{'ordered_ids': ['E01']}, copy.deepcopy(reply)]
                        result = self.run_explanation(mode='auto')
                self.assertEqual(result['verification_by_candidate']['a']['final_retrieval_citation_count'], 1)
                self.assertNotIn('아파트 배경 통계입니다.', result['cards'][0]['context_notes'])

    def test_catalog_retains_other_candidate_source_without_prompting_it(self):
        other = {**self.row, 'spatial_unit_code': '999', 'value': '99'}
        result = self.run_explanation([self.row, other])
        catalog = result['sources_by_candidate']['a']
        self.assertEqual(sum(ref.startswith('retrieval-') for ref in catalog), 2)
        selected = result['relevance_by_candidate']['a']['selected_ids']
        self.assertEqual(sum(ref.startswith('retrieval-') for ref in selected), 1)

    def test_no_client_no_retrieval_does_not_invent_observed_values(self):
        result = self.run_explanation([])
        self.assertEqual(result['cards'][0]['context_notes'], [])
        self.assertEqual(result['verification_by_candidate']['a']['final_retrieval_citation_count'], 0)
        self.assertTrue(result['cards'][0]['missing_features'])

    def test_excluded_only_topic_is_not_restored_by_offline_template(self):
        self.query = {'normalized_text': '경쟁은 제외해 주세요'}
        self.candidate['reasons'] = ['동일 업종 점포수가 적어 경쟁이 약함']
        self.candidate['counter_evidence'] = []
        self.candidate['missing_features'] = []
        result = self.run_explanation([])
        self.assertEqual(result['cards'][0]['reasons'], [])
        self.assertTrue(result['cards'][0]['missing_features'])

    def test_multiple_proxy_regions_do_not_silently_choose_first(self):
        result = self.run_explanation([self.row, {**self.row, 'source_region': '신촌', 'value': '90'}])
        self.assertEqual(result['cards'][0]['context_notes'], [])
        self.assertEqual(result['question_grounding_by_candidate']['a']['ambiguous_topics'], ['rent'])
        self.assertTrue(any('여러 관측값' in claim for claim in result['cards'][0]['missing_features']))

    def test_other_industry_never_becomes_same_industry_background(self):
        self.query = {'normalized_text': '카페 경쟁을 비교'}
        row = {**self.row, 'dimension': 'stores', 'industry_code': 'CS100001',
               'source_table': 'location.store_quarter', 'value': {'total_store_count': 500}}
        result = self.run_explanation([row])
        self.assertEqual(result['cards'][0]['context_notes'], [])

    def test_comparison_reaches_cards_and_every_reference_resolves(self):
        self.candidate['evidence'] = [{'metric_name': '총_직장_인구_수', 'value': 500, 'unit': '명',
                                      'period': '20261', 'spatial_grain': '상권', 'grain_is_proxy': False}]
        other = copy.deepcopy(self.candidate)
        other.update(candidate_id='b')
        other['location']['host_commercial_area']['code'] = '3120104'
        other['location']['place_name'] = '후보B'
        other['evidence'][0]['value'] = 200
        result = explain_candidates([self.candidate, other], llm_mode='offline',
                                    query_context={'normalized_text': '직장인구를 비교'})
        for card in result['cards']:
            self.assertTrue(any('300명' in claim and '2026년 1분기' in claim for claim in card['context_notes']))
            self.assertFalse(any('설명을 구성하지 못했습니다' in claim for claim in card['missing_features']))
            sources = result['sources_by_candidate'][card['candidate_id']]
            for refs in card['citations'].values():
                self.assertTrue(all(ref in sources for ref in refs))
            d = result['question_grounding_by_candidate'][card['candidate_id']]
            self.assertTrue(d['optional_answer_available'])
            for claim in d['comparison']['server_rendered_claims']:
                bucket, index = claim['final_position'].split(':')
                self.assertTrue(card[bucket][int(index)])
