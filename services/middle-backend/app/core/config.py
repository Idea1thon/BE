"""애플리케이션 설정. 환경변수 기반 (.env)."""

from functools import lru_cache
from typing import Annotated

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

# 저장소·예시 파일에 실려 있던 값. 어떤 환경에서도 사용을 막는다.
_KNOWN_WEAK_SECRETS = frozenset({"dev-only-change-me", "change-me", "changeme", "secret"})
_MIN_JWT_SECRET_LENGTH = 32
_SUPPORTED_JWT_ALGORITHMS = frozenset({"HS256", "HS384", "HS512"})


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    environment: str = "development"

    # DB
    database_url: str = "postgresql+asyncpg://localhost:5432/fmp"
    db_echo: bool = False
    # 테스트에서 이벤트 루프가 매번 바뀌는 환경 대응 (asyncpg 커넥션은 루프에 묶인다)
    db_use_null_pool: bool = False

    # 인증 (D2: JWT Bearer Access Token + Refresh Token)
    # JWT_SECRET에는 기본값을 두지 않는다. 기본값이 있으면 .env 없이 기동했을 때
    # 저장소에 공개된 값으로 임의 계정 토큰을 위조할 수 있다.
    jwt_secret: str = ""
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 30
    refresh_token_expire_days: int = 14

    # 시드 전용 (scripts/seed.py). 기본값을 두지 않는다 — 알려진 비밀번호로 계정이 생긴다.
    seed_password: str = ""

    # 입지 추천 서비스 (services/recommendation-api). 서버 간 호출 전용이다.
    #
    # 토큰에 기본값을 두지 않는다. 값이 없으면 추천 API 가 503 fail closed 로
    # 답하는데, 그쪽이 의도적으로 그렇게 만들어져 있으므로 우리도 맞춘다.
    # 설정 누락이 "추천 결과가 비어 있다" 로 조용히 나타나면 원인을 못 찾는다.
    recommendation_api_url: str = "http://localhost:8001"
    internal_api_token: str = ""

    # 추천 서비스의 파이프라인 실행 상한. **상대와 같은 환경변수 이름을 읽는다**
    # (RECOMMENDATION_REQUEST_TIMEOUT_SECONDS). compose 가 두 서비스에 같은 값을
    # 넣으므로 운영자가 한 곳만 바꾸면 양쪽이 함께 움직인다.
    #
    # 우리 HTTP 타임아웃을 이 값보다 짧게 두면 안 된다. 우리가 먼저 끊으면 상대는
    # 계속 돌고 있는데 run_id 를 못 받아 폴링조차 못 하는 상태가 된다. 그래서
    # 아래 여유를 더한 값을 쓴다.
    recommendation_request_timeout_seconds: float = 180.0
    recommendation_timeout_margin_seconds: float = 10.0

    @property
    def recommendation_client_timeout(self) -> float:
        return (
            self.recommendation_request_timeout_seconds
            + self.recommendation_timeout_margin_seconds
        )

    # 위험도 사이렌 서비스 (services/siren). 서버 간 호출 전용이다.
    #
    # 상대 api.py 에는 토큰 검사가 없다. 공개 네트워크에 노출하면 누구나 부를 수
    # 있으므로 compose 내부 네트워크에만 둔다. 토큰이 붙는 날을 대비해
    # INTERNAL_API_TOKEN 이 있으면 헤더로 실어 보낸다(지금은 무시된다).
    #
    # 추천과 달리 순수 계산 동기 호출이라 타임아웃이 짧아도 된다. 그래도 12개월치
    # 보고서를 한 번에 보내므로 기본 60초를 둔다.
    siren_api_url: str = "http://localhost:8002"
    siren_request_timeout_seconds: float = 60.0

    # CORS — FE(Vite dev server)가 브라우저에서 호출한다.
    # 쉼표로 구분된 문자열 또는 JSON 배열을 받는다. 와일드카드는 허용하지 않는다.
    # NoDecode: pydantic-settings가 환경변수를 JSON으로 먼저 파싱하지 않게 한다
    #           (그래야 "a,b" 형태의 쉼표 구분 값을 아래 validator에서 처리할 수 있다).
    cors_allow_origins: Annotated[list[str], NoDecode] = ["http://localhost:5173"]

    @field_validator("cors_allow_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        if isinstance(value, str):
            text = value.strip()
            if text.startswith("["):
                import json

                return json.loads(text)
            return [item.strip() for item in text.split(",") if item.strip()]
        return value

    @field_validator("cors_allow_origins")
    @classmethod
    def _reject_wildcard_origin(cls, value: list[str]) -> list[str]:
        if "*" in value:
            raise ValueError(
                "CORS_ALLOW_ORIGINS must list explicit origins. "
                "'*' is not allowed — allow_credentials=True와 함께 쓰면 브라우저가 "
                "요청을 거부하고, 쿠키 인증으로 전환할 때 보안 구멍이 된다."
            )
        return value

    @field_validator("jwt_algorithm")
    @classmethod
    def _supported_algorithm(cls, value: str) -> str:
        if value not in _SUPPORTED_JWT_ALGORITHMS:
            raise ValueError(
                f"JWT_ALGORITHM must be one of {sorted(_SUPPORTED_JWT_ALGORITHMS)} (got {value!r})"
            )
        return value

    @model_validator(mode="after")
    def _require_strong_jwt_secret(self) -> "Settings":
        """개발 환경을 포함해 모든 환경에서 JWT_SECRET을 요구한다.

        환경으로 조건을 걸면 ENVIRONMENT가 누락됐을 때 기본값 'development'로
        떨어져 가드가 통과한다. 그래서 환경과 무관하게 검사한다.
        """
        secret = self.jwt_secret.strip()
        if not secret:
            raise ValueError(
                "JWT_SECRET is required. 생성: "
                "python -c \"import secrets; print(secrets.token_urlsafe(48))\""
            )
        if secret in _KNOWN_WEAK_SECRETS:
            raise ValueError("JWT_SECRET must not be a placeholder value shipped with this repository")
        if len(secret) < _MIN_JWT_SECRET_LENGTH:
            raise ValueError(
                f"JWT_SECRET must be at least {_MIN_JWT_SECRET_LENGTH} characters (got {len(secret)})"
            )
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
