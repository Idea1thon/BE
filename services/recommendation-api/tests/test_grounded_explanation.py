"""Regression checks for cited paraphrases and supplementary retrieval facts."""
import unittest
from unittest.mock import Mock, patch
from recommendation.llm_explanation import (
    explanation_sources, template_card, validate_card, verify_grounded_claims,
    explain_candidates, prune_unverified_claims,
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
        self.assertTrue(validate_card(self.candidate, self.card, verified_claims=verified, source_catalog=sources)[0])
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

    def test_candidate_evidence_is_citable_from_any_observed_bucket(self):
        cand = {
            'candidate_id': 'j', 'fit_tier': '조건부 검토', 'reasons': ['직장인구 관측치 있음'],
            'counter_evidence': [], 'context_notes': [], 'missing_features': [],
            'evidence': [{'metric_name': 'jobs', 'value': 4206, 'seoul_percentile': 88.6}],
        }
        sources = explanation_sources(cand, [])
        self.assertEqual(sources['candidate-evidence:0']['bucket'], 'evidence')
        card = template_card(cand)
        card['reasons'] = ['상권 배경 직장인구가 4,206명으로 서울 88.6백분위입니다.']
        card['citations'] = {'reasons:0': ['candidate-evidence:0']}
        self.client.generate_json.return_value = {'verdicts': [{'claim_id': 'reasons:0', 'supported': True}]}
        verified = verify_grounded_claims(cand, card, sources, self.client)
        self.assertEqual(verified, {'reasons:0'})
        self.assertTrue(validate_card(cand, card, verified_claims=verified, source_catalog=sources)[0])

    def test_summary_may_cite_other_observed_buckets_but_reasons_may_not(self):
        cand = {
            'candidate_id': 's', 'fit_tier': '주의', 'reasons': [],
            'counter_evidence': ['임대료 확인 불가'], 'context_notes': [], 'missing_features': [],
            'evidence': [{'metric_name': 'jobs', 'value': 4206}],
        }
        sources = explanation_sources(cand, [])
        card = template_card(cand)
        card['summary'] = '주의 후보입니다. 직장인구 4,206명 배경은 있으나 임대료 확인 불가 등은 별도 검토가 필요합니다.'
        card['citations'] = {'summary': ['candidate-evidence:0', 'counter_evidence:0']}
        self.client.generate_json.return_value = {'verdicts': [{'claim_id': 'summary', 'supported': True}]}
        self.assertEqual(verify_grounded_claims(cand, card, sources, self.client), {'summary'})
        # a reasons claim cannot launder a counter_evidence source
        card['reasons'] = ['임대료 확인 불가']
        card['citations'] = {'reasons:0': ['counter_evidence:0']}
        self.assertFalse(validate_card(cand, card, source_catalog=sources)[0])

    def test_prune_keeps_verified_and_reverts_or_drops_the_rest(self):
        cand = {
            'candidate_id': 'p', 'fit_tier': '조건부 검토',
            'reasons': ['버스정류장 3개', '지하철역 2개'], 'counter_evidence': [],
            'context_notes': [], 'missing_features': [], 'evidence': [],
        }
        card = template_card(cand)
        card['summary'] = '아주 좋은 입지입니다.'
        card['reasons'] = ['버스정류장이 3개 있습니다.', '지하철역이 2개 있습니다.']
        card['citations'] = {'summary': ['summary'], 'reasons:0': ['reasons:0'], 'reasons:1': ['reasons:1']}
        pruned, verified = prune_unverified_claims(cand, card, {'reasons:0'})
        self.assertEqual(pruned['summary'], template_card(cand)['summary'])
        self.assertEqual(pruned['reasons'], ['버스정류장이 3개 있습니다.'])
        self.assertEqual(verified, {'reasons:0'})
        self.assertEqual(pruned['citations'], {'reasons:0': ['reasons:0']})
        self.assertTrue(validate_card(cand, pruned, verified_claims=verified)[0])

    def test_verified_rewrites_do_not_trip_the_bucket_count_or_length_cap(self):
        # note가 많은 후보(서교동: 인구 FC-03~06 + 도시계획 FC-51/52)는 원본
        # verbatim 15개에 검증 통과 재서술 몇 개만 붙어도 max(12, len) 상한을 넘었다.
        notes = [f'배경 지표 {i} 관측치 (FC-0{i})' for i in range(15)]
        cand = {
            'candidate_id': 'seogyo', 'fit_tier': '조건부 검토',
            'reasons': [], 'counter_evidence': [], 'missing_features': [],
            'context_notes': notes, 'evidence': [],
        }
        card = template_card(cand)
        card['context_notes'] = notes + [
            '배경 지표 0 관측치는 서울 대비 낮은 편입니다.',
            '배경 지표 1 관측치는 최신 스냅샷 기준입니다.',
            '배경 지표 ' + '길게 ' * 250 + '끝.',   # 700자 초과 재서술
        ]
        card['citations'] = {
            'context_notes:15': ['context_notes:0'],
            'context_notes:16': ['context_notes:1'],
            'context_notes:17': ['context_notes:2'],
        }
        verified = {'context_notes:15', 'context_notes:16', 'context_notes:17'}
        sources = explanation_sources(cand, [])
        ok, errs = validate_card(cand, card, verified_claims=verified, source_catalog=sources)
        self.assertTrue(ok, errs)
        # 검증 안 된 재서술까지 얹으면 다시 상한에 걸린다.
        card['context_notes'].append('검증 안 된 추가 문장')
        self.assertFalse(validate_card(cand, card, verified_claims=verified, source_catalog=sources)[0])

    @patch('recommendation.llm_explanation.LLMConfig.from_env')
    @patch('recommendation.llm_explanation.OpenAICompatibleJsonClient')
    def test_partial_verification_yields_llm_card_without_the_bad_claim(self, client_type, config):
        config.return_value = LLMConfig(endpoint='https://example.invalid', api_key='test', model='test')
        cand = {
            'candidate_id': 'test', 'fit_tier': '조건부 검토',
            'reasons': ['반경 내 버스정류장 3개', '반경 내 지하철역 2개'],
            'counter_evidence': [], 'context_notes': [], 'missing_features': [], 'evidence': [],
        }
        card = template_card(cand)
        card['reasons'] = ['버스정류장이 반경 내에 3개 있습니다.', '지하철역까지 5분 거리입니다.']
        card['citations'] = {'reasons:0': ['reasons:0'], 'reasons:1': ['reasons:1']}
        client_type.return_value.generate_json.side_effect = [
            card, {'verdicts': [{'claim_id': 'reasons:0', 'supported': True},
                                {'claim_id': 'reasons:1', 'supported': False}]},
        ]
        result = explain_candidates([cand], llm_mode='required')
        self.assertEqual(result['explanation_mode'], 'llm')
        self.assertEqual(result['cards'][0]['reasons'], ['버스정류장이 반경 내에 3개 있습니다.'])

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
