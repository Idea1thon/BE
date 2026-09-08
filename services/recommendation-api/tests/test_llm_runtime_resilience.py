"""Retries and bounded runtime settings are explicit and observable."""
import json
import os
import unittest
from unittest.mock import patch

from recommendation.llm_runtime import (
    LLMConfig,
    LLMRuntimeError,
    OpenAICompatibleJsonClient,
    llm_stage,
    reset_call_budget,
    summarize_telemetry,
)


class LLMRuntimeResilienceTests(unittest.TestCase):
    def test_environment_exposes_bounded_retry_and_concurrency_settings(self):
        with patch.dict(os.environ, {
            'LLM_TIMEOUT_SECONDS': '7.5',
            'LLM_RETRY_COUNT': '99',
            'LLM_RETRY_BACKOFF_SECONDS': '9',
            'LLM_MAX_CONCURRENCY': '0',
        }, clear=False):
            config = LLMConfig.from_env('auto')
        self.assertEqual(config.timeout_s, 7.5)
        self.assertEqual(config.max_retries, 4)
        self.assertEqual(config.retry_backoff_s, 5.0)
        self.assertEqual(config.max_concurrency, 1)
        self.assertEqual(config.public_metadata()['retry_count'], 4)
        self.assertEqual(config.public_metadata()['max_concurrency'], 1)

    def test_transient_network_failure_retries_with_backoff(self):
        config = LLMConfig(
            mode='auto', endpoint='https://example.invalid', api_key='test', model='test',
            max_retries=1, retry_backoff_s=0,
        )
        client = OpenAICompatibleJsonClient(config)
        body = json.dumps({
            'choices': [{'message': {'content': '{"ok": true}'}}],
        }).encode('utf-8')
        with patch('recommendation.llm_runtime._http_post',
                   side_effect=[ConnectionResetError('temporary'), (200, body)]) as http_post:
            result, meta = client._post_chat({'model': 'test'})
        self.assertEqual(result, {'ok': True})
        self.assertEqual(http_post.call_count, 2)
        self.assertEqual(meta['attempts'], 2)

    def test_http_error_status_maps_to_runtime_error_and_retry_policy(self):
        config = LLMConfig(
            mode='auto', endpoint='https://example.invalid', api_key='test', model='test',
            max_retries=1, retry_backoff_s=0,
        )
        client = OpenAICompatibleJsonClient(config)
        with patch('recommendation.llm_runtime._http_post',
                   side_effect=[(503, b'busy'), (200, b'{"choices":[{"message":{"content":"{}"}}]}')]) as http_post:
            client._post_chat({'model': 'test'})
        self.assertEqual(http_post.call_count, 2)  # 5xx retried
        with patch('recommendation.llm_runtime._http_post', return_value=(400, b'bad model')) as http_post:
            with self.assertRaises(LLMRuntimeError) as ctx:
                client._post_chat({'model': 'test'})
        self.assertIn('400', str(ctx.exception))
        self.assertEqual(http_post.call_count, 1)  # 4xx not retried

    def test_run_telemetry_records_stage_latency_and_token_usage(self):
        config = LLMConfig(
            mode='auto', endpoint='https://example.invalid', api_key='test', model='test',
            max_retries=0, retry_backoff_s=0,
        )
        client = OpenAICompatibleJsonClient(config)
        ok_body = json.dumps({
            'choices': [{'message': {'content': '{"ok": true}'}, 'finish_reason': 'stop'}],
            'usage': {'prompt_tokens': 40, 'completion_tokens': 12,
                      'completion_tokens_details': {'reasoning_tokens': 7}},
        }).encode('utf-8')
        reset_call_budget()
        with patch('recommendation.llm_runtime._http_post', return_value=(200, ok_body)):
            with llm_stage('explanation_card'):
                client.generate_json('sys', {'a': 1})
        with patch('recommendation.llm_runtime._http_post', side_effect=ConnectionError('down')):
            with llm_stage('explanation_verify'), self.assertRaises(LLMRuntimeError):
                client.generate_json('sys', {'a': 1})
        summary = summarize_telemetry()
        self.assertEqual(summary['call_count'], 2)
        self.assertEqual(summary['ok_count'], 1)
        self.assertEqual(summary['prompt_tokens_total'], 40)
        self.assertEqual(summary['reasoning_tokens_total'], 7)
        self.assertEqual(summary['by_stage']['explanation_card']['count'], 1)
        self.assertEqual(summary['by_stage']['explanation_verify']['ok'], 0)


if __name__ == '__main__':
    unittest.main()
