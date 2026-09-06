import sys
import unittest
from os import environ
from pathlib import Path
from unittest.mock import patch

SERVICE_ROOT = Path(__file__).resolve().parents[1]
if str(SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICE_ROOT))

from recommendation.llm_explanation import explain_candidates, template_card, validate_card
from recommendation.llm_input_planner import (
    _normalize_remote_conditions,
    _valid_preferences,
    parse_conditions,
    parse_preferences,
    plan_input,
)
from recommendation.llm_runtime import (
    LLMConfig,
    LLMRuntimeError,
    _charge_call,
    reset_call_budget,
)
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
        text = "월세 300만원 이하, 20평 이상, 주차 가능"
        baseline = parse_conditions(text)
        remote = {
            # bare 숫자는 인용이 없으므로 조건이 되지 못한다 (plan B).
            "monthly_rent_max_krw": 9_999_999,
            "store_area_min_m2": 999,
            "parking_required": False,
            "target_customer": ["관광객"],
            "operating_hours": "night",
        }
        normalized = _normalize_remote_conditions(remote, baseline, text)
        self.assertIsNone(normalized["monthly_rent_max_krw"])
        self.assertIsNone(normalized["store_area_min_m2"])
        self.assertTrue(normalized["parking_required"])
        self.assertEqual(normalized["target_customer"], [])
        self.assertIsNone(normalized["operating_hours"])

    def test_numeric_conditions_need_a_verbatim_quote(self):
        text = "월세 300만원 이하, 20평 이상, 주차 가능"
        baseline = parse_conditions(text)
        # 인용이 원문에 있고 라벨·단위·방향·범위가 맞으면 LLM 숫자를 신뢰한다.
        ok = _normalize_remote_conditions(
            {"monthly_rent_max_krw": {"value": 3_000_000, "source_text": "월세 300만원 이하"},
             "store_area_min_m2": {"value": 66.12, "source_text": "20평 이상"}},
            baseline, text,
        )
        self.assertEqual(ok["monthly_rent_max_krw"], 3_000_000)
        self.assertEqual(ok["store_area_min_m2"], 66.12)
        # 문자열 값이나 원문에 없는 인용은 거부한다.
        bad = _normalize_remote_conditions(
            {"monthly_rent_max_krw": {"value": "300만 원", "source_text": "월세 300만원 이하"},
             "store_area_min_m2": {"value": 66.12, "source_text": "면적 20평 이상"}},
            baseline, text,
        )
        self.assertIsNone(bad["monthly_rent_max_krw"])
        self.assertIsNone(bad["store_area_min_m2"])

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


class LLMRuntimeConfigTests(unittest.TestCase):
    """#25: OpenAI 연결용 자격증명 인식과 요청당 호출 예산."""

    BASE = {"LLM_API_URL": "https://api.openai.com/v1", "LLM_MODEL": "gpt-5.6-luna"}

    def setUp(self):
        self.addCleanup(reset_call_budget)  # thread-local 카운터가 다른 테스트로 새지 않게

    def test_only_llm_api_key_activates_not_shell_openai_api_key(self):
        # 셸에 흔히 떠 있는 OPENAI_API_KEY 로는 활성화되지 않는다(과금 방지).
        env = {**self.BASE, "OPENAI_API_KEY": "sk-shell"}
        with patch.dict(environ, env, clear=False):
            environ.pop("LLM_API_KEY", None)
            off = LLMConfig.from_env("auto")
            self.assertIsNone(off.api_key)
            self.assertFalse(off.available)
            environ["LLM_API_KEY"] = "sk-explicit"
            on = LLMConfig.from_env("auto")
        self.assertEqual(on.api_key, "sk-explicit")
        self.assertTrue(on.available)

    def test_call_budget_trips_after_limit_then_resets(self):
        with patch.dict(environ, {"LLM_MAX_CALLS_PER_RUN": "2"}, clear=False):
            reset_call_budget()
            _charge_call()
            _charge_call()
            with self.assertRaises(LLMRuntimeError):
                _charge_call()
            reset_call_budget()
            _charge_call()  # 초기화 후 다시 허용

    def test_call_budget_zero_means_unlimited(self):
        with patch.dict(environ, {"LLM_MAX_CALLS_PER_RUN": "0"}, clear=False):
            reset_call_budget()
            for _ in range(50):
                _charge_call()

    def test_required_mode_ignores_call_budget(self):
        with patch.dict(environ, {"LLM_MAX_CALLS_PER_RUN": "1"}, clear=False):
            reset_call_budget()
            _charge_call(enforce=False)
            _charge_call(enforce=False)  # required 경로는 캡 무시 → 예외 없음

    def test_budget_exhaustion_mid_run_degrades_explain_candidates(self):
        # generate_json 의 첫 줄이 _charge_call 이라, 예산이 소진되면 LLMRuntimeError 를
        # 던진다. explain_candidates 는 그 예외를 후보별로 잡아 template 로 떨어뜨려야
        # 하며(auto 모드) 요청 전체가 깨지면 안 된다.
        cands = [{**ExplanationValidationTests.CANDIDATE, "candidate_id": f"APT-{i}"} for i in range(3)]
        good_card = {
            "candidate_id": None, "summary": "조건부 검토 후보입니다. 관측된 근거와 확인되지 않은 조건을 함께 검토해야 합니다.",
            "reasons": [], "counter_evidence": [], "context_notes": [],
            "missing_features": ["FC-10: 핵심 지표 결측"], "inference_hypotheses": [], "claim_type": "descriptive",
        }

        def _card(_prompt, payload):
            _charge_call()  # 실제 generate_json 의 첫 줄
            return {**good_card, "candidate_id": payload["candidate_evidence"]["candidate_id"]}

        env = {**self.BASE, "LLM_API_KEY": "sk-explicit", "LLM_MAX_CALLS_PER_RUN": "1"}
        with patch.dict(environ, env, clear=False), patch(
            "recommendation.llm_explanation.OpenAICompatibleJsonClient.generate_json", side_effect=_card,
        ):
            reset_call_budget()
            result = explain_candidates(cands, llm_mode="auto")  # 예외 없이 반환돼야
        self.assertIn(result["explanation_mode"], {"mixed", "template"})
        self.assertEqual(len(result["cards"]), 3)
        self.assertTrue(result["degraded"])

    def test_generate_json_charges_the_budget(self):
        # 계약 고정: 실제 generate_json 첫 동작이 _charge_call 이다(HTTP 이전).
        import inspect

        from recommendation.llm_runtime import OpenAICompatibleJsonClient
        src = inspect.getsource(OpenAICompatibleJsonClient.generate_json)
        self.assertIn("_charge_call(", src)

    def test_generate_json_retries_legacy_param_shape_on_400(self):
        # 최신 OpenAI 모델은 max_tokens/temperature=0 을 400 으로 거부한다. 최신
        # 형식으로 먼저 시도하고, 파라미터 400 이면 구형 형식으로 한 번 재시도한다.
        from recommendation.llm_runtime import LLMConfig, OpenAICompatibleJsonClient

        env = {**self.BASE, "LLM_API_KEY": "sk-explicit"}
        with patch.dict(environ, env, clear=False):
            client = OpenAICompatibleJsonClient(LLMConfig.from_env("auto"))
        sent = []

        def fake_post(body):
            sent.append(body)
            if "max_completion_tokens" in body:
                raise LLMRuntimeError("LLM HTTP 오류 400: Unsupported parameter: 'max_tokens' ... use max_completion_tokens")
            return {"ok": True}

        with patch.object(client, "_post_chat", side_effect=fake_post):
            reset_call_budget()
            out = client.generate_json("sys", {"q": 1})
        self.assertEqual(out, {"ok": True})
        self.assertEqual(len(sent), 2)
        self.assertIn("max_completion_tokens", sent[0])
        self.assertIn("max_tokens", sent[1])
        self.assertEqual(sent[1]["temperature"], 0)

    def test_generate_json_does_not_retry_non_param_400(self):
        from recommendation.llm_runtime import LLMConfig, OpenAICompatibleJsonClient

        env = {**self.BASE, "LLM_API_KEY": "sk-explicit"}
        with patch.dict(environ, env, clear=False):
            client = OpenAICompatibleJsonClient(LLMConfig.from_env("auto"))
        calls = []

        def fake_post(body):
            calls.append(body)
            raise LLMRuntimeError("LLM HTTP 오류 400: model not found")

        with patch.object(client, "_post_chat", side_effect=fake_post):
            reset_call_budget()
            with self.assertRaises(LLMRuntimeError):
                client.generate_json("sys", {"q": 1})
        self.assertEqual(len(calls), 1)  # 재시도 안 함

    def test_truncated_reasoning_response_is_a_clear_error(self):
        # 추론 모델이 max_completion_tokens 안에서 추론만 하다 잘리면 content 가 빈
        # 문자열로 온다 — "JSON 아님" 이 아니라 잘림이라고 알려야 한다.
        import json as _json

        from recommendation.llm_runtime import LLMConfig, OpenAICompatibleJsonClient

        env = {**self.BASE, "LLM_API_KEY": "sk-explicit"}
        with patch.dict(environ, env, clear=False):
            client = OpenAICompatibleJsonClient(LLMConfig.from_env("auto"))
        raw = _json.dumps({"choices": [{"finish_reason": "length", "message": {"content": ""}}]}).encode()

        class _Resp:
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def read(self, *_): return raw

        with patch("recommendation.llm_runtime.urlopen", return_value=_Resp()):
            reset_call_budget()
            with self.assertRaises(LLMRuntimeError) as ctx:
                client.generate_json("sys", {"q": 1})
        self.assertIn("잘림", str(ctx.exception))


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

    def test_verbatim_copy_of_many_context_notes_passes(self):
        # 파이프라인이 만든 context_notes 는 인구 FC-03~06·도시계획 FC-51/52 등으로
        # 12개를 넘을 수 있다. 그대로 복사한 카드는 통과해야 한다(예전 상한 12 회귀).
        notes = [f"FC-{i:02d} 배경 관측 서술 {i}." for i in range(15)]
        candidate = {**self.CANDIDATE, "context_notes": notes}
        card = {
            "candidate_id": "APT-1",
            "summary": "조건부 검토 후보입니다. 관측된 근거와 확인되지 않은 조건을 함께 검토해야 합니다.",
            "reasons": list(candidate["reasons"]),
            "counter_evidence": [],
            "context_notes": list(notes),
            "missing_features": ["FC-10: 핵심 지표 결측"],
            "inference_hypotheses": [],
            "claim_type": "descriptive",
        }
        valid, errors = validate_card(candidate, card)
        self.assertTrue(valid, errors)

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
