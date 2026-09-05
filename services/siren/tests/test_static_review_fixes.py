import unittest
from datetime import date
from fastapi.testclient import TestClient
from services.siren.api import app
from services.siren.pipeline import analyze
from services.siren.models import BranchMonthlyReport
from services.siren.reports import derive_month
from services.siren.risk_signals import _consecutive_negative_profit
from services.siren.tests.test_risk_siren import complete_payload, _month_report


class WarningTest(unittest.TestCase):
    def test_missing_market_does_not_suppress_confirmed_branch_warning(self):
        p = complete_payload(margin_start=0.05, margin_end=-0.22, loan_end=3000000)
        p["market_data"] = None
        r = analyze(p)
        self.assertIsNone(r["risk"]["score"])
        self.assertIsNone(r["risk"]["grade"])
        self.assertTrue(r["alert"]["should_fire"])
        self.assertEqual(r["alert"]["trigger"]["basis"], "branch_risk")
        self.assertTrue(r["alert"]["trigger"]["evidence_ids"])
        self.assertEqual(r["projections"]["branch_owner"]["alert"], r["alert"])

    def test_available_market_does_not_cancel_confirmed_profitability_warning(self):
        p = complete_payload(margin_start=-0.05, margin_end=-0.05)
        complete = analyze(p)
        p["market_data"] = None
        partial = analyze(p)
        self.assertTrue(complete["alert"]["should_fire"])
        self.assertTrue(partial["alert"]["should_fire"])
        self.assertEqual(complete["alert"]["trigger"], partial["alert"]["trigger"])
        self.assertEqual(complete["risk"]["grade"], "주의")
        self.assertIsNone(partial["risk"]["grade"])

    def test_missing_market_with_low_or_missing_branch_does_not_warn(self):
        for reports_missing in (False, True):
            p = complete_payload()
            p["market_data"] = None
            if reports_missing:
                p["branch_reports"] = []
            r = analyze(p)
            self.assertFalse(r["alert"]["should_fire"])

    def test_confirmed_profitability_can_warn_without_sales_comparison(self):
        p = complete_payload()
        p["market_data"] = None
        p["branch_reports"] = [_month_report(m, 0, margin=0) for m in ("2026-01", "2026-02", "2026-03")]
        r = analyze(p)
        self.assertTrue(r["alert"]["should_fire"])
        self.assertEqual(r["alert"]["trigger"]["basis"], "profitability")
        self.assertIsNone(r["risk"]["score"])


class LossStreakTest(unittest.TestCase):
    def streak(self, months):
        metrics = [derive_month(BranchMonthlyReport.model_validate(_month_report(m, 10000000, margin=-0.1))) for m in months]
        return _consecutive_negative_profit(metrics, date(2026, 3, 31))

    def test_gap_breaks_negative_streak(self):
        self.assertEqual(self.streak(["2026-03", "2026-01", "2025-12"]), 1)

    def test_missing_current_month_is_not_a_current_streak(self):
        self.assertEqual(self.streak(["2026-02", "2026-01"]), 0)

    def test_year_boundary_and_future_records(self):
        self.assertEqual(self.streak(["2026-04", "2026-03", "2026-02", "2026-01", "2025-12"]), 4)


class HqHttpValidationTest(unittest.TestCase):
    def request(self):
        return dict(request_id="hq", franchise_id="fr-001", as_of="2026-03-31",
                    branch_results=[analyze(complete_payload())])

    def post(self, req):
        with TestClient(app, raise_server_exceptions=False) as c:
            return c.post("/internal/risk-sirens/hq-summary", json=req)

    def test_null_nested_objects_are_422(self):
        for field in ("risk", "components", "review_signal", "data_provenance"):
            req = self.request()
            req["branch_results"][0][field] = None
            with self.subTest(field=field):
                self.assertEqual(self.post(req).status_code, 422)

    def test_malformed_risk_values_are_422(self):
        for patch in ({"score": True}, {"score": 101}, {"grade": []}, {"calculation_status": "partial"}):
            req = self.request()
            req["branch_results"][0]["risk"].update(patch)
            with self.subTest(patch=patch):
                self.assertEqual(self.post(req).status_code, 422)

    def test_future_stale_missing_and_invalid_snapshot_dates_are_422(self):
        for value in ("2026-04-30", "2026-02-28", None, "not-a-date"):
            req = self.request()
            req["branch_results"][0]["branch"]["as_of"] = value
            with self.subTest(value=value):
                self.assertEqual(self.post(req).status_code, 422)

    def test_valid_snapshot_is_200(self):
        self.assertEqual(self.post(self.request()).status_code, 200)
