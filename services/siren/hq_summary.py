"""Head-office aggregation over per-branch risk results.

Deterministic projection only: it never recomputes a branch score, and it keeps
``danger_ratio_pct`` and ``average_score`` as separate fields so a screen shows
one, not a blend (03-pipeline-design.md section 6).
"""

from __future__ import annotations

from typing import Any

from .models import HqSummaryRequest


def summarize(request: HqSummaryRequest | dict[str, Any]) -> dict[str, Any]:
    if not isinstance(request, HqSummaryRequest):
        request = HqSummaryRequest.model_validate(request)

    grades = {"정상": 0, "주의": 0, "위험": 0}
    risk_levels = {"NORMAL": 0, "CAUTION": 0, "DANGER": 0}
    scores: list[float] = []
    watchlist: list[dict[str, Any]] = []
    contains_synthetic = False
    alert_candidate_count = 0

    for result in request.branch_results:
        res = result.model_dump()
        risk = res.get("risk", {})
        prov = res.get("data_provenance", {})
        contains_synthetic = contains_synthetic or bool(prov.get("contains_synthetic"))
        grade = risk.get("grade")
        if grade in grades:
            grades[grade] += 1
        risk_level = risk.get("risk_level")
        if risk_level in risk_levels:
            risk_levels[risk_level] += 1
        if risk.get("calculation_status") == "calculated" and isinstance(risk.get("score"), (int, float)):
            scores.append(float(risk["score"]))
        if res.get("alert", {}).get("should_fire"):
            alert_candidate_count += 1

        review = res.get("review_signal", {})
        reasons: list[str] = []
        if grade == "위험":
            reasons.append("grade:위험")
        elif res["alert"]["should_fire"]:
            reasons.append("confirmed_branch_warning")
        prof = res.get("components", {}).get("profitability", {})
        if isinstance(prof.get("consecutive_negative_months"), int) and prof["consecutive_negative_months"] >= 2:
            reasons.append("SR-05")
        if review.get("watchlist_flag"):
            reasons.append("SR-04")
        if res.get("risk", {}).get("calculation_status") == "partial":
            reasons.append("partial")
        if reasons:
            watchlist.append({
                "branch_id": res.get("branch", {}).get("branch_id"),
                "grade": grade,
                "risk_level": risk_level,
                "reasons": reasons,
            })

    # 비표준 grade_policy의 잠정 등급은 grade_distribution/watchlist에는
    # 표시하되, 확정 위험 비율·평균 점수의 분모에는 포함하지 않는다.
    calculated = sum(
        1
        for result in request.branch_results
        if result.risk.calculation_status == "calculated"
    )
    calculated_danger = sum(
        1
        for result in request.branch_results
        if result.risk.calculation_status == "calculated" and result.risk.grade == "위험"
    )
    danger_ratio = round(calculated_danger / calculated * 100, 4) if calculated else None
    average_score = round(sum(scores) / len(scores), 4) if scores else None

    return {
        "franchise_id": request.franchise_id,
        "as_of": request.as_of.isoformat(),
        "branch_count": len(request.branch_results),
        "calculated_count": calculated,
        "grade_distribution": grades,
        "risk_level_distribution": risk_levels,
        "danger_ratio_pct": danger_ratio,
        "average_score": average_score,
        "watchlist": watchlist,
        "alert_candidate_count": alert_candidate_count,
        "unread_alert_count": None,
        "data_provenance": {
            "contains_synthetic": contains_synthetic,
            "disclosure": (
                "일부 가맹점 결과에 대회 데모용 합성 데이터가 포함되어 있습니다."
                if contains_synthetic
                else "모든 신호가 실측 데이터입니다."
            ),
        },
    }
