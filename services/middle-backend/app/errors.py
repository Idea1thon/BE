"""API_SPEC.md 0-3의 공통 오류 봉투."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger("app.errors")


class ApiError(Exception):
    """API_SPEC 0-3의 오류 코드를 그대로 사용한다."""

    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        extra: dict[str, Any] | None = None,
    ) -> None:
        self.status_code = status_code
        self.code = code
        self.message = message
        self.extra = extra or {}
        super().__init__(message)


def unauthorized(message: str = "인증이 필요합니다") -> ApiError:
    return ApiError(status.HTTP_401_UNAUTHORIZED, "UNAUTHORIZED", message)


def forbidden(message: str = "접근 권한이 없습니다") -> ApiError:
    return ApiError(status.HTTP_403_FORBIDDEN, "FORBIDDEN", message)


def not_found(message: str = "리소스를 찾을 수 없습니다") -> ApiError:
    return ApiError(status.HTTP_404_NOT_FOUND, "NOT_FOUND", message)


def conflict(message: str = "이미 존재하는 리소스입니다") -> ApiError:
    return ApiError(status.HTTP_409_CONFLICT, "CONFLICT", message)


def validation_error(message: str, extra: dict[str, Any] | None = None) -> ApiError:
    return ApiError(
        status.HTTP_400_BAD_REQUEST, "VALIDATION_ERROR", message, extra
    )


def _envelope(code: str, message: str, extra: dict[str, Any] | None = None) -> dict:
    error: dict[str, Any] = {
        "code": code,
        "message": message,
        "occurred_at": datetime.now(UTC).astimezone().isoformat(timespec="seconds"),
    }
    if extra:
        error.update(extra)
    return {"error": error}


# OpenAPI 문서용 응답 스펙. 런타임 동작에는 영향이 없다.
# RequestValidationError를 400 VALIDATION_ERROR로 변환하므로 422는 발생하지 않는다.
ERROR_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "error": {
            "type": "object",
            "properties": {
                "code": {"type": "string"},
                "message": {"type": "string"},
                "occurred_at": {"type": "string", "format": "date-time"},
            },
        }
    },
}


def error_response(description: str) -> dict:
    return {
        "description": description,
        "content": {"application/json": {"schema": ERROR_RESPONSE_SCHEMA}},
    }


VALIDATION_400 = {400: error_response("VALIDATION_ERROR — 필수 필드 누락·형식 오류")}
UNAUTHORIZED_401 = {401: error_response("UNAUTHORIZED — 토큰 없음·만료·무효")}
FORBIDDEN_403 = {403: error_response("FORBIDDEN — 권한 범위 밖")}
NOT_FOUND_404 = {404: error_response("NOT_FOUND — 리소스 없음")}
CONFLICT_409 = {409: error_response("CONFLICT — 유니크 제약 위반")}


_STATUS_TO_CODE = {
    400: "VALIDATION_ERROR",
    401: "UNAUTHORIZED",
    403: "FORBIDDEN",
    404: "NOT_FOUND",
    409: "CONFLICT",
    500: "INTERNAL_ERROR",
}


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(_: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content=_envelope(exc.code, exc.message, exc.extra),
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = _STATUS_TO_CODE.get(exc.status_code, "INTERNAL_ERROR")
        return JSONResponse(
            status_code=exc.status_code,
            content=_envelope(code, str(exc.detail)),
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        first = exc.errors()[0] if exc.errors() else {}
        loc = ".".join(str(p) for p in first.get("loc", []) if p != "body")
        message = f"{loc}: {first.get('msg', 'invalid request')}" if loc else "invalid request"
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content=_envelope("VALIDATION_ERROR", message),
        )

    @app.exception_handler(Exception)
    async def _unhandled_error(request: Request, exc: Exception) -> JSONResponse:
        """예상하지 못한 예외를 공통 오류 봉투로 반환한다 (REQ-NFR-07).

        내부 상세(예외 타입·메시지·스택)는 응답에 담지 않고 서버 로그에만 남긴다.
        """
        logger.exception(
            "unhandled error on %s %s", request.method, request.url.path, exc_info=exc
        )
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=_envelope("INTERNAL_ERROR", "서버 내부 오류가 발생했습니다"),
        )
