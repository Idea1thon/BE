"""Plan B: numeric conditions come from the LLM's number, checked only against
the user's own quote (verbatim substring, label, unit, bound direction, range).
The Korean numeral is not re-parsed by the server."""
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

    def test_llm_number_is_trusted_across_phrasings_the_parser_missed(self):
        # 종결어미(이하면/이상이면), 혼합 표기(3천만), "넘지 않게" 모두 통과한다.
        for text, key, value, expected, warning in [
            ("임대료는 매달 삼백만 원 이하면 좋겠어요", "monthly_rent_max_krw", 3000000, 3000000,
             "monthly_rent_max_krw: 개별 매물 월세 데이터 없음"),
            ("보증금 3천만원 이하이고", "deposit_max_krw", 30000000, 30000000,
             "deposit_max_krw: 개별 매물 보증금 데이터 없음"),
            ("매장 면적은 20평 이상이면 합니다", "store_area_min_m2", 66.12, 66.12,
             "store_area_m2: 개별 매물 면적 데이터 없음"),
            ("월세 300만원 넘지 않게", "monthly_rent_max_krw", 3000000, 3000000,
             "monthly_rent_max_krw: 개별 매물 월세 데이터 없음"),
        ]:
            with self.subTest(text=text):
                result = self.normalize(text, key, value)
                self.assertEqual(result[key], expected)
                self.assertIn(warning, result["unsupported_conditions"])

    def test_quote_must_be_verbatim_and_name_the_condition(self):
        for text, key, value, source in [
            ("저렴한 임대료면 좋겠어요", "monthly_rent_max_krw", 3000000, None),   # numeral/unit 없음
            ("임대료 삼백만원 이하", "monthly_rent_max_krw", 3000000, "월세 삼백만원 이하"),  # 인용이 원문에 없음
            ("면적은 삼백만 원 이하", "monthly_rent_max_krw", 3000000, None),        # 금액 라벨 없음
            ("보증금 3천만원 이하", "store_area_max_m2", 40.0, None),               # 면적 라벨/단위 없음
        ]:
            with self.subTest(text=text):
                self.assertIsNone(self.normalize(text, key, value, source)[key])

    def test_bound_direction_and_negation_and_range_are_enforced(self):
        self.assertIsNone(self.normalize("임대료 삼백만원 이상", "monthly_rent_max_krw", 3000000)["monthly_rent_max_krw"])
        self.assertIsNone(self.normalize("매장 면적 20평 이하", "store_area_min_m2", 66.12)["store_area_min_m2"])
        self.assertIsNone(self.normalize("임대료 삼백만원 이하는 아니고", "monthly_rent_max_krw", 3000000,
                                         "임대료 삼백만원 이하는 아니고")["monthly_rent_max_krw"])
        self.assertIsNone(self.normalize("임대료 100원 이하", "monthly_rent_max_krw", 100)["monthly_rent_max_krw"])
        self.assertIsNone(self.normalize("임대료 삼백만원 이하", "monthly_rent_max_krw", float("inf"))["monthly_rent_max_krw"])
        self.assertIsNone(self.normalize("임대료 삼백만원 이하", "monthly_rent_max_krw", 10 ** 400)["monthly_rent_max_krw"])

    def test_bare_scalar_without_a_quote_is_never_a_condition(self):
        result = _normalize_remote_conditions(
            {"monthly_rent_max_krw": 9_999_999, "deposit_max_krw": 5_000_000},
            parse_conditions("월세 300만원 이하"), "월세 300만원 이하",
        )
        self.assertIsNone(result["monthly_rent_max_krw"])
        self.assertIsNone(result["deposit_max_krw"])

    def test_deterministic_numeric_baseline_does_not_leak_on_the_llm_path(self):
        # parse_conditions 가 "3천만"을 3 으로 잘못 읽어도 LLM 경로엔 반영되지 않는다.
        base = parse_conditions("보증금 3천만원 이하")
        self.assertEqual(base["deposit_max_krw"], 3)  # 결정론 파서의 알려진 한계
        result = _normalize_remote_conditions({}, base, "보증금 3천만원 이하")
        self.assertIsNone(result["deposit_max_krw"])
        self.assertNotIn("deposit_max_krw: 개별 매물 보증금 데이터 없음", result["unsupported_conditions"])

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
        self.assertEqual(result["conditions"]["unsupported_conditions"],
                         ["monthly_rent_max_krw: 개별 매물 월세 데이터 없음"])
        self.assertNotIn("invented", result["conditions"]["unsupported_conditions"])


if __name__ == "__main__":
    unittest.main()
