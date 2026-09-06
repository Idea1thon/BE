"""Map provider snapshots into the canonical ``RiskSirenRequest`` contract."""

from __future__ import annotations

import copy
import datetime as dt
from typing import Any

from ..models import SirenAnalyzeTrigger
from ..providers.fmp_provider import BranchSnapshot
from ..providers.ideaton_provider import MarketSnapshot


FIELD_TO_SIREN: dict[str, tuple[str, ...]] = {
    "HALL_CARD": ("sales", "hall", "credit"),
    "HALL_CASH": ("sales", "hall", "cash"),
    "HALL_EASYPAY": ("sales", "hall", "simple_pay"),
    "DLV_BAEMIN": ("sales", "delivery", "baemin"),
    "DLV_COUPANG": ("sales", "delivery", "coupang_eats"),
    "DLV_ETC": ("sales", "delivery", "other"),
    "TOGO_CARD": ("sales", "takeout", "credit"),
    "TOGO_CASH": ("sales", "takeout", "cash"),
    "TOGO_EASYPAY": ("sales", "takeout", "simple_pay"),
    "DED_REFUND": ("deductions", "customer_refund"),
    "DED_COUPON": ("deductions", "own_coupon_discount"),
    "MAT_FOOD": ("cogs", "food_ingredients"),
    "MAT_SUB": ("cogs", "sub_materials"),
    "BEV_ALCOHOL": ("cogs", "alcohol"),
    "BEV_DRINK": ("cogs", "beverage"),
    "INV_BEGIN": ("cogs", "inventory_begin"),
    "INV_END": ("cogs", "inventory_end"),
    "LAB_FULLTIME": ("labor", "fulltime"),
    "LAB_PARTTIME": ("labor", "parttime"),
    "LAB_INSURANCE": ("labor", "four_major_insurance"),
    "LAB_WELFARE": ("labor", "meal_welfare"),
    "LAB_SHORTTERM": ("labor", "short_term"),
    "VAR_PLATFORM_FEE": ("variable", "platform_fee"),
    "VAR_DELIVERY_FEE": ("variable", "delivery_agency_fee"),
    "VAR_SUPPLIES": ("variable", "supplies"),
    "VAR_UTILITY": ("variable", "utilities"),
    "VAR_MARKETING": ("variable", "marketing_ad"),
    "OPS_RENT": ("opex", "rent_mgmt"),
    "OPS_RENTAL": ("opex", "equipment_rental"),
    "OPS_TELECOM": ("opex", "telecom_it"),
    "OPS_ACCOUNTING": ("opex", "tax_bookkeeping"),
    "OPS_INSURANCE": ("opex", "insurance"),
    "OPS_CARD_FEE": ("opex", "card_fee"),
    "FIN_LOAN_INTEREST": ("finance", "loan_interest"),
    "FIN_MISC": ("finance", "other_misc"),
}


class IncompleteMonthlyReport(ValueError):
    """A source report is missing one or more form fields.

    Missing fields are not converted to zero. The orchestrator drops that month
    so the calculator can surface a partial window instead of a fabricated value.
    """

    def __init__(self, month: object, missing_fields: list[str]) -> None:
        self.month = str(month)
        self.missing_fields = tuple(missing_fields)
        super().__init__(
            f"report {self.month} is missing required fields: {', '.join(missing_fields)}"
        )

_EMPTY_BLOCKS: dict[str, Any] = {
    "sales": {
        "hall": {"credit": 0.0, "cash": 0.0, "simple_pay": 0.0},
        "delivery": {"baemin": 0.0, "coupang_eats": 0.0, "other": 0.0},
        "takeout": {"credit": 0.0, "cash": 0.0, "simple_pay": 0.0},
    },
    "deductions": {"customer_refund": 0.0, "own_coupon_discount": 0.0},
    "cogs": {
        "food_ingredients": 0.0,
        "sub_materials": 0.0,
        "alcohol": 0.0,
        "beverage": 0.0,
        "inventory_begin": 0.0,
        "inventory_end": 0.0,
    },
    "labor": {
        "fulltime": 0.0,
        "parttime": 0.0,
        "four_major_insurance": 0.0,
        "meal_welfare": 0.0,
        "short_term": 0.0,
    },
    "variable": {
        "platform_fee": 0.0,
        "delivery_agency_fee": 0.0,
        "supplies": 0.0,
        "utilities": 0.0,
        "marketing_ad": 0.0,
    },
    "opex": {
        "rent_mgmt": 0.0,
        "equipment_rental": 0.0,
        "telecom_it": 0.0,
        "tax_bookkeeping": 0.0,
        "insurance": 0.0,
        "card_fee": 0.0,
    },
    "finance": {"loan_interest": 0.0, "other_misc": 0.0},
}


def _blank_blocks() -> dict[str, Any]:
    return copy.deepcopy(_EMPTY_BLOCKS)


def _source_tag(value: object, *, synthetic: bool = False) -> str:
    raw = str(getattr(value, "value", value) or "MANUAL").upper()
    if raw == "POS":
        return "synthetic_pos" if synthetic else "pos"
    if raw == "MANUAL":
        return "synthetic_self_reported" if synthetic else "self_reported"
    raise ValueError(f"unsupported FMP input_source: {value!r}")


def _monthly_report(report: dict[str, Any]) -> dict[str, Any]:
    items = report.get("items") or {}
    unknown = sorted(set(items) - set(FIELD_TO_SIREN))
    if unknown:
        raise ValueError(f"unsupported FMP input fields: {', '.join(unknown)}")

    missing = sorted(set(FIELD_TO_SIREN) - set(items))
    if missing:
        raise IncompleteMonthlyReport(report.get("month"), missing)

    blocks = _blank_blocks()
    for code, amount in items.items():
        target = blocks
        path = FIELD_TO_SIREN[code]
        for key in path[:-1]:
            target = target[key]
        target[path[-1]] = float(amount)
    # Synthetic fixtures can carry this marker without changing the FMP
    # input_source enum. Preserve it so a report without reviews still
    # discloses that its financial inputs are synthetic.
    source = _source_tag(
        report.get("input_source"),
        synthetic=bool(report.get("synthetic", False)),
    )
    month = report["month"]
    if isinstance(month, dt.date):
        month_text = month.strftime("%Y-%m")
    else:
        month_text = str(month)[:7]
    return {
        "month": month_text,
        **blocks,
        "sales_source": source,
        "cost_source": source,
    }


def build_risk_request(
    trigger: SirenAnalyzeTrigger,
    branch: BranchSnapshot,
    market: MarketSnapshot,
    reviews: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the canonical request after all source reads have completed."""

    if trigger.branch_id != branch.branch_id:
        raise ValueError("FMP branch does not match the trigger branch_id")
    if trigger.franchise_id != branch.franchise_id:
        raise ValueError("FMP branch does not match the trigger franchise_id")

    mapped_reports: list[dict[str, Any]] = []
    excluded_report_months: list[str] = []
    for report in branch.reports:
        try:
            mapped_reports.append(_monthly_report(report))
        except IncompleteMonthlyReport as exc:
            excluded_report_months.append(exc.month[:7])

    payload: dict[str, Any] = {
        "request_id": trigger.request_id,
        "franchise_id": branch.franchise_id,
        "branch_id": branch.branch_id,
        "brand_name": branch.branch_name,
        "as_of": trigger.as_of.isoformat(),
        "industry_code": branch.industry_code,
        "location": market.location,
        "branch_reports": mapped_reports,
        "excluded_report_months": sorted(set(excluded_report_months)),
        # Hosted LLM and dispatch are intentionally off. explanation_only uses
        # the local deterministic template and never calls a model. Preserve the
        # caller's explicit grade policy so trigger and canonical paths behave
        # identically.
        "options": {
            "llm_mode": trigger.options.llm_mode,
            "send_notifications": bool(trigger.options.send_notifications),
            "grade_policy": trigger.options.grade_policy,
        },
    }
    if market.market_data is not None:
        payload["market_data"] = market.market_data
    if reviews is not None:
        payload["reviews"] = reviews
    if branch.franchise_closure is not None:
        payload["franchise_closure"] = branch.franchise_closure
    return payload
