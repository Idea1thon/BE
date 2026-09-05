"""Risk-siren v1 request and response contracts.

Two-layer model:

* ``market_data``   — trade-area x industry aggregates from Seoul open data (real).
* ``branch_reports``— accumulated monthly operating reports (demo: synthetic).
* ``reviews``       — auxiliary signal, may be absent for many branches.

The contract preserves absence: a missing signal returns ``partial`` /
``missing_data`` rather than being treated as safe. Prediction-style fields
(success probability, closure probability, profit forecast, vacancy rate) are
rejected by ``extra="forbid"``.
"""

from __future__ import annotations

import re
from datetime import date
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


MONTH_PATTERN = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
QUARTER_PATTERN = re.compile(r"^\d{4}Q[1-4]$")


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class IndustryCode(str, Enum):
    korean = "CS100001"
    chinese = "CS100002"
    japanese = "CS100003"
    western = "CS100004"
    bakery = "CS100005"
    fastfood = "CS100006"
    chicken = "CS100007"
    bunsik = "CS100008"
    pub = "CS100009"
    cafe = "CS100010"


INDUSTRY_LABELS: dict[str, str] = {
    "CS100001": "한식음식점",
    "CS100002": "중식음식점",
    "CS100003": "일식음식점",
    "CS100004": "양식음식점",
    "CS100005": "제과점",
    "CS100006": "패스트푸드점",
    "CS100007": "치킨전문점",
    "CS100008": "분식전문점",
    "CS100009": "호프-간이주점",
    "CS100010": "커피-음료",
}


# --------------------------------------------------------------------------- #
# Location
# --------------------------------------------------------------------------- #
class BranchLocation(ContractModel):
    gu_code: str = Field(min_length=1)
    admin_dong_code: str | None = None
    trade_area_code: str = Field(min_length=1)
    x_5181: float
    y_5181: float


# --------------------------------------------------------------------------- #
# market_data — SR-01, SR-02.market, SR-03
# --------------------------------------------------------------------------- #
class MarketClosureQuarter(ContractModel):
    quarter: str
    active_count_end: int = Field(ge=0)
    new_openings: int = Field(ge=0)
    closures: int = Field(ge=0)
    quarter_status: Literal["완전", "부분"] = "완전"

    @model_validator(mode="after")
    def _q(self) -> "MarketClosureQuarter":
        if not QUARTER_PATTERN.fullmatch(self.quarter):
            raise ValueError("quarter must use YYYYQn format")
        return self


class MarketSalesQuarter(ContractModel):
    quarter: str
    amount_krw: float = Field(ge=0)
    txn_count: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def _q(self) -> "MarketSalesQuarter":
        if not QUARTER_PATTERN.fullmatch(self.quarter):
            raise ValueError("quarter must use YYYYQn format")
        return self


class MarketCompetition(ContractModel):
    radius_m: float | None = Field(default=None, gt=0)
    same_industry_new_recent_3m: int | None = Field(default=None, ge=0)
    same_industry_new_previous_3m: int | None = Field(default=None, ge=0)
    similar_industry_new_recent_3m: int | None = Field(default=None, ge=0)
    similar_industry_new_previous_3m: int | None = Field(default=None, ge=0)
    similar_industry_codes: list[str] = Field(default_factory=list)
    active_only: bool = True
    source: str | None = None


class MarketData(ContractModel):
    source: str = Field(min_length=1)
    closure_source: str | None = None  # 신호별 정확 출처 (없으면 source 사용)
    sales_source: str | None = None
    as_of_quarter: str | None = None
    closure_quarters: list[MarketClosureQuarter] = Field(default_factory=list)
    sales_quarters: list[MarketSalesQuarter] = Field(default_factory=list)
    competition: MarketCompetition | None = None

    @model_validator(mode="after")
    def _no_dupe_quarters(self) -> "MarketData":
        for label, items in (
            ("closure_quarters", [q.quarter for q in self.closure_quarters]),
            ("sales_quarters", [q.quarter for q in self.sales_quarters]),
        ):
            if len(items) != len(set(items)):
                raise ValueError(f"{label} must not contain duplicate quarters")
        return self


# --------------------------------------------------------------------------- #
# branch_reports — SR-02.branch, SR-05
# --------------------------------------------------------------------------- #
class HallSales(ContractModel):
    credit: float = Field(ge=0)
    cash: float = Field(ge=0)
    simple_pay: float = Field(ge=0)


class DeliverySales(ContractModel):
    baemin: float = Field(ge=0)
    coupang_eats: float = Field(ge=0)
    other: float = Field(ge=0)


class TakeoutSales(ContractModel):
    credit: float = Field(ge=0)
    cash: float = Field(ge=0)
    simple_pay: float = Field(ge=0)


class SalesBlock(ContractModel):
    hall: HallSales
    delivery: DeliverySales
    takeout: TakeoutSales


class Deductions(ContractModel):
    customer_refund: float = Field(ge=0)
    own_coupon_discount: float = Field(ge=0)


class Cogs(ContractModel):
    food_ingredients: float = Field(ge=0)
    sub_materials: float = Field(ge=0)
    alcohol: float = Field(ge=0)
    beverage: float = Field(ge=0)
    inventory_begin: float = Field(ge=0)
    inventory_end: float = Field(ge=0)


class Labor(ContractModel):
    fulltime: float = Field(ge=0)
    parttime: float = Field(ge=0)
    four_major_insurance: float = Field(ge=0)
    meal_welfare: float = Field(ge=0)
    short_term: float = Field(ge=0)


class VariableCost(ContractModel):
    platform_fee: float = Field(ge=0)
    delivery_agency_fee: float = Field(ge=0)
    supplies: float = Field(ge=0)
    utilities: float = Field(ge=0)
    marketing_ad: float = Field(ge=0)


class Opex(ContractModel):
    rent_mgmt: float = Field(ge=0)
    equipment_rental: float = Field(ge=0)
    telecom_it: float = Field(ge=0)
    tax_bookkeeping: float = Field(ge=0)
    insurance: float = Field(ge=0)
    card_fee: float = Field(ge=0)


class Finance(ContractModel):
    loan_interest: float = Field(ge=0)
    other_misc: float = Field(ge=0)


SourceTag = Literal["pos", "synthetic_pos", "self_reported", "synthetic_self_reported"]


class BranchMonthlyReport(ContractModel):
    month: str
    sales: SalesBlock
    deductions: Deductions
    cogs: Cogs
    labor: Labor
    variable: VariableCost
    opex: Opex
    finance: Finance
    sales_source: SourceTag = "self_reported"
    cost_source: SourceTag = "self_reported"

    @model_validator(mode="after")
    def _month(self) -> "BranchMonthlyReport":
        if not MONTH_PATTERN.fullmatch(self.month):
            raise ValueError("month must use YYYY-MM format")
        return self


# --------------------------------------------------------------------------- #
# reviews — SR-04 auxiliary
# --------------------------------------------------------------------------- #
SentimentLabel = Literal["긍정", "부정", "중립"]


class ReviewRecord(ContractModel):
    review_id: str = Field(min_length=1)
    branch_id: str | None = None  # 점포 연결 키 (RS-T07 검증용; 불일치 시 uncertainty)
    written_at: date
    rating: int = Field(ge=1, le=5)
    text: str = Field(min_length=1)
    sentiment_label: SentimentLabel


class ReviewsInput(ContractModel):
    source: Literal["synthetic_reviews", "naver_place", "kakao_map", "delivery_app"]
    records: list[ReviewRecord] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# options / request / response
# --------------------------------------------------------------------------- #
class RiskOptions(ContractModel):
    llm_mode: Literal["disabled", "explanation_only", "explanation_and_review_assist"] = (
        "explanation_only"
    )
    send_notifications: bool = False


class FranchiseClosureYear(ContractModel):
    """브랜드 연간 실폐업 집계. 계약 종료·해지 건수를 대신 넣지 않는다."""

    franchise_id: str = Field(min_length=1)
    year: int = Field(ge=1, le=9999, strict=True)
    previous_year_end_count: int = Field(ge=0, strict=True)
    new_openings: int = Field(ge=0, strict=True)
    closures: int = Field(ge=0, strict=True)
    source: str = Field(min_length=1)
    synthetic: bool

    @model_validator(mode="after")
    def _consistent_population(self) -> "FranchiseClosureYear":
        if self.closures > self.previous_year_end_count + self.new_openings:
            raise ValueError("closures exceeds the annual operating population")
        return self


class FranchiseClosureResult(ContractModel):
    status: Literal["missing", "calculated", "partial", "not_calculable"]
    year: int | None = None
    previous_year_end_count: int | None = None
    new_openings: int | None = None
    closures: int | None = None
    operating_base_count: int | None = None
    operating_base_rate_pct: float | None = None
    previous_year_base_rate_pct: float | None = None
    source: str | None = None
    synthetic: bool | None = None
    formula_version: str = "franchise-annual-v1"
    formula_authority: str = "project_defined"


class RiskSirenRequest(ContractModel):
    request_id: str = Field(min_length=1)
    franchise_id: str = Field(min_length=1)
    branch_id: str = Field(min_length=1)
    brand_name: str | None = None
    as_of: date
    industry_code: IndustryCode
    location: BranchLocation
    market_data: MarketData | None = None
    branch_reports: list[BranchMonthlyReport] = Field(default_factory=list)
    reviews: ReviewsInput | None = None
    options: RiskOptions = Field(default_factory=RiskOptions)
    franchise_closure: FranchiseClosureYear | None = None

    @model_validator(mode="after")
    def _no_dupe_months(self) -> "RiskSirenRequest":
        if self.franchise_closure is not None:
            if self.franchise_closure.franchise_id != self.franchise_id:
                raise ValueError("franchise_closure must belong to the requested franchise")
            if self.franchise_closure.year >= self.as_of.year:
                raise ValueError("franchise_closure requires a completed calendar year before as_of")
        months = [r.month for r in self.branch_reports]
        if len(months) != len(set(months)):
            raise ValueError("branch_reports must not contain duplicate months")
        return self


class RiskSirenResponse(ContractModel):
    request_id: str
    branch: dict
    risk: dict
    layers: dict
    components: dict
    review_signal: dict
    evidence: list[dict]
    missing_data: list[dict]
    uncertainty: list[str]
    excluded_future_months: list[str]
    data_provenance: dict
    alert: dict
    financial_products: dict
    explanation: dict
    projections: dict
    franchise_closure: FranchiseClosureResult


# --------------------------------------------------------------------------- #
# hq-summary
# --------------------------------------------------------------------------- #
class HqSection(BaseModel):
    # 집계에서 소비하는 필드만 검증하고 분석 응답의 나머지 필드는 무시한다.
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)


class HqBranch(HqSection):
    franchise_id: str = Field(min_length=1)
    branch_id: str = Field(min_length=1)
    as_of: date


class HqRisk(HqSection):
    score: float | None = Field(ge=0, le=100, strict=True, allow_inf_nan=False)
    grade: Literal["정상", "주의", "위험"] | None
    calculation_status: Literal["calculated", "partial"]

    @model_validator(mode="after")
    def _consistent_result(self) -> "HqRisk":
        if self.calculation_status == "calculated":
            if self.score is None or self.grade is None:
                raise ValueError("calculated risk requires score and grade")
        elif self.score is not None or self.grade is not None:
            raise ValueError("partial risk must not claim a composite score or grade")
        return self


class HqProfitability(HqSection):
    consecutive_negative_months: int = Field(default=0, ge=0, strict=True)


class HqComponents(HqSection):
    profitability: HqProfitability = Field(default_factory=HqProfitability)


class HqReview(HqSection):
    watchlist_flag: bool = Field(default=False, strict=True)


class HqProvenance(HqSection):
    contains_synthetic: bool = Field(strict=True)


class HqAlert(HqSection):
    should_fire: bool = Field(default=False, strict=True)


class HqBranchResult(HqSection):
    branch: HqBranch
    risk: HqRisk
    components: HqComponents
    review_signal: HqReview
    data_provenance: HqProvenance
    alert: HqAlert = Field(default_factory=HqAlert)


class HqSummaryRequest(ContractModel):
    request_id: str = Field(min_length=1)
    franchise_id: str = Field(min_length=1)
    as_of: date
    branch_results: list[HqBranchResult] = Field(default_factory=list)

    @model_validator(mode="after")
    def _matching_snapshots(self) -> "HqSummaryRequest":
        seen: set[str] = set()
        for result in self.branch_results:
            branch = result.branch
            if branch.franchise_id != self.franchise_id:
                raise ValueError("every branch result must belong to the requested franchise")
            if branch.as_of != self.as_of:
                raise ValueError("every branch result must have the same as_of as the summary")
            if branch.branch_id in seen:
                raise ValueError("branch_results must not contain duplicate branches")
            seen.add(branch.branch_id)
        return self


class HqSummaryResponse(ContractModel):
    franchise_id: str
    as_of: str
    branch_count: int
    calculated_count: int
    grade_distribution: dict
    danger_ratio_pct: float | None
    average_score: float | None
    watchlist: list[dict]
    data_provenance: dict
