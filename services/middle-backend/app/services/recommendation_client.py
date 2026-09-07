"""통합 pipeline-api의 입지 추천 HTTP 클라이언트.

추천 endpoint를 서버 간 호출로 부른다. 브라우저에 노출하지
않는 이유는 `X-Internal-Token` 때문이다 — 그 값이 사용자에게 보이는 순간
누구나 추천 API 를 직접 부를 수 있다.

**호출 규약** (상대 쪽 api/main.py 기준)

  POST /internal/recommendations
    200  완료. 본문에 candidates·explanations 가 다 들어 있다
    504  180초 초과. run_id 를 주고 폴링하라는 뜻이다 (실패가 아니다)
    429  동시 실행 슬롯 초과. Retry-After 헤더가 온다
    422  미지원 지역·업종
    401  토큰 불일치 / 503 토큰 미설정

  GET /internal/recommendations/{run_id}
    200  완료  /  202 실행 중  /  404 없음

즉 이 서비스는 "동기 호출 + 오래 걸리면 폴링" 구조다. 우리 엔드포인트도
그대로 옮긴다 — 여기서 구조를 바꾸면 양쪽 계약이 어긋난다.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from app.core.config import settings
from app.errors import ApiError, validation_error

logger = logging.getLogger("app.recommendation")

# 상대가 쓰는 경로. 버전 접두사가 없다.
_CREATE_PATH = "/internal/recommendations"
_RUN_PATH = "/internal/recommendations/{run_id}"


class RecommendationPending(Exception):
    """아직 실행 중. 실패가 아니라 '나중에 다시 물어보라'는 신호다."""

    def __init__(self, run_id: str, retry_after: int = 5) -> None:
        self.run_id = run_id
        self.retry_after = retry_after
        super().__init__(run_id)


def _service_unavailable(message: str, retry_after: int | None = None) -> ApiError:
    extra: dict[str, Any] = {}
    if retry_after is not None:
        extra["retry_after"] = retry_after
    return ApiError(503, "SERVICE_UNAVAILABLE", message, extra)


def _internal_error(message: str) -> ApiError:
    return ApiError(500, "INTERNAL_ERROR", message)


def _detail_message(response: httpx.Response, fallback: str) -> str:
    """상대의 오류 본문에서 사람이 읽을 메시지만 꺼낸다.

    상대는 {"detail": {"code": ..., "message": ..., "questions": [...]}} 형태다.
    구조를 그대로 흘리지 않는 이유는 우리 오류 봉투(API_SPEC 0-3)와 형태가
    다르기 때문이다. FE 가 두 가지 오류 형식을 다루게 만들지 않는다.
    """
    try:
        detail = response.json().get("detail")
    except ValueError:
        return fallback
    if isinstance(detail, dict) and isinstance(detail.get("message"), str):
        return detail["message"]
    return fallback


def _retry_after(response: httpx.Response, default: int) -> int:
    raw = response.headers.get("Retry-After", "")
    try:
        return max(1, int(raw))
    except ValueError:
        return default


# 커넥션을 재사용한다. 폴링은 몇 초 간격으로 반복되는 호출이라 매번 새 클라이언트를
# 열면 TCP 핸드셰이크 비용이 그대로 쌓인다. 수명은 앱 lifespan 이 관리한다.
_shared_client: httpx.AsyncClient | None = None


def get_client() -> httpx.AsyncClient:
    global _shared_client
    if not settings.internal_api_token.strip():
        # 상대도 토큰 미설정이면 503 으로 답한다. 우리도 같은 자리에서 막아
        # "추천 결과가 비어 있다" 로 조용히 나타나지 않게 한다.
        raise _internal_error("추천 서비스 호출 설정이 완료되지 않았습니다")
    if _shared_client is None or _shared_client.is_closed:
        _shared_client = httpx.AsyncClient(
            base_url=settings.recommendation_api_url.rstrip("/"),
            headers={"X-Internal-Token": settings.internal_api_token},
            timeout=settings.recommendation_client_timeout,
        )
    return _shared_client


async def close_client() -> None:
    """앱 종료 시 커넥션을 정리한다."""
    global _shared_client
    if _shared_client is not None and not _shared_client.is_closed:
        await _shared_client.aclose()
    _shared_client = None


def _raise_for_common_errors(response: httpx.Response) -> None:
    status = response.status_code

    if status == 429:
        raise _service_unavailable(
            _detail_message(response, "추천 요청이 많아 잠시 후 다시 시도해 주세요"),
            retry_after=_retry_after(response, 10),
        )
    if status == 422:
        # 상대가 지원하지 않는 지역·업종. 사용자 입력 문제이므로 400 이다.
        raise validation_error(_detail_message(response, "지원하지 않는 지역 또는 업종입니다"))
    if status in (401, 403):
        # 토큰이 어긋난 것은 우리 설정 문제다. 사용자에게 401 을 돌려주면
        # 로그인이 풀린 것으로 오해하게 된다.
        logger.error("추천 서비스 인증 실패 status=%s", status)
        raise _internal_error("추천 서비스 호출 설정이 올바르지 않습니다")
    if status >= 500:
        logger.error("추천 서비스 오류 status=%s body=%s", status, response.text[:500])
        raise _service_unavailable("추천 서비스를 사용할 수 없습니다")


async def request_recommendation(
    *,
    sido: str,
    sigungu: str,
    dong: str | None,
    industry_code: str | None,
    special_condition_text: str,
    limit: int | None,
    request_id: str,
) -> dict[str, Any]:
    """추천을 요청한다. 완료되면 결과를, 오래 걸리면 RecommendationPending."""
    payload: dict[str, Any] = {
        "request_id": request_id,
        "region": {"sido": sido, "sigungu": sigungu, "dong": dong},
        "industry_code": industry_code,
        "special_condition_text": special_condition_text,
    }
    if limit is not None:
        payload["limit"] = limit

    try:
        response = await get_client().post(_CREATE_PATH, json=payload)
    except httpx.TimeoutException as exc:
        # 우리 타임아웃이 상대보다 길므로 여기 도달하면 상대가 응답 자체를
        # 못 준 것이다. run_id 를 모르니 폴링도 불가능하다.
        logger.warning("추천 서비스 응답 없음 request_id=%s", request_id)
        raise _service_unavailable("추천 서비스 응답이 지연되고 있습니다") from exc
    except httpx.HTTPError as exc:
        logger.warning("추천 서비스 연결 실패 request_id=%s error=%s", request_id, exc)
        raise _service_unavailable("추천 서비스에 연결할 수 없습니다") from exc

    if response.status_code == 504:
        # 실패가 아니다. 상대는 계속 돌고 있고 run_id 로 결과를 받을 수 있다.
        run_id = _run_id_from_error(response)
        if run_id:
            raise RecommendationPending(run_id)
        raise _service_unavailable("추천 실행 시간이 초과되었습니다")

    _raise_for_common_errors(response)

    if response.status_code != 200:
        logger.error("추천 서비스 예상 밖 응답 status=%s", response.status_code)
        raise _service_unavailable("추천 서비스를 사용할 수 없습니다")

    return response.json()


async def fetch_recommendation_run(run_id: str) -> dict[str, Any]:
    """폴링. 완료면 결과, 진행 중이면 RecommendationPending."""
    try:
        response = await get_client().get(_RUN_PATH.format(run_id=run_id))
    except httpx.HTTPError as exc:
        logger.warning("추천 실행 조회 실패 run_id=%s error=%s", run_id, exc)
        raise _service_unavailable("추천 서비스에 연결할 수 없습니다") from exc

    if response.status_code == 202:
        raise RecommendationPending(run_id)
    if response.status_code == 404:
        raise ApiError(404, "NOT_FOUND", "추천 실행을 찾을 수 없습니다")

    _raise_for_common_errors(response)

    if response.status_code != 200:
        raise _service_unavailable("추천 서비스를 사용할 수 없습니다")

    return response.json()


def _run_id_from_error(response: httpx.Response) -> str | None:
    try:
        detail = response.json().get("detail")
    except ValueError:
        return None
    if isinstance(detail, dict):
        run_id = detail.get("run_id")
        if isinstance(run_id, str) and run_id:
            return run_id
    return None
