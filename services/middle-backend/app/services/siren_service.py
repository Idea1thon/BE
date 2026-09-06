"""위험도 분석(사이렌) 연동. DB 조회 → 요청 조립 → 호출 → 결과 변환.

`POST /reports` 가 제출 직후 이 흐름을 부른다. 사이렌은 순수 계산이라 외부를
부르지 않고 동기로 답한다 — 202+폴링 계층을 우리가 얹지 않는다.

위치(`trade_area_code`·`x_5181`·`y_5181`)는 0004 에서 `branch` 컬럼이 됐다.
값이 없는 점포는 분석하지 않는다. 좌표를 지어내면 다른 상권의 위험도가 그 점포
것으로 표시된다.

시장 데이터는 `market_context` 가 (시군구, 업종) 으로 찾는다. 없으면 넘기지
않고, 상대는 종합 점수·등급이 null 인 부분 결과를 준다. 실패가 아니다.
"""

from __future__ import annotations

import calendar
import datetime as dt
import decimal
import logging
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.errors import ApiError
from app.models import (
    Branch,
    Notification,
    OperationReport,
    ReportAnalysis,
    ReportInputItem,
    UserAccount,
)
from app.models.enums import ReportStatus, UserType
from app.services import market_context, siren_client
from app.services.siren_mapper import (
    AnalysisValues,
    LocationInputs,
    SirenMappingError,
    build_analyze_request,
    build_location,
    build_monthly_report,
    notification_message,
    should_notify,
    to_analysis_values,
)

logger = logging.getLogger("app.siren")

# 사이렌이 보는 최장 구간은 SR-02.branch 의 최근 3개월 · 직전 3개월 · 그 이전
# 3개월(9개월)이다. 연속 적자(SR-05)는 더 길 수 있어 12개월을 보낸다.
# 무제한이면 payload 가 점포 수명에 비례해 커진다.
HISTORY_MONTHS = 12


@dataclass
class SirenAnalysis:
    """분석 1회 결과. 원문과 우리 컬럼값을 함께 들고 다닌다."""

    response: dict[str, Any]
    values: AnalysisValues

    @property
    def raw_risk(self) -> dict[str, Any]:
        return self.response.get("risk") or {}


def _month_end(month: dt.date) -> dt.date:
    """보고서 월의 마지막 날.

    사이렌 `as_of` 는 "이 날짜까지의 자료로 계산" 이라는 뜻이고 월 단위 창을
    `(as_of.year, as_of.month)` 로 잡는다. 월 1일을 넘겨도 그 달이 창에 들어오지만
    시장 층의 `_last_completed_quarter_index()` 는 날짜까지 본다. 보고서가 그 달
    전체를 담고 있으므로 말일이 사실에 맞다.
    """
    return dt.date(month.year, month.month, calendar.monthrange(month.year, month.month)[1])


async def _load_history(
    session: AsyncSession, branch_id: int, upto: dt.date
) -> list[OperationReport]:
    """`upto` 월까지 최근 HISTORY_MONTHS 개월 보고서. 월 오름차순."""
    first = upto
    for _ in range(HISTORY_MONTHS - 1):
        first = (first.replace(day=1) - dt.timedelta(days=1)).replace(day=1)
    rows = (
        await session.execute(
            select(OperationReport)
            .where(
                OperationReport.branch_id == branch_id,
                OperationReport.report_month <= upto,
                OperationReport.report_month >= first,
            )
            .order_by(OperationReport.report_month)
        )
    ).scalars().all()
    return list(rows)


async def _load_items(
    session: AsyncSession, report_ids: list[int]
) -> dict[int, dict[str, decimal.Decimal]]:
    if not report_ids:
        return {}
    rows = (
        await session.execute(
            select(ReportInputItem).where(ReportInputItem.report_id.in_(report_ids))
        )
    ).scalars().all()
    out: dict[int, dict[str, decimal.Decimal]] = {}
    for row in rows:
        out.setdefault(row.report_id, {})[row.field_code] = row.amount
    return out


def location_inputs_for(branch: Branch) -> LocationInputs | None:
    """점포에서 사이렌 위치 입력을 만든다. 값이 없으면 None — 분석하지 않는다."""
    if not branch.trade_area_code or branch.x_5181 is None or branch.y_5181 is None:
        return None
    return LocationInputs(
        trade_area_code=branch.trade_area_code,
        x_5181=float(branch.x_5181),
        y_5181=float(branch.y_5181),
    )


async def analyze_report(
    session: AsyncSession,
    report: OperationReport,
    *,
    location_inputs: LocationInputs | None = None,
    market_data: dict[str, Any] | None = None,
) -> SirenAnalysis:
    """보고서 1건을 기준으로 사이렌을 호출한다.

    `location_inputs`·`market_data` 를 넘기지 않으면 점포와 스냅샷에서 조달한다.
    테스트에서 특정 조합을 강제하려고 인자로 열어 뒀다.

    `market_data` 가 없으면 상대는 시장 층을 `missing` 으로 두고 종합 점수·등급이
    null 인 partial 결과를 준다. 실패가 아니다.
    """
    branch = await session.get(Branch, report.branch_id)
    if branch is None:  # FK 가 보장하지만 조회 실패를 조용히 넘기지 않는다.
        raise SirenMappingError(f"점포를 찾을 수 없습니다: branch_id={report.branch_id}")

    if location_inputs is None:
        location_inputs = location_inputs_for(branch)
    if location_inputs is None:
        raise SirenMappingError(
            "점포에 상권 코드·좌표가 없어 위험도 분석을 요청할 수 없습니다"
        )
    if market_data is None:
        market_data = market_context.market_data_for(
            branch.region_code, branch.business_category_code
        )

    history = await _load_history(session, branch.id, report.report_month)
    items_by_report = await _load_items(session, [row.id for row in history])

    monthly = [
        build_monthly_report(
            report_month=row.report_month,
            items=items_by_report.get(row.id, {}),
            input_source=row.input_source,
        )
        for row in history
    ]

    payload = build_analyze_request(
        # 보고서에 이미 발급된 분석 요청 ID 가 있으면 재사용한다. 재시도 때 상대
        # 응답의 request_id 로 어느 보고서인지 되짚을 수 있다.
        request_id=str(report.analysis_request_id or uuid.uuid4()),
        franchise_id=branch.franchise_id,
        branch_id=branch.id,
        as_of=_month_end(report.report_month),
        industry_code=branch.business_category_code,
        location=build_location(region_code=branch.region_code, inputs=location_inputs),
        monthly_reports=monthly,
        brand_name=None,
        market_data=market_data,
    )

    response = await siren_client.analyze(payload)
    return SirenAnalysis(response=response, values=to_analysis_values(response))


def build_analysis_row(report_id: int, analysis: SirenAnalysis) -> ReportAnalysis:
    """`report_analysis` 행을 만든다. 현행 스키마로 저장 불가면 실패한다.

    막힌 이유를 그대로 올린다. 잘라 넣거나 0 으로 채우는 선택은 데이터를 조용히
    왜곡하므로 여기서 하지 않는다 — 스키마를 바꿀지 사이렌 계약을 바꿀지는
    사람이 정할 일이다.
    """
    values = analysis.values
    if not values.storable:
        raise ApiError(
            500,
            "INTERNAL_ERROR",
            "위험도 분석 결과를 저장할 수 없습니다",
            {"blockers": values.blockers, "calculation_status": values.calculation_status},
        )
    return ReportAnalysis(
        report_id=report_id,
        risk_score=values.risk_score,
        risk_level=values.risk_level,
        factors=values.factors,
        risk_periods=values.risk_periods,
        recommendations=values.recommendations,
        rule_version=values.rule_version,
        alert_policy_version=values.alert_policy_version,
        calculation_status=values.calculation_status,
        calculated_at=values.calculated_at,
    )


async def _notify(
    session: AsyncSession, report: OperationReport, branch: Branch, analysis: SirenAnalysis
) -> int:
    """경고가 발생하면 점주와 같은 프랜차이즈 본사에게 알림을 만든다.

    사이렌 CONTRACT.md 상 알림 생성·발송·읽음 관리는 Backend 책임이다. 상대는
    `alert.should_fire` 로 "이 내용으로 알림을 만들라" 는 신호만 준다.

    등급이 아니라 `should_fire` 를 따른다. 시장 자료가 없어 종합 등급이 미확정
    이어도 확인된 점포 수익성 위험이면 참이 되는데(alert_policy
    `confirmed-branch-v1`), 등급만 보면 그 경고를 놓친다.

    이메일 발송은 아직 구현하지 않았다. `email_status` 는 기본값 PENDING 으로
    남아 사후에 보낼 대상을 찾을 수 있다.
    """
    if not should_notify(analysis.response):
        return 0

    message = notification_message(analysis.response, branch_name=branch.name)
    recipients = [branch.owner_user_id]
    hq_ids = (
        await session.execute(
            select(UserAccount.id).where(
                UserAccount.franchise_id == branch.franchise_id,
                UserAccount.user_type == UserType.HQ,
            )
        )
    ).scalars().all()
    recipients.extend(i for i in hq_ids if i != branch.owner_user_id)

    for user_id in recipients:
        session.add(
            Notification(recipient_user_id=user_id, report_id=report.id, message=message)
        )
    return len(recipients)


async def run_analysis(session: AsyncSession, report: OperationReport) -> SirenAnalysis | None:
    """제출 직후 위험도 분석. 보고서 상태를 확정한다.

    **분석 실패로 보고서 제출을 되돌리지 않는다.** 점주는 이미 값을 냈고, 그
    사실은 분석 성공 여부와 무관하다. 실패는 `status=FAILED` 와 `analysis_error`
    로 보고서에 남고 REQ 상 재시도 대상이 된다.
    """
    branch = await session.get(Branch, report.branch_id)
    try:
        analysis = await analyze_report(session, report)
        row = build_analysis_row(report.id, analysis)
    except (SirenMappingError, ApiError) as exc:
        logger.warning("위험도 분석 실패 report_id=%s error=%s", report.id, exc)
        report.status = ReportStatus.FAILED
        # 사용자에게 그대로 보이는 문자열이다. 내부 스택이나 상대 본문을 넣지 않는다.
        report.analysis_error = str(getattr(exc, "message", None) or exc)[:500]
        await session.commit()
        return None

    await session.merge(row)
    report.status = ReportStatus.COMPLETED
    report.analysis_error = None
    if branch is not None:
        await _notify(session, report, branch, analysis)
    await session.commit()
    return analysis
