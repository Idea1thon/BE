import sys
import unittest
from pathlib import Path

SERVICE_ROOT = Path(__file__).resolve().parents[1]
if str(SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICE_ROOT))

from service.recommendation.llm_explanation import validate_card
from service.recommendation.llm_input_planner import (
    _normalize_remote_conditions,
    parse_conditions,
    plan_input,
)


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

    def test_llm_cannot_invent_condition_values(self):
        baseline = parse_conditions("월세 300만원 이하, 20평 이상, 주차 가능")
        remote = {
            "monthly_rent_max_krw": 9_999_999,
            "store_area_min_m2": 999,
            "parking_required": False,
            "target_customer": ["관광객"],
            "operating_hours": "night",
        }
        normalized = _normalize_remote_conditions(remote, baseline)
        self.assertEqual(normalized["monthly_rent_max_krw"], 3_000_000)
        self.assertEqual(normalized["store_area_min_m2"], 66.12)
        self.assertTrue(normalized["parking_required"])
        self.assertEqual(normalized["target_customer"], [])
        self.assertIsNone(normalized["operating_hours"])

    def test_remote_condition_parser_rejects_partial_numeric_tokens(self):
        baseline = parse_conditions("월세 300만원 이하, 20평 이상, 주차 가능")
        remote = {
            "monthly_rent_max_krw": "300만 원",
            "store_area_min_m2": "66.12e2",
            "parking_required": "false",
        }
        normalized = _normalize_remote_conditions(remote, baseline)
        self.assertEqual(normalized["monthly_rent_max_krw"], 3_000_000)
        self.assertEqual(normalized["store_area_min_m2"], 66.12)
        self.assertTrue(normalized["parking_required"])


class ExplanationValidationTests(unittest.TestCase):
    CANDIDATE = {
        "candidate_id": "APT-1",
        "fit_tier": "조건부 검토",
        "reasons": [],
        "counter_evidence": [],
        "missing_features": [],
        "feature_build": {"features": ["FC-21"]},
        "evidence": [{"metric_name": "반경500m_역수", "value": 2}],
    }

    def test_explanation_with_evidence_free_text_passes(self):
        card = {
            "candidate_id": "APT-1",
            "summary": "조건부 검토 후보입니다.",
            "reasons": ["반경 내 역 접근성이 관측됩니다."],
            "counter_evidence": [],
            "context_notes": [],
            "missing_features": [],
            "inference_hypotheses": [],
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
            "context_notes": [],
            "missing_features": [],
            "inference_hypotheses": [],
            "claim_type": "descriptive",
        }
        valid, errors = validate_card(self.CANDIDATE, card)
        self.assertFalse(valid)
        self.assertTrue(any("Evidence에 없는 숫자" in error for error in errors))

    def test_unseen_number_is_allowed_only_as_unverified_hypothesis(self):
        card = {
            "candidate_id": "APT-1",
            "summary": "조건부 검토 후보입니다.",
            "reasons": [],
            "counter_evidence": [],
            "context_notes": [],
            "missing_features": [],
            "inference_hypotheses": [{
                "claim": "추가 매물 확인 시 월세 300만원 이하일 가능성을 별도 검토할 수 있습니다.",
                "status": "unverified",
                "claim_type": "estimate",
                "basis_refs": ["FC-21"],
                "confidence": "low",
            }],
            "claim_type": "descriptive",
        }
        valid, errors = validate_card(self.CANDIDATE, card)
        self.assertTrue(valid, errors)

    def test_hypothesis_cannot_be_presented_as_verified(self):
        card = {
            "candidate_id": "APT-1",
            "summary": "조건부 검토 후보입니다.",
            "reasons": [],
            "counter_evidence": [],
            "context_notes": [],
            "missing_features": [],
            "inference_hypotheses": [{
                "claim": "공실률은 5%입니다.",
                "status": "observed",
                "claim_type": "estimate",
                "basis_refs": ["FC-21"],
                "confidence": "high",
            }],
            "claim_type": "descriptive",
        }
        valid, errors = validate_card(self.CANDIDATE, card)
        self.assertFalse(valid)
        self.assertTrue(any("status=unverified" in error for error in errors))


if __name__ == "__main__":
    unittest.main()
