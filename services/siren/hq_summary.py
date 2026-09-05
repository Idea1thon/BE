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
    scores: list[float] = []
    watchlist: list[dict[str, Any]] = []
    contains_synthetic = False

    seen: set[str] = set()
    for res in request.branch_results:
        branch = res.get("branch")
        if not isinstance(branch, dict) or branch.get("franchise_id") != request.franchise_id:
            raise ValueError("every branch result must belong to the requested franchise")
        branch_id = branch.get("branch_id")
        if not isinstance(branch_id, str) or not branch_id.strip():
            raise ValueError("every branch result requires a branch_id")
        if branch_id in seen:
            raise ValueError("branch_results must not contain duplicate branches")
        seen.add(branch_id)
        risk = res.get("risk", {})
        prov = res.get("data_provenance", {})
        contains_synthetic = contains_synthetic or bool(prov.get("contains_synthetic"))
        grade = risk.get("grade")
        if grade in grades:
            grades[grade] += 1
        if isinstance(risk.get("score"), (int, float)):
            scores.append(float(risk["score"]))

        review = res.get("review_signal", {})
        reasons: list[str] = []
        if grade == "위험":
            reasons.append("grade:위험")
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
                "reasons": reasons,
            })

    calculated = grades["정상"] + grades["주의"] + grades["위험"]
    danger_ratio = round(grades["위험"] / calculated * 100, 4) if calculated else None
    average_score = round(sum(scores) / len(scores), 4) if scores else None

    return {
        "franchise_id": request.franchise_id,
        "as_of": request.as_of.isoformat(),
        "branch_count": len(request.branch_results),
        "calculated_count": calculated,
        "grade_distribution": grades,
        "danger_ratio_pct": danger_ratio,
        "average_score": average_score,
        "watchlist": watchlist,
        "data_provenance": {
            "contains_synthetic": contains_synthetic,
            "disclosure": (
                "일부 가맹점 결과에 대회 데모용 합성 데이터가 포함되어 있습니다."
                if contains_synthetic
                else "모든 신호가 실측 데이터입니다."
            ),
        },
    }
