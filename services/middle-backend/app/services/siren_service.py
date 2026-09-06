"""위험도 분석(사이렌) 연동. DB 조회 → 요청 조립 → 호출 → 결과 변환.

**아직 `POST /reports` 에 붙이지 않는다.** 사이렌 `BranchLocation` 이 요구하는
`trade_area_code`·`x_5181`·`y_5181` 를 우리 `branch` 테이블이 갖고 있지 않고
조달 경로가 정해지지 않았다. 값이 정해지는 순간 `analyze_report()` 에
`LocationInputs` 만 넘기면 되도록 그 지점을 인자로 열어 뒀다.

저장도 아직 하지 않는다. `report_analysis` 의 `risk_score`·`risk_level` 이
NOT NULL 인데 사이렌은 시장 자료가 없으면 둘 다 null 을 주고, `rule_version` 은
VARCHAR(20) 인데 상대 `score_version` 은 27자다. 값을 지어내거나 잘라 넣으면
"데이터 부족" 이 "정상" 으로 바뀐다. `build_analysis_row()` 는 저장이 불가능하면
이유를 들고 실패한다.
"""

from __future__ import annotations

import calendar
import datetime as dt
import decimal
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.errors import ApiError
from app.models import Branch, OperationReport, ReportAnalysis, ReportInputItem
from app.services import siren_client
from app.services.siren_mapper import (
    AnalysisValues,
    LocationInputs,
    SirenMappingError,
    build_analyze_request,
    build_location,
    build_monthly_report,
    to_analysis_values,
)

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


async def analyze_report(
    session: AsyncSession,
    report: OperationReport,
    *,
    location_inputs: LocationInputs,
    market_data: dict[str, Any] | None = None,
) -> SirenAnalysis:
    """보고서 1건을 기준으로 사이렌을 호출한다.

    `location_inputs` 는 호출부가 넣는다. `branch` 에 없는 값이라 여기서
    만들어낼 수 없고, 만들어내면 좌표가 틀린 상권의 위험도를 그 점포 것으로
    표시하게 된다.

    `market_data` 가 없으면 상대는 시장 층을 `missing` 으로 두고 종합 점수·등급이
    null 인 partial 결과를 준다. 실패가 아니다.
    """
    branch = await session.get(Branch, report.branch_id)
    if branch is None:  # FK 가 보장하지만 조회 실패를 조용히 넘기지 않는다.
        raise SirenMappingError(f"점포를 찾을 수 없습니다: branch_id={report.branch_id}")

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
        calculated_at=values.calculated_at,
    )
