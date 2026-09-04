"""Risk-siren v1 orchestration over the pure signal calculators.

Composite score = SR-01 + SR-02.market + SR-03 (market layer)
                  and SR-02.branch + SR-05 (branch layer).
SR-04 review response is auxiliary and never changes score or grade.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from .alerts import build_alert
from .explanation import build_explanation
from .models import RiskSirenRequest, INDUSTRY_LABELS
from .reports import build_metric_series
from .risk_signals import (
    DEFAULT_POLICY,
    RiskPolicy,
    calculate_branch_sales,
    calculate_competition,
    calculate_market_closure,
    calculate_market_sales,
    calculate_profitability,
    calculate_review_signal,
)

PROVISIONAL_NOTE = (
    "가중치와 기준값은 데이터 검증 전의 provisional 정책이며 운영 적용 전 사람 승인이 필요합니다."
)


def _is_synthetic(value: str | None) -> bool:
    return bool(value) and "synthetic" in value


def _evidence(
    evidence_id: str,
    signal_id: str,
    layer: str,
    value: Any,
    unit: str,
    period: str,
    grain: str,
    source: str | None,
    synthetic: bool,
    supports: str,
) -> dict[str, Any]:
    return {
        "evidence_id": evidence_id,
        "signal_id": signal_id,
        "layer": layer,
        "value": value,
        "unit": unit,
        "period": period,
        "grain": grain,
        "source": source,
        "synthetic": synthetic,
        "supports": supports,
    }


def _weighted(pairs: list[tuple[float, float | None]]) -> float | None:
    if any(score is None for _, score in pairs):
        return None
    return round(sum(weight * score for weight, score in pairs), 4)


def analyze(
    request: RiskSirenRequest | dict[str, Any], policy: RiskPolicy = DEFAULT_POLICY
) -> dict[str, Any]:
    if not isinstance(request, RiskSirenRequest):
        request = RiskSirenRequest.model_validate(request)

    as_of = request.as_of
    missing_data: list[dict[str, str]] = []
    uncertainty: list[str] = [PROVISIONAL_NOTE]
    evidence: list[dict[str, Any]] = []

    # 신호별 synthetic 판정 — 부모 source 뿐 아니라 신호별 하위 source 도 검사 (RS04-D13)
    if request.market_data:
        md = request.market_data
        closure_synth = _is_synthetic(md.closure_source or md.source)
        market_sales_synth = _is_synthetic(md.sales_source or md.source)
        comp_synth = _is_synthetic(
            (md.competition.source if md.competition and md.competition.source else md.source)
        )
    else:
        closure_synth = market_sales_synth = comp_synth = False
    market_synth = any([closure_synth, market_sales_synth, comp_synth])
    sales_synth = any(_is_synthetic(r.sales_source) for r in request.branch_reports)
    cost_synth = any(_is_synthetic(r.cost_source) for r in request.branch_reports)
    review_synth = bool(request.reviews) and request.reviews.source == "synthetic_reviews"

    # ------------------------------------------------------------------ market
    if request.market_data is None:
        closure = {"score": None, "status": "missing"}
        market_sales = {"score": None, "status": "missing"}
        competition = {"score": None, "status": "missing"}
        missing_data.append({"signal_id": "SR-01", "reason": "market_data가 전달되지 않음"})
        missing_data.append({"signal_id": "SR-02.market", "reason": "market_data가 전달되지 않음"})
        missing_data.append({"signal_id": "SR-03", "reason": "market_data가 전달되지 않음"})
    else:
        closure = calculate_market_closure(request.market_data, as_of, policy)
        market_sales = calculate_market_sales(request.market_data, as_of, policy)
        competition = calculate_competition(request.market_data.competition, policy)
        if closure["status"] not in ("calculated", "partial"):
            missing_data.append({"signal_id": "SR-01", "reason": "분기 폐업률 계산 불가 (데이터 부족 또는 분모 0)"})
        if market_sales["status"] not in ("calculated", "partial"):
            missing_data.append({"signal_id": "SR-02.market", "reason": "분기 매출 변화 계산 불가"})
        if competition["status"] not in ("calculated", "partial"):
            missing_data.append({"signal_id": "SR-03", "reason": competition.get("reason", "반경·신규 경쟁 업체 수·출처 부족")})
        if closure.get("window_has_partial_quarter") or closure.get("rate_exceeds_100"):
            uncertainty.append("SR-01 폐업률에 미완결(부분) 분기 또는 100% 초과 값이 포함되어 완전성을 보장할 수 없습니다.")

        if closure.get("rolling_2q_rate") is not None:
            evidence.append(_evidence(
                "ev-closure-rolling-2q", "SR-01", "market", closure["rolling_2q_rate"],
                "percent", f"rolling-2q~{closure.get('latest_quarter')}",
                "trade_area-industry-quarter", closure.get("source"), closure_synth,
                "최근 2분기 폐업률",
            ))
        if closure.get("quarter_rate") is not None:
            evidence.append(_evidence(
                "ev-closure-quarter", "SR-01", "market", closure["quarter_rate"],
                "percent", str(closure.get("latest_quarter")),
                "trade_area-industry-quarter", closure.get("source"), closure_synth,
                "최근 분기 폐업률",
            ))
        if market_sales.get("recent_quarter_change_pct") is not None:
            evidence.append(_evidence(
                "ev-market-sales-qoq", "SR-02.market", "market", market_sales["recent_quarter_change_pct"],
                "percent", f"QoQ~{market_sales.get('latest_quarter')}",
                "trade_area-industry-quarter", market_sales.get("source"), market_sales_synth,
                "상권·업종 분기 매출 변화율",
            ))
        if competition.get("weighted_new_count") is not None and competition.get("status") in ("calculated", "partial"):
            evidence.append(_evidence(
                "ev-competition-new-3m", "SR-03", "market", competition["weighted_new_count"],
                "weighted-count", f"recent-3m-radius-{competition.get('radius_m')}m",
                "point-radius-industry", competition.get("source"), comp_synth,
                "반경 내 동일·유사업종 신규 경쟁업체 가중 관측",
            ))

    market_layer_score = _weighted([
        (policy.market_closure_weight, closure.get("score")),
        (policy.market_sales_weight, market_sales.get("score")),
        (policy.market_competition_weight, competition.get("score")),
    ])
    market_statuses = [closure.get("status"), market_sales.get("status"), competition.get("status")]
    if market_layer_score is None:
        market_layer_status = "missing"
    elif all(s == "calculated" for s in market_statuses):
        market_layer_status = "calculated"
    else:
        market_layer_status = "partial"

    # ------------------------------------------------------------------ branch
    metrics, future_months = build_metric_series(request.branch_reports, as_of)
    if future_months:
        uncertainty.append(f"기준일 이후 월은 계산에서 제외했습니다: {', '.join(future_months)}")
    mismatched_reviews = (
        sorted({r.branch_id for r in request.reviews.records
                if r.branch_id is not None and r.branch_id != request.branch_id})
        if request.reviews else []
    )
    if mismatched_reviews:
        uncertainty.append(
            f"다른 branch_id의 리뷰가 섞여 있어 제외했습니다: {', '.join(mismatched_reviews)}"
        )
    if request.reviews and any(r.written_at > as_of for r in request.reviews.records):
        uncertainty.append("기준일 이후 작성된 리뷰는 계산에서 제외했습니다.")
    if any(mm.cogs_clamped for mm in metrics):
        uncertainty.append("기말 재고가 기초보다 커서 일부 월의 매출원가를 0으로 하한 처리했습니다.")

    if not metrics:
        branch_sales = {"score": None, "status": "missing"}
        profitability = {"score": None, "status": "missing"}
        missing_data.append({"signal_id": "SR-02.branch", "reason": "월별 운영보고서가 전달되지 않음"})
        missing_data.append({"signal_id": "SR-05", "reason": "월별 운영보고서가 전달되지 않음"})
    else:
        branch_sales = calculate_branch_sales(metrics, as_of, policy)
        profitability = calculate_profitability(metrics, as_of, policy)
        if branch_sales["status"] not in ("calculated",):
            missing_data.append({"signal_id": "SR-02.branch", "reason": "최근·직전 3개월 순매출 구간이 완전하지 않음"})
        if profitability["status"] not in ("calculated",):
            missing_data.append({"signal_id": "SR-05", "reason": "최근·직전 3개월 손익 구간이 완전하지 않음"})

        if branch_sales.get("recent_3m_change_pct") is not None:
            evidence.append(_evidence(
                "ev-branch-sales-3m", "SR-02.branch", "branch", branch_sales["recent_3m_change_pct"],
                "percent", "recent-3m-vs-previous-3m", "branch-month",
                "synthetic_pos" if sales_synth else "operating_report", sales_synth,
                "가맹점 순매출 최근 3개월 변화율",
            ))
        if isinstance(profitability.get("operating_margin_recent_3m_pct"), (int, float)):
            evidence.append(_evidence(
                "ev-profit-margin-3m", "SR-05", "branch", profitability["operating_margin_recent_3m_pct"],
                "percent", "recent-3m", "branch-month",
                "synthetic_self_reported" if cost_synth else "operating_report", cost_synth,
                "최근 3개월 영업이익률",
            ))
        if profitability.get("net_sales_nonpositive"):
            evidence.append(_evidence(
                "ev-profit-net-sales-nonpositive", "SR-05", "branch",
                profitability.get("recent_3m_net_sales_krw"),
                "krw", "recent-3m", "branch-month",
                "synthetic_self_reported" if cost_synth else "operating_report", cost_synth,
                "최근 3개월 순매출 합 0 이하 — 영업 정지에 준하는 상태",
            ))
        if profitability.get("consecutive_negative_months"):
            evidence.append(_evidence(
                "ev-profit-negative-streak", "SR-05", "branch", profitability["consecutive_negative_months"],
                "months", f"~{as_of.isoformat()}", "branch-month",
                "synthetic_self_reported" if cost_synth else "operating_report", cost_synth,
                "영업이익 연속 적자 개월 수",
            ))

    branch_layer_score = _weighted([
        (policy.branch_sales_weight, branch_sales.get("score")),
        (policy.branch_profitability_weight, profitability.get("score")),
    ])
    # 지속적·심각한 수익성 붕괴(SR-05)는 단독으로도 충분한 폐업 위험 신호다.
    # 다른 요소는 이 위에 더할 수 있어도 끌어내리지 못한다.
    prof_score = profitability.get("score")
    if branch_layer_score is not None and prof_score is not None and prof_score >= 80.0:
        branch_layer_score = round(max(branch_layer_score, prof_score - 12.0), 4)
    branch_statuses = [branch_sales.get("status"), profitability.get("status")]
    if branch_layer_score is None:
        branch_layer_status = "missing"
    elif all(s == "calculated" for s in branch_statuses):
        branch_layer_status = "calculated"
    else:
        branch_layer_status = "partial"

    # --------------------------------------------------------------- auxiliary
    reviews_for_branch = request.reviews
    if request.reviews and mismatched_reviews:
        kept = [r for r in request.reviews.records
                if r.branch_id is None or r.branch_id == request.branch_id]
        reviews_for_branch = request.reviews.model_copy(update={"records": kept})
    review_signal = calculate_review_signal(reviews_for_branch, as_of, policy)
    if review_signal["status"] == "missing":
        missing_data.append({"signal_id": "SR-04", "reason": review_signal.get("note", "리뷰 데이터 없음 (보조 신호, 종합 점수 영향 없음)")})
    elif review_signal.get("negative_ratio") is not None:
        evidence.append(_evidence(
            "ev-review-neg-ratio", "SR-04", "auxiliary", review_signal["negative_ratio"],
            "ratio", "recent-90d", "branch-review",
            review_signal.get("source"), review_synth,
            "최근 리뷰 부정 반응 비율 (보조 신호)",
        ))

    # --------------------------------------------------------------- composite
    weighted = _weighted([
        (policy.layer_market_weight, market_layer_score),
        (policy.layer_branch_weight, branch_layer_score),
    ])
    composite = weighted
    branch_floor_applied = False
    if composite is not None and branch_layer_score is not None and branch_layer_score > composite:
        composite = round(branch_layer_score, 4)
        branch_floor_applied = True
        uncertainty.append(
            "가맹점 자체 위험이 시장 가중 평균보다 높아, 종합 점수를 가맹점 위험 수준으로 유지했습니다 "
            "(양호한 시장이 가맹점 위험을 상쇄하지 않음)."
        )
    both_calculated = market_layer_status == "calculated" and branch_layer_status == "calculated"
    grade = None
    if composite is not None and both_calculated:
        calculation_status = "calculated"
        if composite < policy.normal_upper_bound:
            grade = "정상"
        elif composite < policy.caution_upper_bound:
            grade = "주의"
        else:
            grade = "위험"
    else:
        calculation_status = "partial"
        if market_layer_status == "missing":
            uncertainty.append("시장(market_risk) 층이 계산되지 않아 종합 점수를 확정하지 않았습니다.")
        elif market_layer_status == "partial":
            uncertainty.append("시장(market_risk) 층 일부 신호가 partial 이라 종합 등급을 확정하지 않았습니다.")
        if branch_layer_status == "missing":
            uncertainty.append("가맹점(branch_risk) 층이 계산되지 않아 종합 점수를 확정하지 않았습니다.")
        elif branch_layer_status == "partial":
            uncertainty.append("가맹점(branch_risk) 층 일부 신호가 partial 이라 종합 등급을 확정하지 않았습니다.")

    risk = {
        "score": composite if both_calculated else None,
        "grade": grade,
        "score_version": policy.version,
        "calculation_status": calculation_status,
        "policy_status": "provisional",
        "composite_basis": ["SR-01", "SR-02.market", "SR-03", "SR-02.branch", "SR-05"],
        "excludes": ["SR-04"],
        "branch_floor_applied": branch_floor_applied if both_calculated else False,
    }
    layers = {
        "market_risk": {"score": market_layer_score, "status": market_layer_status},
        "branch_risk": {"score": branch_layer_score, "status": branch_layer_status},
    }
    components = {
        "closure": closure,
        "sales_decline": {"market": market_sales, "branch": branch_sales},
        "competition": competition,
        "profitability": profitability,
    }

    contains_synthetic = any([market_synth, sales_synth, cost_synth, review_synth])
    synthetic_signals = [
        name for name, flag in (
            ("SR-01/02.market/03(시장)", market_synth), ("SR-02.branch(매출)", sales_synth),
            ("SR-05(손익)", cost_synth), ("SR-04(리뷰)", review_synth),
        ) if flag
    ]
    if not contains_synthetic:
        disclosure = "모든 신호가 실측 데이터입니다."
    elif not market_synth:
        disclosure = (
            "가맹점 매출·손익·리뷰는 대회 데모용 합성 데이터입니다. "
            "상권×업종 폐업률·시장 매출·경쟁업체 등장은 서울시 공개데이터 실측입니다."
        )
    else:
        disclosure = "합성 데이터가 포함되어 있습니다: " + ", ".join(synthetic_signals) + "."
    data_provenance = {
        "contains_synthetic": contains_synthetic,
        "disclosure": disclosure,
        "by_signal": [
            {"signal_id": "SR-01", "layer": "market", "source": closure.get("source"), "synthetic": closure_synth},
            {"signal_id": "SR-02.market", "layer": "market", "source": market_sales.get("source"), "synthetic": market_sales_synth},
            {"signal_id": "SR-03", "layer": "market", "source": competition.get("source"), "synthetic": comp_synth},
            {"signal_id": "SR-02.branch", "layer": "branch", "source": "synthetic_pos" if sales_synth else "operating_report", "synthetic": sales_synth},
            {"signal_id": "SR-05", "layer": "branch", "source": "synthetic_self_reported" if cost_synth else "operating_report", "synthetic": cost_synth},
            {"signal_id": "SR-04", "layer": "auxiliary", "source": review_signal.get("source"), "synthetic": review_synth},
        ],
    }

    alert = build_alert(
        branch_id=request.branch_id,
        as_of=as_of,
        score=risk["score"],
        grade=grade,
        score_version=policy.version,
        evidence_ids=[item["evidence_id"] for item in evidence],
    )

    result: dict[str, Any] = {
        "request_id": request.request_id,
        "branch": {
            "franchise_id": request.franchise_id,
            "branch_id": request.branch_id,
            "brand_name": request.brand_name,
            "as_of": as_of.isoformat(),
            "industry_code": request.industry_code.value,
            "industry_label": INDUSTRY_LABELS[request.industry_code.value],
            "trade_area_code": request.location.trade_area_code,
        },
        "risk": risk,
        "layers": layers,
        "components": components,
        "review_signal": review_signal,
        "evidence": evidence,
        "missing_data": missing_data,
        "uncertainty": uncertainty,
        "excluded_future_months": future_months,
        "data_provenance": data_provenance,
        "alert": alert,
        "financial_products": {"status": "catalog_match_pending", "items": []},
        "explanation": {},
        "projections": {},
    }
    result["explanation"] = build_explanation(result, request.options.llm_mode)
    result["projections"] = _build_projections(result)

    if request.options.send_notifications:
        raise ValueError(
            "send_notifications=true is disabled until an approved dispatch adapter exists"
        )
    return result


def _build_projections(result: dict[str, Any]) -> dict[str, Any]:
    risk = result["risk"]
    review = result["review_signal"]
    branch_owner = {
        "branch_id": result["branch"]["branch_id"],
        "score": risk["score"],
        "grade": risk["grade"],
        "calculation_status": risk["calculation_status"],
        "layers": result["layers"],
        "components": result["components"],
        "review_signal": review,
        "evidence": result["evidence"],
        "missing_data": result["missing_data"],
        "uncertainty": result["uncertainty"],
        "recommended_actions": [],
        "financial_products": result["financial_products"],
        "report_link": None,
        "data_provenance": result["data_provenance"],
    }
    franchise_hq = {
        "branch_id": result["branch"]["branch_id"],
        "franchise_id": result["branch"]["franchise_id"],
        "score": risk["score"],
        "grade": risk["grade"],
        "calculation_status": risk["calculation_status"],
        "component_status": {
            "closure": result["components"]["closure"].get("status"),
            "sales_decline_market": result["components"]["sales_decline"]["market"].get("status"),
            "sales_decline_branch": result["components"]["sales_decline"]["branch"].get("status"),
            "competition": result["components"]["competition"].get("status"),
            "profitability": result["components"]["profitability"].get("status"),
        },
        "review_watchlist_flag": bool(review.get("watchlist_flag")),
        "data_provenance": {
            "contains_synthetic": result["data_provenance"]["contains_synthetic"],
            "disclosure": result["data_provenance"]["disclosure"],
        },
    }
    return {"branch_owner": branch_owner, "franchise_hq": franchise_hq}
