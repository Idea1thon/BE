import copy
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from shapely.geometry import box
from recommendation.pipeline import RecommendationRequest, resolve_region, PipelineInputError
from recommendation.llm_explanation import explain_candidates


class AdminDongGroundingTests(unittest.TestCase):
    def test_empty_candidates_do_not_require_or_call_model(self):
        from recommendation.llm_runtime import LLMConfig
        with patch('recommendation.llm_explanation.LLMConfig.from_env', return_value=LLMConfig(mode='required')), \
             patch('recommendation.llm_explanation.OpenAICompatibleJsonClient') as client:
            result = explain_candidates([], llm_mode='required', retrieval_context={'results': []})
        self.assertEqual(result['cards'], [])
        self.assertEqual(result['empty_reason'], 'no_candidates')
        self.assertEqual(result['llm']['calls_succeeded'], 0)
        client.assert_not_called()

    def setUp(self):
        self.candidate = {'candidate_id': 'a', 'fit_tier': '조건부 검토', 'industry_code': 'CS100010',
            'location': {'sigungu': '표시명', 'place_name': 'A', 'host_commercial_area': None,
                         'overlapping_units': {'admin_dong': ['11440680']}},
            'evidence': [], 'reasons': [], 'context_notes': [], 'counter_evidence': [], 'missing_features': []}
        self.row = {'spatial_unit_type': 'admin_dong', 'spatial_unit_code': '11440680',
            'spatial_unit_name': '합정동', 'sigungu_name': '마포구', 'dimension': 'workplace_population',
            'period': '20244', 'value': 1000, 'source_table': 'context.population_snapshot',
            'source_region': '합정동', 'grain_is_proxy': False, 'limitation': '실제 점심 방문량이 아님'}

    def explain(self, rows, candidates=None):
        return explain_candidates(candidates or [self.candidate], llm_mode='offline',
            query_context={'normalized_text': '직장인구를 비교'},
            retrieval_context={'results': [{'rows': rows}]})

    def test_dong_only_observation_is_cited_without_host_or_matching_name(self):
        result = self.explain([self.row])
        card = result['cards'][0]
        self.assertTrue(any('합정동 행정동' in claim and '1000명' in claim for claim in card['context_notes']))
        ref = card['citations']['context_notes:0'][0]
        self.assertIn(ref, result['sources_by_candidate']['a'])
        cell = result['question_comparison']['rows'][0]['cells']['jobs']
        self.assertEqual((cell['value'], cell['spatial_grain']), (1000, '행정동'))

    def test_same_name_wrong_code_is_not_citable(self):
        result = self.explain([{**self.row, 'spatial_unit_code': '11680640'}])
        self.assertEqual(result['cards'][0]['context_notes'], [])

    def test_dong_and_commercial_values_remain_separate(self):
        self.candidate['location']['host_commercial_area'] = {'code': '3120103'}
        commercial = {**self.row, 'spatial_unit_type': 'commercial_area', 'spatial_unit_code': '3120103',
                      'spatial_unit_name': '홍대입구역', 'source_region': '홍대입구역', 'value': 200}
        result = self.explain([self.row, commercial])
        self.assertEqual(len(result['cards'][0]['context_notes']), 2)
        self.assertEqual(result['question_grounding_by_candidate']['a']['ambiguous_topics'], [])
        self.assertEqual(result['question_comparison']['rows'][0]['cells']['jobs']['value'], 200)

    def test_common_dong_does_not_create_candidate_difference(self):
        other = copy.deepcopy(self.candidate)
        other['candidate_id'] = 'b'
        result = self.explain([self.row], [self.candidate, other])
        self.assertEqual(result['question_comparison']['criteria'][0]['status'], 'incomparable')

    def test_region_selection_uses_code_and_rejects_missing_or_wrong_parent(self):
        records = [SimpleNamespace(code='11440680', name='합정동'), SimpleNamespace(code='11680640', name='동명이름')]
        layer = SimpleNamespace(records=records, geoms=[box(0,0,1,1), box(5,5,6,6)])
        names = {'11440': '마포구', '11680': '강남구'}
        request = RecommendationRequest('서울특별시', '이전 표시명', '이전 표시명', 'CS100010',
                                        sigungu_code='11440', admin_dong_code='11440680')
        self.assertEqual(resolve_region(request, layer, names)[0], [records[0]])
        for code in ('11440999', '11680640'):
            with self.assertRaises(PipelineInputError):
                resolve_region(RecommendationRequest('서울특별시', '마포구', '합정동', 'CS100010',
                               sigungu_code='11440', admin_dong_code=code), layer, names)

    def test_legal_alias_resolves_all_administrative_codes(self):
        codes = ['11710670', '11710680', '11710710']
        records = [SimpleNamespace(code=code, name=name) for code, name in
                   zip(codes, ['잠실2동', '잠실3동', '잠실7동'])]
        layer = SimpleNamespace(records=records, geoms=[box(i,0,i+1,1) for i in range(3)])
        selected, _, _ = resolve_region(RecommendationRequest('서울특별시','송파구','잠실동','CS100010'),
                                        layer, {'11710':'송파구'})
        self.assertEqual([record.code for record in selected], codes)
