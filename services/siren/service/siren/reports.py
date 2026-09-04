"""Deterministic derivation of monthly P&L metrics from operating reports.

Pure functions. No IO. Used by the branch-layer signal calculators
(SR-02.branch sales decline, SR-05 profitability deterioration).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

from .models import BranchMonthlyReport


def _month_key(year: int, month: int) -> str:
    return f"{year:04d}-{month:02d}"


def _month_shift(year: int, month: int, offset: int) -> tuple[int, int]:
    index = year * 12 + month - 1 + offset
    return index // 12, index % 12 + 1


@dataclass(frozen=True)
class MonthlyMetrics:
    month: str
    gross_sales: float
    net_sales: float
    cogs: float
    gross_profit: float
    labor_total: float
    variable_total: float
    opex_total: float
    finance_total: float
    operating_profit: float
    operating_margin: float | None
    labor_ratio: float | None
    delivery_ratio: float | None
    coupon_ratio: float | None
    loan_interest: float
    cogs_clamped: bool


def derive_month(report: BranchMonthlyReport) -> MonthlyMetrics:
    s = report.sales
    hall = s.hall.credit + s.hall.cash + s.hall.simple_pay
    delivery = s.delivery.baemin + s.delivery.coupang_eats + s.delivery.other
    takeout = s.takeout.credit + s.takeout.cash + s.takeout.simple_pay
    gross_sales = hall + delivery + takeout

    deductions = report.deductions.customer_refund + report.deductions.own_coupon_discount
    net_sales = gross_sales - deductions

    c = report.cogs
    raw_cogs = (
        c.food_ingredients
        + c.sub_materials
        + c.alcohol
        + c.beverage
        + (c.inventory_begin - c.inventory_end)
    )
    cogs_clamped = raw_cogs < 0
    cogs = max(0.0, raw_cogs)

    gross_profit = net_sales - cogs
    labor_total = (
        report.labor.fulltime
        + report.labor.parttime
        + report.labor.four_major_insurance
        + report.labor.meal_welfare
        + report.labor.short_term
    )
    variable_total = (
        report.variable.platform_fee
        + report.variable.delivery_agency_fee
        + report.variable.supplies
        + report.variable.utilities
        + report.variable.marketing_ad
    )
    opex_total = (
        report.opex.rent_mgmt
        + report.opex.equipment_rental
        + report.opex.telecom_it
        + report.opex.tax_bookkeeping
        + report.opex.insurance
        + report.opex.card_fee
    )
    finance_total = report.finance.loan_interest + report.finance.other_misc
    operating_profit = (
        gross_profit - labor_total - variable_total - opex_total - finance_total
    )

    def ratio(numerator: float) -> float | None:
        if net_sales <= 0:
            return None
        return round(numerator / net_sales, 4)

    return MonthlyMetrics(
        month=report.month,
        gross_sales=round(gross_sales, 2),
        net_sales=round(net_sales, 2),
        cogs=round(cogs, 2),
        gross_profit=round(gross_profit, 2),
        labor_total=round(labor_total, 2),
        variable_total=round(variable_total, 2),
        opex_total=round(opex_total, 2),
        finance_total=round(finance_total, 2),
        operating_profit=round(operating_profit, 2),
        operating_margin=ratio(operating_profit),
        labor_ratio=ratio(labor_total),
        delivery_ratio=(round(delivery / gross_sales, 4) if gross_sales > 0 else None),
        coupon_ratio=(
            round(report.deductions.own_coupon_discount / gross_sales, 4)
            if gross_sales > 0
            else None
        ),
        loan_interest=round(report.finance.loan_interest, 2),
        cogs_clamped=cogs_clamped,
    )


def build_metric_series(
    reports: list[BranchMonthlyReport], as_of: date
) -> tuple[list[MonthlyMetrics], list[str]]:
    """Return (metrics up to and including as_of month, excluded future months)."""
    cutoff = (as_of.year, as_of.month)
    kept: list[MonthlyMetrics] = []
    future: list[str] = []
    for report in reports:
        y, m = int(report.month[:4]), int(report.month[5:])
        if (y, m) > cutoff:
            future.append(report.month)
            continue
        kept.append(derive_month(report))
    kept.sort(key=lambda x: x.month)
    return kept, sorted(future)


def window_ending(
    metrics: list[MonthlyMetrics], end: tuple[int, int], length: int
) -> tuple[list[MonthlyMetrics], list[str]]:
    """Contiguous ``length``-month window ending at ``end`` (inclusive)."""
    by_month = {mm.month: mm for mm in metrics}
    picked: list[MonthlyMetrics] = []
    missing: list[str] = []
    for offset in range(-(length - 1), 1):
        y, m = _month_shift(end[0], end[1], offset)
        key = _month_key(y, m)
        if key in by_month:
            picked.append(by_month[key])
        else:
            missing.append(key)
    return picked, missing


def sum_field(window: list[MonthlyMetrics], field: str) -> float:
    return round(sum(getattr(mm, field) for mm in window), 2)


def mean_ratio(window: list[MonthlyMetrics], field: str) -> float | None:
    values = [getattr(mm, field) for mm in window if getattr(mm, field) is not None]
    if not values:
        return None
    return round(sum(values) / len(values), 4)
