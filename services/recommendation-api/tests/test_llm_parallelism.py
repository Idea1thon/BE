"""Bounded parallel explanation generation preserves API ordering."""
import os
import threading
import time
import unittest
from unittest.mock import patch

from recommendation.llm_explanation import explain_candidates, template_card
from recommendation.llm_runtime import LLMConfig, _charge_call, reset_call_budget


def _candidate(identifier: str) -> dict:
    return {
        'candidate_id': identifier,
        'fit_tier': '조건부 검토',
        'reasons': [f'{identifier} 관측 근거'],
        'counter_evidence': [],
        'context_notes': [],
        'missing_features': [],
        'evidence': [],
    }


class LLMParallelismTests(unittest.TestCase):
    def test_candidate_cards_run_in_parallel_but_return_in_input_order(self):
        candidates = [_candidate(f'candidate-{index}') for index in range(3)]
        lock = threading.Lock()
        active = 0
        peak = 0

        def respond(_prompt, payload):
            nonlocal active, peak
            with lock:
                active += 1
                peak = max(peak, active)
            try:
                time.sleep(0.05)
                identifier = payload['candidate_evidence']['candidate_id']
                return template_card(next(item for item in candidates
                                          if item['candidate_id'] == identifier))
            finally:
                with lock:
                    active -= 1

        config = LLMConfig(
            endpoint='https://example.test', api_key='test', model='test',
            max_concurrency=3, max_retries=0,
        )
        with patch('recommendation.llm_explanation.LLMConfig.from_env', return_value=config), \
             patch('recommendation.llm_explanation.OpenAICompatibleJsonClient') as factory:
            factory.return_value.generate_json.side_effect = respond
            result = explain_candidates(candidates, query_context={'normalized_text': ''})

        self.assertGreaterEqual(peak, 2)
        self.assertEqual([card['candidate_id'] for card in result['cards']], [
            'candidate-0', 'candidate-1', 'candidate-2',
        ])
        self.assertEqual(result['llm']['calls_succeeded'], 3)
        self.assertEqual(result['llm']['max_concurrency'], 3)

    def test_parallel_workers_share_per_run_call_budget(self):
        candidates = [_candidate(f'candidate-{index}') for index in range(3)]
        config = LLMConfig(
            endpoint='https://example.test', api_key='test', model='test',
            max_concurrency=3, max_retries=0,
        )
        def respond(_prompt, payload):
            _charge_call()
            return template_card(next(item for item in candidates
                                      if item['candidate_id'] == payload['candidate_evidence']['candidate_id']))

        with patch.dict(os.environ, {'LLM_MAX_CALLS_PER_RUN': '2'}), \
             patch('recommendation.llm_explanation.LLMConfig.from_env', return_value=config), \
             patch('recommendation.llm_explanation.OpenAICompatibleJsonClient.generate_json', side_effect=respond):
            reset_call_budget()
            result = explain_candidates(candidates, query_context={'normalized_text': ''})

        self.assertEqual(result['llm']['calls_succeeded'], 2)
        self.assertEqual(len(result['llm']['validation_or_runtime_errors']), 1)


if __name__ == '__main__':
    unittest.main()
