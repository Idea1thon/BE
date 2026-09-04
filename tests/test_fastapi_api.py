"""FastAPI boundary tests; API dependencies are required by requirements-api.txt."""
from __future__ import annotations

import asyncio
import unittest

from fastapi import HTTPException
from pydantic import ValidationError

from api.main import (
    PipelineRecommendationRequest,
    app,
    create_recommendation,
    healthz,
    industries,
    regions,
)


class FastApiBoundaryTests(unittest.TestCase):
    def test_healthz(self) -> None:
        response = asyncio.run(healthz())
        self.assertEqual(response["status"], "ok")

    def test_catalog_endpoints_support_the_wireframe_selectors(self) -> None:
        industry_response = asyncio.run(industries())
        self.assertEqual(len(industry_response), 10)
        self.assertEqual(industry_response[-1]["code"], "CS100010")

        region_response = asyncio.run(regions("송파구"))
        self.assertEqual(region_response["sido"], "서울특별시")
        self.assertIn("잠실2동", region_response["dong"])

    def test_recommendation_rejects_ambiguous_input_before_data_access(self) -> None:
        with self.assertRaises(HTTPException) as raised:
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

    def test_backend_contract_is_forwarded_to_pipeline(self) -> None:
        from unittest.mock import patch

        fake_result = {
            "summary": {"candidate_count": 0},
            "candidates": [],
            "explanations": {"explanation_mode": "template", "degraded": True, "llm": {}},
            "input_interpretation": {"resolved_industry_code": "CS100010"},
        }
        payload = PipelineRecommendationRequest(
            request_id="backend-42",
            region={"sido": "서울특별시", "sigungu": "송파구", "dong": "잠실동"},
            industry_code="CS100010",
            special_condition_text="월세 300만원 이하, 주차 가능",
        )
        with patch("api.main.run_pipeline", return_value=fake_result) as mocked:
            response = asyncio.run(create_recommendation(payload))

        forwarded = mocked.call_args.args[0]
        self.assertEqual(forwarded.sido, "서울특별시")
        self.assertEqual(forwarded.sigungu, "송파구")
        self.assertEqual(forwarded.dong, "잠실동")
        self.assertEqual(forwarded.special_condition_text, "월세 300만원 이하, 주차 가능")
        self.assertEqual(response["request_id"], "backend-42")
        self.assertEqual(response["status"], "completed")


if __name__ == "__main__":
    unittest.main()
