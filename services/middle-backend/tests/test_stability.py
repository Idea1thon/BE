"""안정성 보완 검증.

1. Refresh token 회전의 원자성 (동시 요청 시 1건만 성공)
2. JWT 클레임 오류의 401 정규화
3. 예상하지 못한 예외의 500 공통 오류 봉투
4. CORS 설정
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import jwt
import pytest
from fastapi import APIRouter

from app.core.config import settings

LOGIN = "/api/v1/auth/login"
REFRESH = "/api/v1/auth/refresh"
ME = "/api/v1/users/me"

OWNER = {"email": "owner1@example.com", "password": "devpass1234"}
DEV_ORIGIN = "http://localhost:5173"


async def _login(client) -> dict:
    res = await client.post(LOGIN, json=OWNER)
    assert res.status_code == 200, res.text
    return res.json()


def _signed(payload: dict) -> str:
    """서명은 유효하지만 클레임이 규격 밖인 access token을 만든다."""
    now = datetime.now(UTC)
    base = {
        "type": "access",
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=5)).timestamp()),
    }
    return jwt.encode(
        {**base, **payload}, settings.jwt_secret, algorithm=settings.jwt_algorithm
    )


# ------------------------------------------------------------------ 1. 회전 원자성
async def test_concurrent_refresh_consumes_token_once(client):
    """같은 refresh token으로 동시에 5번 요청하면 1건만 성공해야 한다."""
    tokens = await _login(client)

    responses = await asyncio.gather(
        *[
            client.post(REFRESH, json={"refresh_token": tokens["refresh_token"]})
            for _ in range(5)
        ]
    )
    codes = [r.status_code for r in responses]

    assert codes.count(200) == 1, f"성공이 1건이 아니다: {codes}"
    assert codes.count(401) == 4, f"나머지가 401이 아니다: {codes}"

    for res in responses:
        if res.status_code == 401:
            assert res.json()["error"]["code"] == "UNAUTHORIZED"

    # 성공한 응답의 새 토큰은 정상 동작한다
    winner = next(r for r in responses if r.status_code == 200).json()
    me = await client.get(ME, headers={"Authorization": f"Bearer {winner['token']}"})
    assert me.status_code == 200


async def test_concurrent_refresh_issues_exactly_one_new_token(client):
    """DB에도 새 토큰이 1건만 남아야 한다 (한 토큰 → 여러 토큰 방지)."""
    from sqlalchemy import func, select

    from app.db.session import SessionLocal
    from app.models import RefreshToken, UserAccount

    tokens = await _login(client)

    async with SessionLocal() as session:
        user_id = (
            await session.execute(
                select(UserAccount.id).where(UserAccount.email == OWNER["email"])
            )
        ).scalar_one()
        before = (
            await session.execute(
                select(func.count())
                .select_from(RefreshToken)
                .where(
                    RefreshToken.user_id == user_id,
                    RefreshToken.revoked_at.is_(None),
                )
            )
        ).scalar_one()

    await asyncio.gather(
        *[
            client.post(REFRESH, json={"refresh_token": tokens["refresh_token"]})
            for _ in range(5)
        ]
    )

    async with SessionLocal() as session:
        after = (
            await session.execute(
                select(func.count())
                .select_from(RefreshToken)
                .where(
                    RefreshToken.user_id == user_id,
                    RefreshToken.revoked_at.is_(None),
                )
            )
        ).scalar_one()

    # 사용한 토큰 1건이 폐기되고 새 토큰 1건이 생긴다 → 유효 토큰 수는 그대로다.
    assert after == before, f"유효 refresh token 수가 늘었다: {before} -> {after}"


# ------------------------------------------------------------------ 2. JWT 클레임 오류
@pytest.mark.parametrize(
    ("name", "payload"),
    [
        ("sub 누락", {"user_type": "OWNER"}),
        ("sub 문자열", {"sub": "not-a-number", "user_type": "OWNER"}),
        ("sub None", {"sub": None, "user_type": "OWNER"}),
        ("sub 실수 문자열", {"sub": "1.5", "user_type": "OWNER"}),
        ("sub 리스트", {"sub": [1], "user_type": "OWNER"}),
    ],
)
async def test_malformed_jwt_claims_return_401(client, name, payload):
    token = _signed(payload)
    res = await client.get(ME, headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 401, f"{name}: {res.status_code} {res.text}"
    assert res.json()["error"]["code"] == "UNAUTHORIZED"


async def test_wrong_token_type_returns_401(client):
    """refresh 용도의 type을 가진 토큰은 access token으로 쓸 수 없다."""
    token = _signed({"sub": "1", "type": "refresh"})
    res = await client.get(ME, headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 401


async def test_unknown_user_id_returns_401(client):
    token = _signed({"sub": "999999", "user_type": "OWNER"})
    res = await client.get(ME, headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 401


# ------------------------------------------------------------------ 3. 500 오류 봉투
@pytest.fixture
def boom_route():
    """의도적으로 예외를 던지는 임시 라우트. 운영 라우터에는 추가하지 않는다."""
    from app.main import app

    router = APIRouter()

    @router.get("/__test_boom")
    async def _boom() -> None:
        raise RuntimeError("intentional failure with secret=abc123")

    app.include_router(router)
    yield "/__test_boom"
    app.router.routes = [
        r for r in app.router.routes if getattr(r, "path", None) != "/__test_boom"
    ]
    app.openapi_schema = None


async def test_unhandled_exception_returns_500_envelope(client, boom_route):
    res = await client.get(boom_route)
    assert res.status_code == 500
    error = res.json()["error"]
    assert error["code"] == "INTERNAL_ERROR"
    assert "occurred_at" in error
    # 내부 상세가 새지 않아야 한다
    assert "secret=abc123" not in res.text
    assert "RuntimeError" not in res.text
    assert "Traceback" not in res.text


# ------------------------------------------------------------------ 4. CORS
async def test_cors_preflight_allows_dev_origin(client):
    res = await client.options(
        LOGIN,
        headers={
            "Origin": DEV_ORIGIN,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization,content-type",
        },
    )
    assert res.status_code == 200, res.text
    assert res.headers["access-control-allow-origin"] == DEV_ORIGIN
    assert res.headers["access-control-allow-credentials"] == "true"
    assert "POST" in res.headers["access-control-allow-methods"]
    allowed_headers = res.headers["access-control-allow-headers"].lower()
    assert "authorization" in allowed_headers
    assert "content-type" in allowed_headers


async def test_cors_simple_request_echoes_origin(client):
    res = await client.get("/health", headers={"Origin": DEV_ORIGIN})
    assert res.status_code == 200
    assert res.headers["access-control-allow-origin"] == DEV_ORIGIN


async def test_cors_rejects_unknown_origin(client):
    res = await client.get("/health", headers={"Origin": "http://evil.example.com"})
    # 요청 자체는 처리되지만 CORS 허용 헤더가 없어 브라우저가 응답을 차단한다.
    assert "access-control-allow-origin" not in res.headers


async def test_cors_preflight_rejects_unknown_origin(client):
    res = await client.options(
        LOGIN,
        headers={
            "Origin": "http://evil.example.com",
            "Access-Control-Request-Method": "POST",
        },
    )
    assert "access-control-allow-origin" not in res.headers


def test_cors_default_origin_is_vite_dev_server():
    assert settings.cors_allow_origins == [DEV_ORIGIN]


def test_cors_settings_parse_comma_separated_and_reject_wildcard():
    from pydantic import ValidationError

    from app.core.config import Settings

    parsed = Settings(
        cors_allow_origins="https://a.example.com, https://b.example.com"
    )
    assert parsed.cors_allow_origins == [
        "https://a.example.com",
        "https://b.example.com",
    ]

    with pytest.raises(ValidationError):
        Settings(cors_allow_origins="*")
