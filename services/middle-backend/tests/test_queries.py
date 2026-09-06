"""Phase 3 조회 API 테스트. API_SPEC 3-2 / 3-3 / 3-4 / 4-1 / 5-1 / 6-1."""

from __future__ import annotations

import datetime as dt

import pytest

from app.models.enums import RegionLevel

OWNER1 = {"email": "owner1@example.com", "password": "devpass1234"}
OWNER2 = {"email": "owner2@example.com", "password": "devpass1234"}
HQ = {"email": "hq@example.com", "password": "devpass1234"}


async def _auth(client, creds) -> dict[str, str]:
    res = await client.post("/api/v1/auth/login", json=creds)
    assert res.status_code == 200, res.text
    return {"Authorization": f"Bearer {res.json()['token']}"}


@pytest.fixture
async def danger_report() -> dict:
    """owner1 점포에 위험 등급 보고서 + 알림 2건을 만든다.

    보고서 제출 API는 다음 Phase이므로 DB에 직접 넣는다.
    `net_sales`는 D1 확정 산식과 무관하게 NULL로 둔다 — 이번 Phase에서 쓰지 않는다.
    이미 만들어져 있으면 재사용한다(멱등).
    """
    from sqlalchemy import select

    from app.db.session import SessionLocal
    from app.models import (
        Branch,
        Notification,
        OperationReport,
        ReportAnalysis,
        UserAccount,
    )
    from app.models.enums import ReportStatus, RiskLevel

    async with SessionLocal() as session:
        owner = (
            await session.execute(
                select(UserAccount).where(UserAccount.email == OWNER1["email"])
            )
        ).unique().scalar_one()
        hq = (
            await session.execute(
                select(UserAccount).where(UserAccount.email == HQ["email"])
            )
        ).unique().scalar_one()
        branch = (
            await session.execute(
                select(Branch).where(Branch.owner_user_id == owner.id)
            )
        ).scalar_one()

        existing = (
            await session.execute(
                select(OperationReport).where(
                    OperationReport.branch_id == branch.id,
                    OperationReport.report_month == dt.date(2026, 8, 1),
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            return {
                "report_id": existing.id,
                "branch_id": branch.id,
                "branch_name": branch.name,
            }

        report = OperationReport(
            branch_id=branch.id,
            report_month=dt.date(2026, 8, 1),
            status=ReportStatus.COMPLETED,
        )
        session.add(report)
        await session.flush()

        session.add(
            ReportAnalysis(
                report_id=report.id,
                risk_score=74,
                risk_level=RiskLevel.DANGER,
                factors=[
                    {"type": "CLOSURE_RATE", "score": 72, "weight": 0.3, "contribution": 21.6}
                ],
                risk_periods=[{"period": "CURRENT_MONTH", "score": 74, "level": "DANGER"}],
                recommendations=[{"priority": 1, "text": "임차료 재협상 검토"}],
                rule_version="v1.0",
                calculated_at=dt.datetime.now(dt.UTC),
            )
        )
        session.add_all(
            [
                Notification(
                    recipient_user_id=owner.id,
                    report_id=report.id,
                    message=f"{branch.name} 위험 등급 판정",
                ),
                Notification(
                    recipient_user_id=hq.id,
                    report_id=report.id,
                    message=f"{branch.name} 위험 등급 판정 (본사 통보)",
                ),
            ]
        )
        await session.commit()
        return {"report_id": report.id, "branch_id": branch.id, "branch_name": branch.name}


# ------------------------------------------------------------------ 3-3 regions
async def test_regions_returns_sido_when_no_parent(client):
    headers = await _auth(client, HQ)
    res = await client.get("/api/v1/regions", headers=headers)
    assert res.status_code == 200, res.text
    items = res.json()["items"]
    assert items and all(i["level"] == "SIDO" for i in items)
    assert {"code", "name", "level"} == set(items[0])


async def test_regions_returns_children_for_parent_code(client):
    """서울 하위는 자치구 전부. 기대값을 시드에서 끌어온다.

    이름을 여기에 적어 두면 시드에 구를 하나 더할 때마다 관계없는 테스트가
    깨진다. 확인하려는 것은 "parent_code 의 자식만, 코드 순으로 온다" 이지
    자치구 목록 자체가 아니다.
    """
    from scripts.seed import REGIONS

    expected = [
        (code, name)
        for code, parent, level, name in REGIONS
        if parent == "11" and level is RegionLevel.SIGUNGU
    ]
    headers = await _auth(client, HQ)
    res = await client.get("/api/v1/regions?parent_code=11", headers=headers)
    assert res.status_code == 200
    items = res.json()["items"]
    assert [(i["code"], i["name"]) for i in items] == sorted(expected)
    assert all(i["level"] == "SIGUNGU" for i in items)


async def test_regions_expose_all_seoul_dong(client):
    """마이그레이션 0005 로 25개 자치구 전부가 행정동 목록을 갖는다.

    이전에는 강남·마포만 시드돼 다른 구를 고르면 동 선택이 비었다.
    """
    headers = await _auth(client, HQ)
    gu = (await client.get("/api/v1/regions?parent_code=11", headers=headers)).json()["items"]
    assert len(gu) == 25

    total = 0
    for item in gu:
        dongs = (await client.get(
            f"/api/v1/regions?parent_code={item['code']}", headers=headers
        )).json()["items"]
        assert dongs, f"{item['name']} 행정동이 비어 있음"
        assert all(d["level"] == "DONG" and d["code"].startswith(item["code"]) for d in dongs)
        total += len(dongs)
    assert total == 425

    songpa = (await client.get("/api/v1/regions?parent_code=11710", headers=headers)).json()["items"]
    assert ("11710670", "잠실2동") in {(d["code"], d["name"]) for d in songpa}


async def test_regions_unknown_parent_returns_empty(client):
    headers = await _auth(client, HQ)
    res = await client.get("/api/v1/regions?parent_code=99999", headers=headers)
    assert res.status_code == 200
    assert res.json()["items"] == []


async def test_regions_requires_auth(client):
    res = await client.get("/api/v1/regions")
    assert res.status_code == 401


# ------------------------------------------------------------------ 3-4 categories
async def test_business_categories(client):
    headers = await _auth(client, OWNER1)
    res = await client.get("/api/v1/business-categories", headers=headers)
    assert res.status_code == 200
    items = res.json()["items"]
    assert {"code", "name"} == set(items[0])
    assert "CS100001" in {i["code"] for i in items}
    assert len(items) == 10


# ------------------------------------------------------------------ 4-1 input fields
async def test_input_fields_returns_35_ordered(client):
    headers = await _auth(client, OWNER1)
    res = await client.get("/api/v1/reports/input-fields", headers=headers)
    assert res.status_code == 200, res.text
    items = res.json()["items"]
    assert len(items) == 35
    assert sum(1 for i in items if i["is_required"]) == 9
    assert [i["display_order"] for i in items] == sorted(
        i["display_order"] for i in items
    )
    assert {"code", "name", "group_name", "is_required", "display_order"} == set(items[0])
    assert len({i["group_name"] for i in items}) == 11


async def test_input_fields_forbidden_for_hq(client):
    headers = await _auth(client, HQ)
    res = await client.get("/api/v1/reports/input-fields", headers=headers)
    assert res.status_code == 403
    assert res.json()["error"]["code"] == "FORBIDDEN"


# ------------------------------------------------------------------ 3-2 branch detail
async def test_branch_detail_for_owner(client, danger_report):
    headers = await _auth(client, OWNER1)
    res = await client.get(f"/api/v1/branches/{danger_report['branch_id']}", headers=headers)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["branch_id"] == danger_report["branch_id"]
    assert body["region"] == {"code": "11680", "name": "강남구"}
    assert body["business_category"]["code"] == "CS100001"
    assert body["owner"]["name"] == "이점주"


async def test_branch_detail_visible_to_same_franchise_hq(client, danger_report):
    headers = await _auth(client, HQ)
    res = await client.get(f"/api/v1/branches/{danger_report['branch_id']}", headers=headers)
    assert res.status_code == 200


async def test_branch_detail_forbidden_for_other_owner(client, danger_report):
    headers = await _auth(client, OWNER2)
    res = await client.get(f"/api/v1/branches/{danger_report['branch_id']}", headers=headers)
    assert res.status_code == 403
    assert res.json()["error"]["code"] == "FORBIDDEN"


async def test_branch_detail_404(client):
    headers = await _auth(client, HQ)
    res = await client.get("/api/v1/branches/999999", headers=headers)
    assert res.status_code == 404
    assert res.json()["error"]["code"] == "NOT_FOUND"


# ------------------------------------------------------------------ 5-1 notifications
async def test_notifications_returns_own_only_with_joined_fields(client, danger_report):
    headers = await _auth(client, OWNER1)
    res = await client.get("/api/v1/notifications", headers=headers)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["unread_count"] >= 1
    assert len(body["items"]) >= 1
    item = body["items"][0]
    assert item["branch_name"] == danger_report["branch_name"]
    assert item["risk_level"] == "DANGER"
    assert item["report_id"] == danger_report["report_id"]
    assert item["is_read"] is False


async def test_notifications_are_scoped_per_recipient(client, danger_report):
    owner_res = await client.get(
        "/api/v1/notifications", headers=await _auth(client, OWNER1)
    )
    hq_res = await client.get("/api/v1/notifications", headers=await _auth(client, HQ))
    other_res = await client.get(
        "/api/v1/notifications", headers=await _auth(client, OWNER2)
    )
    assert len(owner_res.json()["items"]) == 1
    assert len(hq_res.json()["items"]) == 1
    assert other_res.json()["items"] == []
    assert other_res.json()["unread_count"] == 0


async def test_notifications_is_read_filter(client, danger_report):
    headers = await _auth(client, OWNER1)
    unread = await client.get("/api/v1/notifications?is_read=false", headers=headers)
    read = await client.get("/api/v1/notifications?is_read=true", headers=headers)
    assert len(unread.json()["items"]) == 1
    assert read.json()["items"] == []
    # 필터와 무관하게 unread_count는 전체 미확인 수다
    assert read.json()["unread_count"] == 1


# ------------------------------------------------------------------ 6-1 financial products
async def test_financial_products_matches_latest_risk_level(client, danger_report):
    headers = await _auth(client, OWNER1)
    res = await client.get("/api/v1/financial-products", headers=headers)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["risk_level"] == "DANGER"
    # REQ-OW-09: 최대 3개
    assert len(body["items"]) == 3
    assert [i["display_order"] for i in body["items"]] == [1, 2, 3]
    assert {"product_id", "name", "description", "link_url", "display_order"} == set(
        body["items"][0]
    )


async def test_financial_products_empty_when_no_report(client):
    """[결정 필요 D13] 등급 없는 신규 점포. 기본값은 빈 목록."""
    headers = await _auth(client, OWNER2)
    res = await client.get("/api/v1/financial-products", headers=headers)
    assert res.status_code == 200
    assert res.json() == {"risk_level": None, "items": []}


async def test_financial_products_forbidden_for_hq(client):
    headers = await _auth(client, HQ)
    res = await client.get("/api/v1/financial-products", headers=headers)
    assert res.status_code == 403


# ------------------------------------------------------------------ 범위 밖
async def test_signup_is_not_implemented(client):
    """API_SPEC 1-4. 회원가입은 별도 Phase 이고 라우터에 없다.

    3-1 목록 · 4-5 상세 · 4-6 상태는 Phase 4 에서 구현됐다
    (`test_report_queries.py`). 그 자리를 대신하는 범위 확인이다.
    """
    res = await client.post(
        "/api/v1/auth/signup",
        json={"email": "new@example.com", "password": "devpass1234"},
    )
    assert res.status_code == 404
