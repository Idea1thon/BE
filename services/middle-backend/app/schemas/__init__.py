"""요청·응답 스키마. API_SPEC.md 1장·2장 기준."""

from __future__ import annotations

import uuid
from datetime import date, datetime

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
