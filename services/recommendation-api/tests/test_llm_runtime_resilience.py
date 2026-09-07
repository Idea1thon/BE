"""Retries and bounded runtime settings are explicit and observable."""
import json
import os
import unittest
from unittest.mock import patch
from urllib.error import URLError

from recommendation.llm_runtime import LLMConfig, OpenAICompatibleJsonClient


class _Response:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, _limit):
        return self.payload


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
        with patch('recommendation.llm_runtime.urlopen', side_effect=[URLError('temporary'), _Response(body)]) as urlopen:
            result = client._post_chat({'model': 'test'})
        self.assertEqual(result, {'ok': True})
        self.assertEqual(urlopen.call_count, 2)


if __name__ == '__main__':
    unittest.main()
