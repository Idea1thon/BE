"""Pipeline integration without production DB, model calls or spatial files."""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from recommendation.pipeline import DbSource, FileSource, RecommendationRequest, run_pipeline


class ExplanationReached(Exception):
    pass


class QuestionPipelineTests(unittest.TestCase):
    def _run_until_explanation(self, hosts):
        source = MagicMock()
        source.describe.return_value = {}
        source.layers.return_value = (SimpleNamespace(records=[]), None, SimpleNamespace(records=[]), {})
        source.scope_index.return_value = ({}, Path('/tmp/observations.csv'))
        source.naver_attention.return_value = (None, {})
        source.news_catalogs.return_value = ([], {})
        source.environment.return_value = ({}, {})
        source.radius_points.return_value = ([], [], [], {})
        source.seeds.return_value = [{'id': str(i)} for i in range(len(hosts))]
        source.rent.return_value = ({}, None, None)
        source.vacancy.return_value = ({}, None, None)
        source.retrieve_requests.return_value = {'results': [], 'mode': 'db'}
        candidates = [{'candidate_id': str(i), 'candidate_type': 'building', 'fit_tier': '추천',
                       '_sort_confidence': 'high', 'counter_evidence': [],
                       'feature_build': {'coverage': {}},
                       'location': {'host_commercial_area': host}}
                      for i, host in enumerate(hosts)]
        request = RecommendationRequest('서울특별시', '마포구', None, 'CS100010', '임대료와 공실 비교')
        contract = {'topic_ids': ['rent', 'vacancy'], 'comparison_requested': True}
        with tempfile.TemporaryDirectory() as directory, \
             patch('recommendation.pipeline.make_source', return_value=source), \
             patch('recommendation.pipeline.resolve_region', return_value=([], None, None)), \
             patch('recommendation.pipeline.build_candidate', side_effect=candidates), \
             patch('recommendation.pipeline.validate_candidates', return_value=[]), \
             patch('recommendation.pipeline.build_question_contract', return_value=contract), \
             patch('recommendation.pipeline.retrieval_requests_for_contract', return_value=[]) as requests, \
             patch('recommendation.pipeline.explain_candidates', side_effect=ExplanationReached) as explain:
            with self.assertRaises(ExplanationReached):
                run_pipeline(request, out_dir=Path(directory), limit=1, llm_mode='offline')
        return source, requests, explain, contract

    def test_only_final_limited_host_is_retrieved_and_contract_is_shared(self):
        source, requests, explain, contract = self._run_until_explanation([
            {'code': '123', 'name': '선택 상권'}, {'code': '456', 'name': '제외 상권'}])
        self.assertEqual(source.retrieve_requests.call_args.kwargs['target_areas'], [
            {'spatial_unit_type': 'commercial_area', 'spatial_unit_code': '123', 'spatial_unit_name': '선택 상권'}])
        self.assertIs(explain.call_args.kwargs['query_context']['question_contract'], contract)
        self.assertIs(requests.call_args.args[0], contract)
        self.assertEqual(len(explain.call_args.args[0]), 1)

    def test_host_absence_preserves_explicit_empty_target_list(self):
        source, _, _, _ = self._run_until_explanation([None])
        self.assertEqual(source.retrieve_requests.call_args.kwargs['target_areas'], [])

    def test_sources_accept_and_forward_empty_targets(self):
        db = DbSource.__new__(DbSource)
        db._query = MagicMock(return_value=[])
        db._db = SimpleNamespace(ServingDbError=RuntimeError)
        with patch('recommendation.pipeline.execute_retrieval_requests', return_value={'results': []}) as execute:
            db.retrieve_requests([], {}, 'CS100010', '20262', target_areas=[])
        self.assertEqual(execute.call_args.kwargs['target_areas'], [])
        result = FileSource().retrieve_requests([], {}, 'CS100010', '20262', target_areas=[])
        self.assertEqual(result['executed_count'], 0)
