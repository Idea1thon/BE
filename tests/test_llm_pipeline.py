import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from llm_explanation import validate_card  # noqa: E402
from llm_input_planner import plan_input  # noqa: E402


class LLMInputPlannerTests(unittest.TestCase):
    REGION = {"sido": "서울특별시", "sigungu": "송파구", "dong": "잠실동"}

    def test_offline_fallback_resolves_industry_and_conditions(self):
        result = plan_input(
            self.REGION,
            "아침 손님이 많은 커피 매장, 월세 300만원 이하, 20평 이상, 주차 가능",
            llm_mode="offline",
        )
        self.assertEqual(result["resolved_industry_code"], "CS100010")
        self.assertEqual(result["conditions"]["monthly_rent_max_krw"], 3_000_000)
        self.assertEqual(result["conditions"]["store_area_min_m2"], 66.12)
        self.assertEqual(result["conditions"]["operating_hours"], "morning")
        self.assertFalse(result["confirmation_required"])
        self.assertEqual(len(result["analysis_plan"]), 4)

    def test_missing_industry_requires_confirmation(self):
        result = plan_input(self.REGION, "조용하고 좋은 가게를 하고 싶어요", llm_mode="offline")
        self.assertIsNone(result["resolved_industry_code"])
        self.assertTrue(result["confirmation_required"])
        self.assertTrue(result["clarification_questions"])


class ExplanationValidationTests(unittest.TestCase):
    CANDIDATE = {
        "candidate_id": "APT-1",
        "fit_tier": "조건부 검토",
        "reasons": [],
        "counter_evidence": [],
        "missing_features": [],
        "evidence": [{"metric_name": "반경500m_역수", "value": 2}],
    }

    def test_explanation_with_evidence_free_text_passes(self):
        card = {
            "candidate_id": "APT-1",
            "summary": "조건부 검토 후보입니다.",
            "reasons": ["반경 내 역 접근성이 관측됩니다."],
            "counter_evidence": [],
            "missing_features": [],
            "claim_type": "descriptive",
        }
        valid, errors = validate_card(self.CANDIDATE, card)
        self.assertTrue(valid, errors)

    def test_unseen_number_is_rejected(self):
        card = {
            "candidate_id": "APT-1",
            "summary": "반경 내 역은 99개입니다.",
            "reasons": [],
            "counter_evidence": [],
            "missing_features": [],
            "claim_type": "descriptive",
        }
        valid, errors = validate_card(self.CANDIDATE, card)
        self.assertFalse(valid)
        self.assertTrue(any("Evidence에 없는 숫자" in error for error in errors))


if __name__ == "__main__":
    unittest.main()
