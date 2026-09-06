"""ORM 모델. DB_SCHEMA.md 4장의 12개 테이블과 1:1 대응한다.

물리 스키마의 정본은 `DB_SCHEMA.md` 7장의 CREATE TABLE SQL이며,
Alembic 초기 리비전이 그 SQL을 그대로 실행한다. 이 모델은 ORM 접근용이다.
"""

from __future__ import annotations

import datetime as dt
import decimal
import uuid

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ENUM as PGEnum, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.enums import (
    EmailStatus,
    InputSource,
    RegionLevel,
    ReportStatus,
    RiskLevel,
    UserType,
)


def _enum(py_enum, name: str) -> PGEnum:
    """DB에 이미 존재하는 native enum 타입을 참조한다.

    generic `sqlalchemy.Enum`은 `create_type`을 인자로 받지 않아 조용히 무시되고,
    `metadata.create_all()`이 CREATE TYPE을 다시 실행해 마이그레이션이 만든 타입과
    충돌한다. PostgreSQL 전용 ENUM만 이 옵션을 실제로 적용한다.
    """
    return PGEnum(
        py_enum,
        name=name,
        create_type=False,
        values_callable=lambda e: [m.value for m in e],
    )


_TS = DateTime(timezone=True)


# ---------------------------------------------------------------- 기준정보
class Region(Base):
    __tablename__ = "region"

    code: Mapped[str] = mapped_column(String(20), primary_key=True)
    parent_code: Mapped[str | None] = mapped_column(
        String(20), ForeignKey("region.code")
    )
    level: Mapped[RegionLevel] = mapped_column(_enum(RegionLevel, "region_level"))
    name: Mapped[str] = mapped_column(String(50))


class BusinessCategory(Base):
    __tablename__ = "business_category"

    code: Mapped[str] = mapped_column(String(20), primary_key=True)
    name: Mapped[str] = mapped_column(String(50))


# ---------------------------------------------------------------- 조직·계정
class Franchise(Base):
    __tablename__ = "franchise"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    fixture_key: Mapped[str | None] = mapped_column(String(80), nullable=True)
    synthetic: Mapped[bool] = mapped_column(Boolean, server_default="false")
    created_at: Mapped[dt.datetime] = mapped_column(_TS, server_default=func.now())


class UserAccount(Base):
    __tablename__ = "user_account"
    __table_args__ = (
        # branch(owner_user_id, franchise_id) composite FK 의 참조 대상.
        # 0003 리비전이 DB 에 같은 제약을 만든다. 여기서 빼면 autogenerate 가
        # 다음 revision 에서 이 제약과 FK 를 삭제 대상으로 제안한다.
        UniqueConstraint("id", "franchise_id", name="uq_user_id_franchise"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    franchise_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("franchise.id"), index=True
    )
    email: Mapped[str] = mapped_column(String(255), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    user_type: Mapped[UserType] = mapped_column(_enum(UserType, "user_type"))
    name: Mapped[str] = mapped_column(String(50))
    is_active: Mapped[bool] = mapped_column(Boolean, server_default="true")
    last_login_at: Mapped[dt.datetime | None] = mapped_column(_TS)
    created_at: Mapped[dt.datetime] = mapped_column(_TS, server_default=func.now())

    franchise: Mapped[Franchise] = relationship(lazy="joined")
    branch: Mapped["Branch | None"] = relationship(
        back_populates="owner",
        lazy="joined",
        uselist=False,
        foreign_keys="Branch.owner_user_id",
    )


class Branch(Base):
    __tablename__ = "branch"
    __table_args__ = (
        # 점주와 점포가 같은 프랜차이즈에 속함을 DB 가 보장한다 (이슈 #7).
        # owner_user_id 단독 FK 와 함께 두 경로가 생기므로 아래 relationship 에
        # foreign_keys 를 명시한다.
        ForeignKeyConstraint(
            ["owner_user_id", "franchise_id"],
            ["user_account.id", "user_account.franchise_id"],
            name="fk_branch_owner_same_franchise",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    franchise_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("franchise.id"))
    owner_user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("user_account.id"), unique=True
    )
    name: Mapped[str] = mapped_column(String(100))
    address: Mapped[str] = mapped_column(String(255))
    # F11: 시군구(SIGUNGU) 코드만 저장한다. 상위 시도는 region.parent_code로 조회.
    region_code: Mapped[str] = mapped_column(String(20), ForeignKey("region.code"))
    business_category_code: Mapped[str] = mapped_column(
        String(20), ForeignKey("business_category.code")
    )
    trade_area_code: Mapped[str | None] = mapped_column(String(20), nullable=True)
    x_5181: Mapped[decimal.Decimal | None] = mapped_column(
        Numeric(12, 2), nullable=True
    )
    y_5181: Mapped[decimal.Decimal | None] = mapped_column(
        Numeric(12, 2), nullable=True
    )
    fixture_key: Mapped[str | None] = mapped_column(String(80), nullable=True)
    synthetic: Mapped[bool] = mapped_column(Boolean, server_default="false")
    created_at: Mapped[dt.datetime] = mapped_column(_TS, server_default=func.now())

    owner: Mapped[UserAccount] = relationship(
        back_populates="branch", foreign_keys=[owner_user_id]
    )


# ---------------------------------------------------------------- 보고서
class ReportInputField(Base):
    __tablename__ = "report_input_field"

    code: Mapped[str] = mapped_column(String(40), primary_key=True)
    name: Mapped[str] = mapped_column(String(50))
    group_name: Mapped[str] = mapped_column(String(30))
    is_required: Mapped[bool] = mapped_column(Boolean)
    display_order: Mapped[int] = mapped_column(SmallInteger)


class OperationReport(Base):
    __tablename__ = "operation_report"
    __table_args__ = (
        UniqueConstraint("branch_id", "report_month", name="uq_report_branch_month"),
        # unique 만으로는 같은 점포에 2026-08-01 과 2026-08-15 를 모두 넣을 수 있어
        # "월 1건"이 보장되지 않는다. 0003 리비전이 DB에 같은 제약을 만든다.
        CheckConstraint(
            "EXTRACT(DAY FROM report_month) = 1", name="ck_report_month_first_day"
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    branch_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("branch.id"))
    report_month: Mapped[dt.date] = mapped_column(Date)
    status: Mapped[ReportStatus] = mapped_column(
        _enum(ReportStatus, "report_status"), server_default="DRAFT"
    )
    input_source: Mapped[InputSource] = mapped_column(
        _enum(InputSource, "input_source"), server_default="MANUAL"
    )
    net_sales: Mapped[decimal.Decimal | None] = mapped_column(Numeric(14, 0))
    fixture_key: Mapped[str | None] = mapped_column(String(120), nullable=True)
    source_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    synthetic: Mapped[bool] = mapped_column(Boolean, server_default="false")
    analysis_request_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    analysis_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = mapped_column(_TS, server_default=func.now())
    updated_at: Mapped[dt.datetime] = mapped_column(_TS, server_default=func.now())


class ReportInputItem(Base):
    __tablename__ = "report_input_item"

    report_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("operation_report.id", ondelete="CASCADE"), primary_key=True
    )
    field_code: Mapped[str] = mapped_column(
        String(40), ForeignKey("report_input_field.code"), primary_key=True
    )
    amount: Mapped[decimal.Decimal] = mapped_column(Numeric(14, 0))


class ReportAnalysis(Base):
    __tablename__ = "report_analysis"
    __table_args__ = (
        CheckConstraint(
            "risk_score BETWEEN 0 AND 100", name="report_analysis_risk_score_check"
        ),
        CheckConstraint(
            "(risk_score IS NULL) = (risk_level IS NULL)",
            name="ck_analysis_score_grade_together",
        ),
    )

    report_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("operation_report.id", ondelete="CASCADE"), primary_key=True
    )
    # 시장·리뷰 자료가 없으면 Siren은 score/grade를 null로 보존한 partial
    # 결과를 반환한다. 이를 FAILED로 바꾸거나 정상으로 대체하지 않는다.
    risk_score: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    risk_level: Mapped[RiskLevel | None] = mapped_column(
        _enum(RiskLevel, "risk_level"), nullable=True
    )
    # Existing rows use the documented list shape; the siren v1 response writes
    # its components dict. JSONB is intentionally tolerant during this contract
    # transition so historical reports remain readable.
    factors: Mapped[list] = mapped_column(JSONB)
    risk_periods: Mapped[list] = mapped_column(JSONB)
    recommendations: Mapped[list] = mapped_column(JSONB)
    rule_version: Mapped[str] = mapped_column(String(60))
    alert_policy_version: Mapped[str | None] = mapped_column(
        String(60), nullable=True
    )
    calculation_status: Mapped[str] = mapped_column(
        String(20), server_default="calculated"
    )
    calculated_at: Mapped[dt.datetime] = mapped_column(_TS)


# ---------------------------------------------------------------- 알림·금융
class Notification(Base):
    __tablename__ = "notification"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    recipient_user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("user_account.id")
    )
    report_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("operation_report.id", ondelete="CASCADE")
    )
    message: Mapped[str] = mapped_column(Text)
    is_read: Mapped[bool] = mapped_column(Boolean, server_default="false")
    read_at: Mapped[dt.datetime | None] = mapped_column(_TS)
    email_status: Mapped[EmailStatus] = mapped_column(
        _enum(EmailStatus, "email_status"), server_default="PENDING"
    )
    email_sent_at: Mapped[dt.datetime | None] = mapped_column(_TS)
    created_at: Mapped[dt.datetime] = mapped_column(_TS, server_default=func.now())


class FinancialProduct(Base):
    __tablename__ = "financial_product"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    target_risk_level: Mapped[RiskLevel] = mapped_column(_enum(RiskLevel, "risk_level"))
    name: Mapped[str] = mapped_column(String(100))
    description: Mapped[str | None] = mapped_column(Text)
    link_url: Mapped[str] = mapped_column(String(500))
    display_order: Mapped[int] = mapped_column(SmallInteger, server_default="0")
    is_active: Mapped[bool] = mapped_column(Boolean, server_default="true")


# ---------------------------------------------------------------- 인증 (D2)
class RefreshToken(Base):
    __tablename__ = "refresh_token"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("user_account.id", ondelete="CASCADE")
    )
    token_hash: Mapped[str] = mapped_column(String(255), unique=True)
    expires_at: Mapped[dt.datetime] = mapped_column(_TS)
    revoked_at: Mapped[dt.datetime | None] = mapped_column(_TS)
    created_at: Mapped[dt.datetime] = mapped_column(_TS, server_default=func.now())
