import unittest
from unittest.mock import patch

from recommendation.llm_input_planner import plan_input
from recommendation.llm_runtime import _extract_json, LLMRuntimeError


class PlannerEmptyArraysTests(unittest.TestCase):
    def plan(self, reply):
        with patch.dict('os.environ', {'LLM_API_URL': 'https://example.test/v1', 'LLM_API_KEY': 'test', 'LLM_MODEL': 'test'}), \
             patch('recommendation.llm_input_planner.OpenAICompatibleJsonClient') as client:
            client.return_value.generate_json.return_value = reply
            return plan_input({'sigungu': '강남구'}, '직장인이 많은 곳', explicit_industry_code='CS100010', llm_mode='required')

    def test_empty_container_fields_do_not_stop_required_planner(self):
        for conditions, preferences in (([], []), ({'target_customer': []}, {'demand_preferences': []})):
            result = self.plan({'conditions': conditions, 'preferences': preferences, 'retrieval_requests': [],
                                'analysis_plan': [], 'industry_candidates': [], 'inference_hypotheses': []})
            self.assertFalse(result['confirmation_required'])
            self.assertIsInstance(result['conditions'], dict)
            self.assertIsInstance(result['preferences'], dict)
            self.assertTrue(result['retrieval_requests'])
            self.assertEqual(result['retrieval_requests'][0]['tool'], 'search_region_evidence')
            self.assertEqual(result['planner']['execution'], 'llm')
            self.assertEqual(result['resolved_industry_code'], 'CS100010')

    def test_array_in_optional_preference_scalar_does_not_crash(self):
        for key in ('strength', 'mode', 'anchor_type'):
            for value in ([], ['unexpected'], {}):
                with self.subTest(key=key, value=value):
                    result = self.plan({'preferences': {'demand_preferences': [
                        {'type': 'worker', 'source_text': '직장인이 많은 곳', key: value}]}})
                    self.assertFalse(result['confirmation_required'])
                    for item in result['preferences']['demand_preferences']:
                        self.assertNotIsInstance(item.get(key), (dict, list))

    def test_nonobject_top_level_is_still_rejected(self):
        for value in ('[]', '[1]', 'false'):
            with self.assertRaises(LLMRuntimeError):
                _extract_json(value)

    def test_array_in_plan_industry_or_hypothesis_scalar_is_ignored(self):
        for reply in ({'analysis_plan': [{'tool': []}]},
                      {'industry_candidates': [{'industry_code': []}]},
                      {'inference_hypotheses': [{'claim': '검증 전 가설', 'claim_type': []}]},
                      {'inference_hypotheses': [{'claim': '검증 전 가설', 'claim_type': 'hypothesis', 'confidence': []}]}):
            with self.subTest(reply=reply):
                self.assertFalse(self.plan(reply)['confirmation_required'])
