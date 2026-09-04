"""기준정보 조회. API_SPEC 3-3 / 3-4."""

from __future__ import annotations

from fastapi import APIRouter, Query
from sqlalchemy import select

from app.api.deps import CurrentUser, SessionDep
from app.errors import UNAUTHORIZED_401
from app.models import BusinessCategory, Region
from app.models.enums import RegionLevel
from app.schemas import (
    BusinessCategoryItem,
    BusinessCategoryListResponse,
    RegionItem,
    RegionListResponse,
)

router = APIRouter(tags=["reference"])


@router.get(
    "/regions",
    response_model=RegionListResponse,
    responses={**UNAUTHORIZED_401},
)
async def list_regions(
    _: CurrentUser,
    session: SessionDep,
    parent_code: str | None = Query(
        default=None, description="미지정 시 level=SIDO 반환. 지정 시 해당 하위 목록"
    ),
) -> RegionListResponse:
    stmt = select(Region)
    if parent_code is None:
        stmt = stmt.where(Region.level == RegionLevel.SIDO)
    else:
        stmt = stmt.where(Region.parent_code == parent_code)
    rows = (await session.execute(stmt.order_by(Region.code))).scalars().all()
    return RegionListResponse(items=[RegionItem.model_validate(r) for r in rows])


@router.get(
    "/business-categories",
    response_model=BusinessCategoryListResponse,
    responses={**UNAUTHORIZED_401},
)
async def list_business_categories(
    _: CurrentUser, session: SessionDep
) -> BusinessCategoryListResponse:
    rows = (
        await session.execute(select(BusinessCategory).order_by(BusinessCategory.code))
    ).scalars().all()
    return BusinessCategoryListResponse(
        items=[BusinessCategoryItem.model_validate(r) for r in rows]
    )
