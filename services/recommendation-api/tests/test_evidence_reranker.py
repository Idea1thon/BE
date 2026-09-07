import unittest
from unittest.mock import Mock, patch
from recommendation.evidence_reranker import select_sources, order_card_claims, source_topic_ids
from recommendation.llm_explanation import explain_candidates, template_card, validate_card
from recommendation.llm_runtime import LLMConfig, LLMRuntimeError
from recommendation.query_context import build_query_context


class EvidenceRerankerTests(unittest.TestCase):
    def setUp(self):
        self.sources = {
            'context_notes:0': {'bucket': 'context_notes', 'text': '직장인 지역 매출 정보'},
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
        client.generate_json.return_value = {'ordered_ids': ['E02', 'E01']}
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
        self.assertEqual(meta['diagnostics']['backfilled_count'], 1)
        payload = client.generate_json.call_args.args[1]
        self.assertEqual(payload['top_k'], 2)
        self.assertEqual([row['id'] for row in payload['sources']], ['E01', 'E02'])

    def test_top_k_selection_does_not_require_all_48_ids(self):
        sources = {f'long-source-{i}': {'bucket': 'context_notes', 'text': '직장인 점심 근거'} for i in range(60)}
        client = Mock()
        client.generate_json.return_value = {'ordered_ids': [f'E{i:02d}' for i in range(48, 16, -1)]}
        selected, meta = select_sources(self.query, sources, client)
        self.assertEqual(list(selected), [f'long-source-{i}' for i in range(47, 15, -1)])
        self.assertEqual(meta['mode'], 'llm')
        self.assertEqual(meta['diagnostics']['backfilled_count'], 0)
        self.assertEqual(client.generate_json.call_args.args[1]['top_k'], 32)

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
        self.assertIn('warning', selected)
        self.assertNotIn('fact', selected)
        self.assertNotIn('retrieval-late', selected)

    def test_counter_and_missing_sources_survive_context_limit(self):
        sources = {str(i): {'bucket': 'context_notes', 'text': '직장인 점심'} for i in range(60)}
        sources.update(self.sources)
        sources['counter'] = {'bucket': 'counter_evidence', 'text': '위험 경고'}
        selected, _ = select_sources(self.query, sources)
        self.assertIn('counter', selected)
        self.assertIn('missing_features:0', selected)
        self.assertLess(len(selected), len(sources))

    def test_unrelated_evidence_and_retrieval_sources_are_excluded(self):
        sources = {str(i): {'bucket': 'context_notes', 'text': '무관한 배경'} for i in range(60)}
        sources['candidate-evidence:0'] = {'bucket': 'evidence', 'text': '{"metric": "jobs"}'}
        sources['retrieval-abc'] = {'bucket': 'context_notes', 'text': '{"dimension": "sales"}'}
        selected, _ = select_sources(self.query, sources)
        self.assertNotIn('candidate-evidence:0', selected)
        self.assertNotIn('retrieval-abc', selected)
        self.assertLess(len(selected), len(sources))

    def test_contract_filters_by_metric_instead_of_incidental_text(self):
        sources = {
            'rent': {'bucket': 'evidence', 'text': '{"metric_name":"R-ONE_임대가격지수", "value":105.3}'},
            'unrelated': {'bucket': 'evidence', 'text': '{"metric_name":"계획철도", "note":"임대료 직장인"}'},
            'retrieval-vacancy': {'bucket': 'context_notes', 'text': '{"dimension":"vacancy", "value":14.91}'},
            'warning': {'bucket': 'counter_evidence', 'text': '관련 없는 위험도 보존'},
        }
        query = {'normalized_text': '임대료가 낮은 곳', 'question_contract': {'topic_ids': ['rent']}}
        selected, meta = select_sources(query, sources)
        self.assertEqual(set(selected), {'rent', 'warning'})
        self.assertEqual(meta['excluded_count'], 2)
        self.assertEqual(len(sources), 4)

    def test_source_topic_ids_maps_allowlisted_metrics_and_plain_claims(self):
        self.assertEqual(source_topic_ids({'text': '{"metric_name":"CS100010_점포수"}'}), {'competition'})
        self.assertEqual(source_topic_ids({'text': '{"dimension":"workplace_population"}'}), {'jobs'})
        self.assertEqual(source_topic_ids({'text': '공실률과 임대지수를 확인하세요'}), {'rent', 'vacancy'})
        self.assertEqual(source_topic_ids({'text': '{"dimension":"unknown", "note":"공실률"}'}), set())

    def test_no_relevant_sources_does_not_invoke_model_or_fill_quota(self):
        client = Mock()
        selected, meta = select_sources({'normalized_text': '임대료'}, {
            'unrelated': {'bucket': 'context_notes', 'text': '철도 계획'},
            'summary': {'bucket': 'summary', 'text': '요약'},
        }, client)
        self.assertEqual(list(selected), ['summary'])
        self.assertEqual(meta['exploration_count'], 1)
        client.generate_json.assert_not_called()

    def test_topic_is_soft_and_unclassified_question_term_gets_budget_coverage(self):
        query = {'normalized_text': '직장인 수요와 지하철 접근성을 비교해줘',
                 'question_contract': {'topic_ids': ['jobs']}}
        sources = {f'jobs-{i}': {'bucket': 'evidence', 'text': '{"metric_name":"총_직장_인구_수", "interpretation":"직장인 수요"}'} for i in range(60)}
        sources['transit'] = {'bucket': 'evidence', 'text': '{"metric_name":"지하철 접근성", "value":300}'}
        selected, meta = select_sources(query, sources)
        self.assertIn('transit', selected)
        self.assertEqual(len(selected), 32)
        self.assertIn('transit', meta['coverage_ids'])

    def test_requested_topic_representative_survives_without_literal_query_word(self):
        query = {'normalized_text': '임대료와 직장인 수요 비교',
                 'question_contract': {'topic_ids': ['rent', 'jobs']}}
        sources = {f'rent-{i}': {'bucket': 'evidence', 'text': '{"metric_name":"R-ONE_임대가격지수", "interpretation":"임대료 비교"}'} for i in range(60)}
        sources['jobs'] = {'bucket': 'evidence', 'text': '{"metric_name":"총_직장_인구_수"}'}
        selected, _ = select_sources(query, sources)
        self.assertIn('jobs', selected)
        self.assertEqual(len(selected), 32)
        client = Mock()
        client.generate_json.return_value = {'ordered_ids': [f'E{i:02d}' for i in range(1, 33)]}
        selected, _ = select_sources(query, sources, client)
        self.assertIn('jobs', selected)
        self.assertTrue(any('총_직장_인구_수' in source['text'] for source in client.generate_json.call_args.args[1]['sources']))
        self.assertEqual(len(selected), 32)

    def test_model_shortlist_and_final_budget_preserve_unclassified_term(self):
        query = {'normalized_text': '직장인 수요와 지하철 접근성 비교',
                 'question_contract': {'topic_ids': ['jobs']}}
        sources = {f'jobs-{i}': {'bucket': 'context_notes', 'text': '직장인 수요'} for i in range(60)}
        sources['transit'] = {'bucket': 'evidence', 'text': '{"metric_name":"지하철 접근성"}'}
        client = Mock()
        client.generate_json.return_value = {'ordered_ids': [f'E{i:02d}' for i in range(1, 33)]}
        selected, meta = select_sources(query, sources, client)
        payload = client.generate_json.call_args.args[1]
        self.assertTrue(any('지하철' in source['text'] for source in payload['sources']))
        self.assertIn('transit', selected)
        self.assertEqual(len(selected), 32)

    def test_explicit_excluded_only_topic_does_not_return_but_warning_survives(self):
        query = {'normalized_text': '임대료 말고 직장인과 지하철',
                 'question_contract': {'topic_ids': ['jobs'], 'excluded_topics': ['rent']}}
        sources = {'rent': {'bucket': 'evidence', 'text': '{"metric_name":"R-ONE_임대가격지수"}'},
                   'warning': {'bucket': 'counter_evidence', 'text': '임대료 미확인'},
                   'transit': {'bucket': 'context_notes', 'text': '지하철 접근성'}}
        selected, _ = select_sources(query, sources, Mock())
        self.assertNotIn('rent', selected)
        self.assertIn('warning', selected)
        self.assertIn('transit', selected)

    def test_zero_lexical_exploration_requires_model_selection_and_is_bounded(self):
        query = {'normalized_text': '점심 손님', 'question_contract': {'topic_ids': []}}
        sources = {f's-{i}': {'bucket': 'evidence', 'text': '{"metric_name":"metric' + str(i) + '"}'} for i in range(20)}
        self.assertEqual(select_sources(query, sources)[0], {})
        client = Mock()
        client.generate_json.return_value = {'ordered_ids': ['E02']}
        selected, meta = select_sources(query, sources, client)
        self.assertEqual(list(selected), ['s-1'])
        self.assertEqual(meta['exploration_count'], 8)
        self.assertLessEqual(len(client.generate_json.call_args.args[1]['sources']), 8)

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
        self.assertEqual(seen[-1]['original_user_text'], self.query['original_text'])
        self.assertEqual(seen[-1]['preferences'], self.query['preferences'])
        self.assertEqual(seen[-1]['question_contract'], result['query_context']['question_contract'])
        self.assertIn('evidence', seen[-1]['candidate_evidence'])
        self.assertIn('dimension_evidence', seen[-1]['candidate_evidence'])
        self.assertNotIn('context_notes', seen[-1]['candidate_evidence'])
        self.assertIn('candidate-evidence:0', seen[-1]['explanation_sources'])
        self.assertEqual(result['cards'][0]['counter_evidence'], ['위험 경고'])
        self.assertEqual(candidate['fit_tier'], '주의')


if __name__ == '__main__':
    unittest.main()
