"""Regressions for the empty/ill-typed Azure explanations observed after PR47."""
import copy
import unittest
from unittest.mock import patch

from recommendation.llm_explanation import explain_candidates, template_card
from recommendation.llm_runtime import LLMConfig, LLMRuntimeError


class ExplanationRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.candidate = {
            'candidate_id': 'building', 'fit_tier': '조건부 검토',
            'reasons': ['버스정류장 3개'],
            'counter_evidence': ['권역 통계이며 개별 건물 공실이 아님'],
            'context_notes': [],
            'missing_features': [{'feature': '월세', 'reason': '확인 안 됨'}],
            'evidence': [],
        }

    def run_card(self, card, verdict=None, *, mode='auto'):
        replies = [copy.deepcopy(card)]
        if verdict is not None:
            replies.append(verdict)
        with patch('recommendation.llm_explanation.LLMConfig.from_env', return_value=LLMConfig(
                endpoint='https://example.invalid', api_key='test', model='test')):
            with patch('recommendation.llm_explanation.OpenAICompatibleJsonClient') as factory:
                factory.return_value.generate_json.side_effect = replies
                result = explain_candidates([self.candidate], llm_mode=mode)
                return result, factory.return_value.generate_json.call_count

    def test_entirely_pruned_card_restores_template_and_is_not_llm_success(self):
        card = template_card(self.candidate)
        card.update(reasons=['버스정류장은 9개입니다.'], counter_evidence=[], missing_features=[])
        card['citations'] = {'reasons:0': ['reasons:0']}
        result, calls = self.run_card(card)
        self.assertEqual(result['cards'][0], template_card(self.candidate))
        self.assertEqual(result['explanation_mode'], 'template')
        self.assertTrue(result['degraded'])
        self.assertEqual(result['llm']['calls_succeeded'], 0)
        self.assertEqual(result['verification_by_candidate']['building']['fallback_reason'], 'empty_explanation')
        self.assertEqual(calls, 1)
        with self.assertRaises(LLMRuntimeError):
            self.run_card(card, mode='required')

    def test_model_cannot_succeed_with_empty_lists_or_only_a_rewritten_summary(self):
        card = template_card(self.candidate)
        for bucket in ['reasons', 'counter_evidence', 'context_notes', 'missing_features']:
            card[bucket] = []
        for rewrite in [False, True]:
            with self.subTest(rewrite=rewrite):
                if rewrite:
                    card['summary'] = '조건부 검토 후보로 별도 확인이 필요합니다.'
                    card['citations'] = {'summary': ['summary']}
                verdict = {'verdicts': [{'claim_id': 'summary', 'supported': True}]} if rewrite else None
                result, _ = self.run_card(card, verdict)
                if rewrite:
                    self.assertEqual(result['generation_status'], 'partial')
                    self.assertIn('summary', result['generated_sections_by_candidate']['building'])
                    self.assertEqual(result['cards'][0]['summary'], card['summary'])
                else:
                    self.assertEqual(result['explanation_mode'], 'template')
                self.assertEqual(result['cards'][0]['reasons'], [] if rewrite else ['버스정류장 3개'])

    def test_one_grounded_section_is_retained_with_explicit_partial_status(self):
        card = template_card(self.candidate)
        card.update(
            summary='건물 데이터와 확인되지 않은 임대 조건을 함께 검토해야 합니다.',
            reasons=[], counter_evidence=[], context_notes=[], missing_features=[],
        )
        card['citations'] = {'summary': ['summary']}
        result, _ = self.run_card(card, {'verdicts': [{'claim_id': 'summary', 'supported': True}]})
        diagnostic = result['verification_by_candidate']['building']
        self.assertIn('summary', diagnostic['generated_sections'])
        self.assertIn('strengths', diagnostic['failed_sections'])
        self.assertEqual(diagnostic['grounding_status'], 'grounded')
        self.assertEqual(result['generation_status'], 'partial')
        self.assertEqual(result['generated_sections_by_candidate']['building'], ['summary'])
        self.assertNotEqual(result['cards'][0]['summary'], template_card(self.candidate)['summary'])

    def test_malformed_array_items_are_rejected_before_pruning_or_semantic_call(self):
        for malformed in [[{'text': '버스정류장 3개'}], [None], '버스정류장 3개', [''], ['  ']]:
            with self.subTest(malformed=malformed):
                card = template_card(self.candidate)
                card['reasons'] = malformed
                # Even an otherwise verifiable rewrite must not trigger semantic
                # checks while the card's structural contract is broken.
                card['summary'] = '조건부 검토 후보입니다.'
                card['citations'] = {'summary': ['summary']}
                result, calls = self.run_card(card)
                self.assertEqual(result['explanation_mode'], 'template')
                d = result['verification_by_candidate']['building']
                self.assertEqual(d['generation_status'], 'invalid_card')
                self.assertEqual(d['fallback_reason'], 'draft_schema_invalid')
                self.assertEqual(calls, 1)

    def test_missing_or_invalid_required_fields_do_not_disappear_during_pruning(self):
        for key, value in [('summary', None), ('candidate_id', 'other'), ('inference_hypotheses', None)]:
            card = template_card(self.candidate)
            card[key] = value
            result, _ = self.run_card(card)
            self.assertEqual(result['verification_by_candidate']['building']['fallback_reason'], 'draft_schema_invalid')
        card = template_card(self.candidate)
        del card['missing_features']
        result, _ = self.run_card(card)
        self.assertEqual(result['verification_by_candidate']['building']['fallback_reason'], 'draft_schema_invalid')

    def test_valid_reason_kept_but_omitted_required_context_is_restored_and_reported(self):
        card = template_card(self.candidate)
        card.update(reasons=['버스정류장이 3개 있습니다.'], counter_evidence=[], missing_features=[])
        card['citations'] = {'reasons:0': ['reasons:0']}
        result, calls = self.run_card(card, {'verdicts': [{'claim_id': 'reasons:0', 'supported': True}]})
        final = result['cards'][0]
        self.assertEqual(final['reasons'], ['버스정류장이 3개 있습니다.'])
        self.assertEqual(final['counter_evidence'], self.candidate['counter_evidence'])
        self.assertEqual(final['missing_features'], ['월세: 확인 안 됨'])
        self.assertEqual(final['citations'], {
            'reasons:0': ['reasons:0'], 'summary': ['reasons:0'],
        })
        self.assertEqual(final['explanation_mode'], 'mixed')
        self.assertEqual(result['explanation_mode'], 'mixed')
        self.assertTrue(result['degraded'])
        d = result['verification_by_candidate']['building']
        self.assertIsNone(d['fallback_reason'])
        self.assertTrue(d['summary_replaced'])
        self.assertEqual(d['restored_claim_count'], 2)
        self.assertEqual(d['restored_claims'], [
            {'source_id': 'counter_evidence:0', 'final_position': 'counter_evidence:0'},
            {'source_id': 'missing_features:0', 'final_position': 'missing_features:0'},
        ])
        self.assertEqual(calls, 2)

    def test_warning_paraphrases_do_not_substitute_for_complete_original_warnings(self):
        card = template_card(self.candidate)
        card['counter_evidence'] = ['공실 수치는 권역 기준이며 개별 건물의 실적이 아닙니다.']
        card['missing_features'] = ['월세는 아직 확인되지 않았습니다.']
        card['citations'] = {'counter_evidence:0': ['counter_evidence:0'], 'missing_features:0': ['missing_features:0']}
        result, _ = self.run_card(card, {'verdicts': [
            {'claim_id': 'counter_evidence:0', 'supported': True},
            {'claim_id': 'missing_features:0', 'supported': True}]})
        self.assertEqual(result['explanation_mode'], 'mixed')
        self.assertEqual(set(result['cards'][0]['counter_evidence']), set(card['counter_evidence'] + self.candidate['counter_evidence']))
        self.assertEqual(set(result['cards'][0]['missing_features']), set(card['missing_features'] + ['월세: 확인 안 됨']))
        self.assertEqual(result['verification_by_candidate']['building']['restored_claim_count'], 2)

    def test_citing_a_warning_does_not_prove_all_its_missing_conditions_survive(self):
        self.candidate['missing_features'] = [{'feature': '임대 조건', 'reason': '월세와 관리비 모두 미확인'}]
        card = template_card(self.candidate)
        card['missing_features'] = ['월세는 확인되지 않았습니다.']
        card['citations'] = {'missing_features:0': ['missing_features:0']}
        result, _ = self.run_card(card, {'verdicts': [{'claim_id': 'missing_features:0', 'supported': True}]})
        self.assertIn('임대 조건: 월세와 관리비 모두 미확인', result['cards'][0]['missing_features'])
        self.assertEqual(result['verification_by_candidate']['building']['restored_claim_count'], 1)

    def test_malformed_enum_values_fail_closed_without_unhashable_type_errors(self):
        for malformed in [{}, []]:
            card = template_card(self.candidate)
            card['claim_type'] = malformed
            result, _ = self.run_card(card)
            self.assertEqual(result['verification_by_candidate']['building']['fallback_reason'], 'draft_schema_invalid')
            card = template_card(self.candidate)
            card['inference_hypotheses'] = [{'claim': '미검증 가설', 'status': 'unverified',
                                            'claim_type': malformed, 'confidence': malformed, 'basis_refs': []}]
            result, _ = self.run_card(card)
            self.assertEqual(result['verification_by_candidate']['building']['fallback_reason'], 'draft_schema_invalid')

    def test_actual_period_paraphrases_keep_values_without_relaxing_numeric_checks(self):
        self.candidate['evidence'] = [{'metric_name': '임대가격지수', 'value': 105.3, 'period': '20262'}]
        card = template_card(self.candidate)
        for period, value, success in [('2026년 2분기', '105.3', True),
                                       ('2026년 3분기', '105.3', False),
                                       ('2026년 2분기', '109.3', False)]:
            with self.subTest(period=period, value=value):
                card['reasons'] = [f'임대가격지수는 {period} {value}입니다.']
                card['citations'] = {'reasons:0': ['candidate-evidence:0']}
                verdict = {'verdicts': [{'claim_id': 'reasons:0', 'supported': True}]} if success else None
                result, calls = self.run_card(card, verdict)
                self.assertEqual(bool(result['cards'][0]['reasons']), success)
                self.assertEqual(calls, 2 if success else 1)

    def test_iso_snapshot_month_uses_source_local_alias_without_losing_sign_checks(self):
        for source_period in ['2026-08', '2026-08-09']:
            self.candidate['evidence'] = [{'metric_name': 'bus_count', 'value': 3, 'period': source_period}]
            for month, count, success in [('8', '3', True), ('9', '3', False), ('8', '-3', False)]:
                with self.subTest(source_period=source_period, month=month, count=count):
                    card = template_card(self.candidate)
                    card['reasons'] = [f'2026년 {month}월 버스정류장 {count}개가 확인됩니다.']
                    card['citations'] = {'reasons:0': ['candidate-evidence:0']}
                    verdict = {'verdicts': [{'claim_id': 'reasons:0', 'supported': True}]} if success else None
                    result, calls = self.run_card(card, verdict)
                    self.assertEqual(bool(result['cards'][0]['reasons']), success)
                    self.assertEqual(calls, 2 if success else 1)
