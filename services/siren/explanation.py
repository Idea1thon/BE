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


def build_explanation(result: dict[str, Any], mode: str) -> dict[str, Any]:
    if mode == "disabled":
        return {"text": None, "evidence_ids": [], "model": None}

    evidence_ids = [item["evidence_id"] for item in result.get("evidence", [])]
    risk = result["risk"]
    review = result.get("review_signal", {})
    lines: list[str] = []

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
