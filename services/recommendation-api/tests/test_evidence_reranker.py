import unittest
from unittest.mock import Mock, patch
from recommendation.evidence_reranker import select_sources, order_card_claims
from recommendation.llm_explanation import explain_candidates, template_card, validate_card
from recommendation.llm_runtime import LLMConfig, LLMRuntimeError
from recommendation.query_context import build_query_context


class EvidenceRerankerTests(unittest.TestCase):
    def setUp(self):
        self.sources = {
            'context_notes:0': {'bucket': 'context_notes', 'text': '지역 매출 정보'},
            'context_notes:1': {'bucket': 'context_notes', 'text': '직장인 점심 시간대 수요 정보'},
            'missing_features:0': {'bucket': 'missing_features', 'text': '매물 면적 미확인'},
        }
        self.query = build_query_context('직장인 점심 수요를 알고 싶어요')

    def test_relevant_passage_moves_up_without_editing(self):
        selected, meta = select_sources(self.query, self.sources)
        self.assertEqual(next(iter(selected)), 'context_notes:1')
        self.assertEqual(selected['context_notes:1'], self.sources['context_notes:1'])
        self.assertEqual(meta['mode'], 'lexical')

    def test_llm_ranks_ids_only_and_invalid_list_falls_back(self):
        client = Mock()
        client.generate_json.return_value = {'ordered_ids': ['E02', 'E01', 'E03']}
        selected, meta = select_sources(self.query, self.sources, client)
        self.assertEqual(meta['mode'], 'llm')
        for response in ({'ordered_ids': ['invented']}, {'ordered_ids': ['context_notes:0'] * 3}, {}):
            client.generate_json.return_value = response
            selected, meta = select_sources(self.query, self.sources, client)
            self.assertEqual(meta['mode'], 'lexical')
            self.assertEqual(next(iter(selected)), 'context_notes:1')
        client.generate_json.side_effect = LLMRuntimeError('budget')
        self.assertEqual(select_sources(self.query, self.sources, client)[1]['mode'], 'lexical')

    def test_partial_ranking_keeps_valid_order_and_reports_repairs(self):
        client = Mock()
        # E01 is the lexically strongest passage; choose E02 first instead.
        client.generate_json.return_value = {'ordered_ids': ['E02', 'E02', 'bogus', None]}
        selected, meta = select_sources(self.query, self.sources, client)
        self.assertEqual(list(selected)[:2], ['context_notes:0', 'context_notes:1'])
        self.assertEqual(meta['mode'], 'llm_partial')
        self.assertEqual(meta['diagnostics']['accepted_count'], 1)
        self.assertEqual(meta['diagnostics']['duplicate_count'], 1)
        self.assertEqual(meta['diagnostics']['unknown_count'], 1)
        self.assertEqual(meta['diagnostics']['invalid_type_count'], 1)
        self.assertEqual(meta['diagnostics']['backfilled_count'], 2)
        payload = client.generate_json.call_args.args[1]
        self.assertEqual(payload['top_k'], 3)
        self.assertEqual([row['id'] for row in payload['sources']], ['E01', 'E02', 'E03'])

    def test_top_k_selection_does_not_require_all_48_ids(self):
        sources = {f'long-source-{i}': {'bucket': 'context_notes', 'text': '근거'} for i in range(60)}
        client = Mock()
        client.generate_json.return_value = {'ordered_ids': [f'E{i:02d}' for i in range(48, 32, -1)]}
        selected, meta = select_sources(self.query, sources, client)
        self.assertEqual(list(selected), [f'long-source-{i}' for i in range(47, 31, -1)])
        self.assertEqual(meta['mode'], 'llm')
        self.assertEqual(meta['diagnostics']['backfilled_count'], 0)
        self.assertEqual(client.generate_json.call_args.args[1]['top_k'], 16)

    def test_non_object_reply_falls_back_with_format_reason(self):
        client = Mock()
        for reply in (None, [], {'ordered_ids': None}):
            client.generate_json.return_value = reply
            _, meta = select_sources(self.query, self.sources, client)
            self.assertEqual(meta['mode'], 'lexical')
            self.assertEqual(meta['diagnostics']['failure_reason'], 'invalid_format')

    def test_partial_ranking_keeps_mandatory_facts_outside_shortlist(self):
        sources = {str(i): {'bucket': 'context_notes', 'text': '직장인 점심'} for i in range(60)}
        sources.update({'fact': {'bucket': 'evidence', 'text': '사실'},
                        'warning': {'bucket': 'counter_evidence', 'text': '경고'},
                        'retrieval-late': {'bucket': 'context_notes', 'text': '조회'}})
        client = Mock()
        client.generate_json.return_value = {'ordered_ids': ['E02']}
        selected, meta = select_sources(self.query, sources, client)
        self.assertEqual(next(iter(selected)), '1')
        self.assertEqual(meta['mode'], 'llm_partial')
        self.assertTrue({'fact', 'warning', 'retrieval-late'}.issubset(selected))

    def test_counter_and_missing_sources_survive_context_limit(self):
        sources = {str(i): {'bucket': 'context_notes', 'text': '직장인 점심'} for i in range(60)}
        sources.update(self.sources)
        sources['counter'] = {'bucket': 'counter_evidence', 'text': '위험 경고'}
        selected, _ = select_sources(self.query, sources)
        self.assertIn('counter', selected)
        self.assertIn('missing_features:0', selected)
        self.assertLess(len(selected), len(sources))

    def test_evidence_and_retrieval_sources_survive_context_limit(self):
        sources = {str(i): {'bucket': 'context_notes', 'text': '무관한 배경'} for i in range(60)}
        sources['candidate-evidence:0'] = {'bucket': 'evidence', 'text': '{"metric": "jobs"}'}
        sources['retrieval-abc'] = {'bucket': 'context_notes', 'text': '{"dimension": "sales"}'}
        selected, _ = select_sources(self.query, sources)
        self.assertIn('candidate-evidence:0', selected)
        self.assertIn('retrieval-abc', selected)
        self.assertLess(len(selected), len(sources))

    def test_reordering_preserves_citation_target(self):
        selected, _ = select_sources(self.query, self.sources)
        card = {'context_notes': ['지역 매출 정보', '직장인 점심 시간대 수요 정보'],
                'citations': {'context_notes:0': ['context_notes:0'], 'context_notes:1': ['context_notes:1']}}
        ordered = order_card_claims(card, selected)
        self.assertEqual(ordered['context_notes'][0], card['context_notes'][1])
        self.assertEqual(ordered['citations']['context_notes:0'], ['context_notes:1'])
        self.assertEqual(card['context_notes'][0], '지역 매출 정보')

    def test_exact_copy_cannot_carry_invented_citation(self):
        candidate = {'candidate_id': 'test', 'fit_tier': '주의', 'reasons': ['관측'],
                     'counter_evidence': [], 'context_notes': [], 'missing_features': []}
        card = template_card(candidate)
        for citation in ({'reasons:0': ['invented-source']}, {'reasons:99': ['reasons:0']},
                         {'reasons:0': ['summary']}):
            card['citations'] = citation
            self.assertFalse(validate_card(candidate, card)[0])

    def test_empty_query_preserves_all_sources(self):
        client = Mock()
        selected, meta = select_sources(build_query_context(''), self.sources, client)
        self.assertEqual(list(selected), list(self.sources))
        client.generate_json.assert_not_called()

    @patch('recommendation.llm_explanation.LLMConfig.from_env')
    @patch('recommendation.llm_explanation.OpenAICompatibleJsonClient')
    def test_query_and_all_built_metric_families_reach_explanation(self, client_type, config):
        config.return_value = LLMConfig(endpoint='https://example.invalid', api_key='test', model='test')
        candidate = {'candidate_id': 'test', 'fit_tier': '주의', 'reasons': [], 'counter_evidence': ['위험 경고'],
                     'context_notes': [], 'missing_features': [],
                     'evidence': [{'metric_name': '직장인 인구', 'value': 100, 'source': 'population_snapshot'}]}
        seen = []
        def respond(prompt, payload):
            seen.append(payload)
            if 'sources' in payload:
                return {'ordered_ids': [item['id'] for item in reversed(payload['sources'])]}
            return template_card(candidate)
        client_type.return_value.generate_json.side_effect = respond
        result = explain_candidates([candidate], query_context=self.query)
        self.assertEqual(seen[-1]['query_context']['original_text'], self.query['original_text'])
        self.assertIn('candidate-evidence:0', seen[-1]['explanation_sources'])
        self.assertEqual(result['cards'][0]['counter_evidence'], ['위험 경고'])
        self.assertEqual(candidate['fit_tier'], '주의')


if __name__ == '__main__':
    unittest.main()
