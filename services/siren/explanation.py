"""Evidence-grounded explanation adapter.

The v1 implementation uses a deterministic template. A hosted LLM adapter may be
added later, but it must receive only validated Evidence and cannot change
score, grade, layer scores, review sub-score, alert, or financial-product
fields. It may also assist review-sentiment classification when
``llm_mode == "explanation_and_review_assist"`` — the deterministic counts still
drive the sub-score.
"""

from __future__ import annotations

from typing import Any


def _percent(value: object) -> str | None:
    if not isinstance(value, (int, float)):
        return None
    return f"{float(value):.1f}%"


def build_explanation(result: dict[str, Any], mode: str) -> dict[str, Any]:
    if mode == "disabled":
        return {"text": None, "evidence_ids": [], "model": None}

    evidence_ids = [item["evidence_id"] for item in result.get("evidence", [])]
    risk = result["risk"]
    review = result.get("review_signal", {})
    components = result.get("components", {})
    lines: list[str] = []

    score = risk.get("score")
    grade = risk.get("grade")
    if isinstance(score, (int, float)) and grade:
        lines.append(f"종합 위험도는 {float(score):.1f}점으로 '{grade}' 등급입니다.")

    branch_sales = (components.get("sales_decline") or {}).get("branch") or {}
    recent_change = _percent(branch_sales.get("recent_3m_change_pct"))
    previous_change = _percent(branch_sales.get("previous_3m_change_pct"))
    weights = branch_sales.get("decay_weight") or {}
    if recent_change is not None:
        if previous_change is not None:
            recent_weight = float(weights.get("recent_3m", 0.70)) * 100
            previous_weight = float(weights.get("previous_3m", 0.30)) * 100
            lines.append(
                "가맹점 매출은 최근 3개월 기준 "
                f"{recent_change}, 직전 3개월 기준 {previous_change}이며, "
                f"최근 구간 {recent_weight:.0f}%·직전 구간 {previous_weight:.0f}% 가중으로 반영했습니다."
            )
        else:
            lines.append(f"가맹점 매출은 최근 3개월 기준 {recent_change} 변화가 확인되었습니다.")

    profitability = components.get("profitability") or {}
    recent_margin = _percent(profitability.get("operating_margin_recent_3m_pct"))
    margin_decline_value = profitability.get("operating_margin_decline_pt")
    margin_decline = (
        f"{float(margin_decline_value):.1f}"
        if isinstance(margin_decline_value, (int, float))
        else None
    )
    if recent_margin is not None:
        margin_text = f"최근 3개월 영업이익률은 {recent_margin}"
        if margin_decline is not None:
            margin_text += f", 직전 구간 대비 {margin_decline}포인트 변화입니다"
        lines.append(margin_text + ".")

    market_closure = components.get("closure") or {}
    market_rate = _percent(
        market_closure.get(market_closure.get("score_basis"))
        if market_closure.get("score_basis")
        else None
    )
    if market_rate is not None:
        basis_label = {
            "quarter_rate": "최근 분기",
            "rolling_2q_rate": "최근 2분기",
            "rolling_4q_rate": "최근 4분기",
        }.get(market_closure.get("score_basis"), "선택된 기간")
        lines.append(
            f"상권 폐업률은 {basis_label} 기준 {market_rate}입니다."
        )

    franchise_closure = result.get("franchise_closure") or {}
    if franchise_closure.get("status") == "calculated":
        operating_rate = _percent(franchise_closure.get("operating_base_rate_pct"))
        previous_rate = _percent(franchise_closure.get("previous_year_base_rate_pct"))
        if operating_rate is not None and previous_rate is not None:
            lines.append(
                "브랜드 연간 폐업률은 영업 모집단 기준 "
                f"{operating_rate}, 전년 말 모집단 기준 {previous_rate}입니다."
            )
    elif franchise_closure.get("status") in {"missing", "not_calculable"}:
        lines.append("브랜드 연간 폐업 통계는 현재 연결된 원천에 없어 별도 폐업률로 산출하지 않았습니다.")

    if risk["calculation_status"] != "calculated":
        lines.append(
            "현재 입력된 근거만으로는 종합 위험도를 완전히 계산할 수 없습니다. "
            "누락된 자료(시장 또는 가맹점 층)를 확인해 주세요."
        )
    elif risk["grade"] == "위험":
        lines.append("검증된 위험 신호를 종합한 결과 집중 관리가 필요한 상태입니다. 각 근거와 기간을 함께 확인해 주세요.")
    elif risk["grade"] == "주의":
        lines.append("일부 위험 신호가 관찰되었습니다. 최근 기간의 변화와 세부 근거를 확인해 주세요.")
    else:
        lines.append("현재 입력된 검증 근거 기준으로 높은 위험 신호는 확인되지 않았습니다. 이후 기간의 변화를 계속 확인해 주세요.")

    if review.get("status") == "calculated" and review.get("watchlist_flag"):
        direction = review.get("direction")
        if direction == "worsening":
            note = "최근 리뷰의 부정 반응이 늘고 있습니다"
        elif direction == "improving":
            note = "최근 리뷰의 부정 반응 비율이 높지만 직전 기간보다는 줄었습니다"
        else:
            note = "최근 리뷰의 부정 반응 비율이 높습니다"
        lines.append(f"참고: {note} (보조 지표, 종합 점수에는 반영되지 않음).")

    if result.get("data_provenance", {}).get("contains_synthetic"):
        lines.append(result["data_provenance"]["disclosure"])

    return {
        "text": " ".join(lines),
        "evidence_ids": evidence_ids,
        "model": "deterministic-template-v1",
    }
