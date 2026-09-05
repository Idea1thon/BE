"""운영보고서. API_SPEC 4-1(입력 항목) · 4-2(제출).

목록(4-4)·상세(4-5)는 이후 Phase다.
"""

from __future__ import annotations

from fastapi import APIRouter, status
from sqlalchemy import select

from app.api.deps import OwnerUser, SessionDep
from app.errors import CONFLICT_409, FORBIDDEN_403, UNAUTHORIZED_401, VALIDATION_400
from app.models import ReportInputField
from app.schemas import (
    InputFieldItem,
    InputFieldListResponse,
    ReportCreateRequest,
    ReportCreateResponse,
)
from app.services import report_service

router = APIRouter(prefix="/reports", tags=["reports"])


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
    body: ReportCreateRequest, owner: OwnerUser, session: SessionDep
) -> ReportCreateResponse:
    """REQ-OW-11~16. 점포는 인증 사용자로 결정한다 — 요청으로 받지 않는다.

    202 인 이유는 분석이 비동기이고 FE 가 폴링하기 때문이다(REQ-OW-16).
    분석 서비스 연동 전까지 보고서는 ANALYZING 상태로 남는다.
    """
    report = await report_service.create_report(session, owner, body)
    return ReportCreateResponse(
        report_id=report.id,
        status=report.status,
        analysis_request_id=report.analysis_request_id,
    )
