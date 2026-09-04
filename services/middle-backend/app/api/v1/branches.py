"""가맹점 조회. API_SPEC 3-2.

목록 조회(3-1)는 D1(`net_sales` 정렬)·M2(랭킹 범위)가 남아 이번 Phase 범위 밖이다.
"""

from __future__ import annotations

from fastapi import APIRouter
from sqlalchemy import select
from sqlalchemy.orm import joinedload

from app.api.deps import CurrentUser, SessionDep
from app.errors import FORBIDDEN_403, NOT_FOUND_404, UNAUTHORIZED_401, forbidden, not_found
from app.models import Branch, BusinessCategory, Region, UserAccount
from app.models.enums import UserType
from app.schemas import BranchDetailResponse, CodeName, OwnerBrief

router = APIRouter(prefix="/branches", tags=["branches"])


@router.get(
    "/{branch_id}",
    response_model=BranchDetailResponse,
    responses={**UNAUTHORIZED_401, **FORBIDDEN_403, **NOT_FOUND_404},
)
async def get_branch(
    branch_id: int, current_user: CurrentUser, session: SessionDep
) -> BranchDetailResponse:
    row = (
        await session.execute(
            select(Branch, Region, BusinessCategory, UserAccount)
            .join(Region, Region.code == Branch.region_code)
            .join(
                BusinessCategory,
                BusinessCategory.code == Branch.business_category_code,
            )
            .join(UserAccount, UserAccount.id == Branch.owner_user_id)
            .options(joinedload(UserAccount.branch, innerjoin=False))
            .where(Branch.id == branch_id)
        )
    ).unique().first()

    if row is None:
        raise not_found("가맹점을 찾을 수 없습니다")

    branch, region, category, owner = row

    # 권한: HQ는 자사 소속만, OWNER는 본인 점포만 (REQ-AUTH-06/07)
    if current_user.user_type is UserType.HQ:
        allowed = branch.franchise_id == current_user.franchise_id
    else:
        allowed = branch.owner_user_id == current_user.id
    if not allowed:
        raise forbidden("해당 가맹점에 접근할 권한이 없습니다")

    return BranchDetailResponse(
        branch_id=branch.id,
        name=branch.name,
        address=branch.address,
        region=CodeName(code=region.code, name=region.name),
        business_category=CodeName(code=category.code, name=category.name),
        owner=OwnerBrief(id=owner.id, name=owner.name),
    )
