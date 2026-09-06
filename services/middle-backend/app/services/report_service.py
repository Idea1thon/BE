"""운영보고서 제출과 선택적 위험도 분석 작업. API_SPEC 4-2, REQ-OW-11~16.

분석은 요청 트랜잭션과 분리된 background task에서 ID-only siren trigger로
실행한다. feature flag가 꺼져 있으면 기존처럼 ANALYZING 상태로 남긴다.
"""

from __future__ import annotations

import datetime as dt
import decimal
import logging
import uuid
from collections import Counter

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import SessionLocal
from app.errors import ApiError
from app.errors import conflict, forbidden, validation_error
from app.models import (
    Branch,
    OperationReport,
    ReportAnalysis,
    ReportInputField,
    ReportInputItem,
    UserAccount,
)
from app.models.enums import ReportStatus
from app.schemas import AMOUNT_MAX, ReportCreateRequest
from app.services import siren_service

logger = logging.getLogger("app.report_service")

# net_sales 산식 (DB_SCHEMA 4-7 D1 확정).
#   net_sales = 매출 3그룹 합계 − 매출 차감 항목 합계
# 개별 코드가 아니라 그룹으로 정의한다. 입력 항목 표가 바뀌어 항목이 늘어도
# 그룹만 맞으면 산식이 따라간다.
SALES_GROUPS = ("홀 매출", "배달 매출", "포장 매출")
DEDUCTION_GROUP = "매출 차감 항목"


async def _owner_branch(session: AsyncSession, owner: UserAccount) -> Branch:
    branch = (
        await session.execute(select(Branch).where(Branch.owner_user_id == owner.id))
    ).scalar_one_or_none()
    if branch is None:
        # 1점주 1점포(REQ-AUTH-07)가 전제다. 점포가 없는 점주 계정은 제출 대상이 없다.
        raise forbidden("연결된 점포가 없습니다")
    return branch


def _report_month(value: str) -> dt.date:
    year, month = value.split("-")
    return dt.date(int(year), int(month), 1)


def _validate_items(
    payload: ReportCreateRequest, fields: dict[str, ReportInputField]
) -> None:
    codes = [item.field_code for item in payload.items]

    unknown = sorted({c for c in codes if c not in fields})
    if unknown:
        raise validation_error(
            "정의되지 않은 입력 항목입니다", {"unknown_field_codes": unknown}
        )

    # codes.count() 를 코드마다 부르면 O(n^2) 이다. 한 번만 순회한다.
    duplicated = sorted({code for code, n in Counter(codes).items() if n > 1})
    if duplicated:
        raise validation_error(
            "같은 항목이 여러 번 들어왔습니다", {"duplicated_field_codes": duplicated}
        )

    # REQ-OW-14 필수 항목 검증
    required = {code for code, f in fields.items() if f.is_required}
    missing = sorted(required - set(codes))
    if missing:
        raise validation_error(
            "필수 입력 항목이 누락되었습니다", {"missing_field_codes": missing}
        )


def _net_sales(
    payload: ReportCreateRequest, fields: dict[str, ReportInputField]
) -> decimal.Decimal:
    total = decimal.Decimal(0)
    for item in payload.items:
        group = fields[item.field_code].group_name
        if group in SALES_GROUPS:
            total += item.amount
        elif group == DEDUCTION_GROUP:
            total -= item.amount

    # 개별 금액이 모두 범위 안이어도 합계는 넘칠 수 있다. net_sales 는 부호가 있어
    # 차감이 매출보다 크면 음수가 된다. DB 에 닿기 전에 400 으로 돌려준다.
    if not -AMOUNT_MAX <= total <= AMOUNT_MAX:
        raise validation_error(
            "매출 합계가 저장 가능한 범위를 벗어났습니다",
            {"net_sales": str(total), "allowed_abs_max": str(AMOUNT_MAX)},
        )
    return total


async def create_report(
    session: AsyncSession, owner: UserAccount, payload: ReportCreateRequest
) -> OperationReport:
    branch = await _owner_branch(session, owner)

    rows = (await session.execute(select(ReportInputField))).scalars().all()
    fields = {row.code: row for row in rows}
    _validate_items(payload, fields)

    report = OperationReport(
        branch_id=branch.id,
        report_month=_report_month(payload.report_month),
        status=ReportStatus.ANALYZING,
        input_source=payload.input_source,
        net_sales=_net_sales(payload, fields),
        analysis_request_id=uuid.uuid4(),
    )
    session.add(report)

    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        # uq_report_branch_month. 같은 점포·같은 월 보고서는 1건이다.
        raise conflict("해당 월 보고서가 이미 존재합니다") from exc

    session.add_all(
        [
            ReportInputItem(
                report_id=report.id, field_code=item.field_code, amount=item.amount
            )
            for item in payload.items
        ]
    )
    await session.commit()
    await session.refresh(report)
    return report


async def process_report_analysis(report_id: int, franchise_id: int) -> None:
    """Run the ID-only siren trigger and persist complete/partial results safely.

    This background job intentionally uses a fresh session because the request
    transaction has already committed before FastAPI schedules it. Partial
    results are stored as ``COMPLETED`` reports with nullable score/grade and
    ``analysis.calculation_status=partial``; they are never coerced to score
    zero, a normal grade, or a truncated rule version.
    """

    async with SessionLocal() as session:
        row = (
            await session.execute(
                select(OperationReport, Branch)
                .join(Branch, Branch.id == OperationReport.branch_id)
                .where(
                    OperationReport.id == report_id,
                    Branch.franchise_id == franchise_id,
                )
            )
        ).first()
        if row is None:
            logger.error("analysis target not found report_id=%s franchise_id=%s", report_id, franchise_id)
            return
        report, branch = row

        try:
            analysis = await siren_service.request_report_analysis(
                report,
                franchise_id=branch.franchise_id,
                branch_id=branch.id,
            )
            if not analysis.values.storable:
                report.status = ReportStatus.FAILED
                report.analysis_error = (
                    "ANALYSIS_RESULT_NOT_STORED: "
                    + "; ".join(analysis.values.blockers)
                )
                await session.commit()
                return

            existing = await session.get(ReportAnalysis, report.id)
            values = analysis.values
            if existing is None:
                session.add(siren_service.build_analysis_row(report.id, analysis))
            else:
                existing.risk_score = values.risk_score
                existing.risk_level = values.risk_level
                existing.factors = values.factors
                existing.risk_periods = values.risk_periods
                existing.recommendations = values.recommendations
                existing.rule_version = values.rule_version
                existing.calculated_at = values.calculated_at
            report.status = ReportStatus.COMPLETED
            report.analysis_error = None
            await session.commit()
        except ApiError as exc:
            await session.rollback()
            await _mark_analysis_failed(session, report_id, f"{exc.code}: {exc.message}")
        except Exception:
            logger.exception("risk siren processing failed report_id=%s", report_id)
            await session.rollback()
            await _mark_analysis_failed(session, report_id, "분석 서비스 처리 중 오류가 발생했습니다")


async def _mark_analysis_failed(
    session: AsyncSession, report_id: int, message: str
) -> None:
    report = await session.get(OperationReport, report_id)
    if report is None:
        return
    report.status = ReportStatus.FAILED
    report.analysis_error = message[:4000]
    await session.commit()
