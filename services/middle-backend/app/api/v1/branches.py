"""가맹점 조회. API_SPEC 3-1(목록) · 3-2(상세) · 4-4(점포별 보고서 목록).

3-1 의 `latest_report` 는 `branch` 에 캐시 컬럼이 없어 LATERAL 조인으로 얻는다
(DB_SCHEMA 4-5 주석). 점포마다 최신 1건만 읽으므로 전체 보고서를 정렬한 뒤
group by 하는 것보다 싸다.

미결 항목에 대한 이 구현의 선택 — 확정되면 이 주석과 API_SPEC 을 함께 고친다.
- REQ-HQ-06 "점포명 또는 지역" : `q` 를 `branch.name` 과 `region.name` 양쪽에
  적용한다. `region_code` 는 정확 일치 필터로 역할을 나눈다.
- REQ-HQ-01 2섹션(정상/위험) : 응답 구조를 나누지 않는다. FE 가 `risk_level`
  필터로 두 번 호출하거나 한 번 받아 나눈다. 구조를 나누면 정렬·페이지 경계가
  섹션마다 따로 생겨 되돌리기 어렵다.
- 페이지네이션(0-5 미확정) : `items` 래퍼를 유지한 채 `limit`/`offset` 을 둔다.
  점포 수는 프랜차이즈 단위로 유계라 알림처럼 keyset 이 필요하지 않다.
"""

from __future__ import annotations

from fastapi import APIRouter, Query
from sqlalchemy import Select, case, or_, select, true
from sqlalchemy.orm import joinedload

from app.api.deps import CurrentUser, HqUser, SessionDep, authorize_branch
from app.errors import (
    FORBIDDEN_403,
    NOT_FOUND_404,
    UNAUTHORIZED_401,
    VALIDATION_400,
    not_found,
)
from app.models import (
    Branch,
    BusinessCategory,
    OperationReport,
    Region,
    ReportAnalysis,
    UserAccount,
)
from app.models.enums import RiskLevel
from app.schemas import (
    BranchDetailResponse,
    BranchListItem,
    BranchListResponse,
    BranchSort,
    CodeName,
    LatestReportBrief,
    OwnerBrief,
    ReportListItem,
    ReportListResponse,
    ReportSort,
)

router = APIRouter(prefix="/branches", tags=["branches"])

DEFAULT_LIMIT = 100
MAX_LIMIT = 500


def _escape_like(value: str) -> str:
    """`%` `_` 는 LIKE 의 와일드카드다. 사용자 입력은 리터럴로 다룬다."""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _latest_report_lateral():
    """점포별 최신 보고서 1건 + 분석 결과."""
    return (
        select(
            OperationReport.id.label("report_id"),
            OperationReport.report_month.label("report_month"),
            OperationReport.created_at.label("created_at"),
            OperationReport.status.label("status"),
            OperationReport.net_sales.label("net_sales"),
            ReportAnalysis.risk_level.label("risk_level"),
            ReportAnalysis.risk_score.label("risk_score"),
        )
        .outerjoin(ReportAnalysis, ReportAnalysis.report_id == OperationReport.id)
        .where(OperationReport.branch_id == Branch.id)
        .order_by(OperationReport.report_month.desc())
        .limit(1)
        .lateral("latest")
    )


def _apply_sort(stmt: Select, latest, sort: BranchSort) -> Select:
    """정렬. 보고서가 없는 점포는 어느 정렬에서도 뒤로 보낸다.

    NULLS LAST 를 빼면 PostgreSQL 은 DESC 에서 NULL 을 맨 앞에 둔다. 매출 랭킹
    화면 첫 줄이 "보고서 없음"이 되는 건 REQ-HQ-04 의 의도가 아니다.
    """
    if sort is BranchSort.NET_SALES_DESC:
        return stmt.order_by(latest.c.net_sales.desc().nulls_last(), Branch.id.asc())
    if sort is BranchSort.NET_SALES_ASC:
        return stmt.order_by(latest.c.net_sales.asc().nulls_last(), Branch.id.asc())

    # risk_desc — DANGER > CAUTION > NORMAL > 분석 없음. enum 의 정의 순서에
    # 의존하지 않고 명시한다.
    rank = case(
        (latest.c.risk_level == RiskLevel.DANGER, 3),
        (latest.c.risk_level == RiskLevel.CAUTION, 2),
        (latest.c.risk_level == RiskLevel.NORMAL, 1),
        else_=0,
    )
    return stmt.order_by(
        rank.desc(), latest.c.risk_score.desc().nulls_last(), Branch.id.asc()
    )


@router.get(
    "",
    response_model=BranchListResponse,
    responses={**VALIDATION_400, **UNAUTHORIZED_401, **FORBIDDEN_403},
)
async def list_branches(
    hq: HqUser,
    session: SessionDep,
    risk_level: RiskLevel | None = Query(default=None, description="최신 보고서 등급"),
    region_code: str | None = Query(default=None, max_length=20),
    q: str | None = Query(default=None, max_length=100, description="점포명·지역명 검색"),
    sort: BranchSort = Query(default=BranchSort.NET_SALES_DESC),
    limit: int = Query(default=DEFAULT_LIMIT, ge=1, le=MAX_LIMIT),
    offset: int = Query(default=0, ge=0),
) -> BranchListResponse:
    """REQ-HQ-01~06. 본사 전용이며 자사 소속만 본다 (REQ-AUTH-06)."""
    latest = _latest_report_lateral()

    stmt = (
        select(
            Branch,
            latest.c.report_id,
            latest.c.report_month,
            latest.c.created_at,
            latest.c.status,
            latest.c.net_sales,
            latest.c.risk_level,
            latest.c.risk_score,
        )
        .join(Region, Region.code == Branch.region_code)
        .outerjoin(latest, true())
        .where(Branch.franchise_id == hq.franchise_id)
    )

    if region_code is not None:
        stmt = stmt.where(Branch.region_code == region_code)
    if risk_level is not None:
        stmt = stmt.where(latest.c.risk_level == risk_level)
    if q:
        pattern = f"%{_escape_like(q)}%"
        stmt = stmt.where(
            or_(
                Branch.name.ilike(pattern, escape="\\"),
                Region.name.ilike(pattern, escape="\\"),
            )
        )

    stmt = _apply_sort(stmt, latest, sort).limit(limit).offset(offset)
    rows = (await session.execute(stmt)).all()

    items = []
    for branch, report_id, month, created_at, status_, net_sales, level, score in rows:
        latest_report = None
        if report_id is not None:
            latest_report = LatestReportBrief(
                report_id=report_id,
                report_month=month.strftime("%Y-%m"),
                created_at=created_at,
                status=status_,
                risk_level=level,
                risk_score=score,
                net_sales=int(net_sales) if net_sales is not None else None,
            )
        items.append(
            BranchListItem(
                branch_id=branch.id,
                name=branch.name,
                address=branch.address,
                region_code=branch.region_code,
                latest_report=latest_report,
            )
        )
    return BranchListResponse(items=items)


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
    authorize_branch(current_user, branch.franchise_id, branch.owner_user_id)

    return BranchDetailResponse(
        branch_id=branch.id,
        name=branch.name,
        address=branch.address,
        region=CodeName(code=region.code, name=region.name),
        business_category=CodeName(code=category.code, name=category.name),
        owner=OwnerBrief(id=owner.id, name=owner.name),
    )


@router.get(
    "/{branch_id}/reports",
    response_model=ReportListResponse,
    responses={**VALIDATION_400, **UNAUTHORIZED_401, **FORBIDDEN_403, **NOT_FOUND_404},
)
async def list_branch_reports(
    branch_id: int,
    current_user: CurrentUser,
    session: SessionDep,
    sort: ReportSort = Query(default=ReportSort.MONTH_DESC),
    limit: int = Query(default=DEFAULT_LIMIT, ge=1, le=MAX_LIMIT),
    offset: int = Query(default=0, ge=0),
) -> ReportListResponse:
    """API_SPEC 4-4. REQ-HQ-11~13 / REQ-OW-01, 02.

    점포를 먼저 확인하고 권한을 본다. 권한 없는 점포와 없는 점포를 같은 404 로
    합치지 않는다 — 3-2 가 이미 403/404 를 구분하므로 여기서만 숨겨도 소용없다.
    """
    branch = (
        await session.execute(select(Branch).where(Branch.id == branch_id))
    ).unique().scalar_one_or_none()
    if branch is None:
        raise not_found("가맹점을 찾을 수 없습니다")
    authorize_branch(current_user, branch.franchise_id, branch.owner_user_id)

    order = (
        OperationReport.report_month.desc()
        if sort is ReportSort.MONTH_DESC
        else OperationReport.report_month.asc()
    )
    rows = (
        await session.execute(
            select(OperationReport, ReportAnalysis.risk_level, ReportAnalysis.risk_score)
            .outerjoin(ReportAnalysis, ReportAnalysis.report_id == OperationReport.id)
            .where(OperationReport.branch_id == branch_id)
            .order_by(order, OperationReport.id.desc())
            .limit(limit)
            .offset(offset)
        )
    ).all()

    return ReportListResponse(
        items=[
            ReportListItem(
                report_id=report.id,
                report_month=report.report_month.strftime("%Y-%m"),
                created_at=report.created_at,
                status=report.status,
                risk_level=level,
                risk_score=score,
                net_sales=int(report.net_sales) if report.net_sales is not None else None,
            )
            for report, level, score in rows
        ]
    )
