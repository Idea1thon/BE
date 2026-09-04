from __future__ import annotations

import unittest
from datetime import date

from fastapi import HTTPException
from pydantic import ValidationError

from services.siren.api import analyze_risk, hq_summary
from services.siren.hq_summary import summarize
from services.siren.models import HqSummaryRequest, RiskSirenRequest
from services.siren.pipeline import analyze


# --------------------------------------------------------------------------- #
# fixtures
# --------------------------------------------------------------------------- #
def _month_report(month: str, net_target: float, *, margin: float, loan: int = 0) -> dict:
    """A monthly operating report whose operating margin lands near ``margin``.

    Labor is the plug: operating_profit = gross_profit - labor - variable - opex - finance.
    """
    gross = net_target / 0.97
    delivery = gross * 0.3
    hall = (gross - delivery) * 0.62
    takeout = gross - delivery - hall
    cogs = net_target * 0.30
    variable = net_target * 0.10
    opex_fixed = net_target * 0.11
    finance_total = loan + 200_000
    gross_profit = net_target - cogs
    labor = gross_profit - variable - opex_fixed - finance_total - net_target * margin
    labor = max(net_target * 0.10, labor)
    return {
        "month": month,
        "sales": {
            "hall": {"credit": round(hall * 0.62), "cash": round(hall * 0.1), "simple_pay": round(hall * 0.28)},
            "delivery": {"baemin": round(delivery * 0.55), "coupang_eats": round(delivery * 0.35), "other": round(delivery * 0.1)},
            "takeout": {"credit": round(takeout * 0.55), "cash": round(takeout * 0.15), "simple_pay": round(takeout * 0.30)},
        },
        "deductions": {"customer_refund": round(net_target * 0.005), "own_coupon_discount": round(gross - net_target - net_target * 0.005)},
        "cogs": {
            "food_ingredients": round(cogs), "sub_materials": 0, "alcohol": 0, "beverage": 0,
            "inventory_begin": 0, "inventory_end": 0,
        },
        "labor": {
            "fulltime": round(labor * 0.5), "parttime": round(labor * 0.3),
            "four_major_insurance": round(labor * 0.12), "meal_welfare": round(labor * 0.05),
            "short_term": round(labor * 0.03),
        },
        "variable": {
            "platform_fee": round(variable * 0.4), "delivery_agency_fee": round(variable * 0.2),
            "supplies": round(variable * 0.15), "utilities": round(variable * 0.2), "marketing_ad": round(variable * 0.05),
        },
        "opex": {
            "rent_mgmt": round(opex_fixed * 0.8), "equipment_rental": round(opex_fixed * 0.05),
            "telecom_it": round(opex_fixed * 0.04), "tax_bookkeeping": round(opex_fixed * 0.04),
            "insurance": round(opex_fixed * 0.03), "card_fee": round(opex_fixed * 0.04),
        },
        "finance": {"loan_interest": loan, "other_misc": 200_000},
        "sales_source": "synthetic_pos",
        "cost_source": "synthetic_self_reported",
    }


def _closure_quarters() -> list[dict]:
    out = []
    active = 120
    for i, q in enumerate(["2024Q2", "2024Q3", "2024Q4", "2025Q1", "2025Q2", "2025Q3", "2025Q4", "2026Q1"]):
        active = active - 2 - i
        out.append({"quarter": q, "active_count_end": active, "new_openings": 2, "closures": 3 + i, "quarter_status": "완전"})
    return out


def _sales_quarters(trend: float) -> list[dict]:
    base = 4_000_000_000
    out = []
    for i, q in enumerate(["2024Q2", "2024Q3", "2024Q4", "2025Q1", "2025Q2", "2025Q3", "2025Q4", "2026Q1"]):
        out.append({"quarter": q, "amount_krw": round(base * (trend ** i)), "txn_count": 500_000})
    return out


def complete_payload(*, margin_start: float = 0.10, margin_end: float = 0.10, loan_end: int = 0) -> dict:
    months = [f"{y}-{m:02d}" for y in (2024,) for m in range(4, 13)]
    months += [f"2025-{m:02d}" for m in range(1, 13)]
    months += [f"2026-{m:02d}" for m in range(1, 4)]
    n = len(months)
    reports = []
    for i, month in enumerate(months):
        frac = i / (n - 1)
        net = 50_000_000 * (1.0 - 0.15 * frac)
        margin = margin_start + (margin_end - margin_start) * frac
        loan = round(loan_end * frac)
        reports.append(_month_report(month, net, margin=margin, loan=loan))
    return {
        "request_id": "req-test-001",
        "franchise_id": "fr-001",
        "branch_id": "br-001",
        "brand_name": "테스트 한식 강남점",
        "as_of": "2026-03-31",
        "industry_code": "CS100001",
        "location": {
            "gu_code": "11680", "admin_dong_code": "11680640",
            "trade_area_code": "3120189", "x_5181": 202454.0, "y_5181": 444235.0,
        },
        "market_data": {
            "source": "seoul_open_data:test",
            "closure_quarters": _closure_quarters(),
            "sales_quarters": _sales_quarters(0.98),
            "competition": {
                "radius_m": 250,
                "same_industry_new_recent_3m": 2, "same_industry_new_previous_3m": 1,
                "similar_industry_new_recent_3m": 3, "similar_industry_new_previous_3m": 2,
                "similar_industry_codes": ["CS100008"], "active_only": True,
                "source": "seoul_open_data:음식점_인허가_서울",
            },
        },
        "branch_reports": reports,
        "reviews": None,
        "options": {"llm_mode": "explanation_only", "send_notifications": False},
    }


# --------------------------------------------------------------------------- #
# tests
# --------------------------------------------------------------------------- #
class RiskSirenV1Tests(unittest.TestCase):
    def test_healthy_branch_is_normal_and_deterministic(self) -> None:
        req = RiskSirenRequest.model_validate(complete_payload(margin_start=0.11, margin_end=0.10))
        first = analyze(req)
        second = analyze(req)
        self.assertEqual(first, second)
        self.assertEqual(first["risk"]["calculation_status"], "calculated")
        self.assertEqual(first["risk"]["grade"], "정상")
        self.assertEqual(first["risk"]["score_version"], "risk-siren-v1-provisional")
        self.assertFalse(first["alert"]["should_fire"])
        self.assertEqual(first["alert"]["dispatch_status"], "disabled")

    def test_two_annual_closure_formulas_present(self) -> None:
        result = analyze(complete_payload())
        closure = result["components"]["closure"]
        self.assertIn("quarter_rate", closure)
        self.assertIn("rolling_2q_rate", closure)
        self.assertIn("rolling_4q_rate", closure)
        self.assertEqual(closure["status"], "calculated")

    def test_profit_collapse_raises_grade_even_if_market_calm(self) -> None:
        payload = complete_payload(margin_start=0.05, margin_end=-0.20, loan_end=3_000_000)
        payload["market_data"]["sales_quarters"] = _sales_quarters(1.02)  # 시장은 성장
        payload["market_data"]["closure_quarters"] = [
            {**q, "closures": 1} for q in _closure_quarters()
        ]
        result = analyze(payload)
        self.assertEqual(result["risk"]["grade"], "위험")
        self.assertTrue(result["risk"]["branch_floor_applied"])
        self.assertGreaterEqual(result["components"]["profitability"]["consecutive_negative_months"], 6)

    def test_missing_recent_month_is_partial_not_safe(self) -> None:
        payload = complete_payload()
        payload["branch_reports"] = [r for r in payload["branch_reports"] if r["month"] != "2026-02"]
        result = analyze(payload)
        self.assertEqual(result["risk"]["calculation_status"], "partial")
        self.assertIsNone(result["risk"]["score"])
        self.assertIsNone(result["risk"]["grade"])
        self.assertFalse(result["alert"]["should_fire"])

    def test_missing_market_data_is_partial(self) -> None:
        payload = complete_payload()
        payload["market_data"] = None
        result = analyze(payload)
        self.assertEqual(result["risk"]["calculation_status"], "partial")
        self.assertEqual(result["layers"]["market_risk"]["status"], "missing")
        self.assertTrue(any(m["signal_id"] == "SR-01" for m in result["missing_data"]))

    def test_zero_denominator_closure_is_not_calculable(self) -> None:
        payload = complete_payload()
        payload["market_data"]["closure_quarters"] = [
            {"quarter": q, "active_count_end": 0, "new_openings": 0, "closures": 0, "quarter_status": "완전"}
            for q in ["2025Q3", "2025Q4", "2026Q1"]
        ]
        result = analyze(payload)
        self.assertEqual(result["components"]["closure"]["status"], "not_calculable")
        self.assertEqual(result["risk"]["calculation_status"], "partial")

    def test_future_dated_report_is_excluded(self) -> None:
        payload = complete_payload()
        payload["branch_reports"].append(_month_report("2026-05", 40_000_000, margin=-0.3))
        result = analyze(payload)
        self.assertTrue(any("2026-05" in u for u in result["uncertainty"]))

    def test_review_signal_is_auxiliary_only(self) -> None:
        payload = complete_payload(margin_start=0.11, margin_end=0.10)
        payload["reviews"] = {
            "source": "synthetic_reviews",
            "records": [
                {"review_id": f"rv-{i}", "written_at": "2026-02-15", "rating": 1,
                 "text": "형편없어요", "sentiment_label": "부정"}
                for i in range(20)
            ],
        }
        result = analyze(payload)
        self.assertEqual(result["review_signal"]["status"], "calculated")
        self.assertGreater(result["review_signal"]["sub_score"], 60)
        self.assertNotIn("SR-04", result["risk"]["composite_basis"])
        self.assertIn("SR-04", result["risk"]["excludes"])
        # 종합 점수는 리뷰가 있든 없든 동일해야 한다
        payload_no_rev = complete_payload(margin_start=0.11, margin_end=0.10)
        self.assertEqual(result["risk"]["score"], analyze(payload_no_rev)["risk"]["score"])

    def test_missing_reviews_do_not_affect_score(self) -> None:
        result = analyze(complete_payload())
        self.assertEqual(result["review_signal"]["status"], "missing")
        self.assertTrue(any(m["signal_id"] == "SR-04" for m in result["missing_data"]))

    def test_data_provenance_marks_synthetic(self) -> None:
        result = analyze(complete_payload())
        prov = result["data_provenance"]
        self.assertTrue(prov["contains_synthetic"])
        by_signal = {s["signal_id"]: s for s in prov["by_signal"]}
        self.assertFalse(by_signal["SR-01"]["synthetic"])
        self.assertTrue(by_signal["SR-02.branch"]["synthetic"])
        self.assertTrue(by_signal["SR-05"]["synthetic"])

    def test_projection_hq_has_no_other_branch_links(self) -> None:
        result = analyze(complete_payload())
        hq = result["projections"]["franchise_hq"]
        self.assertNotIn("report_link", hq)
        self.assertNotIn("components", hq)
        self.assertEqual(hq["branch_id"], "br-001")
        self.assertEqual(result["projections"]["branch_owner"]["score"], result["risk"]["score"])

    def test_prediction_fields_are_rejected(self) -> None:
        payload = complete_payload()
        payload["success_probability"] = 0.9
        with self.assertRaises(ValidationError):
            RiskSirenRequest.model_validate(payload)

    def test_negative_amount_rejected(self) -> None:
        payload = complete_payload()
        payload["branch_reports"][0]["labor"]["fulltime"] = -1
        with self.assertRaises(ValidationError):
            RiskSirenRequest.model_validate(payload)

    def test_bad_industry_code_rejected(self) -> None:
        payload = complete_payload()
        payload["industry_code"] = "CS999999"
        with self.assertRaises(ValidationError):
            RiskSirenRequest.model_validate(payload)

    def test_api_blocks_real_dispatch(self) -> None:
        response = analyze_risk(RiskSirenRequest.model_validate(complete_payload()))
        body = response.model_dump(mode="json")
        self.assertEqual(body["request_id"], "req-test-001")
        self.assertIn("data_provenance", body)

        payload = complete_payload()
        payload["options"]["send_notifications"] = True
        with self.assertRaises(HTTPException) as ctx:
            analyze_risk(RiskSirenRequest.model_validate(payload))
        self.assertEqual(ctx.exception.status_code, 422)

    def test_hq_summary_keeps_ratio_and_average_separate(self) -> None:
        r_danger = analyze(complete_payload(margin_start=0.05, margin_end=-0.22, loan_end=3_000_000))
        r_ok = analyze(complete_payload(margin_start=0.11, margin_end=0.10))
        summary = summarize({
            "request_id": "hq-1", "franchise_id": "fr-001", "as_of": "2026-03-31",
            "branch_results": [r_danger, r_ok, r_ok],
        })
        self.assertEqual(summary["branch_count"], 3)
        self.assertIn("danger_ratio_pct", summary)
        self.assertIn("average_score", summary)
        self.assertNotEqual(summary["danger_ratio_pct"], summary["average_score"])

    def test_zero_net_sales_is_high_risk_not_missing(self) -> None:
        """RS04-D01: 최근 3개월 순매출 합 0 이하 → SR-05 최고위험, branch 층 계산됨."""
        payload = complete_payload()
        for r in payload["branch_reports"]:
            if r["month"] >= "2026-01":
                for grp in ("hall", "takeout"):
                    for k in r["sales"][grp]:
                        r["sales"][grp][k] = 0
                for k in r["sales"]["delivery"]:
                    r["sales"]["delivery"][k] = 0
        result = analyze(payload)
        prof = result["components"]["profitability"]
        self.assertTrue(prof.get("net_sales_nonpositive"))
        self.assertEqual(prof["status"], "calculated")
        self.assertGreaterEqual(prof["score"], 90)
        self.assertEqual(result["layers"]["branch_risk"]["status"], "calculated")
        self.assertEqual(result["risk"]["grade"], "위험")
        self.assertTrue(result["alert"]["should_fire"])

    def test_disclosure_not_false_when_market_is_synthetic(self) -> None:
        """RS04-D02: market source가 합성이면 disclosure가 '모두 실측'이라 말하지 않는다."""
        payload = complete_payload()
        payload["market_data"]["source"] = "synthetic_franchise_cohort"
        payload["market_data"]["closure_source"] = "synthetic_franchise_cohort"
        for r in payload["branch_reports"]:
            r["sales_source"] = "pos"
            r["cost_source"] = "self_reported"
        payload["reviews"] = None
        result = analyze(payload)
        self.assertTrue(result["data_provenance"]["contains_synthetic"])
        self.assertNotIn("모든 신호가 실측", result["data_provenance"]["disclosure"])
        self.assertNotIn("모든 신호가 실측", result["explanation"]["text"] or "")

    def test_synthetic_sub_source_marks_signal_synthetic(self) -> None:
        """RS04-D13: closure_source가 합성이면 SR-01 by_signal 플래그도 true."""
        payload = complete_payload()
        payload["market_data"]["source"] = "seoul_open_data"
        payload["market_data"]["closure_source"] = "synthetic_franchise_cohort"
        for r in payload["branch_reports"]:
            r["sales_source"] = "pos"
            r["cost_source"] = "self_reported"
        payload["reviews"] = None
        prov = analyze(payload)["data_provenance"]
        self.assertTrue(prov["contains_synthetic"])
        by = {s["signal_id"]: s["synthetic"] for s in prov["by_signal"]}
        self.assertTrue(by["SR-01"])
        self.assertFalse(by["SR-02.market"])
        self.assertFalse(by["SR-02.branch"])

    def test_excluded_future_months_is_structured(self) -> None:
        """RS04-D04: 기준일 이후 월이 구조화 필드로 노출."""
        payload = complete_payload()
        payload["branch_reports"].append(_month_report("2026-06", 40_000_000, margin=-0.3))
        result = analyze(payload)
        self.assertEqual(result["excluded_future_months"], ["2026-06"])

    def test_incomplete_quarter_not_counted_as_current(self) -> None:
        """RS04-D06: as_of가 분기 중간이면 그 분기를 완결분기로 쓰지 않는다."""
        payload = complete_payload()
        payload["as_of"] = "2026-02-28"  # 2026Q1 미완결
        payload["branch_reports"] = [r for r in payload["branch_reports"] if r["month"] <= "2026-02"]
        result = analyze(payload)
        latest = result["components"]["closure"].get("latest_quarter")
        self.assertEqual(latest, "2025Q4")

    def test_competition_without_radius_is_missing(self) -> None:
        """RS04-D07: 반경 없이 경쟁 점수를 만들지 않는다."""
        payload = complete_payload()
        payload["market_data"]["competition"]["radius_m"] = None
        result = analyze(payload)
        self.assertEqual(result["components"]["competition"]["status"], "missing")
        self.assertIsNone(result["components"]["competition"]["score"])
        self.assertTrue(any(m["signal_id"] == "SR-03" for m in result["missing_data"]))

    def test_mismatched_review_branch_id_excluded(self) -> None:
        """RS04-D09: 다른 점포의 리뷰는 제외하고 uncertainty로 표시."""
        payload = complete_payload()
        payload["reviews"] = {
            "source": "synthetic_reviews",
            "records": [
                {"review_id": "rv-x", "branch_id": "other-branch", "written_at": "2026-02-10",
                 "rating": 1, "text": "별로", "sentiment_label": "부정"},
                {"review_id": "rv-y", "branch_id": "br-001", "written_at": "2026-02-11",
                 "rating": 5, "text": "좋아요", "sentiment_label": "긍정"},
            ],
        }
        result = analyze(payload)
        self.assertTrue(any("다른 branch_id" in u for u in result["uncertainty"]))
        self.assertEqual(result["review_signal"]["review_count"], 1)

    def test_no_shadow_composite_field_when_partial(self) -> None:
        """RS04-D03: partial일 때 종합 점수 값이 새어나가지 않는다."""
        payload = complete_payload()
        payload["market_data"] = None
        result = analyze(payload)
        self.assertNotIn("provisional_composite", result["risk"])
        self.assertIsNone(result["risk"]["score"])

    def test_hq_summary_api(self) -> None:
        r_ok = analyze(complete_payload())
        resp = hq_summary(HqSummaryRequest.model_validate({
            "request_id": "hq-2", "franchise_id": "fr-001", "as_of": "2026-03-31",
            "branch_results": [r_ok],
        }))
        self.assertEqual(resp.model_dump()["franchise_id"], "fr-001")


if __name__ == "__main__":
    unittest.main()
