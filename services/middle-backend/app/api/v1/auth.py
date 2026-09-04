"""Auth 엔드포인트.

- `POST /auth/login`   — API_SPEC 1-1
- `POST /auth/refresh` — D2(Access + Refresh) 채택으로 추가
- `POST /auth/logout`  — API_SPEC 1-2가 "세션이면 필요"였고, D2 확정으로 필요로 확정됨

회원가입(`POST /auth/signup`)은 이번 Phase 범위 밖이다.
"""

from __future__ import annotations

from fastapi import APIRouter, Response, status

from app.api.deps import SessionDep
from app.errors import UNAUTHORIZED_401, VALIDATION_400
from app.schemas import (
    LoginRequest,
    LoginResponse,
    LoginUser,
    LogoutRequest,
    RefreshRequest,
    TokenPair,
)
from app.services import auth_service

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post(
    "/login",
    response_model=LoginResponse,
    responses={**VALIDATION_400, **UNAUTHORIZED_401},
)
async def login(body: LoginRequest, session: SessionDep) -> LoginResponse:
    user = await auth_service.authenticate(session, body.email, body.password)
    access_token, refresh_token, expires_in = await auth_service.issue_token_pair(
        session, user
    )
    await session.commit()
    return LoginResponse(
        token=access_token,
        refresh_token=refresh_token,
        expires_in=expires_in,
        user=LoginUser.model_validate(user),
    )


@router.post(
    "/refresh",
    response_model=TokenPair,
    responses={**VALIDATION_400, **UNAUTHORIZED_401},
)
async def refresh(body: RefreshRequest, session: SessionDep) -> TokenPair:
    _, access_token, new_refresh, expires_in = await auth_service.rotate_refresh_token(
        session, body.refresh_token
    )
    await session.commit()
    return TokenPair(
        token=access_token, refresh_token=new_refresh, expires_in=expires_in
    )


@router.post(
    "/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    responses={
        204: {"description": "폐기 완료 또는 이미 폐기됨 (멱등)"},
        **VALIDATION_400,
    },
)
async def logout(body: LogoutRequest, session: SessionDep) -> Response:
    await auth_service.revoke_refresh_token(session, body.refresh_token)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
