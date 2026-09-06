"""FastAPI boundary tests; API dependencies are required by requirements-api.txt."""
from __future__ import annotations

import asyncio
import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock, patch

SERVICE_ROOT = Path(__file__).resolve().parents[1]
if str(SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICE_ROOT))

from fastapi import HTTPException
from pydantic import ValidationError

from api.main import (
    PipelineRecommendationRequest,
    RecommendationApiResponse,
    ServiceConfig,
    _region_options,
    app,
    create_recommendation,
    get_recommendation_run,
    healthz,
    industries,
    readyz,
    regions,
    verify_internal_token,
)
from recommendation.pipeline import (
    PipelineDependencyError,
    PipelineInputError,
    PipelineInternalError,
    RecommendationRequest,
    load_layers,
    resolve_region,
)


class FastApiBoundaryTests(unittest.TestCase):
    def test_internal_auth_dependency_fails_closed_and_accepts_matching_token(self) -> None:
        with patch.dict("os.environ", {"INTERNAL_API_TOKEN": ""}):
            with self.assertRaises(HTTPException) as raised:
                verify_internal_token("anything")
        self.assertEqual(raised.exception.status_code, 503)
        self.assertEqual(raised.exception.detail["code"], "internal_auth_not_configured")

        with patch.dict("os.environ", {"INTERNAL_API_TOKEN": "test-token"}):
            with self.assertRaises(HTTPException) as raised:
                verify_internal_token("wrong-token")
            self.assertEqual(raised.exception.status_code, 401)
            self.assertIsNone(verify_internal_token("test-token"))

    def test_internal_auth_dependency_is_attached_to_server_to_server_routes(self) -> None:
        protected_paths = {
            "/api/industries",
            "/api/regions",
            "/internal/recommendations",
            "/api/recommendations",
            "/internal/recommendations/{run_id}",
        }
        routes = {route.path: route for route in app.routes if route.path in protected_paths}
        self.assertEqual(set(routes), protected_paths)
        for path, route in routes.items():
            with self.subTest(path=path):
                self.assertIn(verify_internal_token, [dependency.call for dependency in route.dependant.dependencies])

    def test_healthz(self) -> None:
        response = asyncio.run(healthz())
        self.assertEqual(response["status"], "ok")

    def test_readyz_success_is_minimal_and_uses_threadpool_for_db_probe(self) -> None:
        config = ServiceConfig(
            quarter="20261", source="db", llm_mode="offline", limit=5, readiness_timeout_s=0.1,
        )
        with patch("api.main._service_config", return_value=config), patch(
            "api.main.run_in_threadpool", new=AsyncMock(return_value="ideaton @ 127.0.0.1"),
        ) as probe:
            response = asyncio.run(readyz())
        self.assertEqual(response, {"ok": True})
        self.assertEqual(probe.await_count, 1)
        self.assertEqual(probe.await_args.args[1], 0.1)

    def test_readyz_timeout_returns_safe_failure(self) -> None:
        config = ServiceConfig(
            quarter="20261", source="db", llm_mode="offline", limit=5, readiness_timeout_s=0.01,
        )

        async def slow_probe(*args):
            await asyncio.sleep(0.05)

        with patch("api.main._service_config", return_value=config), patch(
            "api.main.run_in_threadpool", new=slow_probe,
        ):
            with self.assertRaises(HTTPException) as raised:
                asyncio.run(readyz())
        self.assertEqual(raised.exception.status_code, 503)
        self.assertNotIn("target", str(raised.exception.detail))

    def test_catalog_endpoints_support_the_wireframe_selectors(self) -> None:
        industry_response = asyncio.run(industries())
        self.assertEqual(len(industry_response), 10)
        self.assertEqual(industry_response[-1]["code"], "CS100010")

        region_response = asyncio.run(regions("송파구"))
        self.assertEqual(region_response["sido"], "서울특별시")
        self.assertIn("잠실2동", region_response["dong"])

    def test_dong_selection_confines_candidates_to_that_dong(self) -> None:
        # 서교동 요청에 대흥동·서강동 건물, 역삼1동 요청에 서초구 건물이 후보로
        # 잡히던 문제(#25 후속). 행정동을 고르면 target 범위 = 그 행정동 폴리곤.
        _, _, dong_layer, sigungu_by_prefix = load_layers()
        for sigungu, dong in (("마포구", "서교동"), ("강남구", "역삼1동")):
            selected, target_poly, buffered = resolve_region(
                RecommendationRequest("서울특별시", sigungu, dong, "CS100001"),
                dong_layer, sigungu_by_prefix,
            )
            self.assertEqual(buffered, target_poly)
            others = [dong_layer.geoms[i] for i, r in enumerate(dong_layer.records)
                      if r not in selected]
            from shapely.ops import unary_union as _uu
            spill = buffered.intersection(_uu(others)).area
            self.assertLess(spill, 1.0, f"{dong} 범위가 다른 행정동으로 {spill:.1f}㎡ 넘어감")

    def test_every_catalog_dong_is_resolvable_by_the_spatial_layer(self) -> None:
        _, _, dong_layer, sigungu_by_prefix = load_layers()
        failures = []
        for sigungu, dong in _region_options():
            try:
                resolve_region(
                    RecommendationRequest("서울특별시", sigungu, dong, "CS100010"),
                    dong_layer,
                    sigungu_by_prefix,
                )
            except Exception as exc:  # pragma: no cover - included in failure message
                failures.append(f"{sigungu}/{dong}: {exc}")
        self.assertEqual(failures, [])

    def test_recommendation_rejects_ambiguous_input_before_data_access(self) -> None:
        with TemporaryDirectory() as temp_dir, patch(
            "api.main._output_root", return_value=Path(temp_dir),
        ), self.assertRaises(HTTPException) as raised:
            asyncio.run(create_recommendation(PipelineRecommendationRequest(
                request_id="middle-backend-test-1",
                region={"sigungu": "송파구", "dong": "잠실동"},
                special_condition_text="조용하고 좋은 가게를 하고 싶어요",
            )))
        self.assertEqual(raised.exception.status_code, 422)
        self.assertEqual(raised.exception.detail["code"], "confirmation_required")
        self.assertEqual(raised.exception.detail["request_id"], "middle-backend-test-1")
        self.assertTrue(raised.exception.detail["questions"])

    def test_transport_contract_rejects_execution_settings(self) -> None:
        with self.assertRaises(ValidationError):
            PipelineRecommendationRequest(
                region={"sigungu": "송파구", "dong": "잠실동"},
                special_condition_text="커피 매장",
                source="files",
            )

    def test_transport_contract_accepts_bounded_limit_and_rejects_invalid_bounds(self) -> None:
        payload = PipelineRecommendationRequest(
            region={"sigungu": "송파구", "dong": "잠실동"},
            industry_code="CS100010",
            limit=3,
        )
        self.assertEqual(payload.limit, 3)
        for invalid_limit in (0, -1, 51):
            with self.subTest(limit=invalid_limit), self.assertRaises(ValidationError):
                PipelineRecommendationRequest(
                    region={"sigungu": "송파구", "dong": "잠실동"},
                    industry_code="CS100010",
                    limit=invalid_limit,
                )

    def test_capacity_error_includes_retry_after(self) -> None:
        payload = PipelineRecommendationRequest(
            request_id="capacity-1",
            region={"sigungu": "송파구", "dong": "잠실동"},
            industry_code="CS100010",
        )
        config = ServiceConfig(
            quarter="20261", source="files", llm_mode="offline", limit=5,
            request_timeout_s=7.2, capacity_retry_after_s=12,
        )
        with patch("api.main._service_config", return_value=config), patch(
            "api.main._try_acquire_recommendation_slot", return_value=False,
        ):
            with self.assertRaises(HTTPException) as raised:
                asyncio.run(create_recommendation(payload))
        self.assertEqual(raised.exception.status_code, 429)
        self.assertEqual(raised.exception.headers, {"Retry-After": "12"})

    def test_running_run_can_be_polled_until_completion(self) -> None:
        run_id = "20260905T000000Z-0123456789ab"
        diagnostics = {"test": {"content_status": "unverified_draft", "removed_claim_count": 1}}
        response_payload = {
            "request_id": "backend-42",
            "run_id": run_id,
            "status": "completed",
            "request": {},
            "input_interpretation": {},
            "summary": {},
            "candidates": [],
            "explanations": {"verification_by_candidate": diagnostics},
        }
        with TemporaryDirectory() as temp_dir:
            run_dir = Path(temp_dir) / run_id
            run_dir.mkdir()
            (run_dir / "run-status.json").write_text(
                json.dumps({"run_id": run_id, "status": "running", "request_id": "backend-42"}),
                encoding="utf-8",
            )
            with patch("api.main._output_root", return_value=Path(temp_dir)):
                running = asyncio.run(get_recommendation_run(run_id))
            self.assertEqual(running.status_code, 202)
            self.assertEqual(json.loads(running.body)["status"], "running")

            (run_dir / "run-status.json").write_text(
                json.dumps({"run_id": run_id, "status": "completed"}),
                encoding="utf-8",
            )
            (run_dir / "api-response.json").write_text(
                json.dumps(response_payload), encoding="utf-8",
            )
            with patch("api.main._output_root", return_value=Path(temp_dir)):
                completed = asyncio.run(get_recommendation_run(run_id))
            self.assertEqual(completed["run_id"], run_id)
            self.assertEqual(completed["request_id"], "backend-42")
            self.assertEqual(completed["explanations"]["verification_by_candidate"], diagnostics)

    def test_timeout_response_points_to_the_run_status_endpoint(self) -> None:
        payload = PipelineRecommendationRequest(
            request_id="timeout-1",
            region={"sigungu": "송파구", "dong": "잠실동"},
            industry_code="CS100010",
        )
        config = ServiceConfig(
            quarter="20261", source="files", llm_mode="offline", limit=5, request_timeout_s=5,
        )

        async def timeout(*args):
            raise asyncio.TimeoutError

        with TemporaryDirectory() as temp_dir, patch(
            "api.main._output_root", return_value=Path(temp_dir),
        ), patch("api.main._service_config", return_value=config), patch(
            "api.main._try_acquire_recommendation_slot", return_value=True,
        ), patch("api.main.run_in_threadpool", new=timeout):
            with self.assertRaises(HTTPException) as raised:
                asyncio.run(create_recommendation(payload))
        self.assertEqual(raised.exception.status_code, 504)
        run_id = raised.exception.detail["run_id"]
        self.assertEqual(raised.exception.detail["status_url"], f"/internal/recommendations/{run_id}")
        self.assertIsNone(raised.exception.headers)

    def test_response_validates_candidates_against_evidence_schema(self) -> None:
        with self.assertRaises(ValidationError):
            RecommendationApiResponse(
                request_id=None,
                run_id="run-1",
                status="completed",
                request={},
                input_interpretation={},
                summary={},
                candidates=[{"candidate_id": "incomplete"}],
                explanations={},
            )

    def test_backend_contract_is_forwarded_to_pipeline(self) -> None:
        from unittest.mock import patch

        diagnostics = {"test": {"content_status": "unverified_draft", "summary_reverted": True}}
        fake_result = {
            "summary": {"candidate_count": 0},
            "candidates": [],
            "explanations": {"explanation_mode": "template", "degraded": True, "llm": {},
                             "verification_by_candidate": diagnostics},
            "input_interpretation": {"resolved_industry_code": "CS100010"},
        }
        payload = PipelineRecommendationRequest(
            request_id="backend-42",
            region={"sido": "서울특별시", "sigungu": "송파구", "dong": "잠실동"},
            industry_code="CS100010",
            special_condition_text="월세 300만원 이하, 주차 가능",
            limit=3,
        )
        with TemporaryDirectory() as temp_dir, patch(
            "api.main._output_root", return_value=Path(temp_dir),
        ), patch("api.main.run_pipeline", return_value=fake_result) as mocked:
            response = asyncio.run(create_recommendation(payload))

        forwarded = mocked.call_args.args[0]
        self.assertEqual(forwarded.sido, "서울특별시")
        self.assertEqual(forwarded.sigungu, "송파구")
        self.assertEqual(forwarded.dong, "잠실동")
        self.assertEqual(forwarded.special_condition_text, "월세 300만원 이하, 주차 가능")
        self.assertEqual(response["request_id"], "backend-42")
        self.assertEqual(response["status"], "completed")
        self.assertEqual(mocked.call_args.args[4], 3)
        self.assertEqual(response["request"]["limit"], 3)
        self.assertEqual(response["summary"]["applied_limit"], 3)
        self.assertEqual(response["explanations"]["verification_by_candidate"], diagnostics)

    def test_pipeline_errors_are_mapped_without_leaking_server_details(self) -> None:
        payload = PipelineRecommendationRequest(
            request_id="error-map-1",
            region={"sigungu": "송파구", "dong": "잠실동"},
            industry_code="CS100010",
            special_condition_text="커피 매장",
        )
        config = ServiceConfig(quarter="20261", source="files", llm_mode="offline", limit=5)
        cases = (
            (PipelineInputError("입력 확인이 필요합니다: 업종을 선택해 주세요."), 422, "confirmation_required"),
            (PipelineDependencyError("DB target=postgresql://internal/ideaton; password=secret"), 503, "dependency_unavailable"),
            (PipelineInternalError("RAG schema at /private/tmp/secret"), 500, "internal_validation_error"),
        )
        for error, expected_status, expected_code in cases:
            with self.subTest(expected_code=expected_code), TemporaryDirectory() as temp_dir, patch(
                "api.main._output_root", return_value=Path(temp_dir),
            ), patch(
                "api.main._service_config", return_value=config,
            ), patch("api.main._try_acquire_recommendation_slot", return_value=True), patch(
                "api.main._run_pipeline_with_slot", side_effect=error,
            ):
                with self.assertRaises(HTTPException) as raised:
                    asyncio.run(create_recommendation(payload))
            self.assertEqual(raised.exception.status_code, expected_status)
            self.assertEqual(raised.exception.detail["code"], expected_code)
            self.assertNotIn("postgresql://internal", str(raised.exception.detail))
            self.assertNotIn("/private/tmp/secret", str(raised.exception.detail))
            self.assertNotIn("password=secret", str(raised.exception.detail))


if __name__ == "__main__":
    unittest.main()
