"""Regression checks for cited paraphrases and supplementary retrieval facts."""
import unittest
from unittest.mock import Mock, patch
from recommendation.llm_explanation import (
    explanation_sources, template_card, validate_card, verify_grounded_claims,
    explain_candidates,
)
from recommendation.llm_runtime import LLMConfig, LLMRuntimeError


class GroundedExplanationTests(unittest.TestCase):
    def setUp(self):
        self.candidate = {
            'candidate_id': 'test', 'fit_tier': '조건부 검토',
            'reasons': ['반경 내 버스정류장 3개'], 'counter_evidence': [],
            'context_notes': [], 'missing_features': [],
            'evidence': [{'metric_name': 'bus_count', 'value': 3}],
        }
        self.card = template_card(self.candidate)
        self.card['reasons'] = ['버스정류장이 반경 내에 3개 있습니다.']
        self.card['citations'] = {'reasons:0': ['reasons:0']}
        self.sources = explanation_sources(self.candidate, [])
        self.client = Mock()
        self.client.generate_json.return_value = {'verdicts': [{'claim_id': 'reasons:0', 'supported': True}]}

    def test_cited_paraphrase_requires_semantic_verification(self):
        self.assertFalse(validate_card(self.candidate, self.card)[0])
        verified = verify_grounded_claims(self.candidate, self.card, self.sources, self.client)
        self.assertTrue(validate_card(self.candidate, self.card, verified_claims=verified)[0])
        self.client.generate_json.assert_called_once()

    def test_unknown_ref_wrong_bucket_and_new_number_never_reach_verifier(self):
        for refs, text in [(['unknown'], '버스정류장 3개'), (['summary'], '버스정류장 3개'), (['reasons:0'], '버스정류장 9개')]:
            with self.subTest(refs=refs, text=text):
                self.card['citations'] = {'reasons:0': refs}
                self.card['reasons'] = [text]
                self.assertEqual(verify_grounded_claims(self.candidate, self.card, self.sources, self.client), set())
        self.client.generate_json.assert_not_called()

    def test_rejected_or_malformed_verdict_fails_closed(self):
        for reply in ({}, {'verdicts': []}, {'verdicts': [{'claim_id': 'reasons:0', 'supported': 'true'}]}, {'verdicts': [{'claim_id': 'other', 'supported': True}]}):
            self.client.generate_json.return_value = reply
            self.assertEqual(verify_grounded_claims(self.candidate, self.card, self.sources, self.client), set())

    def test_grouped_numbers_pass_but_sign_change_fails(self):
        self.candidate['reasons'] = ['분기 매출 120000원']
        self.sources = explanation_sources(self.candidate, [])
        self.card['reasons'] = ['분기 매출은 120,000원입니다.']
        verified = verify_grounded_claims(self.candidate, self.card, self.sources, self.client)
        self.assertEqual(verified, {'reasons:0'})
        self.assertTrue(validate_card(self.candidate, self.card, verified_claims=verified)[0])
        self.card['reasons'] = ['분기 매출은 -120000원입니다.']
        self.assertEqual(verify_grounded_claims(self.candidate, self.card, self.sources, self.client), set())

    def test_exact_copy_uses_no_extra_call(self):
        self.assertEqual(verify_grounded_claims(self.candidate, template_card(self.candidate), self.sources, self.client), set())
        self.client.generate_json.assert_not_called()

    def test_retrieved_fact_can_be_cited_only_as_background(self):
        evidence = [{'evidence_id': 'retrieval-example', 'value': 700, 'unit': '원', 'period': '20261', 'spatial_unit_name': '역삼1동'}]
        sources = explanation_sources(self.candidate, evidence)
        self.card = template_card(self.candidate)
        self.card['context_notes'] = ['역삼1동의 20261 분기 매출은 700원입니다.']
        self.card['citations'] = {'context_notes:0': ['retrieval-example']}
        self.client.generate_json.return_value = {'verdicts': [{'claim_id': 'context_notes:0', 'supported': True}]}
        verified = verify_grounded_claims(self.candidate, self.card, sources, self.client)
        self.assertTrue(validate_card(self.candidate, self.card, verified_claims=verified)[0])
        self.card['reasons'] = self.card.pop('context_notes')
        self.card['citations'] = {'reasons:0': ['retrieval-example']}
        self.assertEqual(verify_grounded_claims(self.candidate, self.card, sources, self.client), set())

    @patch('recommendation.llm_explanation.LLMConfig.from_env')
    @patch('recommendation.llm_explanation.OpenAICompatibleJsonClient')
    def test_generation_verification_and_reference_output(self, client_type, config):
        config.return_value = LLMConfig(endpoint='https://example.invalid', api_key='test', model='test')
        client_type.return_value.generate_json.side_effect = [self.card, {'verdicts': [{'claim_id': 'reasons:0', 'supported': True}]}]
        result = explain_candidates([self.candidate], llm_mode='required')
        self.assertEqual(result['explanation_mode'], 'llm')
        self.assertEqual(result['cards'][0]['reasons'], self.card['reasons'])
        self.assertEqual(result['sources_by_candidate']['test']['reasons:0']['text'], self.candidate['reasons'][0])
        self.assertEqual(client_type.return_value.generate_json.call_count, 2)

    @patch('recommendation.llm_explanation.LLMConfig.from_env')
    @patch('recommendation.llm_explanation.OpenAICompatibleJsonClient')
    def test_auto_fallback_and_required_failure_on_verifier_outage(self, client_type, config):
        config.return_value = LLMConfig(endpoint='https://example.invalid', api_key='test', model='test')
        for mode in ('auto', 'required'):
            client_type.return_value.generate_json.side_effect = [self.card, LLMRuntimeError('verifier unavailable')]
            if mode == 'required':
                with self.assertRaises(LLMRuntimeError):
                    explain_candidates([self.candidate], llm_mode=mode)
            else:
                result = explain_candidates([self.candidate], llm_mode=mode)
                self.assertEqual(result['explanation_mode'], 'template')


if __name__ == '__main__':
    unittest.main()
