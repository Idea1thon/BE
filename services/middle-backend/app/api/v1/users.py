"""User 엔드포인트. API_SPEC 2-1."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.deps import CurrentUser
from app.errors import UNAUTHORIZED_401
from app.schemas import BranchBrief, FranchiseBrief, MeResponse

router = APIRouter(prefix="/users", tags=["users"])


@router.get("/me", response_model=MeResponse, responses={**UNAUTHORIZED_401})
async def get_me(current_user: CurrentUser) -> MeResponse:
    # branch는 OWNER에게만 존재한다 (branch.owner_user_id UNIQUE = 1점주 1점포).
    branch = current_user.branch
    return MeResponse(
        id=current_user.id,
        email=current_user.email,
        name=current_user.name,
        user_type=current_user.user_type,
        franchise=FranchiseBrief.model_validate(current_user.franchise),
        branch=BranchBrief.model_validate(branch) if branch else None,
    )
