from __future__ import annotations

import unittest
from datetime import date
import os
from unittest.mock import patch

from services.siren.models import RiskSirenRequest, SirenAnalyzeTrigger
from services.siren.mappers.risk_request_mapper import build_risk_request
from services.siren.orchestrator import RiskSirenOrchestrator, build_default_orchestrator
from services.siren.providers.fmp_provider import (
    BranchSnapshot,
    FmpProvider,
    ProviderUnavailable,
    _async_database_url,
)
from services.siren.providers.ideaton_provider import MarketSnapshot


class _Fmp:
    def __init__(self) -> None:
        self.report_id: str | None = None

    async def fetch_branch(
        self, branch_id: str, as_of: date, *, report_id: str | None = None
    ) -> BranchSnapshot:
        self.report_id = report_id
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
                    "items": {
                        "HALL_CARD": 1000,
                        "HALL_CASH": 0,
                        "HALL_EASYPAY": 0,
                        "DLV_BAEMIN": 0,
                        "DLV_COUPANG": 0,
                        "DLV_ETC": 0,
                        "TOGO_CARD": 0,
                        "TOGO_CASH": 0,
                        "TOGO_EASYPAY": 0,
                        "DED_REFUND": 0,
                        "DED_COUPON": 0,
                        "MAT_FOOD": 0,
                        "MAT_SUB": 0,
                        "BEV_ALCOHOL": 0,
                        "BEV_DRINK": 0,
                        "INV_BEGIN": 0,
                        "INV_END": 0,
                        "LAB_FULLTIME": 0,
                        "LAB_PARTTIME": 0,
                        "LAB_INSURANCE": 0,
                        "LAB_WELFARE": 0,
                        "LAB_SHORTTERM": 0,
                        "VAR_PLATFORM_FEE": 0,
                        "VAR_DELIVERY_FEE": 0,
                        "VAR_SUPPLIES": 0,
                        "VAR_UTILITY": 0,
                        "VAR_MARKETING": 0,
                        "OPS_RENT": 0,
                        "OPS_RENTAL": 0,
                        "OPS_TELECOM": 0,
                        "OPS_ACCOUNTING": 0,
                        "OPS_INSURANCE": 0,
                        "OPS_CARD_FEE": 0,
                        "FIN_LOAN_INTEREST": 0,
                        "FIN_MISC": 0,
                    },
                    "synthetic": True,
                }
            ],
            franchise_closure={
                "franchise_id": "10",
                "year": 2025,
                "previous_year_end_count": 100,
                "new_openings": 10,
                "closures": 5,
                "source": "fmp:public.franchise_closure_year",
                "synthetic": False,
            },
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
        self.assertEqual(orchestrator.fmp_provider.report_id, "report-1")
        self.assertEqual(len(captured), 1)
        request = captured[0]
        self.assertEqual(request.location.trade_area_code, None)
        self.assertEqual(request.branch_reports[0].sales.hall.credit, 1000)
        self.assertEqual(request.branch_reports[0].sales_source, "synthetic_self_reported")
        self.assertEqual(request.branch_reports[0].cost_source, "synthetic_self_reported")
        self.assertEqual(request.options.llm_mode, "explanation_only")
        self.assertFalse(request.options.send_notifications)
        self.assertEqual(request.options.grade_policy, "strict")
        self.assertEqual(request.franchise_closure.closures, 5)

    def test_incomplete_source_report_is_excluded_not_zero_filled(self) -> None:
        branch = BranchSnapshot(
            branch_id="20",
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
                    "synthetic": True,
                }
            ],
        )
        request = build_risk_request(
            SirenAnalyzeTrigger(
                request_id="req-incomplete",
                report_id="report-1",
                franchise_id="10",
                branch_id="20",
                as_of=date(2026, 1, 31),
            ),
            branch,
            MarketSnapshot(
                location={"gu_code": "11680"},
                market_data=None,
            ),
        )
        self.assertEqual(request["branch_reports"], [])
        self.assertEqual(request["excluded_report_months"], ["2026-01"])

    def test_report_with_required_fields_only_is_kept(self) -> None:
        # The middle-backend form requires 9 fields; the owner may omit the other
        # 26. Such a report must still feed the branch layer, with the absent
        # optional codes left at 0.
        required_only = {
            "HALL_CARD": 3_000_000,
            "HALL_CASH": 200_000,
            "DED_REFUND": 10_000,
            "MAT_FOOD": 900_000,
            "VAR_UTILITY": 120_000,
            "OPS_RENT": 1_500_000,
            "OPS_TELECOM": 60_000,
            "OPS_INSURANCE": 40_000,
            "OPS_CARD_FEE": 55_000,
        }
        branch = BranchSnapshot(
            branch_id="20",
            franchise_id="10",
            branch_name="테스트점",
            address="서울시 테스트구 테스트로 1",
            region_code="11680",
            industry_code="CS100001",
            reports=[
                {
                    "month": date(2026, 1, 1),
                    "input_source": "MANUAL",
                    "items": dict(required_only),
                    "synthetic": False,
                }
            ],
        )
        request = build_risk_request(
            SirenAnalyzeTrigger(
                request_id="req-required-only",
                report_id="report-1",
                franchise_id="10",
                branch_id="20",
                as_of=date(2026, 1, 31),
            ),
            branch,
            MarketSnapshot(location={"gu_code": "11680"}, market_data=None),
        )
        self.assertEqual(request["excluded_report_months"], [])
        self.assertEqual(len(request["branch_reports"]), 1)
        monthly = request["branch_reports"][0]
        self.assertEqual(monthly["sales"]["hall"]["credit"], 3_000_000.0)
        # An omitted optional code (e.g. delivery) is treated as 0, not missing.
        self.assertEqual(monthly["sales"]["delivery"]["baemin"], 0.0)

    def test_report_missing_a_required_field_is_excluded(self) -> None:
        branch = BranchSnapshot(
            branch_id="20",
            franchise_id="10",
            branch_name="테스트점",
            address="서울시 테스트구 테스트로 1",
            region_code="11680",
            industry_code="CS100001",
            reports=[
                {
                    "month": date(2026, 1, 1),
                    "input_source": "MANUAL",
                    # OPS_CARD_FEE (required) is absent.
                    "items": {
                        "HALL_CARD": 3_000_000,
                        "HALL_CASH": 200_000,
                        "DED_REFUND": 10_000,
                        "MAT_FOOD": 900_000,
                        "VAR_UTILITY": 120_000,
                        "OPS_RENT": 1_500_000,
                        "OPS_TELECOM": 60_000,
                        "OPS_INSURANCE": 40_000,
                    },
                    "synthetic": False,
                }
            ],
        )
        request = build_risk_request(
            SirenAnalyzeTrigger(
                request_id="req-missing-required",
                report_id="report-1",
                franchise_id="10",
                branch_id="20",
                as_of=date(2026, 1, 31),
            ),
            branch,
            MarketSnapshot(location={"gu_code": "11680"}, market_data=None),
        )
        self.assertEqual(request["branch_reports"], [])
        self.assertEqual(request["excluded_report_months"], ["2026-01"])

    async def test_synthetic_report_is_disclosed_without_reviews(self) -> None:
        orchestrator = RiskSirenOrchestrator(
            fmp_provider=_Fmp(),
            ideaton_provider=_Ideaton(),
            review_provider=_Reviews(),
        )

        result = await orchestrator.analyze_trigger(
            SirenAnalyzeTrigger(
                request_id="req-synthetic-no-reviews",
                report_id="report-1",
                franchise_id="10",
                branch_id="20",
                as_of=date(2026, 1, 31),
            )
        )

        self.assertIsNone(result["review_signal"].get("source"))
        self.assertTrue(result["data_provenance"]["contains_synthetic"])
        by_signal = {
            item["signal_id"]: item for item in result["data_provenance"]["by_signal"]
        }
        self.assertTrue(by_signal["SR-02.branch"]["synthetic"])
        self.assertTrue(by_signal["SR-05"]["synthetic"])


class FmpProviderConfigTests(unittest.TestCase):
    def test_shared_ideaton_database_is_used_when_fmp_url_is_absent(self) -> None:
        with patch.dict(
            os.environ,
            {
                "SIREN_DATABASE_URL": "postgresql://shared.example/ideaton",
                "SIREN_FMP_DATABASE_URL": "",
                "FMP_DATABASE_URL": "",
                "SIREN_IDEATON_DATABASE_URL": "",
                "IDEATON_DATABASE_URL": "",
            },
            clear=False,
        ):
            orchestrator = build_default_orchestrator()
        self.assertEqual(orchestrator.fmp_provider.database_url, "postgresql://shared.example/ideaton")
        self.assertEqual(orchestrator.ideaton_provider.database_url, "postgresql://shared.example/ideaton")
        self.assertEqual(orchestrator.review_provider.database_url, "postgresql://shared.example/ideaton")

    def test_libpq_sslmode_is_translated_for_asyncpg(self) -> None:
        value = _async_database_url(
            "postgresql://user:pass@example.test:5432/db?sslmode=require&application_name=siren"
        )
        self.assertTrue(value.startswith("postgresql+asyncpg://"))
        self.assertIn("ssl=require", value)
        self.assertIn("application_name=siren", value)
        self.assertNotIn("sslmode", value)

    def test_optional_closure_table_accepts_safe_identifier_only(self) -> None:
        provider = FmpProvider(None, franchise_closure_table="public.franchise_closure_year")
        self.assertEqual(provider.franchise_closure_table, "public.franchise_closure_year")
        with self.assertRaises(ProviderUnavailable):
            FmpProvider(None, franchise_closure_table="public.bad-name")


if __name__ == "__main__":
    unittest.main()
