"""데모 시나리오 — 24개월에 걸친 가맹점 손익 궤적.

각 시나리오는 월 인덱스 t (0..23) 에 대해 배수·비율을 돌려준다.
결정론: 모든 무작위성은 build 단계에서 seed=branch_id 로 고정된 Random 이 담당.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

MONTHS = 24


def _lerp(a: float, b: float, frac: float) -> float:
    return a + (b - a) * max(0.0, min(1.0, frac))


@dataclass(frozen=True)
class ScenarioProfile:
    name: str
    label: str
    # net-sales monthly drift multiplier vs market anchor (t -> factor)
    sales_drift: Callable[[int], float]
    # target operating margin (t -> fraction)
    target_margin: Callable[[int], float]
    # labor cost as fraction of net sales
    labor_ratio: Callable[[int], float]
    # own-coupon discount as fraction of gross sales
    coupon_ratio: Callable[[int], float]
    # delivery share of gross sales
    delivery_ratio: Callable[[int], float]
    # monthly loan interest in KRW
    loan_interest: Callable[[int], float]
    expect: str  # what the siren should eventually show


def _flat(v: float) -> Callable[[int], float]:
    return lambda t: v


def _ramp(a: float, b: float, start: int = 0, end: int = MONTHS - 1) -> Callable[[int], float]:
    return lambda t: _lerp(a, b, (t - start) / max(1, end - start))


def _accel(a: float, b: float) -> Callable[[int], float]:
    """느리게 시작해 최근 구간에서 급격히 (t^2) — 가속 붕괴."""
    return lambda t: a + (b - a) * (t / (MONTHS - 1)) ** 2


SCENARIOS: dict[str, ScenarioProfile] = {
    "healthy": ScenarioProfile(
        name="healthy",
        label="정상 — 시장 추세 추종, 마진 안정",
        sales_drift=_ramp(1.0, 1.04),
        target_margin=_ramp(0.11, 0.09),
        labor_ratio=_flat(0.27),
        coupon_ratio=_flat(0.02),
        delivery_ratio=_flat(0.28),
        loan_interest=_flat(0.0),
        expect="정상",
    ),
    "slow_margin_squeeze": ScenarioProfile(
        name="slow_margin_squeeze",
        label="완만한 마진 잠식 — 매출 유지, 인건비·비용 상승",
        sales_drift=_ramp(1.0, 0.99),
        target_margin=_ramp(0.09, -0.03),
        labor_ratio=_ramp(0.28, 0.41),
        coupon_ratio=_ramp(0.02, 0.05),
        delivery_ratio=_flat(0.30),
        loan_interest=_flat(0.0),
        expect="주의",
    ),
    "sales_decline": ScenarioProfile(
        name="sales_decline",
        label="매출 감소 — 시장보다 빠른 순매출 하락 (가속)",
        sales_drift=_accel(1.0, 0.58),
        target_margin=_ramp(0.08, -0.06),
        labor_ratio=_ramp(0.28, 0.40),
        coupon_ratio=_ramp(0.02, 0.06),
        delivery_ratio=_flat(0.32),
        loan_interest=_flat(0.0),
        expect="주의~위험",
    ),
    "delivery_trap": ScenarioProfile(
        name="delivery_trap",
        label="배달 의존 심화 — 수수료·포장인력으로 마진 붕괴",
        sales_drift=_accel(1.0, 0.82),
        target_margin=_ramp(0.08, -0.10),
        labor_ratio=_ramp(0.29, 0.39),
        coupon_ratio=_accel(0.03, 0.13),
        delivery_ratio=_ramp(0.24, 0.66),
        loan_interest=_flat(0.0),
        expect="주의~위험",
    ),
    "debt_spiral": ScenarioProfile(
        name="debt_spiral",
        label="부채 악순환 — 대출이자 증가 + 쿠폰 남발 + 적자",
        sales_drift=_accel(1.0, 0.60),
        target_margin=lambda t: _lerp(0.04, -0.20, (t / (MONTHS - 1)) ** 1.5),
        labor_ratio=_ramp(0.31, 0.45),
        coupon_ratio=_accel(0.03, 0.16),
        delivery_ratio=_ramp(0.30, 0.46),
        loan_interest=lambda t: 0.0 if t < 8 else _lerp(700_000, 3_200_000, (t - 8) / 15),
        expect="위험",
    ),
    "sparse": ScenarioProfile(
        name="sparse",
        label="보고 누락 — 최근 구간 운영보고서 없음",
        sales_drift=_ramp(1.0, 0.92),
        target_margin=_ramp(0.09, 0.0),
        labor_ratio=_ramp(0.30, 0.34),
        coupon_ratio=_flat(0.03),
        delivery_ratio=_flat(0.30),
        loan_interest=_flat(0.0),
        expect="partial (최근 구간 불완전)",
    ),
}


# 20개 데모 가맹점의 시나리오 배정 (결정론)
SCENARIO_ASSIGNMENT: list[str] = [
    "healthy", "healthy", "healthy", "healthy", "healthy",
    "slow_margin_squeeze", "slow_margin_squeeze", "slow_margin_squeeze", "slow_margin_squeeze",
    "sales_decline", "sales_decline", "sales_decline",
    "delivery_trap", "delivery_trap", "delivery_trap",
    "debt_spiral", "debt_spiral", "debt_spiral",
    "sparse", "sparse",
]

# sparse 시나리오에서 빠지는 월 인덱스 (최근 3개월 구간을 건드려 partial 유발)
SPARSE_MISSING_MONTHS: list[int] = [9, 17, 22]
