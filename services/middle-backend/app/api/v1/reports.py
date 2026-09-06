"""운영보고서. API_SPEC 4-1(입력 항목) · 4-2(제출) · 4-5(상세) · 4-6(분석 상태).

점포별 목록(4-4)은 경로가 `/branches/{id}/reports` 라 branches.py 에 있다.

4-5 의 `inputs[]`(35개 금액 원본)를 HQ 에게도 그대로 반환한다. REQ-HQ-15 는 본사가
입력 데이터를 본다고 하고 REQ-DATA-10 은 재무 정보를 민감 데이터로 규정해 충돌하는데,
API_SPEC 4-5 가 REQ-HQ-15 를 따르기로 한 상태다. 뒤집히면 이 주석과 함께 고친다.
"""

from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    CurrentUser,
    OffsetQuery,
    OwnerUser,
    PathId,
    SessionDep,
    authorize_branch,
)
from app.core.config import settings
from app.errors import (
    CONFLICT_409,
    FORBIDDEN_403,
    NOT_FOUND_404,
    UNAUTHORIZED_401,
    VALIDATION_400,
    not_found,
)
from app.models import (
    Branch,
    OperationReport,
    ReportAnalysis,
    ReportInputField,
    ReportInputItem,
    UserAccount,
)
from app.models.enums import UserType
from app.schemas import (
    AnalysisDetail,
    InputFieldItem,
    InputFieldListResponse,
    ReportBranchBrief,
    ReportCreateRequest,
    ReportCreateResponse,
    ReportDetailResponse,
    ReportInputItemOut,
    ReportListItem,
    ReportListResponse,
    ReportSort,
    ReportStatusResponse,
)
from app.services import report_service
from app.services.siren_mapper import select_analysis_view

router = APIRouter(prefix="/reports", tags=["reports"])


@router.get(
    "",
    response_model=ReportListResponse,
    responses={**UNAUTHORIZED_401, **FORBIDDEN_403},
)
async def list_my_reports(
    owner: OwnerUser,
    session: SessionDep,
    sort: ReportSort = Query(default=ReportSort.MONTH_DESC),
    limit: int = Query(default=100, ge=1, le=500),
    offset: OffsetQuery = 0,
) -> ReportListResponse:
    """점주 본인 점포의 운영보고서 목록(REQ-OW-01~02)."""
    order = (
        OperationReport.report_month.desc()
        if sort is ReportSort.MONTH_DESC
        else OperationReport.report_month.asc()
    )
    rows = (
        await session.execute(
            select(OperationReport, ReportAnalysis.risk_level, ReportAnalysis.risk_score)
            .join(Branch, Branch.id == OperationReport.branch_id)
            .outerjoin(ReportAnalysis, ReportAnalysis.report_id == OperationReport.id)
            .where(Branch.owner_user_id == owner.id)
            .order_by(order, OperationReport.id.desc())
            .limit(limit)
            .offset(offset)
        )
    ).all()

    return ReportListResponse(
        items=[
            ReportListItem(
                report_id=report.id,
                report_month=report.report_month.strftime("%Y-%m"),
                created_at=report.created_at,
                status=report.status,
                risk_level=level,
                risk_score=score,
                net_sales=int(report.net_sales) if report.net_sales is not None else None,
            )
            for report, level, score in rows
        ]
    )


@router.get(
    "/input-fields",
    response_model=InputFieldListResponse,
    responses={**UNAUTHORIZED_401, **FORBIDDEN_403},
)
async def list_input_fields(
    _: OwnerUser, session: SessionDep
) -> InputFieldListResponse:
    """REQ-OW-12의 입력 항목 정의 35행. FE가 이 응답으로 폼을 렌더한다."""
    rows = (
        await session.execute(
            select(ReportInputField).order_by(ReportInputField.display_order)
        )
    ).scalars().all()
    return InputFieldListResponse(
        items=[InputFieldItem.model_validate(r) for r in rows]
    )


@router.post(
    "",
    response_model=ReportCreateResponse,
    status_code=status.HTTP_202_ACCEPTED,
    responses={**VALIDATION_400, **UNAUTHORIZED_401, **FORBIDDEN_403, **CONFLICT_409},
)
async def create_report(
    body: ReportCreateRequest,
    owner: OwnerUser,
    session: SessionDep,
    background_tasks: BackgroundTasks,
) -> ReportCreateResponse:
    """REQ-OW-11~16. 점포는 인증 사용자로 결정한다 — 요청으로 받지 않는다.

    202 인 이유는 분석이 비동기이고 FE 가 폴링하기 때문이다(REQ-OW-16).
    ``SIREN_ANALYSIS_ENABLED=true`` 인 환경에서만 별도 백그라운드 작업을
    등록한다. 기본값은 false라 기존 배포는 보고서를 만들기만 한다.
    """
    report = await report_service.create_report(session, owner, body)
    if settings.siren_analysis_enabled:
        # report 는 이미 commit 되었고, 작업은 독립 세션에서 다시 읽는다.
        background_tasks.add_task(
            report_service.process_report_analysis,
            report.id,
            owner.franchise_id,
        )
    return ReportCreateResponse(
        report_id=report.id,
        status=report.status,
        analysis_request_id=report.analysis_request_id,
    )


async def _load_report_for_read(
    report_id: int, current_user: UserAccount, session: AsyncSession
) -> tuple[OperationReport, Branch]:
    """보고서와 소속 점포를 함께 읽고 권한을 확인한다.

    보고서 자체에는 프랜차이즈가 없다. 권한은 언제나 점포를 거쳐 판정한다.
    """
    row = (
        await session.execute(
            select(OperationReport, Branch)
            .join(Branch, Branch.id == OperationReport.branch_id)
            .where(OperationReport.id == report_id)
        )
    ).unique().first()
    if row is None:
        raise not_found("보고서를 찾을 수 없습니다")
    report, branch = row
    authorize_branch(current_user, branch.franchise_id, branch.owner_user_id)
    return report, branch


@router.get(
    "/{report_id}",
    response_model=ReportDetailResponse,
    responses={**UNAUTHORIZED_401, **FORBIDDEN_403, **NOT_FOUND_404},
)
async def get_report(
    report_id: PathId, current_user: CurrentUser, session: SessionDep
) -> ReportDetailResponse:
    """API_SPEC 4-5. REQ-HQ-14, 15 / REQ-OW-05 / REQ-RPT-01~06."""
    report, branch = await _load_report_for_read(report_id, current_user, session)

    input_rows = (
        await session.execute(
            select(ReportInputItem, ReportInputField)
            .join(ReportInputField, ReportInputField.code == ReportInputItem.field_code)
            .where(ReportInputItem.report_id == report_id)
            .order_by(ReportInputField.display_order)
        )
    ).all()

    analysis = (
        await session.execute(
            select(ReportAnalysis).where(ReportAnalysis.report_id == report_id)
        )
    ).scalar_one_or_none()

    audience = (
        "branch_owner"
        if current_user.user_type is UserType.OWNER
        else "franchise_hq"
    )
    analysis_view = None
    if analysis is not None:
        analysis_view = select_analysis_view(
            analysis.factors,
            audience=audience,
            risk_score=analysis.risk_score,
            risk_level=analysis.risk_level,
            risk_periods=analysis.risk_periods,
            recommendations=analysis.recommendations,
            rule_version=analysis.rule_version,
            calculated_at=analysis.calculated_at,
        )

    return ReportDetailResponse(
        report_id=report.id,
        report_month=report.report_month.strftime("%Y-%m"),
        created_at=report.created_at,
        status=report.status,
        input_source=report.input_source,
        net_sales=int(report.net_sales) if report.net_sales is not None else None,
        branch=ReportBranchBrief(branch_id=branch.id, name=branch.name),
        inputs=[
            ReportInputItemOut(
                field_code=item.field_code,
                name=field.name,
                group_name=field.group_name,
                amount=int(item.amount),
            )
            for item, field in input_rows
        ],
        analysis=None if analysis_view is None else AnalysisDetail(**analysis_view),
        analysis_error=report.analysis_error,
    )


@router.get(
    "/{report_id}/status",
    response_model=ReportStatusResponse,
    responses={**UNAUTHORIZED_401, **FORBIDDEN_403, **NOT_FOUND_404},
)
async def get_report_status(
    report_id: PathId, current_user: CurrentUser, session: SessionDep
) -> ReportStatusResponse:
    """API_SPEC 4-6. FE 가 COMPLETED 까지 폴링한다 (REQ-OW-16, 17).

    상세(4-5)와 같은 정보의 부분집합이지만 따로 둔다. 폴링은 주기적으로 반복되는
    호출이라 35개 입력 항목과 분석 JSONB 를 매번 실어 보낼 이유가 없다.
    """
    report, _ = await _load_report_for_read(report_id, current_user, session)
    return ReportStatusResponse(
        report_id=report.id,
        status=report.status,
        analysis_error=report.analysis_error,
    )
