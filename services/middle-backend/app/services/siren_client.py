"""폐업 위험 사이렌 서비스 HTTP 클라이언트 (`services/siren`).

**호출 규약** (상대 쪽 `services/siren/api.py` 기준)

  POST /internal/risk-sirens/analyze      200 결과 / 422 입력 계약 위반
  POST /internal/risk-sirens/hq-summary   200 결과 / 422 입력 계약 위반
  GET  /health                            200

추천 서비스와 달리 **동기 단일 호출**이다. 202+폴링이 아니다. canonical payload는
순수 계산이고 trigger payload는 siren 내부 provider가 외부 저장소를 읽는다.
INTERFACE_SPEC 4장은
위험도 분석을 202+폴링으로 잡아뒀는데 실제 구현과 다르다 — 어느 쪽에 맞출지는
아직 사람이 정하지 않았다(9장 A11). 여기서는 **상대 구현을 그대로 따른다**.
우리가 임의로 202 계층을 얹으면 상대 계약을 우리 문서에 맞춰 왜곡하게 된다.

**내부 인증.** 상대 `api.py` 는 `SIREN_INTERNAL_API_TOKEN`이 설정된 배포에서
`X-Internal-Token`을 검증한다. 이 클라이언트는 `INTERNAL_API_TOKEN`을 같은 헤더로
전달하므로 두 서비스의 토큰을 동일하게 설정해야 한다. Siren API는 nginx 외부 공개가
아닌 내부 서비스 네트워크에서만 노출한다.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from app.core.config import settings
from app.errors import ApiError

logger = logging.getLogger("app.siren")

_ANALYZE_PATH = "/internal/risk-sirens/analyze"
_ANALYZE_TRIGGER_PATH = "/internal/risk-sirens/analyze-trigger"
_HQ_SUMMARY_PATH = "/internal/risk-sirens/hq-summary"


def _service_unavailable(message: str) -> ApiError:
    return ApiError(503, "SERVICE_UNAVAILABLE", message)


def _internal_error(message: str) -> ApiError:
    return ApiError(500, "INTERNAL_ERROR", message)


def _detail_message(response: httpx.Response, fallback: str) -> str:
    """상대 오류 본문에서 사람이 읽을 부분만 꺼낸다.

    FastAPI 기본 형태라 `{"detail": ...}` 이고 우리 오류 봉투(API_SPEC 0-3)와
    형태가 다르다. 그대로 흘리면 FE 가 두 가지 오류 형식을 다뤄야 한다.
    """
    try:
        detail = response.json().get("detail")
    except ValueError:
        return fallback
    if isinstance(detail, str) and detail:
        return detail
    if isinstance(detail, list) and detail:
        first = detail[0]
        if isinstance(first, dict):
            loc = ".".join(str(p) for p in first.get("loc", []))
            return f"{loc}: {first.get('msg', fallback)}"
    return fallback


# 보고서 제출마다 부르는 호출이라 커넥션을 재사용한다. 수명은 앱 lifespan 이 관리한다.
_shared_client: httpx.AsyncClient | None = None


def get_client() -> httpx.AsyncClient:
    global _shared_client
    if _shared_client is None or _shared_client.is_closed:
        headers: dict[str, str] = {}
        token = settings.internal_api_token.strip()
        if token:
            headers["X-Internal-Token"] = token
        _shared_client = httpx.AsyncClient(
            base_url=settings.siren_api_url.rstrip("/"),
            headers=headers,
            timeout=settings.siren_request_timeout_seconds,
        )
    return _shared_client


async def close_client() -> None:
    global _shared_client
    if _shared_client is not None and not _shared_client.is_closed:
        await _shared_client.aclose()
    _shared_client = None


async def _post(path: str, payload: dict[str, Any], *, what: str) -> dict[str, Any]:
    try:
        response = await get_client().post(path, json=payload)
    except httpx.TimeoutException as exc:
        logger.warning("사이렌 응답 없음 path=%s", path)
        raise _service_unavailable(f"{what} 응답이 지연되고 있습니다") from exc
    except httpx.HTTPError as exc:
        logger.warning("사이렌 연결 실패 path=%s error=%s", path, exc)
        raise _service_unavailable(f"{what}에 연결할 수 없습니다") from exc

    if response.status_code == 422:
        # 우리가 만든 payload 가 상대 계약을 어긴 것이다. 사용자 입력 문제가
        # 아니므로 400 으로 돌려주지 않는다 — 점주에게 "입력이 잘못됐다" 고
        # 말하면 원인을 엉뚱한 곳에서 찾게 된다.
        message = _detail_message(response, "요청 형식이 올바르지 않습니다")
        logger.error("사이렌 계약 위반 path=%s detail=%s", path, message)
        raise _internal_error(f"{what} 호출 형식이 올바르지 않습니다")
    if response.status_code in (401, 403):
        logger.error("사이렌 인증 실패 status=%s", response.status_code)
        raise _internal_error(f"{what} 호출 설정이 올바르지 않습니다")
    if response.status_code >= 500:
        logger.error("사이렌 오류 status=%s body=%s", response.status_code, response.text[:500])
        raise _service_unavailable(f"{what}를 사용할 수 없습니다")
    if response.status_code != 200:
        logger.error("사이렌 예상 밖 응답 status=%s", response.status_code)
        raise _service_unavailable(f"{what}를 사용할 수 없습니다")

    # 200 이라고 본문이 계약을 지킨다는 보장은 없다. 여기서 걸러내지 않으면
    # 매핑 층에서 AttributeError 같은 예상 밖 예외로 터지고, 호출부는 그것을
    # 분석 실패로 인식하지 못한다.
    try:
        body = response.json()
    except ValueError as exc:
        logger.error("사이렌 응답이 JSON 이 아님 path=%s body=%s", path, response.text[:200])
        raise _service_unavailable(f"{what} 응답을 해석할 수 없습니다") from exc
    if not isinstance(body, dict):
        logger.error("사이렌 응답이 객체가 아님 path=%s type=%s", path, type(body).__name__)
        raise _service_unavailable(f"{what} 응답을 해석할 수 없습니다")
    return body


async def analyze(payload: dict[str, Any]) -> dict[str, Any]:
    """점포 1건 위험도 분석.

    Canonical legacy payloads use ``/analyze``. ID-only payloads created by the
    middle-backend trigger mapper use ``/analyze-trigger`` so the old contract's
    validation paths remain stable.
    """
    path = _ANALYZE_TRIGGER_PATH if "report_id" in payload else _ANALYZE_PATH
    return await _post(path, payload, what="위험도 분석 서비스")


async def hq_summary(payload: dict[str, Any]) -> dict[str, Any]:
    """본사 요약. `branch_results` 는 모두 같은 as_of 여야 한다(상대가 422 로 거부)."""
    return await _post(_HQ_SUMMARY_PATH, payload, what="본사 위험도 요약")
