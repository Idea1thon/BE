import unittest
from unittest.mock import patch

from recommendation.candidate_reranker import prefilter_candidates, rerank_candidates
from recommendation.llm_runtime import LLMConfig


def candidate(identifier, confidence="high"):
    return {
        "candidate_id": identifier,
        "candidate_type": "상가건물_인근",
        "fit_tier": "추천",  # preliminary legacy value; rerank must own the final tier
        "data_confidence": {"level": confidence, "reasons": []},
        "feature_build": {"coverage": {}},
        "missing_features": [],
        "counter_evidence": [],
        "greenfield": False,
        "evidence": [{"metric_name": "유동밀도", "value": 10, "unit": "명/㎡·분기", "period": "20261"}],
        "location": {"place_name": identifier, "anchor": {"type": "상가건물"}},
    }


class CandidateRerankerTests(unittest.TestCase):
    def test_prefilter_is_bounded_and_does_not_sort_by_fit_tier(self):
        candidates = [candidate(f"c-{i}") for i in range(40)]
        candidates[0]["fit_tier"] = "주의"
        selected, diagnostics = prefilter_candidates(candidates, top_k=5)
        self.assertEqual(len(selected), 15)
        self.assertEqual(diagnostics["mode"], "deterministic_prefilter")
        self.assertEqual(selected[0]["candidate_id"], "c-0")

    def test_llm_rerank_owns_order_and_final_fit_tier(self):
        candidates = [candidate("c-1"), candidate("c-2")]
        with patch("recommendation.candidate_reranker.LLMConfig.from_env", return_value=LLMConfig(
                endpoint="https://example.test", api_key="test", model="test")), \
             patch("recommendation.candidate_reranker.OpenAICompatibleJsonClient") as factory:
            factory.return_value.generate_json.return_value = {
                "ranked_candidates": [
                    {"candidate_id": "c-2", "rank": 1, "fit_tier": "주의",
                     "evidence_refs": ["c-2::evidence:0"]},
                    {"candidate_id": "c-1", "rank": 2, "fit_tier": "추천",
                     "evidence_refs": ["c-1::evidence:0"]},
                ]
            }
            selected, diagnostics = rerank_candidates(
                candidates, retrieval_evidence=[], query_context={"industry_code": "CS100010"},
                limit=1, llm_mode="auto")
        self.assertEqual([item["candidate_id"] for item in selected], ["c-2"])
        self.assertEqual(selected[0]["fit_tier"], "주의")
        self.assertEqual(diagnostics["mode"], "llm_rerank")
        self.assertEqual(diagnostics["grounding_status"], "grounded")

    def test_invalid_llm_response_keeps_bounded_candidates_in_auto_mode(self):
        candidates = [candidate("c-1"), candidate("c-2")]
        with patch("recommendation.candidate_reranker.LLMConfig.from_env", return_value=LLMConfig(
                endpoint="https://example.test", api_key="test", model="test")), \
             patch("recommendation.candidate_reranker.OpenAICompatibleJsonClient") as factory:
            factory.return_value.generate_json.return_value = {"ranked_candidates": []}
            selected, diagnostics = rerank_candidates(
                candidates, retrieval_evidence=[], query_context={}, limit=1, llm_mode="auto")
        self.assertEqual([item["candidate_id"] for item in selected], ["c-1"])
        self.assertEqual(diagnostics["reasoning_status"], "invalid_response")


if __name__ == "__main__":
    unittest.main()
