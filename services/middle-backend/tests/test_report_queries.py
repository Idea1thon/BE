"""Phase 4 조회 API. API_SPEC 3-1(목록) · 4-4 · 4-5 · 4-6 · 5-2.

시드 계정(owner1·owner2·hq)의 상태를 건드리지 않는다. `test_queries.py` 가
owner1 의 알림 수를 정확히 1건으로, owner2 를 "보고서 없는 점포"로 단정하기
때문이다. 그래서 이 파일은 전용 점주·점포·수신자를 따로 만든다.
"""

from __future__ import annotations

import datetime as dt

import pytest

OWNER1 = {"email": "owner1@example.com", "password": "devpass1234"}
OWNER2 = {"email": "owner2@example.com", "password": "devpass1234"}
HQ = {"email": "hq@example.com", "password": "devpass1234"}
OWNER3 = {"email": "owner3-queries@example.com", "password": "devpass1234"}

BRANCH_NAME = "송파 잠실점"
BRANCH_REGION = "11680"  # 강남구 — 시드에 존재하는 코드만 쓴다


@pytest.fixture
async def fixture_branch() -> dict:
    """전용 점포 1곳 + 보고서 3건(5·6·7월)을 만든다. 멱등."""
    from sqlalchemy import select

    from app.core.security import hash_password
    from app.db.session import SessionLocal
    from app.models import (
        Branch,
        Notification,
        OperationReport,
        ReportAnalysis,
        ReportInputItem,
        UserAccount,
    )
    from app.models.enums import ReportStatus, RiskLevel, UserType

    async with SessionLocal() as session:
        hq = (
            await session.execute(
                select(UserAccount).where(UserAccount.email == HQ["email"])
            )
        ).unique().scalar_one()

        owner = (
            await session.execute(
                select(UserAccount).where(UserAccount.email == OWNER3["email"])
            )
        ).unique().scalar_one_or_none()
        if owner is None:
            owner = UserAccount(
                franchise_id=hq.franchise_id,
                email=OWNER3["email"],
                password_hash=hash_password(OWNER3["password"]),
                user_type=UserType.OWNER,
                name="최점주",
            )
            session.add(owner)
            await session.flush()

        branch = (
            await session.execute(
                select(Branch).where(Branch.owner_user_id == owner.id)
            )
        ).scalar_one_or_none()
        if branch is None:
            branch = Branch(
                franchise_id=hq.franchise_id,
                owner_user_id=owner.id,
                name=BRANCH_NAME,
                address="서울특별시 강남구 삼성동 3-3",
                region_code=BRANCH_REGION,
                business_category_code="CS100001",
            )
            session.add(branch)
            await session.flush()

        existing = (
            await session.execute(
                select(OperationReport).where(OperationReport.branch_id == branch.id)
            )
        ).scalars().all()
        if existing:
            by_month = {r.report_month.strftime("%Y-%m"): r.id for r in existing}
            notification = (
                await session.execute(
                    select(Notification).where(
                        Notification.recipient_user_id == owner.id
                    )
                )
            ).scalars().first()
            return {
                "branch_id": branch.id,
                "owner_id": owner.id,
                "reports": by_month,
                "notification_id": notification.id if notification else None,
            }

        # (월, net_sales, 등급, 점수)
        plan = [
            (dt.date(2026, 5, 1), 5_000_000, RiskLevel.NORMAL, 20),
            (dt.date(2026, 6, 1), 9_000_000, RiskLevel.CAUTION, 55),
            (dt.date(2026, 7, 1), 7_000_000, RiskLevel.DANGER, 80),
        ]
        by_month: dict[str, int] = {}
        for month, net_sales, level, score in plan:
            report = OperationReport(
                branch_id=branch.id,
                report_month=month,
                status=ReportStatus.COMPLETED,
                net_sales=net_sales,
            )
            session.add(report)
            await session.flush()
            by_month[month.strftime("%Y-%m")] = report.id

            session.add(
                ReportAnalysis(
                    report_id=report.id,
                    risk_score=score,
                    risk_level=level,
                    factors=[{"type": "CLOSURE_RATE", "score": score}],
                    risk_periods=[{"period": "CURRENT_MONTH", "level": level.value}],
                    recommendations=[{"priority": 1, "text": "임차료 재협상 검토"}],
                    rule_version="v1.0",
                    calculated_at=dt.datetime.now(dt.UTC),
                )
            )

        # 최신 보고서에만 입력 항목을 붙인다 (4-5 검증용).
        latest_id = by_month["2026-07"]
        session.add_all(
            [
                ReportInputItem(report_id=latest_id, field_code="HALL_CARD", amount=6_200_000),
                ReportInputItem(report_id=latest_id, field_code="HALL_CASH", amount=800_000),
            ]
        )

        notification = Notification(
            recipient_user_id=owner.id,
            report_id=latest_id,
            message=f"{BRANCH_NAME} 위험 등급 판정",
        )
        session.add(notification)
        await session.commit()

        return {
            "branch_id": branch.id,
            "owner_id": owner.id,
            "reports": by_month,
            "notification_id": notification.id,
        }


async def _auth(client, creds) -> dict[str, str]:
    res = await client.post("/api/v1/auth/login", json=creds)
    assert res.status_code == 200, res.text
    return {"Authorization": f"Bearer {res.json()['token']}"}


def _find(items: list[dict], branch_id: int) -> dict:
    match = [i for i in items if i["branch_id"] == branch_id]
    assert match, f"branch {branch_id} 가 목록에 없다"
    return match[0]


# ------------------------------------------------------------------ 3-1 목록
async def test_branch_list_returns_latest_report(client, fixture_branch):
    headers = await _auth(client, HQ)
    res = await client.get("/api/v1/branches", headers=headers)
    assert res.status_code == 200, res.text

    item = _find(res.json()["items"], fixture_branch["branch_id"])
    assert item["name"] == BRANCH_NAME
    assert item["region_code"] == BRANCH_REGION
    # LATERAL 이 최신 1건만 집는다. 5·6월이 아니라 7월이어야 한다.
    assert item["latest_report"]["report_month"] == "2026-07"
    assert item["latest_report"]["risk_level"] == "DANGER"
    assert item["latest_report"]["risk_score"] == 80
    assert item["latest_report"]["net_sales"] == 7_000_000


async def test_branch_list_sorts_by_net_sales_with_nulls_last(client, fixture_branch):
    headers = await _auth(client, HQ)
    res = await client.get("/api/v1/branches?sort=net_sales_desc", headers=headers)
    values = [
        i["latest_report"]["net_sales"] if i["latest_report"] else None
        for i in res.json()["items"]
    ]
    present = [v for v in values if v is not None]
    assert present == sorted(present, reverse=True)
    # 보고서 없는 점포가 랭킹 첫 줄에 오면 안 된다 (REQ-HQ-04).
    if None in values:
        assert values.index(None) >= len(present)


async def test_branch_list_sorts_by_risk(client, fixture_branch):
    headers = await _auth(client, HQ)
    res = await client.get("/api/v1/branches?sort=risk_desc", headers=headers)
    order = {"DANGER": 3, "CAUTION": 2, "NORMAL": 1, None: 0}
    ranks = [
        order[i["latest_report"]["risk_level"] if i["latest_report"] else None]
        for i in res.json()["items"]
    ]
    assert ranks == sorted(ranks, reverse=True)


async def test_branch_list_filters_by_risk_level(client, fixture_branch):
    headers = await _auth(client, HQ)
    res = await client.get("/api/v1/branches?risk_level=DANGER", headers=headers)
    items = res.json()["items"]
    assert items, "DANGER 점포가 하나는 있어야 한다"
    assert all(i["latest_report"]["risk_level"] == "DANGER" for i in items)
    _find(items, fixture_branch["branch_id"])


async def test_branch_list_filters_by_region(client, fixture_branch):
    headers = await _auth(client, HQ)
    res = await client.get(f"/api/v1/branches?region_code={BRANCH_REGION}", headers=headers)
    items = res.json()["items"]
    assert all(i["region_code"] == BRANCH_REGION for i in items)
    _find(items, fixture_branch["branch_id"])


async def test_branch_list_q_matches_branch_name(client, fixture_branch):
    headers = await _auth(client, HQ)
    res = await client.get("/api/v1/branches?q=잠실", headers=headers)
    _find(res.json()["items"], fixture_branch["branch_id"])


async def test_branch_list_q_matches_region_name(client, fixture_branch):
    """REQ-HQ-06 은 '점포명 또는 지역' 검색이다. 지역명으로도 찾혀야 한다."""
    headers = await _auth(client, HQ)
    res = await client.get("/api/v1/branches?q=강남", headers=headers)
    _find(res.json()["items"], fixture_branch["branch_id"])


async def test_branch_list_q_treats_wildcards_literally(client, fixture_branch):
    """`%` 를 그대로 넘기면 전체 매칭이 된다. 이스케이프가 동작해야 한다."""
    headers = await _auth(client, HQ)
    res = await client.get("/api/v1/branches?q=%25", headers=headers)
    assert res.status_code == 200
    assert res.json()["items"] == []


async def test_branch_list_forbidden_for_owner(client):
    headers = await _auth(client, OWNER1)
    res = await client.get("/api/v1/branches", headers=headers)
    assert res.status_code == 403


async def test_branch_list_requires_auth(client):
    res = await client.get("/api/v1/branches")
    assert res.status_code == 401


async def test_branch_list_rejects_unknown_sort(client):
    headers = await _auth(client, HQ)
    res = await client.get("/api/v1/branches?sort=price_desc", headers=headers)
    assert res.status_code == 400
    assert res.json()["error"]["code"] == "VALIDATION_ERROR"


# ------------------------------------------------------------------ 4-4 보고서 목록
async def test_branch_reports_default_is_latest_first(client, fixture_branch):
    headers = await _auth(client, HQ)
    res = await client.get(
        f"/api/v1/branches/{fixture_branch['branch_id']}/reports", headers=headers
    )
    assert res.status_code == 200, res.text
    months = [i["report_month"] for i in res.json()["items"]]
    assert months == ["2026-07", "2026-06", "2026-05"]


async def test_branch_reports_month_asc(client, fixture_branch):
    headers = await _auth(client, HQ)
    res = await client.get(
        f"/api/v1/branches/{fixture_branch['branch_id']}/reports?sort=month_asc",
        headers=headers,
    )
    months = [i["report_month"] for i in res.json()["items"]]
    assert months == ["2026-05", "2026-06", "2026-07"]


async def test_branch_reports_include_analysis_fields(client, fixture_branch):
    headers = await _auth(client, OWNER3)
    res = await client.get(
        f"/api/v1/branches/{fixture_branch['branch_id']}/reports", headers=headers
    )
    first = res.json()["items"][0]
    assert first["risk_level"] == "DANGER"
    assert first["risk_score"] == 80
    assert first["status"] == "COMPLETED"


async def test_branch_reports_forbidden_for_other_owner(client, fixture_branch):
    headers = await _auth(client, OWNER1)
    res = await client.get(
        f"/api/v1/branches/{fixture_branch['branch_id']}/reports", headers=headers
    )
    assert res.status_code == 403


async def test_branch_reports_unknown_branch_is_404(client):
    headers = await _auth(client, HQ)
    res = await client.get("/api/v1/branches/999999/reports", headers=headers)
    assert res.status_code == 404


# ------------------------------------------------------------------ 4-5 상세
async def test_report_detail_returns_inputs_and_analysis(client, fixture_branch):
    headers = await _auth(client, OWNER3)
    report_id = fixture_branch["reports"]["2026-07"]
    res = await client.get(f"/api/v1/reports/{report_id}", headers=headers)
    assert res.status_code == 200, res.text

    body = res.json()
    assert body["report_month"] == "2026-07"
    assert body["branch"]["branch_id"] == fixture_branch["branch_id"]
    assert body["net_sales"] == 7_000_000

    codes = [i["field_code"] for i in body["inputs"]]
    assert codes == ["HALL_CARD", "HALL_CASH"]  # display_order 순
    assert body["inputs"][0]["name"] == "신용카드(홀)"
    assert body["inputs"][0]["group_name"] == "홀 매출"
    assert body["inputs"][0]["amount"] == 6_200_000

    # JSONB 를 변환 없이 그대로 싣는다 (INTERFACE_SPEC 4-2).
    assert body["analysis"]["risk_level"] == "DANGER"
    assert body["analysis"]["factors"] == [{"type": "CLOSURE_RATE", "score": 80}]
    assert body["analysis"]["recommendations"][0]["priority"] == 1
    assert body["analysis_error"] is None


async def test_report_detail_hq_can_read_own_franchise(client, fixture_branch):
    headers = await _auth(client, HQ)
    report_id = fixture_branch["reports"]["2026-05"]
    res = await client.get(f"/api/v1/reports/{report_id}", headers=headers)
    assert res.status_code == 200
    # 입력 항목을 넣지 않은 보고서다. 빈 배열이지 null 이 아니다.
    assert res.json()["inputs"] == []


async def test_report_detail_forbidden_for_other_owner(client, fixture_branch):
    headers = await _auth(client, OWNER2)
    report_id = fixture_branch["reports"]["2026-07"]
    res = await client.get(f"/api/v1/reports/{report_id}", headers=headers)
    assert res.status_code == 403


async def test_report_detail_unknown_is_404(client):
    headers = await _auth(client, HQ)
    res = await client.get("/api/v1/reports/999999", headers=headers)
    assert res.status_code == 404


# ------------------------------------------------------------------ 4-6 분석 상태
async def test_report_status(client, fixture_branch):
    headers = await _auth(client, OWNER3)
    report_id = fixture_branch["reports"]["2026-07"]
    res = await client.get(f"/api/v1/reports/{report_id}/status", headers=headers)
    assert res.status_code == 200, res.text
    assert res.json() == {
        "report_id": report_id,
        "status": "COMPLETED",
        "analysis_error": None,
    }


async def test_report_status_forbidden_for_other_owner(client, fixture_branch):
    headers = await _auth(client, OWNER2)
    report_id = fixture_branch["reports"]["2026-07"]
    res = await client.get(f"/api/v1/reports/{report_id}/status", headers=headers)
    assert res.status_code == 403


# ------------------------------------------------------------------ 5-2 읽음 처리
async def test_mark_notification_read_is_idempotent(client, fixture_branch):
    headers = await _auth(client, OWNER3)
    nid = fixture_branch["notification_id"]

    first = await client.patch(f"/api/v1/notifications/{nid}/read", headers=headers)
    assert first.status_code == 200, first.text
    assert first.json()["is_read"] is True
    read_at = first.json()["read_at"]
    assert read_at is not None

    second = await client.patch(f"/api/v1/notifications/{nid}/read", headers=headers)
    assert second.status_code == 200
    # 재호출이 시각을 밀면 "언제 읽었나"가 사라진다.
    assert second.json()["read_at"] == read_at

    listed = await client.get("/api/v1/notifications", headers=headers)
    assert listed.json()["unread_count"] == 0


async def test_mark_notification_read_forbidden_for_other_user(client, fixture_branch):
    headers = await _auth(client, OWNER1)
    nid = fixture_branch["notification_id"]
    res = await client.patch(f"/api/v1/notifications/{nid}/read", headers=headers)
    assert res.status_code == 403


async def test_mark_notification_read_unknown_is_404(client):
    headers = await _auth(client, HQ)
    res = await client.patch("/api/v1/notifications/999999/read", headers=headers)
    assert res.status_code == 404


# ------------------------------------------------------------------ 입력 경계 (리뷰 P2)
#
# 경로 ID 와 offset 에 상한이 없으면 BIGINT 를 넘는 값이 asyncpg 파라미터 바인딩까지
# 내려가 터지고, 공통 예외 처리가 그것을 404 가 아니라 500 으로 반환한다. 인증만 되면
# 누구나 500 을 만들 수 있다는 뜻이라 DB 를 부르기 전에 400 으로 막는다.

OVER_BIGINT = 2**63  # BIGINT 최댓값 + 1


@pytest.mark.parametrize(
    "path",
    [
        f"/api/v1/reports/{OVER_BIGINT}",
        f"/api/v1/reports/{OVER_BIGINT}/status",
        f"/api/v1/branches/{OVER_BIGINT}",
        f"/api/v1/branches/{OVER_BIGINT}/reports",
    ],
)
async def test_path_id_over_bigint_is_400_not_500(client, path):
    headers = await _auth(client, HQ)
    res = await client.get(path, headers=headers)
    assert res.status_code == 400, res.text
    assert res.json()["error"]["code"] == "VALIDATION_ERROR"


async def test_notification_path_id_over_bigint_is_400(client):
    headers = await _auth(client, HQ)
    res = await client.patch(f"/api/v1/notifications/{OVER_BIGINT}/read", headers=headers)
    assert res.status_code == 400
    assert res.json()["error"]["code"] == "VALIDATION_ERROR"


@pytest.mark.parametrize("path", ["/api/v1/reports/0", "/api/v1/branches/0"])
async def test_path_id_zero_is_400(client, path):
    """PK 는 1부터다. 0 이나 음수는 조회할 필요가 없으므로 경계에서 거른다."""
    headers = await _auth(client, HQ)
    res = await client.get(path, headers=headers)
    assert res.status_code == 400


async def test_offset_over_limit_is_400(client):
    headers = await _auth(client, HQ)
    res = await client.get(f"/api/v1/branches?offset={OVER_BIGINT}", headers=headers)
    assert res.status_code == 400
    assert res.json()["error"]["code"] == "VALIDATION_ERROR"


async def test_branch_reports_offset_over_limit_is_400(client, fixture_branch):
    headers = await _auth(client, HQ)
    res = await client.get(
        f"/api/v1/branches/{fixture_branch['branch_id']}/reports?offset={OVER_BIGINT}",
        headers=headers,
    )
    assert res.status_code == 400


async def test_valid_path_id_still_404s(client):
    """상한을 걸었다고 정상 범위의 없는 ID 까지 400 이 되면 안 된다."""
    headers = await _auth(client, HQ)
    res = await client.get("/api/v1/reports/999999", headers=headers)
    assert res.status_code == 404


async def test_forged_cursor_with_huge_id_is_400(client):
    """커서는 클라이언트가 손댈 수 있다. 경로 ID 와 같은 경계가 필요하다."""
    import base64

    headers = await _auth(client, HQ)
    raw = f"2026-09-01T00:00:00+00:00|{OVER_BIGINT}".encode("utf-8")
    forged = base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
    res = await client.get(f"/api/v1/notifications?cursor={forged}", headers=headers)
    assert res.status_code == 400
    assert res.json()["error"]["code"] == "VALIDATION_ERROR"
