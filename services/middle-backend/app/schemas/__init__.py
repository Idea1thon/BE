"""요청·응답 스키마. API_SPEC.md 1장·2장 기준."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.models.enums import (
    InputSource,
    RegionLevel,
    ReportStatus,
    RiskLevel,
    UserType,
)


# ------------------------------------------------------------------ 공통
class FranchiseBrief(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str


class BranchBrief(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str


# ------------------------------------------------------------------ Auth
class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1)


class LoginUser(BaseModel):
    """API_SPEC 1-1 응답의 `user` 객체."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    email: str
    name: str
    user_type: UserType
    franchise_id: int


class TokenPair(BaseModel):
    """`token`은 API_SPEC 1-1의 필드명을 유지한다 (Access Token).

    `refresh_token` / `expires_in`은 D2(Access + Refresh) 채택으로 추가됐다.
    """

    token: str
    refresh_token: str
    expires_in: int


class LoginResponse(TokenPair):
    user: LoginUser


class RefreshRequest(BaseModel):
    refresh_token: str = Field(min_length=1)


class LogoutRequest(BaseModel):
    refresh_token: str = Field(min_length=1)


# ------------------------------------------------------------------ User
class MeResponse(BaseModel):
    """API_SPEC 2-1."""

    id: int
    email: str
    name: str
    user_type: UserType
    franchise: FranchiseBrief
    branch: BranchBrief | None = None


# ------------------------------------------------------------------ 기준정보
class RegionItem(BaseModel):
    """API_SPEC 3-3."""

    model_config = ConfigDict(from_attributes=True)

    code: str
    name: str
    level: RegionLevel


class RegionListResponse(BaseModel):
    items: list[RegionItem]


class BusinessCategoryItem(BaseModel):
    """API_SPEC 3-4."""

    model_config = ConfigDict(from_attributes=True)

    code: str
    name: str


class BusinessCategoryListResponse(BaseModel):
    items: list[BusinessCategoryItem]


# ------------------------------------------------------------------ Branch
class CodeName(BaseModel):
    code: str
    name: str


class OwnerBrief(BaseModel):
    id: int
    name: str


class BranchDetailResponse(BaseModel):
    """API_SPEC 3-2."""

    branch_id: int
    name: str
    address: str
    region: CodeName
    business_category: CodeName
    owner: OwnerBrief


# ------------------------------------------------------------------ Report
class InputFieldItem(BaseModel):
    """API_SPEC 4-1."""

    model_config = ConfigDict(from_attributes=True)

    code: str
    name: str
    group_name: str
    is_required: bool
    display_order: int


# report_input_item.amount 와 operation_report.net_sales 가 모두 NUMERIC(14,0) 이다.
# DTO 에서 막지 않으면 DB 가 numeric overflow 를 내는데, 그건 중복월 IntegrityError 와
# 달리 500 으로 샌다.
AMOUNT_MAX = 10**14 - 1

# 정의된 입력 항목이 35개다. 그보다 많은 배열은 반드시 중복이거나 미정의 코드라
# 검증에 들어가기 전에 자른다. 상한이 없으면 대량 반복 요청이 검증 비용을 다 쓴다.
MAX_REPORT_ITEMS = 50


class ReportItemInput(BaseModel):
    """보고서 입력 항목 1건. 미입력 선택 항목은 배열에 넣지 않는다."""

    field_code: str = Field(min_length=1, max_length=40)
    amount: int = Field(ge=0, le=AMOUNT_MAX)


class ReportCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    report_month: str = Field(pattern=r"^\d{4}-(0[1-9]|1[0-2])$")
    input_source: InputSource = InputSource.MANUAL
    items: list[ReportItemInput] = Field(min_length=1, max_length=MAX_REPORT_ITEMS)

    @field_validator("report_month")
    @classmethod
    def _representable_month(cls, value: str) -> str:
        """정규식은 0000-01 도 통과시키지만 date(0, 1, 1) 은 ValueError 다.

        형식(YYYY-MM)은 그대로 두고, 실제 날짜로 만들 수 있는지만 여기서 본다.
        서비스 계층까지 흘러가면 500 이 된다.
        """
        year, month = value.split("-")
        try:
            date(int(year), int(month), 1)
        except ValueError as exc:
            raise ValueError("표현할 수 없는 연월입니다") from exc
        return value


class ReportCreateResponse(BaseModel):
    report_id: int
    status: ReportStatus
    analysis_request_id: uuid.UUID


class InputFieldListResponse(BaseModel):
    items: list[InputFieldItem]


# ------------------------------------------------------------------ Notification
class NotificationItem(BaseModel):
    """API_SPEC 5-1. `branch_name`·`risk_level`은 컬럼이 아니라 조인 결과다."""

    notification_id: int
    message: str
    is_read: bool
    created_at: datetime
    report_id: int
    branch_name: str
    risk_level: RiskLevel | None = None


class NotificationListResponse(BaseModel):
    unread_count: int
    # 다음 페이지가 있을 때만 채워진다. 그대로 `cursor` 쿼리에 실어 보낸다.
    next_cursor: str | None = None
    items: list[NotificationItem]


# ------------------------------------------------------------------ Financial Product
class FinancialProductItem(BaseModel):
    """API_SPEC 6-1."""

    model_config = ConfigDict(from_attributes=True)

    product_id: int = Field(validation_alias="id")
    name: str
    description: str | None = None
    link_url: str
    display_order: int


class FinancialProductListResponse(BaseModel):
    risk_level: RiskLevel | None = None
    items: list[FinancialProductItem]


# ------------------------------------------------------------------ 목록 조회 (Phase 4)
class BranchSort(str, Enum):
    """API_SPEC 3-1 `sort`. REQ-HQ-04(매출 랭킹) · REQ-HQ-05."""

    NET_SALES_DESC = "net_sales_desc"
    NET_SALES_ASC = "net_sales_asc"
    RISK_DESC = "risk_desc"


class ReportSort(str, Enum):
    """API_SPEC 4-4 `sort`. 기본은 최신순 (REQ-HQ-13, REQ-OW-01)."""

    MONTH_DESC = "month_desc"
    MONTH_ASC = "month_asc"


class LatestReportBrief(BaseModel):
    """API_SPEC 3-1 `items[].latest_report`.

    보고서가 없으면 객체 자체가 `null`, 분석 전이면 `risk_*` 만 `null` 이다
    (3-1 주석의 표현 방식 결정).
    """

    report_id: int
    report_month: str
    created_at: datetime
    status: ReportStatus
    risk_level: RiskLevel | None = None
    risk_score: int | None = None
    net_sales: int | None = None


class BranchListItem(BaseModel):
    """API_SPEC 3-1."""

    branch_id: int
    name: str
    address: str
    region_code: str
    latest_report: LatestReportBrief | None = None


class BranchListResponse(BaseModel):
    items: list[BranchListItem]


class HqRiskSummaryResponse(BaseModel):
    """본사 전용 프랜차이즈 위험 요약."""

    audience: Literal["franchise_hq"] = "franchise_hq"
    franchise_id: int
    as_of: str
    branch_count: int
    calculated_count: int
    grade_distribution: dict
    danger_ratio_pct: float | None
    average_score: float | None
    watchlist: list[dict]
    data_provenance: dict


class ReportListItem(BaseModel):
    """API_SPEC 4-4."""

    report_id: int
    report_month: str
    created_at: datetime
    status: ReportStatus
    risk_level: RiskLevel | None = None
    risk_score: int | None = None
    net_sales: int | None = None


class ReportListResponse(BaseModel):
    items: list[ReportListItem]


class ReportBranchBrief(BaseModel):
    """API_SPEC 4-5 `branch`. 키 이름이 `branch_id` 라 BranchBrief(id) 와 다르다."""

    branch_id: int
    name: str


class ReportInputItemOut(BaseModel):
    """API_SPEC 4-5 `inputs[]`. 표시용 이름·그룹은 report_input_field 조인 결과다."""

    field_code: str
    name: str
    group_name: str
    amount: int


class AnalysisDetail(BaseModel):
    """API_SPEC 4-5 `analysis`, filtered by the authenticated audience.

    OWNER receives evidence and actionable branch detail. HQ receives the
    franchise-safe component status and watchlist projection. The same stored
    analysis is never returned wholesale to both audiences.
    """

    audience: Literal["branch_owner", "franchise_hq"]
    risk_score: int | None
    risk_level: RiskLevel | None
    calculation_status: Literal["calculated", "partial"]
    factors: dict | list
    risk_periods: list
    recommendations: list
    rule_version: str
    calculated_at: datetime
    components: dict | None = None
    component_status: dict | None = None
    evidence: list[dict] = Field(default_factory=list)
    missing_data: list[dict] = Field(default_factory=list)
    uncertainty: list[str] = Field(default_factory=list)
    alert: dict = Field(default_factory=dict)
    recommended_actions: list = Field(default_factory=list)
    financial_products: dict = Field(default_factory=dict)
    explanation: dict = Field(default_factory=dict)
    review_watchlist_flag: bool | None = None
    data_provenance: dict = Field(default_factory=dict)
    franchise_closure: dict = Field(default_factory=dict)


class ReportDetailResponse(BaseModel):
    """API_SPEC 4-5."""

    report_id: int
    report_month: str
    created_at: datetime
    status: ReportStatus
    input_source: InputSource
    net_sales: int | None = None
    branch: ReportBranchBrief
    inputs: list[ReportInputItemOut]
    analysis: AnalysisDetail | None = None
    analysis_error: str | None = None


class ReportStatusResponse(BaseModel):
    """API_SPEC 4-6. FE 가 COMPLETED 가 될 때까지 폴링한다 (REQ-OW-16/17)."""

    report_id: int
    status: ReportStatus
    analysis_error: str | None = None


class NotificationReadResponse(BaseModel):
    """API_SPEC 5-2."""

    notification_id: int
    is_read: bool
    read_at: datetime | None = None


# ------------------------------------------------------------------ 입지 추천
MAX_CONDITION_TEXT = 2_000  # 추천 서비스의 special_condition_text 상한과 같다


class LocationRecommendationRequest(BaseModel):
    """입지 추천 요청. API_SPEC 에 없는 신규 계약이다.

    지역을 코드로 받는 이유: FE 는 `GET /regions` 로 코드를 이미 갖고 있고,
    이름을 그대로 받으면 오타나 표기 차이("강남"/"강남구")를 우리가 검증할 수
    없다. 추천 서비스가 요구하는 한글 이름으로의 변환은 서버에서 한다.
    """

    model_config = ConfigDict(extra="forbid")

    region_code: str = Field(min_length=1, max_length=20)
    business_category_code: str | None = Field(default=None, max_length=20)
    special_condition_text: str = Field(default="", max_length=MAX_CONDITION_TEXT)
    limit: int | None = Field(default=None, ge=1, le=50)


class LocationRecommendationAccepted(BaseModel):
    """202. 실행이 길어져 결과를 나중에 받아야 할 때.

    `run_ticket` 은 추천 서비스의 run_id 를 요청자에게 묶은 서명 토큰이다.
    FE 는 값을 해석하지 말고 `retry_after` 초 뒤에 그대로 실어 보낸다.

        GET /api/v1/location-recommendations/{run_ticket}
    """

    run_ticket: str
    status: str = "RUNNING"
    retry_after: int = 5


class LocationRecommendationResponse(BaseModel):
    """200. 추천 서비스의 응답을 변환 없이 싣는다.

    candidates·explanations·summary 의 내부 구조는 추천 서비스가 정본이다.
    여기서 다시 모델링하면 상대가 필드를 하나 추가할 때마다 우리가 조용히
    떨어뜨리게 된다. 위험도 분석 JSONB 를 그대로 싣는 것과 같은 판단이다.

    **타입은 상대의 `RecommendationApiResponse`(services/recommendation-api/
    api/main.py)를 정본으로 삼는다.** candidates 는 list, explanations 는
    `{cards, explanation_mode, degraded, llm}` 형태의 **dict** 다. 이 둘을 바꿔
    적으면 정상 응답이 전부 500 으로 떨어진다 (PR #26 리뷰).
    """

    run_id: str
    status: str = "COMPLETED"
    request: dict
    input_interpretation: dict = Field(default_factory=dict)
    summary: dict = Field(default_factory=dict)
    candidates: list = Field(default_factory=list)
    explanations: dict = Field(default_factory=dict)
