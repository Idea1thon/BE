"""요청·응답 스키마. API_SPEC.md 1장·2장 기준."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.models.enums import RegionLevel, RiskLevel, UserType


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
