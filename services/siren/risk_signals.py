"""Pure deterministic calculators for the risk-siren v1 signals.

Layers:
  market  : SR-01 closure rate, SR-02.market sales trend, SR-03 competition
  branch  : SR-02.branch sales decline, SR-05 profitability deterioration
  auxiliary: SR-04 review response (NOT in the composite score)

Every policy constant here is provisional and requires data-team / human
approval before operational use (``RiskPolicy.version``).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from .models import MarketCompetition, MarketData, ReviewsInput
from .reports import MonthlyMetrics, mean_ratio, sum_field, window_ending


@dataclass(frozen=True)
class RiskPolicy:
    """Provisional policy values — require approval before operational use."""

    # Keep the persisted version within middle-backend's existing
    # report_analysis.rule_version VARCHAR(20) until a schema migration is
    # explicitly approved. Do not truncate this value at the storage boundary.
    version: str = "risk-siren-v1.2"

    # composite weights (sum per layer = 1.0)
    market_closure_weight: float = 0.45
    market_sales_weight: float = 0.35
    market_competition_weight: float = 0.20
    branch_sales_weight: float = 0.35
    branch_profitability_weight: float = 0.65
    # 종합 = max(branch_layer, 0.35*market + 0.65*branch)
    #   시장 위험은 가맹점 위험을 "올릴" 수 있어도, 양호한 시장이 가맹점 위험을 상쇄하지 않는다.
    layer_market_weight: float = 0.35
    layer_branch_weight: float = 0.65

    # reference scales (value that maps to score 100)
    closure_rate_reference_pct: float = 12.0          # quarterly / rolling closure rate
    market_sales_decline_reference_pct: float = 15.0  # QoQ / YoY decline
    competition_reference_count: float = 8.0          # weighted new competitors in 3m
    branch_sales_decline_reference_pct: float = 25.0  # 3m vs prior 3m decline
    profit_margin_decline_reference_pt: float = 10.0  # points of operating-margin drop
    labor_ratio_reference: float = 0.35
    coupon_ratio_reference: float = 0.08
    negative_review_reference_ratio: float = 0.40

    # grade thresholds
    normal_upper_bound: float = 40.0
    caution_upper_bound: float = 70.0

    # review direction sensitivity
    review_direction_delta: float = 0.05


DEFAULT_POLICY = RiskPolicy()


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def clamp_score(value: float) -> float:
    return round(max(0.0, min(100.0, value)), 4)


def _decline_score(change_pct: float | None, reference_pct: float) -> float:
    if change_pct is None:
        return 0.0
    return clamp_score(max(0.0, -change_pct) / reference_pct * 100)


def _quarter_index(q: str) -> int:
    year, qn = int(q[:4]), int(q[5])
    return year * 4 + (qn - 1)


def _last_completed_quarter_index(as_of: date) -> int:
    """Index of the most recent quarter whose end date is <= as_of.

    A quarter that ``as_of`` falls inside is excluded unless it has fully elapsed
    (taxonomy §6: no future-period data in a past-period calculation).
    """
    year, qn = as_of.year, (as_of.month - 1) // 3 + 1
    end_month = qn * 3
    quarter_end_day = {3: 31, 6: 30, 9: 30, 12: 31}[end_month]
    if as_of.month == end_month and as_of.day >= quarter_end_day:
        return year * 4 + (qn - 1)
    # previous quarter
    return year * 4 + (qn - 1) - 1


def _rate(numerator: float, denominator: float) -> tuple[float | None, str]:
    if denominator == 0:
        return None, "not_calculable"
    return round(numerator / denominator * 100, 4), "calculated"


# --------------------------------------------------------------------------- #
# SR-01  market closure rate
# --------------------------------------------------------------------------- #
def calculate_market_closure(
    market: MarketData, as_of: date, policy: RiskPolicy = DEFAULT_POLICY
) -> dict[str, Any]:
    cutoff = _last_completed_quarter_index(as_of)
    quarters = sorted(
        (q for q in market.closure_quarters if _quarter_index(q.quarter) <= cutoff),
        key=lambda q: _quarter_index(q.quarter),
    )
    source = market.closure_source or market.source
    if not quarters:
        return {"score": None, "status": "missing", "source": source}

    incomplete = [
        q
        for q in quarters
        if q.active_count_end is None
        or q.new_openings is None
        or q.closures is None
    ]
    if incomplete:
        missing_quarters = [q.quarter for q in incomplete]
        return {
            "score": None,
            "status": "missing",
            "source": source,
            "reason": (
                "개업·폐업·영업중 점포 수가 누락된 분기가 있어 폐업률을 계산하지 않았습니다: "
                + ", ".join(missing_quarters)
            ),
            "missing_quarters": missing_quarters,
        }

    by_index = {_quarter_index(q.quarter): q for q in quarters}
    latest = quarters[-1]
    latest_idx = _quarter_index(latest.quarter)

    def prior_active(idx: int) -> int | None:
        q = by_index.get(idx)
        return q.active_count_end if q is not None else None

    def rolling(window: int) -> tuple[float | None, str]:
        idxs = [latest_idx - k for k in range(window)]
        if any(i not in by_index for i in idxs):
            return None, "missing"
        base = prior_active(min(idxs) - 1)
        if base is None:
            base = by_index[min(idxs)].active_count_end
        total_closures = sum(by_index[i].closures for i in idxs)
        value, st = _rate(total_closures, base)
        if value is not None and any(by_index[i].quarter_status == "부분" for i in idxs):
            st = "partial"  # RS04-D11: 부분 분기가 창에 포함됨
        return value, st

    base_prev = prior_active(latest_idx - 1)
    if base_prev is None:
        base_prev = latest.active_count_end
    quarter_rate, quarter_status = _rate(latest.closures, base_prev)
    rolling_2q_rate, rolling_2q_status = rolling(2)
    rolling_4q_rate, rolling_4q_status = rolling(4)

    if rolling_2q_rate is not None:
        basis, rate, basis_status = "rolling_2q_rate", rolling_2q_rate, rolling_2q_status
    elif quarter_rate is not None:
        basis, rate, basis_status = "quarter_rate", quarter_rate, quarter_status
    elif rolling_4q_rate is not None:
        basis, rate, basis_status = "rolling_4q_rate", rolling_4q_rate, rolling_4q_status
    else:
        basis, rate, basis_status = None, None, "missing"

    rate_exceeds_100 = rate is not None and rate > 100.0  # RS04-D08: 정합성 의심 입력
    score = None if rate is None else clamp_score(rate / policy.closure_rate_reference_pct * 100)
    status = "calculated"
    if score is None:
        status = "not_calculable" if quarter_status == "not_calculable" else "missing"
    elif (
        latest.quarter_status == "부분"
        or basis_status == "partial"
        or "missing" in (rolling_2q_status, rolling_4q_status)
        or rate_exceeds_100
    ):
        status = "partial"

    return {
        "score": score,
        "score_basis": basis,
        "latest_quarter": latest.quarter,
        "latest_quarter_status": latest.quarter_status,
        "window_has_partial_quarter": any(q.quarter_status == "부분" for q in quarters[-4:]),
        "quarter_rate": quarter_rate,
        "rolling_2q_rate": rolling_2q_rate,
        "rolling_4q_rate": rolling_4q_rate,
        "rate_exceeds_100": rate_exceeds_100,
        "active_count_end": latest.active_count_end,
        "new_openings": latest.new_openings,
        "closures": latest.closures,
        "source": source,
        "status": status,
    }


# --------------------------------------------------------------------------- #
# SR-02.market  trade-area x industry sales trend
# --------------------------------------------------------------------------- #
def calculate_market_sales(
    market: MarketData, as_of: date, policy: RiskPolicy = DEFAULT_POLICY
) -> dict[str, Any]:
    cutoff = _last_completed_quarter_index(as_of)
    quarters = sorted(
        (q for q in market.sales_quarters if _quarter_index(q.quarter) <= cutoff),
        key=lambda q: _quarter_index(q.quarter),
    )
    source = market.sales_source or market.source
    if not quarters:
        return {"score": None, "status": "missing", "source": source}

    by_index = {_quarter_index(q.quarter): q.amount_krw for q in quarters}
    latest = quarters[-1]
    latest_idx = _quarter_index(latest.quarter)
    prev = by_index.get(latest_idx - 1)
    prev_year = by_index.get(latest_idx - 4)

    qoq = None
    qoq_status = "missing"
    if prev is not None:
        if prev == 0:
            qoq_status = "not_calculable"
        else:
            qoq = round((latest.amount_krw - prev) / prev * 100, 4)
            qoq_status = "calculated"

    yoy = None
    if prev_year not in (None, 0):
        yoy = round((latest.amount_krw - prev_year) / prev_year * 100, 4)

    score = None
    if qoq is not None:
        s = _decline_score(qoq, policy.market_sales_decline_reference_pct)
        if yoy is not None:
            s = round(0.6 * s + 0.4 * _decline_score(yoy, policy.market_sales_decline_reference_pct), 4)
        score = s

    status = "calculated"
    if score is None:
        status = qoq_status if qoq_status == "not_calculable" else "missing"
    elif yoy is None:
        status = "partial"

    return {
        "score": score,
        "latest_quarter": latest.quarter,
        "recent_quarter_change_pct": qoq,
        "yoy_change_pct": yoy,
        "source": source,
        "status": status,
    }


# --------------------------------------------------------------------------- #
# SR-03  competition
# --------------------------------------------------------------------------- #
def calculate_competition(
    competition: MarketCompetition | None, policy: RiskPolicy = DEFAULT_POLICY
) -> dict[str, Any]:
    if competition is None:
        return {"score": None, "status": "missing", "source": None}

    same = competition.same_industry_new_recent_3m
    similar = competition.similar_industry_new_recent_3m
    if same is None and similar is None:
        return {"score": None, "status": "missing", "source": competition.source}
    if competition.radius_m is None:
        # RS04-D07: 반경을 모른 채 카운트만으로 만든 점수는 근거가 불완전 (계약 §2.2, RS-T04)
        return {
            "score": None,
            "status": "missing",
            "radius_m": None,
            "same_new_recent_3m": same,
            "similar_new_recent_3m": similar,
            "source": competition.source,
            "reason": "radius_m 미정 — 경쟁 점수 미산출",
        }

    weighted = round((same or 0) * 0.65 + (similar or 0) * 0.35, 4)
    score = clamp_score(weighted / policy.competition_reference_count * 100)

    same_delta = None
    if same is not None and competition.same_industry_new_previous_3m is not None:
        same_delta = same - competition.same_industry_new_previous_3m
    similar_delta = None
    if similar is not None and competition.similar_industry_new_previous_3m is not None:
        similar_delta = similar - competition.similar_industry_new_previous_3m
    if same_delta is not None and same_delta > 0:
        score = clamp_score(score + min(15.0, same_delta * 5.0))

    complete = (
        competition.radius_m is not None
        and same is not None
        and similar is not None
        and bool(competition.source)
    )
    return {
        "score": score,
        "radius_m": competition.radius_m,
        "same_new_recent_3m": same,
        "same_new_delta": same_delta,
        "similar_new_recent_3m": similar,
        "similar_new_delta": similar_delta,
        "weighted_new_count": weighted,
        "active_only": competition.active_only,
        "source": competition.source,
        "status": "calculated" if complete else "partial",
    }


# --------------------------------------------------------------------------- #
# SR-02.branch  monthly net-sales decline
# --------------------------------------------------------------------------- #
def calculate_branch_sales(
    metrics: list[MonthlyMetrics], as_of: date, policy: RiskPolicy = DEFAULT_POLICY
) -> dict[str, Any]:
    if not metrics:
        return {"score": None, "status": "missing"}

    end = (as_of.year, as_of.month)
    recent, recent_missing = window_ending(metrics, end, 3)
    prev_end = (end[0] * 12 + end[1] - 1 - 3) // 12, (end[0] * 12 + end[1] - 1 - 3) % 12 + 1
    previous, previous_missing = window_ending(metrics, prev_end, 3)
    older_end = (end[0] * 12 + end[1] - 1 - 6) // 12, (end[0] * 12 + end[1] - 1 - 6) % 12 + 1
    older, older_missing = window_ending(metrics, older_end, 3)

    recent_total = sum_field(recent, "net_sales") if len(recent) == 3 else None
    previous_total = sum_field(previous, "net_sales") if len(previous) == 3 else None
    older_total = sum_field(older, "net_sales") if len(older) == 3 else None

    recent_change = None
    if recent_total is not None and previous_total not in (None, 0):
        recent_change = round((recent_total - previous_total) / previous_total * 100, 4)
    previous_change = None
    if previous_total is not None and older_total not in (None, 0):
        previous_change = round((previous_total - older_total) / older_total * 100, 4)

    score = None
    if recent_change is not None:
        current = _decline_score(recent_change, policy.branch_sales_decline_reference_pct)
        if previous_change is None:
            score = current
        else:
            score = round(
                0.7 * current
                + 0.3 * _decline_score(previous_change, policy.branch_sales_decline_reference_pct),
                4,
            )

    status = "calculated" if score is not None and not recent_missing and not previous_missing else "partial"
    if recent_total is not None and previous_total == 0:
        status = "not_calculable"
    if score is None and status == "partial" and not recent:
        status = "missing"

    return {
        "score": score,
        "recent_3m_total_krw": recent_total,
        "previous_3m_total_krw": previous_total,
        "recent_3m_change_pct": recent_change,
        "previous_3m_change_pct": previous_change,
        "recent_period_missing_months": recent_missing,
        "previous_period_missing_months": previous_missing,
        "status": status,
    }


# --------------------------------------------------------------------------- #
# SR-05  profitability deterioration
# --------------------------------------------------------------------------- #
def _consecutive_negative_profit(metrics: list[MonthlyMetrics], as_of: date) -> int:
    """Trailing run of months (up to as_of) with operating_profit < 0."""
    expected = as_of.year * 12 + as_of.month - 1
    run = 0
    for mm in sorted(metrics, key=lambda m: m.month, reverse=True):
        year, month = map(int, mm.month.split("-"))
        index = year * 12 + month - 1
        if index > expected:
            continue
        if index != expected or mm.operating_profit >= 0:
            break
        run += 1
        expected -= 1
    return run


def calculate_profitability(
    metrics: list[MonthlyMetrics], as_of: date, policy: RiskPolicy = DEFAULT_POLICY
) -> dict[str, Any]:
    if not metrics:
        return {"score": None, "status": "missing"}

    end = (as_of.year, as_of.month)
    recent, recent_missing = window_ending(metrics, end, 3)
    prev_end = (end[0] * 12 + end[1] - 1 - 3) // 12, (end[0] * 12 + end[1] - 1 - 3) % 12 + 1
    previous, previous_missing = window_ending(metrics, prev_end, 3)

    # RS04-D01: 최근 3개월 순매출 합 <= 0 은 비율 계산 불가지만 "안전"이 아니라
    # 사실상 영업 정지에 준하는 최고위험 상태다 (signal-audit SR-05 "매출 0 별도 처리").
    recent_net = sum_field(recent, "net_sales") if len(recent) == 3 else None
    if len(recent) == 3 and recent_net is not None and recent_net <= 0:
        neg_streak = _consecutive_negative_profit(metrics, as_of)
        return {
            "score": 95.0,
            "net_sales_nonpositive": True,
            "operating_margin_recent_3m_pct": "not_calculable",
            "operating_margin_previous_3m_pct": None,
            "operating_margin_decline_pt": None,
            "operating_profit_recent_3m_krw": sum_field(recent, "operating_profit"),
            "recent_3m_net_sales_krw": recent_net,
            "consecutive_negative_months": neg_streak,
            "labor_ratio_recent_3m": None,
            "coupon_ratio_recent_3m": None,
            "delivery_ratio_recent_3m": None,
            "loan_interest_trend": "unknown",
            "recent_period_missing_months": recent_missing,
            "status": "calculated",
            "note": "최근 3개월 순매출 합이 0 이하 — 영업 정지에 준하는 최고위험",
        }

    def margin(window: list[MonthlyMetrics]) -> float | None:
        if len(window) != 3:
            return None
        net = sum_field(window, "net_sales")
        if net <= 0:
            return None
        return round(sum_field(window, "operating_profit") / net * 100, 4)

    recent_margin = margin(recent)
    previous_margin = margin(previous)
    margin_decline_pt = None
    if recent_margin is not None and previous_margin is not None:
        margin_decline_pt = round(previous_margin - recent_margin, 4)

    consecutive_negative = _consecutive_negative_profit(metrics, as_of)

    labor_ratio_recent = mean_ratio(recent, "labor_ratio")
    coupon_ratio_recent = mean_ratio(recent, "coupon_ratio")
    delivery_ratio_recent = mean_ratio(recent, "delivery_ratio")

    loan_recent = sum_field(recent, "loan_interest") if len(recent) == 3 else None
    loan_previous = sum_field(previous, "loan_interest") if len(previous) == 3 else None
    loan_trend = "unknown"
    if loan_recent is not None and loan_previous is not None:
        if loan_recent > loan_previous * 1.1:
            loan_trend = "rising"
        elif loan_recent < loan_previous * 0.9:
            loan_trend = "falling"
        else:
            loan_trend = "flat"

    if recent_margin is None:
        return {
            "score": None,
            "recent_period_missing_months": recent_missing,
            "consecutive_negative_months": consecutive_negative,
            "status": "partial" if recent else "missing",
        }

    # Keep the denominator of the core signals fixed when adding expense penalties.
    # A newly observed risk must not dilute the existing margin/loss evidence.
    parts: list[float] = []
    parts.append(clamp_score(max(0.0, -recent_margin) / 15.0 * 100))  # negative margin
    if margin_decline_pt is not None:
        parts.append(_decline_score(-margin_decline_pt, policy.profit_margin_decline_reference_pt))
    parts.append(min(100.0, consecutive_negative / 4.0 * 100))
    core_signal_count = len(parts)
    if labor_ratio_recent is not None and labor_ratio_recent > policy.labor_ratio_reference:
        parts.append(clamp_score((labor_ratio_recent - policy.labor_ratio_reference) / 0.15 * 100))
    if coupon_ratio_recent is not None and coupon_ratio_recent > policy.coupon_ratio_reference:
        parts.append(clamp_score((coupon_ratio_recent - policy.coupon_ratio_reference) / 0.08 * 100))
    if loan_trend == "rising":
        parts.append(45.0)
    score = clamp_score(0.55 * (sum(parts) / core_signal_count) + 0.45 * max(parts))
    if recent_margin <= -12.0 or consecutive_negative >= 8:
        score = max(score, 75.0)
    elif recent_margin <= -5.0 or consecutive_negative >= 4:
        score = max(score, 55.0)

    status = "calculated" if not recent_missing and not previous_missing and margin_decline_pt is not None else "partial"

    return {
        "score": score,
        "operating_margin_recent_3m_pct": recent_margin,
        "operating_margin_previous_3m_pct": previous_margin,
        "operating_margin_decline_pt": margin_decline_pt,
        "operating_profit_recent_3m_krw": sum_field(recent, "operating_profit") if len(recent) == 3 else None,
        "consecutive_negative_months": consecutive_negative,
        "labor_ratio_recent_3m": labor_ratio_recent,
        "coupon_ratio_recent_3m": coupon_ratio_recent,
        "delivery_ratio_recent_3m": delivery_ratio_recent,
        "loan_interest_trend": loan_trend,
        "cogs_clamped_months": sorted(mm.month for mm in metrics if mm.cogs_clamped),
        "recent_period_missing_months": recent_missing,
        "status": status,
    }


# --------------------------------------------------------------------------- #
# SR-04  review response (auxiliary — not in composite)
# --------------------------------------------------------------------------- #
def calculate_review_signal(
    reviews: ReviewsInput | None, as_of: date, policy: RiskPolicy = DEFAULT_POLICY
) -> dict[str, Any]:
    if reviews is None or not reviews.records:
        return {"status": "missing", "sub_score": None, "source": getattr(reviews, "source", None)}

    recent_cut = as_of - timedelta(days=90)
    prev_cut = as_of - timedelta(days=180)

    recent = [r for r in reviews.records if recent_cut < r.written_at <= as_of]
    previous = [r for r in reviews.records if prev_cut < r.written_at <= recent_cut]

    if not recent:
        return {
            "status": "missing",
            "sub_score": None,
            "source": reviews.source,
            "note": "최근 90일 리뷰 없음",
        }

    def ratios(rows: list[Any]) -> tuple[int, int, int, float | None, float | None]:
        total = len(rows)
        neg = sum(1 for r in rows if r.sentiment_label == "부정")
        pos = sum(1 for r in rows if r.sentiment_label == "긍정")
        return (
            total,
            neg,
            pos,
            round(neg / total, 4) if total else None,
            round(pos / total, 4) if total else None,
        )

    r_total, r_neg, r_pos, r_neg_ratio, r_pos_ratio = ratios(recent)
    _, _, _, p_neg_ratio, _ = ratios(previous)

    sub_score = r_neg_ratio / policy.negative_review_reference_ratio * 100
    delta = None
    if p_neg_ratio is not None:
        delta = round(r_neg_ratio - p_neg_ratio, 4)
        sub_score += max(0.0, delta) / policy.negative_review_reference_ratio * 20
    sub_score = clamp_score(sub_score)

    direction = "stable"
    if delta is not None:
        if delta > policy.review_direction_delta:
            direction = "worsening"
        elif delta < -policy.review_direction_delta:
            direction = "improving"

    watchlist_flag = sub_score >= 60.0 or (direction == "worsening" and r_neg_ratio >= 0.35)

    return {
        "status": "calculated",
        "sub_score": sub_score,
        "direction": direction,
        "review_count": r_total,
        "negative_count": r_neg,
        "positive_count": r_pos,
        "negative_ratio": r_neg_ratio,
        "positive_ratio": r_pos_ratio,
        "negative_ratio_previous_period": p_neg_ratio,
        "negative_ratio_delta": delta,
        "watchlist_flag": watchlist_flag,
        "source": reviews.source,
        "model": "deterministic-count-v0",
    }
