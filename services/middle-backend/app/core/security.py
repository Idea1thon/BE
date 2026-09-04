"""비밀번호 해시와 JWT 발급·검증.

- 비밀번호: bcrypt. `user_account.password_hash` VARCHAR(255)에 저장 (DB_SCHEMA 4-4).
- Access Token: 무상태 JWT. DB에 저장하지 않는다.
- Refresh Token: 원문은 클라이언트에만, 서버는 SHA-256 해시를 `refresh_token` 테이블에 저장 (DB_SCHEMA 4-12).
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import UTC, datetime, timedelta

import bcrypt
import jwt

from app.core.config import settings

ACCESS_TOKEN_TYPE = "access"


# --------------------------------------------------------------------------
# 비밀번호
# --------------------------------------------------------------------------
def hash_password(plain: str) -> str:
    """bcrypt 해시 문자열을 반환한다. 평문은 어디에도 저장하지 않는다."""
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), password_hash.encode("utf-8"))
    except ValueError:
        # 해시 형식이 깨진 경우도 인증 실패로 처리한다.
        return False


# --------------------------------------------------------------------------
# Access Token (JWT)
# --------------------------------------------------------------------------
def create_access_token(user_id: int, user_type: str) -> tuple[str, int]:
    """(token, expires_in_seconds)를 반환한다."""
    now = datetime.now(UTC)
    expires_delta = timedelta(minutes=settings.access_token_expire_minutes)
    payload = {
        "sub": str(user_id),
        "user_type": user_type,
        "type": ACCESS_TOKEN_TYPE,
        "iat": int(now.timestamp()),
        "exp": int((now + expires_delta).timestamp()),
        "jti": uuid.uuid4().hex,
    }
    token = jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)
    return token, int(expires_delta.total_seconds())


def decode_access_token(token: str) -> dict:
    """검증 실패 시 jwt 예외를 그대로 올린다. 호출자가 401로 변환한다."""
    payload = jwt.decode(
        token, settings.jwt_secret, algorithms=[settings.jwt_algorithm]
    )
    if payload.get("type") != ACCESS_TOKEN_TYPE:
        raise jwt.InvalidTokenError("not an access token")
    return payload


# --------------------------------------------------------------------------
# Refresh Token (불투명 문자열 + 해시 저장)
# --------------------------------------------------------------------------
def generate_refresh_token() -> tuple[str, str, datetime]:
    """(원문, 해시, 만료시각)을 반환한다.

    JWT가 아닌 난수 문자열을 쓴다. 서버가 폐기 여부를 DB로 판정하므로
    토큰 자체에 클레임을 담을 이유가 없고, 길이도 짧다.
    """
    raw = secrets.token_urlsafe(48)
    expires_at = datetime.now(UTC) + timedelta(days=settings.refresh_token_expire_days)
    return raw, hash_refresh_token(raw), expires_at


def hash_refresh_token(raw: str) -> str:
    return "sha256:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()
