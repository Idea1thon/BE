"""인증 서비스.

회원가입은 이번 Phase 범위 밖이다. 계정은 시드로만 생성한다.
향후 회원가입을 붙일 때는 `create_account()`만 추가하면 되도록
비밀번호 해시·계정 조회를 이 모듈에 모아 두었다.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import (
    create_access_token,
    generate_refresh_token,
    hash_refresh_token,
    verify_password,
)
from app.errors import unauthorized
from app.models import RefreshToken, UserAccount


async def get_active_user_by_email(
    session: AsyncSession, email: str
) -> UserAccount | None:
    result = await session.execute(
        select(UserAccount).where(UserAccount.email == email)
    )
    return result.unique().scalar_one_or_none()


async def get_user_by_id(session: AsyncSession, user_id: int) -> UserAccount | None:
    result = await session.execute(
        select(UserAccount).where(UserAccount.id == user_id)
    )
    return result.unique().scalar_one_or_none()


async def authenticate(
    session: AsyncSession, email: str, password: str
) -> UserAccount:
    """실패 사유를 응답에서 구분하지 않는다 (계정 열거 방지).

    REQ-AUTH-04는 사유 구분 표시를 요구하지만 구분 수준이 미확정([결정 필요] D3)이다.
    확정 전 기본값으로 통합 메시지를 쓴다.
    """
    user = await get_active_user_by_email(session, email)
    if user is None or not verify_password(password, user.password_hash):
        raise unauthorized("이메일 또는 비밀번호가 올바르지 않습니다")
    if not user.is_active:
        raise unauthorized("비활성화된 계정입니다")

    await session.execute(
        update(UserAccount)
        .where(UserAccount.id == user.id)
        .values(last_login_at=datetime.now(UTC))
    )
    return user


async def issue_token_pair(
    session: AsyncSession, user: UserAccount
) -> tuple[str, str, int]:
    """(access_token, refresh_token_raw, expires_in)."""
    access_token, expires_in = create_access_token(user.id, user.user_type.value)
    raw, token_hash, expires_at = generate_refresh_token()
    session.add(
        RefreshToken(user_id=user.id, token_hash=token_hash, expires_at=expires_at)
    )
    return access_token, raw, expires_in


async def rotate_refresh_token(
    session: AsyncSession, raw_token: str
) -> tuple[UserAccount, str, str, int]:
    """유효한 Refresh Token을 폐기하고 새 쌍을 발급한다.

    **원자적 소비**: 조회 후 갱신(select-then-update)이 아니라 조건부 UPDATE 한 번으로
    처리한다. 동일 토큰으로 동시에 여러 요청이 들어와도 PostgreSQL이 행 잠금을 걸고,
    잠금을 얻은 트랜잭션이 `revoked_at`을 채운 뒤에는 나머지 요청의 WHERE 조건
    (`revoked_at IS NULL`)이 재평가되어 성립하지 않는다. 결과적으로 한 요청만 성공한다.
    """
    result = await session.execute(
        update(RefreshToken)
        .where(
            RefreshToken.token_hash == hash_refresh_token(raw_token),
            RefreshToken.revoked_at.is_(None),
            RefreshToken.expires_at > func.now(),
        )
        .values(revoked_at=func.now())
        .returning(RefreshToken.user_id)
    )
    row = result.first()
    if row is None:
        # 미존재 / 이미 폐기됨(회전 경쟁에서 패배 포함) / 만료. 사유를 구분하지 않는다.
        raise unauthorized("유효하지 않은 토큰입니다")

    user = await get_user_by_id(session, row.user_id)
    if user is None or not user.is_active:
        raise unauthorized("유효하지 않은 토큰입니다")

    access_token, new_raw, expires_in = await issue_token_pair(session, user)
    return user, access_token, new_raw, expires_in


async def revoke_refresh_token(session: AsyncSession, raw_token: str) -> None:
    """로그아웃. 이미 폐기·만료된 토큰이어도 성공으로 취급한다(멱등).

    회전과 같은 이유로 조건부 UPDATE를 쓴다.
    """
    await session.execute(
        update(RefreshToken)
        .where(
            RefreshToken.token_hash == hash_refresh_token(raw_token),
            RefreshToken.revoked_at.is_(None),
        )
        .values(revoked_at=func.now())
    )
