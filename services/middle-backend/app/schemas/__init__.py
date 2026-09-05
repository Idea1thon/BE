"""요청·응답 스키마. API_SPEC.md 1장·2장 기준."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from enum import Enum

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
    """API_SPEC 4-5 `analysis`.

    factors · risk_periods · recommendations 는 분석 서비스 응답 DTO 그대로다.
    Backend 는 변환하지 않는다 (INTERFACE_SPEC 4-2).
    """

    risk_score: int
    risk_level: RiskLevel
    factors: list
    risk_periods: list
    recommendations: list
    rule_version: str
    calculated_at: datetime


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
