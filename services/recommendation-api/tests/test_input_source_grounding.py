"""Regressions for numeric proposals verified against the user's own words."""
import sys
import unittest
from os import environ
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from recommendation.llm_input_planner import _normalize_remote_conditions, parse_conditions, plan_input


class InputSourceGroundingTests(unittest.TestCase):
    def normalize(self, text, key, value, source=None):
        return _normalize_remote_conditions(
            {key: {"value": value, "source_text": text if source is None else source}},
            parse_conditions(text), text,
        )

    def test_korean_rent_paraphrase_is_accepted_with_warning(self):
        result = self.normalize("임대료는 매달 삼백만 원 이하", "monthly_rent_max_krw", 3000000)
        self.assertEqual(result["monthly_rent_max_krw"], 3000000)
        self.assertIn("monthly_rent_max_krw: 개별 매물 월세 데이터 없음", result["unsupported_conditions"])

    def test_korean_area_unit_and_bound_are_server_normalized(self):
        result = self.normalize("매장 면적은 이십 평 이상", "store_area_min_m2", 66.12)
        self.assertEqual(result["store_area_min_m2"], 66.12)
        self.assertIn("store_area_m2: 개별 매물 면적 데이터 없음", result["unsupported_conditions"])
        self.assertIsNone(self.normalize("매장 면적은 이십 평 이상", "store_area_max_m2", 66.12)["store_area_max_m2"])

    def test_false_amount_quote_or_unit_cannot_create_condition(self):
        for text, value, source in [
            ("임대료는 삼백만 원 이하", 4000000, None),
            ("임대료는 삼백만 원 이하", 3000000, "월세 삼백만 원 이하"),
            ("면적은 삼백만 원 이하", 3000000, None),
            ("임대료는 삼백만 원 이상", 3000000, None),
            ("임대료는 삼백만 원 이하", float("inf"), None),
            ("임대료는 삼백만 원 이하", 10 ** 400, None),
        ]:
            with self.subTest(text=text, value=value):
                self.assertIsNone(self.normalize(text, "monthly_rent_max_krw", value, source)["monthly_rent_max_krw"])

    def test_truncated_negation_or_competing_quote_is_rejected(self):
        for text in [
            "임대료는 삼백만 원 이하가 아니에요",
            "임대료는 삼백만 원 이하 아니고 다른 조건",
            "임대료는 삼백만 원 이하; 임대료는 이백만 원 이하",
        ]:
            self.assertIsNone(self.normalize(text, "monthly_rent_max_krw", 3000000,
                                           "임대료는 삼백만 원 이하")["monthly_rent_max_krw"])

    def test_existing_numeric_condition_has_precedence(self):
        text = "월세 200만원 이하; 임대료는 삼백만 원 이하"
        self.assertEqual(self.normalize(text, "monthly_rent_max_krw", 3000000)["monthly_rent_max_krw"], 2000000)

    def test_plan_keeps_grounded_warning_and_explicit_industry(self):
        text = "임대료는 매달 삼백만 원 이하"
        remote = {"conditions": {"monthly_rent_max_krw": {"value": 3000000, "source_text": text}},
                  "industry_candidates": [{"industry_code": "CS100001"}],
                  "unsupported_conditions": ["invented"]}
        with patch.dict(environ, {"LLM_API_URL": "https://example.test", "LLM_API_KEY": "test",
                                  "LLM_MODEL": "test"}), patch(
            "recommendation.llm_input_planner.OpenAICompatibleJsonClient.generate_json", return_value=remote
        ):
            result = plan_input({}, text, explicit_industry_code="CS100010", llm_mode="required")
        self.assertEqual(result["resolved_industry_code"], "CS100010")
        self.assertEqual(result["conditions"]["monthly_rent_max_krw"], 3000000)
        self.assertEqual(len(result["conditions"]["unsupported_conditions"]), 1)
        self.assertNotIn("invented", result["conditions"]["unsupported_conditions"])


if __name__ == "__main__":
    unittest.main()
