"""알림 조회·읽음 처리. API_SPEC 5-1 · 5-2.

`branch_name`·`risk_level`은 컬럼이 아니라 조인 결과다 (DB_SCHEMA 4-10).

전체를 한 번에 반환하지 않는다. 사용자 이력이 늘어나면 한 요청이 전체 행을
정렬·직렬화하면서 DB·메모리·응답 대역폭을 모두 소모한다. 정렬 키와 같은
`(created_at DESC, id DESC)` 기준의 keyset 페이지네이션을 쓴다. OFFSET은 뒤로 갈수록
건너뛴 행을 계속 읽으므로 쓰지 않는다.
"""

from __future__ import annotations

import base64
import binascii
from datetime import datetime

from fastapi import APIRouter, Query
from sqlalchemy import func, select, tuple_, update

from app.api.deps import CurrentUser, SessionDep
from app.errors import (
    FORBIDDEN_403,
    NOT_FOUND_404,
    UNAUTHORIZED_401,
    VALIDATION_400,
    forbidden,
    not_found,
    validation_error,
)
from app.models import Branch, Notification, OperationReport, ReportAnalysis
from app.schemas import (
    NotificationItem,
    NotificationListResponse,
    NotificationReadResponse,
)

router = APIRouter(prefix="/notifications", tags=["notifications"])

DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 100


def _encode_cursor(created_at: datetime, notification_id: int) -> str:
    raw = f"{created_at.isoformat()}|{notification_id}".encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _decode_cursor(cursor: str) -> tuple[datetime, int]:
    padded = cursor + "=" * (-len(cursor) % 4)
    try:
        created_at_raw, id_raw = base64.urlsafe_b64decode(padded).decode("utf-8").split("|", 1)
        return datetime.fromisoformat(created_at_raw), int(id_raw)
    except (binascii.Error, UnicodeDecodeError, ValueError) as exc:
        # 커서는 서버가 만든 값이다. 깨진 값은 500이 아니라 400이다.
        raise validation_error("cursor 형식이 올바르지 않습니다") from exc


@router.get(
    "",
    response_model=NotificationListResponse,
    responses={**VALIDATION_400, **UNAUTHORIZED_401},
)
async def list_notifications(
    current_user: CurrentUser,
    session: SessionDep,
    is_read: bool | None = Query(default=None, description="미확인만 조회 시 false"),
    limit: int = Query(default=DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
    cursor: str | None = Query(default=None, description="이전 응답의 next_cursor"),
) -> NotificationListResponse:
    # REQ-HQ-10 미확인 뱃지. 필터·페이지와 무관하게 항상 전체 미확인 수를 센다.
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
    if cursor is not None:
        last_created_at, last_id = _decode_cursor(cursor)
        # 행 비교. 정렬 키와 같은 순서라 인덱스를 그대로 탄다.
        stmt = stmt.where(
            tuple_(Notification.created_at, Notification.id) < (last_created_at, last_id)
        )

    # 다음 페이지 유무를 알기 위해 한 건 더 읽고, 응답에서는 잘라낸다.
    rows = (await session.execute(stmt.limit(limit + 1))).all()
    has_more = len(rows) > limit
    rows = rows[:limit]

    next_cursor = _encode_cursor(rows[-1][0].created_at, rows[-1][0].id) if has_more else None

    return NotificationListResponse(
        unread_count=unread_count,
        next_cursor=next_cursor,
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


@router.patch(
    "/{notification_id}/read",
    response_model=NotificationReadResponse,
    responses={**UNAUTHORIZED_401, **FORBIDDEN_403, **NOT_FOUND_404},
)
async def mark_notification_read(
    notification_id: int, current_user: CurrentUser, session: SessionDep
) -> NotificationReadResponse:
    """API_SPEC 5-2. REQ-HQ-10 의 미확인 뱃지가 줄어들려면 필요하다.

    **멱등**: 이미 읽은 알림에 다시 호출해도 200 이고 `read_at` 은 최초 시각을
    유지한다. 갱신을 `is_read = false` 조건부 UPDATE 로 두는 이유가 이것이다 —
    무조건 `values(read_at=now())` 면 재호출마다 시각이 밀려 "언제 읽었나"가
    사라진다. 동시 요청에서도 한쪽만 WHERE 를 통과한다.
    """
    row = (
        await session.execute(
            select(
                Notification.recipient_user_id,
                Notification.is_read,
                Notification.read_at,
            ).where(Notification.id == notification_id)
        )
    ).first()
    if row is None:
        raise not_found("알림을 찾을 수 없습니다")
    if row.recipient_user_id != current_user.id:
        raise forbidden("본인 수신 알림만 처리할 수 있습니다")
    if row.is_read:
        return NotificationReadResponse(
            notification_id=notification_id, is_read=True, read_at=row.read_at
        )

    updated = (
        await session.execute(
            update(Notification)
            .where(
                Notification.id == notification_id,
                Notification.recipient_user_id == current_user.id,
                Notification.is_read.is_(False),
            )
            .values(is_read=True, read_at=func.now())
            .returning(Notification.read_at)
        )
    ).first()
    await session.commit()

    if updated is None:
        # 위 SELECT 와 UPDATE 사이에 다른 요청이 먼저 읽음 처리했다. 그쪽 시각을 쓴다.
        read_at = (
            await session.execute(
                select(Notification.read_at).where(Notification.id == notification_id)
            )
        ).scalar_one()
        return NotificationReadResponse(
            notification_id=notification_id, is_read=True, read_at=read_at
        )

    return NotificationReadResponse(
        notification_id=notification_id, is_read=True, read_at=updated.read_at
    )
