"""운영보고서 관련 조회. API_SPEC 4-1.

보고서 생성(4-2)·목록(4-4)·상세(4-5)는 이후 Phase다.
"""

from __future__ import annotations

from fastapi import APIRouter
from sqlalchemy import select

from app.api.deps import OwnerUser, SessionDep
from app.errors import FORBIDDEN_403, UNAUTHORIZED_401
from app.models import ReportInputField
from app.schemas import InputFieldItem, InputFieldListResponse

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
