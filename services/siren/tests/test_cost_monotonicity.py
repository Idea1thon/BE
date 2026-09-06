"""A worsening expense must not dilute already-confirmed risk or cancel its alert."""
import unittest
from copy import deepcopy

from fastapi.testclient import TestClient

from services.siren.api import app
from services.siren.tests.test_risk_siren import complete_payload, _month_report


def expense_payload(kind):
    payload = complete_payload()
    payload["market_data"] = None
    payload["branch_reports"] = []
    months = ["2025-10", "2025-11", "2025-12", "2026-01", "2026-02", "2026-03"]
    for i, month in enumerate(months):
        recent = i >= 3
        report = _month_report(month, 1_000_000, margin=0)
        for section in ("deductions", "cogs", "labor", "variable", "opex", "finance"):
            for key in report[section]:
                report[section][key] = 0
        for channel in report["sales"].values():
            for key in channel:
                channel[key] = 0
        report["sales"]["hall"]["credit"] = 1_000_000
        report["sales_source"] = "pos"
        report["cost_source"] = "self_reported"
        if kind == "labor":
            report["labor"]["fulltime"] = 350_000
            report["cogs"]["food_ingredients"] = 730_000 if recent else 650_000
        elif kind == "coupon":
            report["deductions"]["own_coupon_discount"] = 80_000
            report["labor"]["fulltime"] = 200_000
            report["cogs"]["food_ingredients"] = 793_600 if recent else 720_000
        else:
            report["finance"]["loan_interest"] = 110_000 if recent else 100_000
            report["labor"]["fulltime"] = 200_000
            report["cogs"]["food_ingredients"] = 770_000 if recent else 700_000
        payload["branch_reports"].append(report)
    return payload


EXPENSE_FIELDS = {
    "labor": ("labor", "fulltime"),
    "coupon": ("deductions", "own_coupon_discount"),
    "loan": ("finance", "loan_interest"),
}


class ExpenseMonotonicityTest(unittest.TestCase):
    def test_expense_thresholds_preserve_existing_warning(self):
        with TestClient(app) as client:
            for kind, (section, field) in EXPENSE_FIELDS.items():
                with self.subTest(kind=kind):
                    before = expense_payload(kind)
                    after = deepcopy(before)
                    for report in after["branch_reports"][-3:]:
                        report[section][field] += 1_000
                    results = []
                    for payload in (before, after):
                        response = client.post("/internal/risk-sirens/analyze", json=payload)
                        self.assertEqual(response.status_code, 200)
                        results.append(response.json())
                    original, worsened = results
                    a = original["components"]["profitability"]
                    b = worsened["components"]["profitability"]
                    self.assertEqual(a["status"], "calculated")
                    self.assertEqual(b["status"], "calculated")
                    self.assertLess(b["operating_margin_recent_3m_pct"], a["operating_margin_recent_3m_pct"])
                    self.assertTrue(original["alert"]["should_fire"])
                    self.assertGreaterEqual(b["score"], a["score"])
                    self.assertTrue(worsened["alert"]["should_fire"])

    def test_expense_sweep_cannot_lower_composite_score(self):
        with TestClient(app) as client:
            for kind, (section, field) in EXPENSE_FIELDS.items():
                previous_score = None
                previous_alert = False
                for delta in (-1_000, 0, 1, 100, 1_000, 10_000, 50_000, 100_000):
                    with self.subTest(kind=kind, delta=delta):
                        payload = expense_payload(kind)
                        payload["market_data"] = complete_payload()["market_data"]
                        for report in payload["branch_reports"][-3:]:
                            report[section][field] += delta
                        response = client.post("/internal/risk-sirens/analyze", json=payload)
                        self.assertEqual(response.status_code, 200)
                        result = response.json()
                        self.assertEqual(result["risk"]["calculation_status"], "calculated")
                        score = result["risk"]["score"]
                        if previous_score is not None:
                            self.assertGreaterEqual(score, previous_score)
                        if previous_alert:
                            self.assertTrue(result["alert"]["should_fire"])
                        previous_score = score
                        previous_alert = result["alert"]["should_fire"]
