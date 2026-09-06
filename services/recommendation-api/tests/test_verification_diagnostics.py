"""Observable generation/grounding diagnostics without relaxing acceptance rules."""
import copy
import json
import unittest
from unittest.mock import patch

from recommendation.llm_explanation import explain_candidates, template_card
from recommendation.llm_runtime import LLMConfig, LLMRuntimeError
from recommendation.rag_tools import build_retrieval_evidence


class VerificationDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.candidate = {
            'candidate_id': 'test', 'fit_tier': '조건부 검토',
            'industry_code': 'CS100010',
            'location': {'overlapping_units': {'admin_dong': ['11710610']}},
            'reasons': ['버스정류장 3개'], 'counter_evidence': [],
            'context_notes': [], 'missing_features': [], 'evidence': [],
        }
        self.retrieval = {'results': [{'rows': [{
            'spatial_unit_type': 'admin_dong', 'spatial_unit_code': '11710610',
            'spatial_unit_name': '잠실동', 'sigungu_name': '송파구', 'period': '20261',
            'industry_code': 'CS100010', 'dimension': 'sales', 'value': '700',
            'source_table': 'location.sales_quarter',
        }]}]}
        self.ref = build_retrieval_evidence(self.retrieval)[0]['evidence_id']
        self.card = template_card(self.candidate)
        self.card['context_notes'] = ['잠실동 20261 분기 매출은 700원입니다.']
        self.card['citations'] = {'context_notes:0': [self.ref]}

    def run_card(self, card, verdict=None, *, mode='auto'):
        replies = [copy.deepcopy(card)]
        if verdict is not None:
            replies.append(verdict)
        with patch('recommendation.llm_explanation.LLMConfig.from_env', return_value=LLMConfig(
                endpoint='https://example.invalid', api_key='test', model='test')):
            with patch('recommendation.llm_explanation.OpenAICompatibleJsonClient') as client_type:
                client_type.return_value.generate_json.side_effect = replies
                result = explain_candidates([self.candidate], llm_mode=mode, retrieval_context=self.retrieval)
                return result, client_type.return_value.generate_json.call_count

    def diagnostic(self, result):
        return result['verification_by_candidate']['test']

    def claim(self, diagnostic, position):
        return next(c for c in diagnostic['claims'] if c['draft_position'] == position)

    def test_retrieval_not_generated_is_distinct_from_rejected(self):
        result, calls = self.run_card(template_card(self.candidate))
        d = self.diagnostic(result)
        self.assertEqual(d['draft_retrieval_citation_count'], 0)
        self.assertEqual(d['final_retrieval_citation_count'], 0)
        self.assertEqual(d['rewritten_claim_count'], 0)
        self.assertEqual(d['removed_claim_count'], 0)
        self.assertEqual(calls, 1)

    def test_retrieval_supported_and_rejected_are_visible_even_with_no_card_errors(self):
        for supported, disposition in [(True, 'kept'), (False, 'removed')]:
            with self.subTest(supported=supported):
                result, calls = self.run_card(self.card, {'verdicts': [
                    {'claim_id': 'context_notes:0', 'supported': supported}]})
                d = self.diagnostic(result)
                self.assertEqual(result['explanation_mode'], 'llm')
                self.assertEqual(result['llm']['validation_or_runtime_errors'], [])
                self.assertEqual(d['draft_retrieval_citation_count'], 1)
                self.assertEqual(d['final_retrieval_citation_count'], int(supported))
                c = self.claim(d, 'context_notes:0')
                self.assertEqual(c['disposition'], disposition)
                self.assertEqual(c['verification'], 'supported' if supported else 'semantic_rejected')
                self.assertEqual(c['final_position'], 'context_notes:0' if supported else None)
                self.assertEqual(calls, 2)

    def test_local_rejections_have_specific_reasons_and_do_not_call_verifier(self):
        for refs, text, reason in [
            (None, '매출은 700원입니다.', 'missing_citations'),
            ('bad', '매출은 700원입니다.', 'invalid_citations'),
            (['unknown'], '매출은 700원입니다.', 'unknown_source'),
            (['summary'], '매출은 700원입니다.', 'disallowed_source_bucket'),
            ([self.ref], '매출은 900원입니다.', 'unsupported_number'),
        ]:
            with self.subTest(reason=reason):
                card = copy.deepcopy(self.card)
                card['context_notes'] = [text]
                card['citations'] = {'context_notes:0': refs}
                result, calls = self.run_card(card)
                self.assertEqual(self.claim(self.diagnostic(result), 'context_notes:0')['verification'], reason)
                self.assertEqual(result['cards'][0]['context_notes'], [])
                self.assertEqual(calls, 1)

    def test_semantic_false_missing_and_invalid_verdict_are_distinct(self):
        for verdict, reason in [
            ({'verdicts': [{'claim_id': 'context_notes:0', 'supported': False}]}, 'semantic_rejected'),
            ({'verdicts': []}, 'missing_verdict'),
            ({'verdicts': [{'claim_id': 'context_notes:0', 'supported': 'true'}]}, 'invalid_verdict'),
            ({}, 'invalid_verdict'),
        ]:
            with self.subTest(reason=reason):
                result, _ = self.run_card(self.card, verdict)
                self.assertEqual(self.claim(self.diagnostic(result), 'context_notes:0')['verification'], reason)

    def test_summary_reversion_and_reindexing_preserve_original_diagnostic_positions(self):
        card = copy.deepcopy(self.card)
        card['summary'] = '좋은 후보입니다.'
        card['context_notes'] = ['매출은 900원입니다.', self.card['context_notes'][0]]
        card['citations'] = {'summary': ['summary'], 'context_notes:0': [self.ref], 'context_notes:1': [self.ref]}
        result, _ = self.run_card(card, {'verdicts': [
            {'claim_id': 'summary', 'supported': False},
            {'claim_id': 'context_notes:1', 'supported': True}]})
        d = self.diagnostic(result)
        self.assertTrue(d['summary_reverted'])
        self.assertEqual(self.claim(d, 'summary')['disposition'], 'summary_reverted')
        self.assertEqual(self.claim(d, 'context_notes:0')['disposition'], 'removed')
        self.assertEqual(self.claim(d, 'context_notes:1')['final_position'], 'context_notes:0')
        self.assertEqual(result['cards'][0]['citations'], {'context_notes:0': [self.ref]})

    def test_verifier_outage_reports_fallback_without_changing_required_failure(self):
        result, calls = self.run_card(self.card, LLMRuntimeError('verifier unavailable'))
        d = self.diagnostic(result)
        self.assertEqual(d['fallback_reason'], 'verification_error')
        self.assertEqual(d['final_mode'], 'template')
        self.assertEqual(self.claim(d, 'context_notes:0')['verification'], 'verifier_runtime_error')
        self.assertEqual(self.claim(d, 'context_notes:0')['disposition'], 'card_fallback')
        self.assertEqual(d['final_retrieval_citation_count'], 0)
        self.assertEqual(calls, 2)
        with self.assertRaises(LLMRuntimeError):
            self.run_card(self.card, LLMRuntimeError('verifier unavailable'), mode='required')

    def test_supported_claim_can_still_be_lost_to_whole_card_validation(self):
        card = copy.deepcopy(self.card)
        # Identity/type errors now fail before semantic verification. A banned
        # certainty phrase still demonstrates the later whole-card gate.
        card['context_notes'][0] += ' 무조건 좋습니다.'
        result, _ = self.run_card(card, {'verdicts': [{'claim_id': 'context_notes:0', 'supported': True}]})
        d = self.diagnostic(result)
        self.assertEqual(d['fallback_reason'], 'card_validation_failed')
        self.assertEqual(self.claim(d, 'context_notes:0')['verification'], 'supported')
        self.assertEqual(self.claim(d, 'context_notes:0')['disposition'], 'card_fallback')

    def test_offline_and_generation_failure_do_not_claim_verification(self):
        result = explain_candidates([self.candidate], llm_mode='offline')
        d = self.diagnostic(result)
        self.assertEqual(d['generation_status'], 'not_attempted')
        self.assertEqual(d['claims'], [])
        self.assertFalse(d['summary_reverted'])
        for reply, status, reason in [(LLMRuntimeError('unavailable'), 'runtime_error', 'generation_error'),
                                      ([], 'invalid_card', 'draft_schema_invalid')]:
            result, _ = self.run_card(reply)
            d = self.diagnostic(result)
            self.assertEqual(d['generation_status'], status)
            self.assertEqual(d['fallback_reason'], reason)

    def test_draft_diagnostics_are_marked_and_bounded_without_hiding_total_counts(self):
        card = copy.deepcopy(self.card)
        card['context_notes'] = ['미확인 내용' * 200] * 120
        card['citations'] = {}
        result, calls = self.run_card(card)
        d = self.diagnostic(result)
        self.assertEqual(d['content_status'], 'unverified_draft')
        self.assertEqual(d['removed_claim_count'], 120)
        self.assertEqual(d['verification_counts']['missing_citations'], 120)
        self.assertEqual(d['draft_claim_count'], 122)
        self.assertEqual(len(d['claims']), 100)
        self.assertEqual(d['claims_truncated_count'], 22)
        self.assertTrue(all(len(c['text_excerpt']) <= 500 for c in d['claims']))
        self.assertTrue(self.claim(d, 'context_notes:0')['text_truncated'])
        self.assertEqual(len(self.claim(d, 'context_notes:0')['text_sha256']), 64)
        self.assertEqual(calls, 1)

    def test_final_positions_follow_relevance_order_even_with_identical_claim_texts(self):
        card = copy.deepcopy(self.card)
        card['context_notes'] = [self.card['context_notes'][0]] * 2
        card['citations'] = {'context_notes:0': [self.ref], 'context_notes:1': [self.ref]}
        result, _ = self.run_card(card, {'verdicts': [
            {'claim_id': 'context_notes:0', 'supported': False},
            {'claim_id': 'context_notes:1', 'supported': True}]})
        d = self.diagnostic(result)
        self.assertIsNone(self.claim(d, 'context_notes:0')['final_position'])
        self.assertEqual(self.claim(d, 'context_notes:1')['final_position'], 'context_notes:0')
        # Existing source ordering puts reasons:0 before candidate-evidence:0.
        self.candidate['evidence'] = [{'metric_name': 'bus_count', 'value': 3}]
        card = template_card(self.candidate)
        card['reasons'] = ['버스정류장은 3개입니다.', '관측 버스정류장은 3개입니다.']
        card['citations'] = {'reasons:0': ['candidate-evidence:0'], 'reasons:1': ['reasons:0']}
        result, _ = self.run_card(card, {'verdicts': [
            {'claim_id': 'reasons:0', 'supported': True}, {'claim_id': 'reasons:1', 'supported': True}]})
        d = self.diagnostic(result)
        self.assertEqual(self.claim(d, 'reasons:0')['final_position'], 'reasons:1')
        self.assertEqual(self.claim(d, 'reasons:1')['final_position'], 'reasons:0')

    def test_verbatim_citation_stripping_is_not_reported_as_semantic_rejection(self):
        card = template_card(self.candidate)
        card['citations'] = {'reasons:0': [self.ref]}
        result, calls = self.run_card(card)
        d = self.diagnostic(result)
        self.assertEqual(d['draft_retrieval_citation_count'], 1)
        self.assertEqual(d['final_retrieval_citation_count'], 0)
        claim = self.claim(d, 'reasons:0')
        self.assertEqual(claim['verification'], 'verbatim')
        self.assertEqual(claim['disposition'], 'kept')
        self.assertEqual(claim['final_position'], 'reasons:0')
        self.assertEqual(calls, 1)

    def test_non_string_claims_and_unbounded_source_ids_do_not_break_diagnostics(self):
        card = copy.deepcopy(self.card)
        card['context_notes'] = [None, {'bad': 'value'}, '매출은 700원입니다.']
        card['citations'] = {'context_notes:2': ['retrieval-' + 'x' * 1000] * 20}
        result, _ = self.run_card(card)
        d = self.diagnostic(result)
        self.assertEqual(d['verification_counts']['invalid_claim_type'], 2)
        self.assertEqual(d['fallback_reason'], 'draft_schema_invalid')
        c = self.claim(d, 'context_notes:2')
        self.assertEqual(c['verification'], 'not_checked')
        self.assertEqual(len(c['source_ids']), 8)
        self.assertTrue(all(len(ref) <= 128 for ref in c['source_ids']))
        self.assertTrue(c['source_ids_truncated'])
        self.assertEqual(d['draft_retrieval_citation_count'], 20)

    def test_rejected_surrogate_text_and_refs_cannot_break_utf8_response_serialization(self):
        card = copy.deepcopy(self.card)
        card['context_notes'] = ['거부될 초안\ud800']
        card['citations'] = {'context_notes:0': ['retrieval-\udfff']}
        result, _ = self.run_card(card)
        self.assertEqual(result['cards'][0]['context_notes'], [])
        json.dumps(result, ensure_ascii=False).encode('utf-8')
        claim = self.claim(self.diagnostic(result), 'context_notes:0')
        self.assertEqual(claim['verification'], 'not_checked')
        self.assertEqual(self.diagnostic(result)['fallback_reason'], 'draft_schema_invalid')
        self.assertNotIn('\ud800', claim['text_excerpt'])
        self.assertNotIn('\udfff', claim['source_ids'][0])
