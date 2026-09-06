import sys
import unittest
import unicodedata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from recommendation.query_context import MAX_NORMALIZED_TEXT_LENGTH, build_query_context


class QueryContextTests(unittest.TestCase):
    def test_original_and_all_explicit_constraints_are_preserved(self):
        text = "  월세 300만원 이하.\n\t역 근처는 원하지 않아요!  "
        conditions = {"monthly_rent_max_krw": 3000000,
                      "unsupported_conditions": ["개별 매물 월세 데이터 없음"]}
        region = {"sido": "서울특별시", "sigungu": "강남구", "dong": "역삼1동"}
        result = build_query_context(text, region, "CS100010", conditions)
        self.assertEqual(result["original_text"], text)
        self.assertEqual(result["normalized_text"], "월세 300만원 이하. 역 근처는 원하지 않아요!")
        self.assertEqual(result["selected_region"], region)
        self.assertEqual(result["industry_code"], "CS100010")
        self.assertEqual(result["conditions"], conditions)

    def test_unicode_composition_does_not_rewrite_units_or_symbols(self):
        text = unicodedata.normalize("NFD", "임대료") + " ≤ 300만원, 20㎡ 이상"
        result = build_query_context(text)
        self.assertEqual(result["original_text"], text)
        self.assertEqual(result["normalized_text"], "임대료 ≤ 300만원, 20㎡ 이상")

    def test_only_exact_original_quotes_enter_preferences(self):
        text = "지하철역 근처는 피하고 싶다. 주차는 필요 없음."
        valid = {"type": "near_anchor", "mode": "avoid", "source_text": "지하철역 근처는 피하고 싶다"}
        unsupported = {"type": "parking", "source_text": "주차는 필요 없음"}
        prefs = {"location_preferences": [valid,
                    {"source_text": "지하철역 근처를 원함"}, {"source_text": " "}, {}, None],
                 "unsupported_requests": [unsupported, "invented"],
                 "arbitrary_group": [{"source_text": text}]}
        result = build_query_context(text, preferences=prefs)
        self.assertEqual(result["preferences"], {
            "location_preferences": [valid], "unsupported_requests": [unsupported]})

    def test_quote_matching_happens_before_whitespace_normalization(self):
        result = build_query_context("역  근처", preferences={"location_preferences": [
            {"source_text": "역 근처"}, {"source_text": "역  근처"}]})
        self.assertEqual(result["preferences"]["location_preferences"], [{"source_text": "역  근처"}])

    def test_long_input_never_becomes_a_truncated_positive_claim(self):
        text = "역 근처 " + "가" * MAX_NORMALIZED_TEXT_LENGTH + "는 원하지 않음"
        result = build_query_context(text)
        self.assertEqual(result["original_text"], text)
        self.assertIsNone(result["normalized_text"])
        self.assertEqual(result["normalization_status"], "too_long")

    def test_context_is_detached_from_mutable_caller_contracts(self):
        region = {"dong": "역삼1동"}
        conditions = {"unsupported_conditions": ["매물 없음"]}
        preferences = {"business_preferences": [{"source_text": "배달", "labels": ["delivery"]}]}
        result = build_query_context("배달", region, conditions=conditions, preferences=preferences)
        result["selected_region"]["dong"] = "변경"
        result["conditions"]["unsupported_conditions"].clear()
        result["preferences"]["business_preferences"][0]["labels"].clear()
        self.assertEqual(region["dong"], "역삼1동")
        self.assertEqual(conditions["unsupported_conditions"], ["매물 없음"])
        self.assertEqual(preferences["business_preferences"][0]["labels"], ["delivery"])

    def test_empty_input_and_invalid_preference_shapes(self):
        result = build_query_context("", preferences={"location_preferences": "bad"})
        self.assertEqual(result["original_text"], "")
        self.assertEqual(result["normalized_text"], "")
        self.assertEqual(result["preferences"], {})
        with self.assertRaises(TypeError):
            build_query_context(None)


if __name__ == "__main__":
    unittest.main()
