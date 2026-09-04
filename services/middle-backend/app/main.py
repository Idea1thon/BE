"""FastAPI 앱 엔트리포인트."""

from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi

from app.api.v1.router import api_router
from app.core.config import settings
from app.errors import register_error_handlers

app = FastAPI(
    title="중소 프랜차이즈 운영 지원 서비스 — Backend API",
    version="0.1.0",
    docs_url="/docs",
)

# CORS — 허용 Origin은 환경변수(CORS_ALLOW_ORIGINS)로 관리한다. 와일드카드는 설정에서 차단된다.
#
# allow_credentials=True로 둔 이유: 현재 인증은 Bearer 토큰이라 자격 증명 전송이 필요하지
# 않지만, 쿠키 기반으로 전환할 때 이 값이 False면 브라우저가 쿠키를 보내지 않아 조용히
# 인증이 깨진다. 와일드카드 Origin을 설정 단계에서 금지했으므로 True로 두어도 안전하다
# (브라우저는 `*` + credentials 조합을 거부하는데, 그 조합 자체가 발생할 수 없다).
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_allow_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)

register_error_handlers(app)
app.include_router(api_router)


@app.get("/health", tags=["meta"])
async def health() -> dict[str, str]:
    return {"status": "ok"}


def custom_openapi() -> dict[str, Any]:
    """FastAPI 기본 422 응답을 문서에서 제거한다.

    검증 실패는 `RequestValidationError` 핸들러가 400 `VALIDATION_ERROR`로 변환하므로
    런타임에서 422가 나오지 않는다. 런타임 동작은 건드리지 않고 문서만 실제와 맞춘다.
    """
    if app.openapi_schema:
        return app.openapi_schema

    schema = get_openapi(
        title=app.title,
        version=app.version,
        description=app.description,
        routes=app.routes,
    )
    for operations in schema.get("paths", {}).values():
        for operation in operations.values():
            operation.get("responses", {}).pop("422", None)

    app.openapi_schema = schema
    return schema


app.openapi = custom_openapi  # type: ignore[method-assign]
