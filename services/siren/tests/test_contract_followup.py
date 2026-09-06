from copy import deepcopy
import unittest

from pydantic import ValidationError
from services.siren.pipeline import analyze
from services.siren.hq_summary import summarize
from services.siren.models import RiskSirenResponse, HqSummaryResponse
from services.siren.tests.test_risk_siren import complete_payload


def annual(**changes):
    value = dict(franchise_id="fr-001", year=2025, previous_year_end_count=100,
                 new_openings=20, closures=12, source="synthetic_test", synthetic=True)
    value.update(changes)
    return value


class FranchiseRatesTest(unittest.TestCase):
    def result(self, data):
        payload = complete_payload()
        payload["franchise_closure"] = data
        return analyze(payload)

    def test_two_denominators_and_no_double_counting(self):
        baseline = analyze(complete_payload())
        result = self.result(annual())
        rates = RiskSirenResponse.model_validate(result).model_dump()["franchise_closure"]
        self.assertEqual(rates["operating_base_rate_pct"], 10.0)
        self.assertEqual(rates["previous_year_base_rate_pct"], 12.0)
        self.assertEqual(result["risk"], baseline["risk"])
        self.assertEqual(rates["source"], "synthetic_test")
        self.assertTrue(rates["synthetic"])

    def test_annual_only_synthetic_does_not_relabel_report_data(self):
        payload = complete_payload()
        for report in payload["branch_reports"]:
            report["sales_source"] = "pos"
            report["cost_source"] = "self_reported"
        payload["franchise_closure"] = annual()
        result = analyze(payload)
        self.assertTrue(result["data_provenance"]["contains_synthetic"])
        self.assertNotIn("가맹점 매출·손익·리뷰는 대회 데모용 합성 데이터", result["data_provenance"]["disclosure"])

    def test_new_brand_has_no_previous_year_rate(self):
        rates = self.result(annual(previous_year_end_count=0, new_openings=10, closures=2))["franchise_closure"]
        self.assertEqual(rates["operating_base_rate_pct"], 20.0)
        self.assertIsNone(rates["previous_year_base_rate_pct"])

    def test_empty_base_is_not_zero_risk(self):
        rates = self.result(annual(previous_year_end_count=0, new_openings=0, closures=0))["franchise_closure"]
        self.assertIsNone(rates["operating_base_rate_pct"])
        self.assertIsNone(rates["previous_year_base_rate_pct"])

    def test_missing_input_is_explicit(self):
        result = analyze(complete_payload())
        self.assertEqual(result["franchise_closure"]["status"], "missing")
        self.assertIsNone(result["franchise_closure"]["operating_base_rate_pct"])

    def test_previous_base_ratio_can_exceed_100(self):
        rates = self.result(annual(previous_year_end_count=1, new_openings=9, closures=5))["franchise_closure"]
        self.assertEqual(rates["previous_year_base_rate_pct"], 500.0)
        self.assertEqual(rates["operating_base_rate_pct"], 50.0)

    def test_invalid_scope_period_and_counts_rejected(self):
        for changes in ({"franchise_id": "other"}, {"year": 2026},
                        {"closures": 121}, {"closures": -1}, {"closures": True},
                        {"closures": 1.5}):
            with self.subTest(changes=changes), self.assertRaises(ValidationError):
                self.result(annual(**changes))

    def test_contract_v11_derives_risk_level_and_declares_owners(self):
        result = RiskSirenResponse.model_validate(analyze(complete_payload()))
        body = result.model_dump()
        self.assertEqual(body["contract_version"], "risk-siren-contract-v1.1")
        self.assertEqual(body["risk"]["risk_level"], "NORMAL")
        self.assertEqual(body["alert"]["risk_level"], "NORMAL")
        self.assertEqual(body["alert"]["dispatch_owner"], "middle_backend")
        self.assertEqual(body["financial_products"]["owner"], "middle_backend")
        self.assertEqual(body["financial_products"]["status"], "grade_only")

    def test_annual_closure_provenance_is_separate_from_scored_signals(self):
        result = self.result(annual())
        provenance = result["data_provenance"]
        self.assertTrue(provenance["contains_synthetic"])
        self.assertEqual(
            provenance["annual_franchise_closure"],
            {"source": "synthetic_test", "synthetic": True},
        )

    def test_branch_only_partial_policy_suppresses_alert(self):
        payload = complete_payload(margin_start=0.05, margin_end=-0.22, loan_end=3_000_000)
        payload["market_data"] = None
        payload["options"]["grade_policy"] = "branch_only_provisional"
        result = analyze(payload)
        self.assertEqual(result["risk"]["calculation_status"], "partial")
        self.assertEqual(result["risk"]["grade_policy"], "branch_only_provisional")
        self.assertEqual(result["risk"]["risk_level"], "DANGER")
        self.assertFalse(result["alert"]["should_fire"])
        self.assertEqual(result["alert"]["suppressed_reason"], "grade_policy=branch_only_provisional")

    def test_hq_does_not_count_provisional_grade_as_calculated(self):
        payload = complete_payload(margin_start=0.05, margin_end=-0.22, loan_end=3_000_000)
        payload["market_data"] = None
        payload["options"]["grade_policy"] = "branch_only_provisional"
        result = analyze(payload)
        summary = summarize({
            "request_id": "hq-provisional",
            "franchise_id": "fr-001",
            "as_of": "2026-03-31",
            "branch_results": [result],
        })
        self.assertEqual(summary["calculated_count"], 0)
        self.assertIsNone(summary["danger_ratio_pct"])
        self.assertIsNone(summary["average_score"])
        self.assertEqual(summary["alert_candidate_count"], 0)


class HqBoundaryTest(unittest.TestCase):
    def request(self):
        return dict(request_id="hq-test", franchise_id="fr-001", as_of="2026-03-31",
                    branch_results=[analyze(complete_payload())])

    def test_other_franchise_rejected(self):
        req = self.request()
        req["branch_results"][0]["branch"]["franchise_id"] = "other"
        with self.assertRaises(ValueError):
            summarize(req)

    def test_missing_identity_rejected(self):
        for key in ("branch_id", "franchise_id"):
            req = self.request()
            del req["branch_results"][0]["branch"][key]
            with self.subTest(key=key), self.assertRaises(ValueError):
                summarize(req)

    def test_duplicate_branch_rejected(self):
        req = self.request()
        req["branch_results"].append(deepcopy(req["branch_results"][0]))
        with self.assertRaises(ValueError):
            summarize(req)

    def test_risk_event_does_not_claim_an_unread_notification(self):
        req = self.request()
        req["branch_results"][0]["alert"]["should_fire"] = True
        output = HqSummaryResponse.model_validate(summarize(req)).model_dump()
        self.assertIsNone(output["unread_alert_count"])
        self.assertEqual(output["alert_candidate_count"], 1)
        self.assertEqual(output["branch_count"], 1)

    def test_hq_rejects_non_strict_result_marked_calculated(self):
        req = self.request()
        req["branch_results"][0]["risk"]["grade_policy"] = "branch_only_provisional"
        req["branch_results"][0]["risk"]["calculation_status"] = "calculated"
        with self.assertRaises(ValueError):
            summarize(req)
