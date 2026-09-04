"""알림 조회. API_SPEC 5-1.

`branch_name`·`risk_level`은 컬럼이 아니라 조인 결과다 (DB_SCHEMA 4-10).
페이지네이션은 [결정 필요](0-5)이므로 전체 반환한다. `items` 래퍼로 계약을 유지한다.
"""

from __future__ import annotations

from fastapi import APIRouter, Query
from sqlalchemy import func, select

from app.api.deps import CurrentUser, SessionDep
from app.errors import UNAUTHORIZED_401
from app.models import Branch, Notification, OperationReport, ReportAnalysis
from app.schemas import NotificationItem, NotificationListResponse

router = APIRouter(prefix="/notifications", tags=["notifications"])


@router.get(
    "",
    response_model=NotificationListResponse,
    responses={**UNAUTHORIZED_401},
)
async def list_notifications(
    current_user: CurrentUser,
    session: SessionDep,
    is_read: bool | None = Query(default=None, description="미확인만 조회 시 false"),
) -> NotificationListResponse:
    # REQ-HQ-10 미확인 뱃지. 필터와 무관하게 항상 전체 미확인 수를 센다.
    unread_count = (
        await session.execute(
            select(func.count())
            .select_from(Notification)
            .where(
                Notification.recipient_user_id == current_user.id,
                Notification.is_read.is_(False),
            )
        )
    ).scalar_one()

    stmt = (
        select(Notification, Branch.name, ReportAnalysis.risk_level)
        .join(OperationReport, OperationReport.id == Notification.report_id)
        .join(Branch, Branch.id == OperationReport.branch_id)
        .outerjoin(ReportAnalysis, ReportAnalysis.report_id == OperationReport.id)
        .where(Notification.recipient_user_id == current_user.id)
        .order_by(Notification.created_at.desc(), Notification.id.desc())
    )
    if is_read is not None:
        stmt = stmt.where(Notification.is_read.is_(is_read))

    rows = (await session.execute(stmt)).all()
    return NotificationListResponse(
        unread_count=unread_count,
        items=[
            NotificationItem(
                notification_id=n.id,
                message=n.message,
                is_read=n.is_read,
                created_at=n.created_at,
                report_id=n.report_id,
                branch_name=branch_name,
                risk_level=risk_level,
            )
            for n, branch_name, risk_level in rows
        ],
    )
