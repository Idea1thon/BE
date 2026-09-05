"""알림 keyset 페이지네이션. PR #5 리뷰 [P2] 알림 조회 상한 대응.

리뷰의 재검증 항목 "누락이나 중복 없이 동작하는지"를 실제 DB로 확인한다.

주의: 테스트 DB는 세션 전체가 공유한다. 시드 계정에 알림을 덧붙이면 "알림이 정확히
1건"을 단언하는 다른 테스트가 깨진다. 그래서 이 파일 전용 계정을 따로 만든다.
"""

from __future__ import annotations

import datetime as dt

import pytest

PAGER = {"email": "pagination@example.com", "password": "devpass1234"}
OWNER1_EMAIL = "owner1@example.com"
NOTIFICATION_COUNT = 7
PAGE_SIZE = 2


async def _auth(client, creds) -> dict[str, str]:
    res = await client.post("/api/v1/auth/login", json=creds)
    assert res.status_code == 200, res.text
    return {"Authorization": f"Bearer {res.json()['token']}"}


@pytest.fixture
async def pager(seeded) -> dict:
    """이 파일 전용 수신자와 알림 N건을 만든다. 다른 계정의 데이터는 건드리지 않는다.

    보고서 제출 API는 다음 Phase이므로 DB에 직접 넣는다. 멱등이다.
    created_at을 1분씩 벌려 정렬이 결정적이 되게 한다.
    """
    from sqlalchemy import select

    from app.core.config import settings
    from app.core.security import hash_password
    from app.db.session import SessionLocal
    from app.models import Branch, Notification, OperationReport, UserAccount
    from app.models.enums import ReportStatus, UserType

    async with SessionLocal() as session:
        owner = (
            await session.execute(
                select(UserAccount).where(UserAccount.email == OWNER1_EMAIL)
            )
        ).unique().scalar_one()
        branch = (
            await session.execute(select(Branch).where(Branch.owner_user_id == owner.id))
        ).scalar_one()

        recipient = (
            await session.execute(
                select(UserAccount).where(UserAccount.email == PAGER["email"])
            )
        ).unique().scalar_one_or_none()
        if recipient is None:
            recipient = UserAccount(
                franchise_id=owner.franchise_id,
                email=PAGER["email"],
                password_hash=hash_password(settings.seed_password),
                user_type=UserType.HQ,  # 점포를 붙이지 않는다 (branch.owner_user_id UNIQUE)
                name="페이지네이션 테스트",
            )
            session.add(recipient)
            await session.flush()

        report = (
            await session.execute(
                select(OperationReport).where(
                    OperationReport.branch_id == branch.id,
                    OperationReport.report_month == dt.date(2026, 7, 1),
                )
            )
        ).scalar_one_or_none()
        if report is None:
            report = OperationReport(
                branch_id=branch.id,
                report_month=dt.date(2026, 7, 1),
                status=ReportStatus.COMPLETED,
            )
            session.add(report)
            await session.flush()

        existing = (
            await session.execute(
                select(Notification).where(Notification.recipient_user_id == recipient.id)
            )
        ).scalars().all()
        base = dt.datetime(2026, 7, 1, 9, 0, tzinfo=dt.UTC)
        for i in range(len(existing), NOTIFICATION_COUNT):
            session.add(
                Notification(
                    recipient_user_id=recipient.id,
                    report_id=report.id,
                    message=f"페이지네이션 확인용 {i}",
                    created_at=base + dt.timedelta(minutes=i),
                )
            )
        await session.commit()

    return {"recipient_id": recipient.id, "branch_name": branch.name}


async def _drain(client, headers, *, limit: int, **params) -> tuple[list[int], int]:
    """커서를 따라 끝까지 읽고 (수집한 id 목록, 페이지 수)를 돌려준다."""
    ids: list[int] = []
    cursor = None
    pages = 0
    while True:
        query = {"limit": limit, **params}
        if cursor is not None:
            query["cursor"] = cursor
        res = await client.get("/api/v1/notifications", headers=headers, params=query)
        assert res.status_code == 200, res.text
        body = res.json()
        assert len(body["items"]) <= limit
        ids.extend(item["notification_id"] for item in body["items"])
        pages += 1
        cursor = body["next_cursor"]
        if cursor is None:
            break
        assert pages < 100, "커서가 끝나지 않는다 — 무한 루프"
    return ids, pages


async def test_paging_covers_every_row_exactly_once(client, pager):
    headers = await _auth(client, PAGER)

    full = await client.get("/api/v1/notifications", headers=headers, params={"limit": 100})
    assert full.status_code == 200, full.text
    expected = [item["notification_id"] for item in full.json()["items"]]
    assert len(expected) == NOTIFICATION_COUNT, "전용 수신자에게 다른 데이터가 섞였다"

    paged, pages = await _drain(client, headers, limit=PAGE_SIZE)

    assert paged == expected, "페이지를 이어붙인 결과가 전체 조회와 다르다 (누락 또는 순서 어긋남)"
    assert len(paged) == len(set(paged)), "중복된 알림이 있다"
    assert pages > 1, "페이지가 나뉘지 않았다 — 테스트가 무의미하다"


async def test_last_page_has_no_next_cursor(client, pager):
    headers = await _auth(client, PAGER)
    res = await client.get("/api/v1/notifications", headers=headers, params={"limit": 100})
    assert res.json()["next_cursor"] is None


async def test_full_page_still_reports_next_cursor_only_when_more_remain(client, pager):
    """경계 확인: 남은 행이 정확히 limit과 같으면 next_cursor는 null이어야 한다."""
    headers = await _auth(client, PAGER)
    res = await client.get(
        "/api/v1/notifications", headers=headers, params={"limit": NOTIFICATION_COUNT}
    )
    body = res.json()
    assert len(body["items"]) == NOTIFICATION_COUNT
    assert body["next_cursor"] is None


async def test_ordering_is_newest_first(client, pager):
    headers = await _auth(client, PAGER)
    res = await client.get("/api/v1/notifications", headers=headers, params={"limit": 100})
    created = [item["created_at"] for item in res.json()["items"]]
    assert created == sorted(created, reverse=True)


async def test_unread_count_is_stable_across_pages(client, pager):
    """unread_count는 페이지·필터와 무관하게 전체를 센다 (REQ-HQ-10 뱃지)."""
    headers = await _auth(client, PAGER)
    counts = []
    cursor = None
    while True:
        query = {"limit": PAGE_SIZE}
        if cursor is not None:
            query["cursor"] = cursor
        body = (
            await client.get("/api/v1/notifications", headers=headers, params=query)
        ).json()
        counts.append(body["unread_count"])
        cursor = body["next_cursor"]
        if cursor is None:
            break
    assert len(set(counts)) == 1, f"페이지마다 unread_count가 달라진다: {counts}"
    assert counts[0] == NOTIFICATION_COUNT


async def test_is_read_filter_pages_without_loss(client, pager):
    headers = await _auth(client, PAGER)
    unfiltered, _ = await _drain(client, headers, limit=PAGE_SIZE)
    unread, _ = await _drain(client, headers, limit=PAGE_SIZE, is_read="false")

    assert unread == unfiltered, "전부 미확인 상태이므로 필터 결과가 같아야 한다"
    assert len(unread) == len(set(unread))


async def test_broken_cursor_is_a_400_not_a_500(client, pager):
    headers = await _auth(client, PAGER)
    res = await client.get(
        "/api/v1/notifications", headers=headers, params={"cursor": "!!!not-a-cursor!!!"}
    )
    assert res.status_code == 400, res.text
    assert res.json()["error"]["code"] == "VALIDATION_ERROR"


async def test_limit_is_capped(client, pager):
    headers = await _auth(client, PAGER)
    res = await client.get("/api/v1/notifications", headers=headers, params={"limit": 1000})
    assert res.status_code == 400, res.text
    assert res.json()["error"]["code"] == "VALIDATION_ERROR"
