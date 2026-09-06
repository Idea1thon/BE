"""입지 추천. 추천 서비스(services/recommendation-api)로의 서버 간 호출.

API_SPEC 에 없는 신규 계약이다. 추천 서비스를 브라우저에 직접 노출할 수 없어
(X-Internal-Token 이 노출되면 누구나 호출 가능해진다) 우리가 앞단을 둔다.

**응답이 두 갈래인 이유**

추천 파이프라인은 최대 180초다. 브라우저를 그만큼 세워둘 수 없으므로 추천
서비스는 "180초 안에 끝나면 결과, 넘으면 run_id" 로 답한다. 우리도 같은
구조를 그대로 옮긴다.

  200  결과 전체
  202  run_id — FE 가 GET .../{run_id} 로 폴링

보고서 제출(4-2)이 이미 202 + 폴링이라 FE 에게는 아는 패턴이다.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Response, status
from fastapi.responses import JSONResponse
from sqlalchemy import select

from app.api.deps import CurrentUser, SessionDep
from app.errors import (
    FORBIDDEN_403,
    NOT_FOUND_404,
    UNAUTHORIZED_401,
    VALIDATION_400,
    not_found,
    validation_error,
)
from app.models import BusinessCategory, Region
from app.models.enums import RegionLevel
from app.schemas import (
    LocationRecommendationAccepted,
    LocationRecommendationRequest,
    LocationRecommendationResponse,
)
from app.services import recommendation_client
from app.services.recommendation_client import RecommendationPending

router = APIRouter(prefix="/location-recommendations", tags=["location"])

# 추천 서비스는 현재 서울만 지원한다(상대의 _validate_common_input).
# 우리 region 테이블은 시도 이름을 갖고 있으므로 조회해서 넘긴다.
_SUPPORTED_SIDO = "서울특별시"

SERVICE_UNAVAILABLE_503 = {
    503: {"description": "SERVICE_UNAVAILABLE — 추천 서비스 지연·과부하"}
}


async def _resolve_region(session, region_code: str) -> tuple[str, str, str | None]:
    """지역 코드를 추천 서비스가 요구하는 (시도, 시군구, 동) 이름으로 바꾼다.

    추천 서비스는 코드가 아니라 한글 이름을 받는다. F11 에 따라 branch 는
    시군구 코드만 저장하므로 여기서도 시군구를 기준으로 삼고, 상위 시도는
    parent_code 로 거슬러 올라가 얻는다.
    """
    region = (
        await session.execute(select(Region).where(Region.code == region_code))
    ).scalar_one_or_none()
    if region is None:
        raise not_found("지역을 찾을 수 없습니다")

    if region.level is RegionLevel.DONG:
        sigungu = (
            await session.execute(select(Region).where(Region.code == region.parent_code))
        ).scalar_one_or_none()
        if sigungu is None:
            raise validation_error("지역 계층이 올바르지 않습니다")
        dong_name: str | None = region.name
        sigungu_region = sigungu
    elif region.level is RegionLevel.SIGUNGU:
        dong_name = None
        sigungu_region = region
    else:
        # 시도 단위 추천은 후보 범위가 서울 전체가 되어 의미가 없다.
        raise validation_error("시군구 또는 행정동 코드를 지정해 주세요")

    sido = (
        await session.execute(
            select(Region).where(Region.code == sigungu_region.parent_code)
        )
    ).scalar_one_or_none()
    sido_name = sido.name if sido is not None else _SUPPORTED_SIDO

    return sido_name, sigungu_region.name, dong_name


@router.post(
    "",
    responses={
        200: {"model": LocationRecommendationResponse},
        202: {"model": LocationRecommendationAccepted},
        **VALIDATION_400,
        **UNAUTHORIZED_401,
        **FORBIDDEN_403,
        **NOT_FOUND_404,
        **SERVICE_UNAVAILABLE_503,
    },
)
async def create_location_recommendation(
    body: LocationRecommendationRequest,
    current_user: CurrentUser,
    session: SessionDep,
    response: Response,
):
    """REQ 입지 추천. 로그인한 사용자면 본사·점주 모두 사용할 수 있다."""
    sido, sigungu, dong = await _resolve_region(session, body.region_code)

    if body.business_category_code is not None:
        # 미정의 업종 코드를 그대로 넘기면 추천 서비스가 422 로 답한다.
        # 우리 기준정보로 먼저 거르면 왕복 한 번을 아끼고 메시지도 명확해진다.
        exists = (
            await session.execute(
                select(BusinessCategory.code).where(
                    BusinessCategory.code == body.business_category_code
                )
            )
        ).scalar_one_or_none()
        if exists is None:
            raise validation_error(
                "업종을 찾을 수 없습니다", {"business_category_code": body.business_category_code}
            )

    # 추천 서비스 로그와 우리 로그를 이어 붙일 수 있게 요청마다 식별자를 만든다.
    request_id = str(uuid.uuid4())

    try:
        result = await recommendation_client.request_recommendation(
            sido=sido,
            sigungu=sigungu,
            dong=dong,
            industry_code=body.business_category_code,
            special_condition_text=body.special_condition_text,
            limit=body.limit,
            request_id=request_id,
        )
    except RecommendationPending as pending:
        return _accepted(pending)

    response.status_code = status.HTTP_200_OK
    return _completed(result)


@router.get(
    "/{run_id}",
    responses={
        200: {"model": LocationRecommendationResponse},
        202: {"model": LocationRecommendationAccepted},
        **UNAUTHORIZED_401,
        **NOT_FOUND_404,
        **SERVICE_UNAVAILABLE_503,
    },
)
async def get_location_recommendation(run_id: str, current_user: CurrentUser):
    """폴링. 202 가 오는 동안 FE 는 retry_after 초 간격으로 다시 부른다."""
    try:
        result = await recommendation_client.fetch_recommendation_run(run_id)
    except RecommendationPending as pending:
        return _accepted(pending)
    return _completed(result)


def _accepted(pending: RecommendationPending) -> JSONResponse:
    body = LocationRecommendationAccepted(
        run_id=pending.run_id, retry_after=pending.retry_after
    )
    # Retry-After 를 헤더로도 준다. 폴링 간격을 FE 가 임의로 정하면
    # 동시 실행 슬롯을 우리 쪽 재시도로 채우게 된다.
    return JSONResponse(
        status_code=status.HTTP_202_ACCEPTED,
        content=body.model_dump(),
        headers={"Retry-After": str(pending.retry_after)},
    )


def _completed(result: dict) -> LocationRecommendationResponse:
    return LocationRecommendationResponse(
        run_id=result.get("run_id", ""),
        request=result.get("request", {}),
        input_interpretation=result.get("input_interpretation", {}),
        summary=result.get("summary", {}),
        candidates=result.get("candidates", []),
        explanations=result.get("explanations", []),
    )
