"""공통 의존성. 인증된 사용자 확정 (REQ-NFR-04)."""

from __future__ import annotations

from typing import Annotated

import jwt
from fastapi import Depends, Header
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import decode_access_token
from app.db.session import get_session
from app.errors import forbidden, unauthorized
from app.models import UserAccount
from app.models.enums import UserType
from app.services import auth_service

SessionDep = Annotated[AsyncSession, Depends(get_session)]


async def get_current_user(
    session: SessionDep,
    authorization: Annotated[str | None, Header()] = None,
) -> UserAccount:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise unauthorized("Authorization 헤더가 필요합니다")

    token = authorization.split(" ", 1)[1].strip()
    try:
        payload = decode_access_token(token)
    except jwt.ExpiredSignatureError as exc:
        raise unauthorized("토큰이 만료되었습니다") from exc
    except jwt.InvalidTokenError as exc:
        raise unauthorized("유효하지 않은 토큰입니다") from exc

    # 서명은 유효하지만 클레임이 규격에 맞지 않는 경우도 401이다.
    # (`sub` 누락, 숫자 아님 등 — 500으로 새지 않게 한다)
    try:
        user_id = int(payload["sub"])
    except (KeyError, TypeError, ValueError) as exc:
        raise unauthorized("유효하지 않은 토큰입니다") from exc

    user = await auth_service.get_user_by_id(session, user_id)
    if user is None or not user.is_active:
        raise unauthorized("유효하지 않은 계정입니다")
    return user


CurrentUser = Annotated[UserAccount, Depends(get_current_user)]


async def require_owner(current_user: CurrentUser) -> UserAccount:
    """가맹점주 전용 API. REQ-AUTH-07."""
    if current_user.user_type is not UserType.OWNER:
        raise forbidden("가맹점주 계정만 접근할 수 있습니다")
    return current_user


OwnerUser = Annotated[UserAccount, Depends(require_owner)]


def authorize_branch(user: UserAccount, franchise_id: int, owner_user_id: int) -> None:
    """점포 접근 권한. HQ는 자사 소속만, OWNER는 본인 점포만 (REQ-AUTH-06/07).

    점포 자체를 조회하는 3-2 와 점포에 딸린 보고서를 조회하는 4-4·4-5·4-6 이
    같은 규칙을 쓴다. 규칙이 갈라지면 한쪽만 고치는 사고가 난다.
    """
    if user.user_type is UserType.HQ:
        allowed = franchise_id == user.franchise_id
    else:
        allowed = owner_user_id == user.id
    if not allowed:
        raise forbidden("해당 가맹점에 접근할 권한이 없습니다")


async def require_hq(current_user: CurrentUser) -> UserAccount:
    """본사 전용 API. REQ-AUTH-06."""
    if current_user.user_type is not UserType.HQ:
        raise forbidden("본사 계정만 접근할 수 있습니다")
    return current_user


HqUser = Annotated[UserAccount, Depends(require_hq)]
