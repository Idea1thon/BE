from __future__ import annotations

import unittest
from datetime import date

from services.siren.models import RiskSirenRequest, SirenAnalyzeTrigger
from services.siren.orchestrator import RiskSirenOrchestrator
from services.siren.providers.fmp_provider import BranchSnapshot
from services.siren.providers.ideaton_provider import MarketSnapshot


class _Fmp:
    async def fetch_branch(self, branch_id: str, as_of: date) -> BranchSnapshot:
        return BranchSnapshot(
            branch_id=branch_id,
            franchise_id="10",
            branch_name="테스트점",
            address="서울시 테스트구 테스트로 1",
            region_code="11680",
            industry_code="CS100001",
            reports=[
                {
                    "month": date(2026, 1, 1),
                    "input_source": "MANUAL",
                    "items": {"HALL_CARD": 1000},
                }
            ],
        )

    async def close(self) -> None:
        return None


class _Ideaton:
    async def fetch_market(self, branch: BranchSnapshot, as_of: date) -> MarketSnapshot:
        return MarketSnapshot(
            location={
                "gu_code": "11680",
                "admin_dong_code": None,
                "trade_area_code": None,
                "x_5181": None,
                "y_5181": None,
            },
            market_data=None,
        )

    async def close(self) -> None:
        return None


class _Reviews:
    async def fetch_reviews(self, branch_id: str, as_of: date) -> None:
        return None

    async def close(self) -> None:
        return None


class OrchestratorContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_trigger_reads_then_maps_to_canonical_request(self) -> None:
        captured: list[RiskSirenRequest] = []

        def calculator(request: RiskSirenRequest) -> dict:
            captured.append(request)
            return {"request_id": request.request_id}

        orchestrator = RiskSirenOrchestrator(
            fmp_provider=_Fmp(),
            ideaton_provider=_Ideaton(),
            review_provider=_Reviews(),
            calculator=calculator,
        )
        result = await orchestrator.analyze_trigger(
            SirenAnalyzeTrigger(
                request_id="req-1",
                report_id="report-1",
                franchise_id="10",
                branch_id="20",
                as_of=date(2026, 1, 31),
            )
        )

        self.assertEqual(result, {"request_id": "req-1"})
        self.assertEqual(len(captured), 1)
        request = captured[0]
        self.assertEqual(request.location.trade_area_code, None)
        self.assertEqual(request.branch_reports[0].sales.hall.credit, 1000)
        self.assertEqual(request.options.llm_mode, "disabled")
        self.assertFalse(request.options.send_notifications)


if __name__ == "__main__":
    unittest.main()
