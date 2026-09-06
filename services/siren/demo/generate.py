"""가맹점별 24개월 운영보고서 + 리뷰 합성 생성 (실 추세 앵커).

모든 무작위성은 Random(seed=branch_id) 로 고정 → 완전 재현 가능.
매출·손익 = synthetic_pos / synthetic_self_reported, 리뷰 = synthetic_reviews.
"""

from __future__ import annotations

import random
from datetime import date, timedelta

from .reviews_templates import NEGATIVE, NEUTRAL, POSITIVE
from .scenarios import MONTHS, SCENARIOS, SPARSE_MISSING_MONTHS

DEMO_START = (2024, 4)
BASE_QUARTER = "2024Q2"

# 업종별 (기준 월 순매출[원], 식자재 비중, 주류 비중)
INDUSTRY_BASE = {
    "CS100001": (52_000_000, 0.27, 0.030),  # 한식
    "CS100002": (46_000_000, 0.26, 0.025),  # 중식
    "CS100003": (58_000_000, 0.29, 0.040),  # 일식
    "CS100004": (55_000_000, 0.28, 0.045),  # 양식
    "CS100005": (40_000_000, 0.25, 0.005),  # 제과
    "CS100006": (48_000_000, 0.27, 0.005),  # 패스트푸드
    "CS100007": (44_000_000, 0.30, 0.025),  # 치킨
    "CS100008": (33_000_000, 0.26, 0.005),  # 분식
    "CS100009": (50_000_000, 0.24, 0.100),  # 호프-간이주점
    "CS100010": (38_000_000, 0.23, 0.010),  # 커피-음료
}
FIXED_LABOR_RATIO = 0.13  # base_net 대비 고정 인건비 (매출 하락 시 labor_ratio 상승)

SEASONAL = {  # 월 -> 계절 배수
    1: 0.92, 2: 0.90, 3: 0.99, 4: 1.00, 5: 1.05, 6: 1.01,
    7: 0.98, 8: 0.97, 9: 1.00, 10: 1.04, 11: 1.03, 12: 1.10,
}


def _month_at(t: int) -> tuple[int, int]:
    idx = (DEMO_START[0] * 12 + DEMO_START[1] - 1) + t
    return idx // 12, idx % 12 + 1


def _quarter_of(year: int, month: int) -> str:
    return f"{year}Q{(month - 1) // 3 + 1}"


def _split(total: float, weights: list[float]) -> list[int]:
    s = sum(weights)
    raw = [total * w / s for w in weights]
    out = [int(round(x)) for x in raw]
    out[-1] += int(round(total)) - sum(out)
    return [max(0, v) for v in out]


def _market_index(sales_quarters: list[dict]) -> dict[str, float]:
    by_q = {q["quarter"]: q["amount_krw"] for q in sales_quarters}
    base = by_q.get(BASE_QUARTER) or next((v for v in by_q.values() if v), 1.0)
    idx: dict[str, float] = {}
    last = 1.0
    for y in range(2024, 2027):
        for qn in range(1, 5):
            q = f"{y}Q{qn}"
            if q in by_q and by_q[q] > 0 and base > 0:
                last = by_q[q] / base
            idx[q] = last
    return idx


def generate_branch(branch: dict) -> dict:
    rnd = random.Random(f"risk-siren-demo::{branch['branch_id']}")
    scenario = SCENARIOS[branch["scenario"]]
    base_net, food_frac, alcohol_frac = INDUSTRY_BASE[branch["industry_code"]]
    base_net *= rnd.uniform(0.9, 1.12)
    rent = round(base_net * rnd.uniform(0.09, 0.115) / 100_000) * 100_000
    midx = _market_index(branch["sales_quarters"])

    reports: list[dict] = []
    missing_months: list[str] = []
    for t in range(MONTHS):
        year, month = _month_at(t)
        month_str = f"{year:04d}-{month:02d}"
        if scenario.name == "sparse" and t in SPARSE_MISSING_MONTHS:
            missing_months.append(month_str)
            continue

        mi = midx.get(_quarter_of(year, month), 1.0)
        mi_damped = 0.7 + 0.3 * max(0.75, min(1.4, mi))  # 앵커는 반영하되 시나리오를 상쇄하지 않게
        noise = rnd.uniform(0.97, 1.03)
        net = base_net * mi_damped * scenario.sales_drift(t) * SEASONAL[month] * noise

        coupon_ratio = scenario.coupon_ratio(t) * rnd.uniform(0.9, 1.1)
        refund = net * rnd.uniform(0.003, 0.008)
        gross = (net + refund) / max(0.01, 1 - coupon_ratio)
        coupon = gross - net - refund

        deliv_share = min(0.8, max(0.05, scenario.delivery_ratio(t) * rnd.uniform(0.95, 1.05)))
        delivery = gross * deliv_share
        remainder = gross - delivery
        hall = remainder * rnd.uniform(0.55, 0.68)
        takeout = remainder - hall

        h = _split(hall, [0.62, 0.10, 0.28])
        d = _split(delivery, [0.55, 0.35, 0.10])
        k = _split(takeout, [0.55, 0.15, 0.30])

        food = net * (food_frac + rnd.uniform(-0.015, 0.02))
        sub = net * rnd.uniform(0.008, 0.015)
        alcohol = net * alcohol_frac * rnd.uniform(0.85, 1.15)
        beverage = net * rnd.uniform(0.020, 0.032)
        inv_begin = net * rnd.uniform(0.09, 0.12)
        inv_end = inv_begin * rnd.uniform(0.95, 1.06)

        labor_ratio_t = scenario.labor_ratio(t) * rnd.uniform(0.97, 1.03)
        fixed_labor = base_net * FIXED_LABOR_RATIO
        var_labor = max(net * 0.05, net * (labor_ratio_t - FIXED_LABOR_RATIO))
        labor_total = fixed_labor + var_labor
        lab = _split(labor_total, [0.45, 0.33, 0.13, 0.05, 0.04])

        platform_fee = delivery * rnd.uniform(0.10, 0.125)
        delivery_agency = delivery * rnd.uniform(0.035, 0.06)
        supplies = net * rnd.uniform(0.010, 0.016)
        utilities = net * rnd.uniform(0.028, 0.04)
        marketing = net * (0.007 + (0.018 if scenario.name in ("delivery_trap", "debt_spiral") else 0.003)) * rnd.uniform(0.85, 1.15)

        card_base = h[0] + h[2] + k[0] + k[2]
        card_fee = card_base * rnd.uniform(0.019, 0.023)

        reports.append(
            {
                "month": month_str,
                "sales": {
                    "hall": {"credit": h[0], "cash": h[1], "simple_pay": h[2]},
                    "delivery": {"baemin": d[0], "coupang_eats": d[1], "other": d[2]},
                    "takeout": {"credit": k[0], "cash": k[1], "simple_pay": k[2]},
                },
                "deductions": {
                    "customer_refund": int(round(refund)),
                    "own_coupon_discount": int(round(max(0, coupon))),
                },
                "cogs": {
                    "food_ingredients": int(round(food)),
                    "sub_materials": int(round(sub)),
                    "alcohol": int(round(alcohol)),
                    "beverage": int(round(beverage)),
                    "inventory_begin": int(round(inv_begin)),
                    "inventory_end": int(round(inv_end)),
                },
                "labor": {
                    "fulltime": lab[0], "parttime": lab[1], "four_major_insurance": lab[2],
                    "meal_welfare": lab[3], "short_term": lab[4],
                },
                "variable": {
                    "platform_fee": int(round(platform_fee)),
                    "delivery_agency_fee": int(round(delivery_agency)),
                    "supplies": int(round(supplies)),
                    "utilities": int(round(utilities)),
                    "marketing_ad": int(round(marketing)),
                },
                "opex": {
                    "rent_mgmt": rent,
                    "equipment_rental": 450_000,
                    "telecom_it": 220_000,
                    "tax_bookkeeping": 280_000,
                    "insurance": 200_000,
                    "card_fee": int(round(card_fee)),
                },
                "finance": {
                    "loan_interest": int(round(scenario.loan_interest(t))),
                    "other_misc": int(round(rnd.uniform(200_000, 380_000))),
                },
                "sales_source": "synthetic_pos",
                "cost_source": "synthetic_self_reported",
            }
        )

    reviews = _generate_reviews(branch, rnd, scenario)
    return {
        "branch_id": branch["branch_id"],
        "scenario": branch["scenario"],
        "missing_months": missing_months,
        "branch_reports": reports,
        "reviews": reviews,
    }


# 리뷰가 전혀 없는 가맹점 (시나리오와 무관하게 분산) — "리뷰 없는 집이 많다"
NO_REVIEW_BRANCH_INDEX = {1, 4, 8, 11, 14, 17}


def _generate_reviews(branch: dict, rnd: random.Random, scenario) -> dict | None:
    idx = int(branch["branch_id"].split("-")[-1]) - 1
    if idx in NO_REVIEW_BRANCH_INDEX:
        return None
    volume = 20 + int(rnd.random() ** 1.3 * 120)

    worsening = scenario.name in ("sales_decline", "delivery_trap", "debt_spiral", "slow_margin_squeeze")
    records = []
    start = date(DEMO_START[0], DEMO_START[1], 1)
    span_days = MONTHS * 30
    for i in range(volume):
        # 최근으로 갈수록 리뷰 밀도↑
        frac = (rnd.random() ** 0.55)
        d = start + timedelta(days=int(frac * span_days))
        neg_p = 0.12 + (0.4 * frac if worsening else 0.02)
        pos_p = 0.75 - (0.45 * frac if worsening else 0.0)
        roll = rnd.random()
        if roll < neg_p:
            label, pool, rating = "부정", NEGATIVE, rnd.choice([1, 2, 2])
        elif roll < neg_p + max(0.05, pos_p):
            label, pool, rating = "긍정", POSITIVE, rnd.choice([4, 5, 5])
        else:
            label, pool, rating = "중립", NEUTRAL, 3
        records.append(
            {
                "review_id": f"{branch['branch_id']}-rv-{i + 1:04d}",
                "branch_id": branch["branch_id"],
                "written_at": d.isoformat(),
                "rating": rating,
                "text": rnd.choice(pool),
                "sentiment_label": label,
            }
        )
    records.sort(key=lambda r: r["written_at"])
    return {"source": "synthetic_reviews", "records": records}
