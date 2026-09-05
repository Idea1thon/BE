"""운영보고서 제출. API_SPEC 4-2, REQ-OW-11~16.

위험도 분석 서비스 호출(처리 순서 7단계)은 아직 붙이지 않는다. 사이렌 서비스의
요청 계약에 저희가 만들 수 없는 값(상권 코드·행정동 코드 체계)이 남아 있어,
그게 정해진 뒤 별도로 연동한다. 그때까지 보고서는 ANALYZING 으로 남는다.
"""

from __future__ import annotations

import datetime as dt
import decimal
import uuid

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.errors import conflict, forbidden, validation_error
from app.models import Branch, OperationReport, ReportInputField, ReportInputItem, UserAccount
from app.models.enums import ReportStatus
from app.schemas import ReportCreateRequest

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

    duplicated = sorted({c for c in codes if codes.count(c) > 1})
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
