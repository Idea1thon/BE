"""애플리케이션 설정. 환경변수 기반 (.env)."""

from functools import lru_cache
from typing import Annotated

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

_INSECURE_DEFAULT_SECRET = "dev-only-change-me"


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
    jwt_secret: str = _INSECURE_DEFAULT_SECRET
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 30
    refresh_token_expire_days: int = 14

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

    @model_validator(mode="after")
    def _reject_default_secret_outside_dev(self) -> "Settings":
        if self.environment != "development" and self.jwt_secret == _INSECURE_DEFAULT_SECRET:
            raise ValueError(
                "JWT_SECRET must be set explicitly when ENVIRONMENT is not 'development'"
            )
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
