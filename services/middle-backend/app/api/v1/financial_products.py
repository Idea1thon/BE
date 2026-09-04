"""금융상품 조회. API_SPEC 6-1.

노출 대상 등급은 서버가 결정한다 (REQ-OW-06). 클라이언트가 등급을 넘기지 않는다.
"""

from __future__ import annotations

from fastapi import APIRouter
from sqlalchemy import select

from app.api.deps import OwnerUser, SessionDep
from app.errors import FORBIDDEN_403, UNAUTHORIZED_401
from app.models import Branch, FinancialProduct, OperationReport, ReportAnalysis
from app.schemas import FinancialProductItem, FinancialProductListResponse

router = APIRouter(prefix="/financial-products", tags=["financial-products"])

MAX_BANNERS = 3  # REQ-OW-09 "최대 3개까지 슬라이드"


@router.get(
    "",
    response_model=FinancialProductListResponse,
    responses={**UNAUTHORIZED_401, **FORBIDDEN_403},
)
async def list_financial_products(
    current_user: OwnerUser, session: SessionDep
) -> FinancialProductListResponse:
    # 본인 점포의 최신 보고서 등급을 서버가 확정한다.
    risk_level = (
        await session.execute(
            select(ReportAnalysis.risk_level)
            .join(OperationReport, OperationReport.id == ReportAnalysis.report_id)
            .join(Branch, Branch.id == OperationReport.branch_id)
            .where(Branch.owner_user_id == current_user.id)
            .order_by(OperationReport.report_month.desc())
            .limit(1)
        )
    ).scalar_one_or_none()

    # [결정 필요 D13] 등급이 없는 신규 점포의 동작. 확정 전 기본값은 빈 목록이다.
    # 임의로 NORMAL을 적용하지 않는다 — REQ에 근거가 없다.
    if risk_level is None:
        return FinancialProductListResponse(risk_level=None, items=[])

    rows = (
        await session.execute(
            select(FinancialProduct)
            .where(
                FinancialProduct.target_risk_level == risk_level,
                FinancialProduct.is_active.is_(True),
            )
            .order_by(FinancialProduct.display_order, FinancialProduct.id)
            .limit(MAX_BANNERS)
        )
    ).scalars().all()

    return FinancialProductListResponse(
        risk_level=risk_level,
        items=[FinancialProductItem.model_validate(r) for r in rows],
    )
