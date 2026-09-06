import copy
import json
import unittest

from recommendation.candidate_comparison import build_candidate_comparison
from recommendation.comparison_explanation import add_comparison_context


def candidate(identifier, value, **changes):
    return {'candidate_id': identifier, 'location': {'place_name': identifier + ' 건물', 'host_commercial_area': {'code': identifier}},
            'evidence': [{'metric_name': '총_직장_인구_수', 'value': value, 'period': '20262', 'unit': '명',
                          'spatial_grain': '상권', 'grain_is_proxy': False, **changes}]}


class ComparisonExplanationTests(unittest.TestCase):
    def test_observed_difference_is_cited_and_inputs_are_unchanged(self):
        candidates = [candidate('a', 20), candidate('b', 50)]
        comparison = build_candidate_comparison(candidates, {'topic_ids': ['jobs']})
        card = {'context_notes': ['기존 설명'], 'missing_features': [], 'citations': {'context_notes:0': ['existing']}, 'explanation_mode': 'llm'}
        before = copy.deepcopy((card, comparison, candidates))
        sources = {}
        result, diagnostic = add_comparison_context(card, candidates[0], comparison, sources)
        self.assertEqual((card, comparison, candidates), before)
        self.assertEqual(result['explanation_mode'], 'mixed')
        self.assertIn('-30명', result['context_notes'][1])
        self.assertIn('a 건물', result['context_notes'][1])
        self.assertIn('b 건물', result['context_notes'][1])
        ref = result['citations']['context_notes:1'][0]
        source = json.loads(sources[ref]['text'])
        self.assertEqual(source['left_observation']['source_ids'], ['candidate-evidence:0'])
        self.assertEqual(source['right_candidate_id'], 'b')
        self.assertEqual(diagnostic['server_rendered_claims'][0]['final_position'], 'context_notes:1')

    def test_incompatible_and_single_candidate_comparisons_are_missing(self):
        for candidates in ([candidate('a', 20)], [candidate('a', 20), candidate('b', 30, period='20261')]):
            comparison = build_candidate_comparison(candidates, {'topic_ids': ['jobs']})
            sources = {}
            result, diagnostic = add_comparison_context({'explanation_mode': 'llm'}, candidates[0], comparison, sources)
            self.assertEqual(result['context_notes'], [])
            self.assertIn('비교 미확인', result['missing_features'][0])
            self.assertEqual(sources, {})
            self.assertEqual(diagnostic['server_rendered_claims'][0]['method'], 'comparison_unavailable')

    def test_first_pair_per_topic_is_bounded_and_idempotent(self):
        candidates = [candidate('a', 20), candidate('b', 30), candidate('c', 40)]
        comparison = build_candidate_comparison(candidates, {'topic_ids': ['jobs']})
        result, _ = add_comparison_context({}, candidates[0], comparison, {})
        repeated, diagnostic = add_comparison_context(result, candidates[0], comparison, {})
        self.assertEqual(len(repeated['context_notes']), 1)
        self.assertEqual(diagnostic['server_rendered_claims'], [])

    def test_no_compatible_pair_for_candidate_is_not_reported_comparable(self):
        candidates = [candidate('a', None), candidate('b', 30), candidate('c', 40)]
        comparison = build_candidate_comparison(candidates, {'topic_ids': ['jobs']})
        result, _ = add_comparison_context({}, candidates[0], comparison, {})
        self.assertEqual(result['context_notes'], [])
        self.assertIn('이 후보에 비교 가능한 관측 쌍이 없습니다', result['missing_features'][0])

    def test_no_topics_and_unknown_topics_do_not_invent_claims(self):
        for comparison in ({'rows': [], 'criteria': []}, {'rows': [{'candidate_id': 'a'}], 'criteria': [{'topic_id': 'unknown'}]}):
            result, diagnostic = add_comparison_context({}, {'candidate_id': 'a'}, comparison, {})
            self.assertEqual(result['context_notes'], [])
            self.assertEqual(diagnostic['server_rendered_claims'], [])
