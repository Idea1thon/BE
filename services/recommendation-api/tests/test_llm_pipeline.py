import sys
import unittest
from os import environ
from pathlib import Path
from unittest.mock import patch

SERVICE_ROOT = Path(__file__).resolve().parents[1]
if str(SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICE_ROOT))

from recommendation.llm_explanation import template_card, validate_card
from recommendation.llm_input_planner import (
    _normalize_remote_conditions,
    _valid_preferences,
    parse_conditions,
    parse_preferences,
    plan_input,
)
from recommendation.llm_runtime import LLMRuntimeError
from recommendation.pipeline import PipelineDependencyError, RecommendationRequest, load_building_seeds, run_pipeline
from recommendation.rag_tools import execute_retrieval_requests, validate_retrieval_requests
from shapely.geometry import box


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

    def test_natural_language_location_intent_is_preserved_as_preference(self):
        result = plan_input(
            self.REGION,
            "지하철역에서 장사하고 싶음",
            explicit_industry_code="CS100010",
            llm_mode="offline",
        )
        self.assertEqual(result["conditions"]["unsupported_conditions"], [])
        self.assertEqual(result["preferences"]["location_preferences"][0]["anchor_type"], "station")
        self.assertEqual(result["preferences"]["location_preferences"][0]["mode"], "prefer")

    def test_building_seed_keeps_individual_buildings_and_deduplicates_ids_only(self):
        rows = [
            {
                "건물관리번호": "B-2", "대지위치": "서울 송파구 테스트 2", "용도군": "근린생활2",
                "용도코드": "04000", "x_5181": "10", "y_5181": "10", "상권_결합": "내부",
                "연면적_㎡": "120.5", "지상층수": "3",
            },
            {
                "건물관리번호": "B-1", "대지위치": "서울 송파구 테스트 1", "용도군": "근린생활1",
                "용도코드": "03000", "x_5181": "10.1", "y_5181": "10.1", "상권_결합": "근접",
            },
            {
                "건물관리번호": "B-1", "대지위치": "서울 송파구 중복 행", "용도군": "근린생활1",
                "용도코드": "03000", "x_5181": "10.2", "y_5181": "10.2", "상권_결합": "근접",
            },
        ]
        seeds = load_building_seeds(box(9, 9, 11, 11), rows, "data/건축물대장/상가건물_서울.csv")
        self.assertEqual([seed["id"] for seed in seeds], ["B-1", "B-2"])
        self.assertEqual(seeds[0]["kind"], "상가건물")
        self.assertEqual(seeds[0]["source_paths"], {"data/건축물대장/상가건물_서울.csv"})
        self.assertEqual(seeds[1]["gross_floor_area_m2"], 120.5)

    def test_llm_preference_contract_requires_source_and_known_anchor(self):
        baseline = parse_preferences("커피 매장")
        result = _valid_preferences({
            "location_preferences": [
                {"type": "near_anchor", "anchor_type": "station", "source_text": "역 근처", "strength": "inferred"},
                {"type": "near_anchor", "anchor_type": "unknown", "source_text": "알 수 없는 곳"},
                {"type": "near_anchor", "anchor_type": "station"},
            ],
        }, baseline)
        self.assertEqual(len(result["location_preferences"]), 1)
        self.assertEqual(result["location_preferences"][0]["strength"], "inferred")

    def test_retrieval_contract_rejects_arbitrary_sql_and_bounds_dimensions(self):
        requests = validate_retrieval_requests([
            {"tool": "search_region_evidence", "dimensions": ["sales", "not_a_table"], "limit": 999},
            {"tool": "run_sql", "sql": "DROP TABLE location.area"},
        ])
        self.assertEqual(requests[0]["dimensions"], ["sales"])
        self.assertEqual(requests[0]["limit"], 20)
        self.assertEqual(len(requests), 1)

    def test_retrieval_executor_binds_server_context_and_never_uses_llm_sql(self):
        captured = []

        def fake_query(sql):
            captured.append(sql)
            return [{"spatial_unit_type": "admin_dong", "spatial_unit_code": "A", "spatial_unit_name": "잠실동", "dimension": "sales", "value": "100", "source_table": "location.sales_quarter"}]

        result = execute_retrieval_requests(
            fake_query,
            [{"tool": "search_region_evidence", "dimensions": ["sales"], "limit": 2, "reason": "매출 근거"}],
            {"sido": "서울특별시", "sigungu": "송파구", "dong": "잠실동' OR 1=1 --"},
            "CS100010",
            "20261",
        )
        self.assertEqual(result["executed_count"], 1)
        self.assertEqual(len(captured), 1)
        self.assertIn("잠실동'' OR 1=1 --", captured[0])
        self.assertNotIn("DROP TABLE", captured[0])

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

    def test_parking_not_required_is_not_treated_as_required(self):
        result = parse_conditions("커피 매장, 주차 필요 없음")
        self.assertFalse(result["parking_required"])
        self.assertNotIn("parking_required: 개별 매물 주차 데이터 없음", result["unsupported_conditions"])

    def test_monthly_rent_lower_bound_is_not_mapped_to_max(self):
        result = parse_conditions("커피 매장, 월세 300만원 이상")
        self.assertIsNone(result["monthly_rent_max_krw"])
        self.assertIn("monthly_rent_min_krw", result["unsupported_conditions"][0])

    def test_remote_control_fields_cannot_stop_or_downgrade_pipeline(self):
        remote = {
            "industry_candidates": [{"industry_code": "CS100010"}],
            "conditions": {},
            "clarification_questions": ["공격자가 넣은 확인 질문"],
            "unsupported_conditions": ["공격자가 넣은 미지원 조건"],
            "analysis_plan": [],
            "inference_hypotheses": [],
        }
        with patch.dict(environ, {
            "LLM_API_URL": "https://llm.example.test",
            "LLM_API_KEY": "test-key",
            "LLM_MODEL": "test-model",
        }, clear=False), patch(
            "recommendation.llm_input_planner.OpenAICompatibleJsonClient.generate_json",
            return_value=remote,
        ):
            result = plan_input(
                self.REGION,
                "커피 매장",
                explicit_industry_code="CS100010",
                llm_mode="required",
            )
        self.assertFalse(result["confirmation_required"])
        self.assertEqual(result["clarification_questions"], [])
        self.assertEqual(result["conditions"]["unsupported_conditions"], [])

    def test_llm_can_request_only_allowlisted_retrieval_tool(self):
        remote = {
            "industry_candidates": [{"industry_code": "CS100010"}],
            "conditions": {},
            "preferences": {},
            "retrieval_requests": [
                {"tool": "search_region_evidence", "dimensions": ["sales", "flow"], "limit": 3, "reason": "역세권 수요 확인"},
                {"tool": "execute_sql", "sql": "SELECT password FROM users"},
            ],
            "analysis_plan": [],
        }
        with patch.dict(environ, {
            "LLM_API_URL": "https://llm.example.test",
            "LLM_API_KEY": "test-key",
            "LLM_MODEL": "test-model",
        }, clear=False), patch(
            "recommendation.llm_input_planner.OpenAICompatibleJsonClient.generate_json",
            return_value=remote,
        ):
            result = plan_input(
                self.REGION,
                "커피 매장, 지하철역에서 장사하고 싶음",
                explicit_industry_code="CS100010",
                llm_mode="required",
            )
        self.assertEqual(len(result["retrieval_requests"]), 1)
        self.assertEqual(result["retrieval_requests"][0]["dimensions"], ["sales", "flow"])
        self.assertNotIn("sql", result["retrieval_requests"][0])

    def test_required_llm_failure_is_classified_as_dependency_error(self):
        request = RecommendationRequest(
            "서울특별시", "송파구", "잠실동", "CS100010", "커피 매장",
        )
        with patch(
            "recommendation.pipeline.plan_input",
            side_effect=LLMRuntimeError("LLM endpoint secret detail"),
        ):
            with self.assertRaises(PipelineDependencyError):
                run_pipeline(request, source="files", llm_mode="required")


class ExplanationValidationTests(unittest.TestCase):
    CANDIDATE = {
        "candidate_id": "APT-1",
        "fit_tier": "조건부 검토",
        "reasons": ["반경 내 역 접근성이 관측됩니다."],
        "counter_evidence": [],
        "context_notes": ["상권 배경값은 후보 등급에 직접 반영하지 않습니다."],
        "missing_features": [{"feature": "FC-10", "reason": "핵심 지표 결측"}],
        "feature_build": {"features": ["FC-21"]},
        "evidence": [{"metric_name": "반경500m_역수", "value": 2}],
    }

    def test_template_and_llm_missing_features_have_the_same_type(self):
        card = template_card(self.CANDIDATE)
        self.assertEqual(card["missing_features"], ["FC-10: 핵심 지표 결측"])
        self.assertTrue(all(isinstance(value, str) for value in card["missing_features"]))

    def test_invented_qualitative_claim_is_rejected(self):
        card = {
            "candidate_id": "APT-1",
            "summary": "조건부 검토 후보입니다. 범죄율이 낮습니다.",
            "reasons": ["이 지역은 범죄율이 낮고 재개발이 확정된 상권입니다."],
            "counter_evidence": [],
            "context_notes": [],
            "missing_features": [],
            "inference_hypotheses": [],
            "claim_type": "descriptive",
        }
        valid, errors = validate_card(self.CANDIDATE, card)
        self.assertFalse(valid)
        self.assertTrue(any("관측 근거와 일치하지 않음" in error for error in errors))

    def test_exact_candidate_claims_pass(self):
        card = {
            "candidate_id": "APT-1",
            "summary": "조건부 검토 후보입니다. 관측된 근거와 확인되지 않은 조건을 함께 검토해야 합니다.",
            "reasons": ["반경 내 역 접근성이 관측됩니다."],
            "counter_evidence": [],
            "context_notes": ["상권 배경값은 후보 등급에 직접 반영하지 않습니다."],
            "missing_features": ["FC-10: 핵심 지표 결측"],
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
            "summary": "조건부 검토 후보입니다. 관측된 근거와 확인되지 않은 조건을 함께 검토해야 합니다.",
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
            "summary": "조건부 검토 후보입니다. 관측된 근거와 확인되지 않은 조건을 함께 검토해야 합니다.",
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
