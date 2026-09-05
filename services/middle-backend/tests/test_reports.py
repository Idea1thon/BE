"""보고서 제출과 DB 무결성 제약. API_SPEC 4-2, 이슈 #7."""

from __future__ import annotations

import datetime as dt

import pytest

OWNER1 = {"email": "owner1@example.com", "password": "devpass1234"}
OWNER2 = {"email": "owner2@example.com", "password": "devpass1234"}
HQ = {"email": "hq@example.com", "password": "devpass1234"}


async def _auth(client, creds) -> dict[str, str]:
    res = await client.post("/api/v1/auth/login", json=creds)
    assert res.status_code == 200, res.text
    return {"Authorization": f"Bearer {res.json()['token']}"}


async def _required_codes(client, headers) -> list[str]:
    res = await client.get("/api/v1/reports/input-fields", headers=headers)
    return [i["code"] for i in res.json()["items"] if i["is_required"]]


def _payload(month: str, items: list[dict]) -> dict:
    return {"report_month": month, "input_source": "MANUAL", "items": items}


async def _minimal_items(client, headers, amount: int = 1000) -> list[dict]:
    """필수 항목만 채운 최소 입력."""
    return [{"field_code": c, "amount": amount} for c in await _required_codes(client, headers)]


# ------------------------------------------------------------------ 제출 성공
async def test_submit_returns_202_with_analysis_request_id(client, seeded):
    headers = await _auth(client, OWNER1)
    res = await client.post(
        "/api/v1/reports",
        headers=headers,
        json=_payload("2026-01", await _minimal_items(client, headers)),
    )
    assert res.status_code == 202, res.text
    body = res.json()
    assert body["status"] == "ANALYZING"
    assert isinstance(body["report_id"], int)
    assert len(body["analysis_request_id"]) == 36  # uuid4


async def test_net_sales_is_gross_minus_deductions(client, seeded):
    """DB_SCHEMA 4-7 D1: 매출 3그룹 합계 − 매출 차감 2항목."""
    headers = await _auth(client, OWNER1)
    fields = (await client.get("/api/v1/reports/input-fields", headers=headers)).json()["items"]
    by_group: dict[str, list[str]] = {}
    for f in fields:
        by_group.setdefault(f["group_name"], []).append(f["code"])

    items = [{"field_code": c, "amount": 0} for c in [f["code"] for f in fields if f["is_required"]]]
    amounts = {i["field_code"]: i for i in items}

    def bump(code: str, value: int) -> None:
        if code in amounts:
            amounts[code]["amount"] = value
        else:
            items.append({"field_code": code, "amount": value})

    bump(by_group["홀 매출"][0], 5_000_000)
    bump(by_group["배달 매출"][0], 3_000_000)
    bump(by_group["매출 차감 항목"][0], 1_000_000)

    res = await client.post(
        "/api/v1/reports", headers=headers, json=_payload("2026-02", items)
    )
    assert res.status_code == 202, res.text

    from sqlalchemy import select

    from app.db.session import SessionLocal
    from app.models import OperationReport

    async with SessionLocal() as session:
        report = (
            await session.execute(
                select(OperationReport).where(
                    OperationReport.id == res.json()["report_id"]
                )
            )
        ).scalar_one()
        assert int(report.net_sales) == 5_000_000 + 3_000_000 - 1_000_000


# ------------------------------------------------------------------ 검증 실패
async def test_missing_required_field_is_400(client, seeded):
    headers = await _auth(client, OWNER1)
    items = await _minimal_items(client, headers)
    dropped = items.pop()

    res = await client.post(
        "/api/v1/reports", headers=headers, json=_payload("2026-03", items)
    )
    assert res.status_code == 400, res.text
    body = res.json()["error"]
    assert body["code"] == "VALIDATION_ERROR"
    assert dropped["field_code"] in body["missing_field_codes"]


async def test_unknown_field_code_is_400(client, seeded):
    headers = await _auth(client, OWNER1)
    items = await _minimal_items(client, headers)
    items.append({"field_code": "NO_SUCH_FIELD", "amount": 1})

    res = await client.post(
        "/api/v1/reports", headers=headers, json=_payload("2026-03", items)
    )
    assert res.status_code == 400, res.text
    assert "NO_SUCH_FIELD" in res.json()["error"]["unknown_field_codes"]


async def test_duplicated_field_code_is_400(client, seeded):
    headers = await _auth(client, OWNER1)
    items = await _minimal_items(client, headers)
    items.append(dict(items[0]))

    res = await client.post(
        "/api/v1/reports", headers=headers, json=_payload("2026-03", items)
    )
    assert res.status_code == 400, res.text
    assert items[0]["field_code"] in res.json()["error"]["duplicated_field_codes"]


async def test_mid_month_report_month_is_rejected_by_format(client, seeded):
    """요청 형식이 YYYY-MM 이라 월중 날짜는 API 경계에서 걸린다."""
    headers = await _auth(client, OWNER1)
    res = await client.post(
        "/api/v1/reports",
        headers=headers,
        json=_payload("2026-03-15", await _minimal_items(client, headers)),
    )
    assert res.status_code == 400, res.text
    assert res.json()["error"]["code"] == "VALIDATION_ERROR"


# ------------------------------------------------------------------ 권한·중복
async def test_hq_cannot_submit(client, seeded):
    headers = await _auth(client, HQ)
    res = await client.post(
        "/api/v1/reports",
        headers=headers,
        json=_payload("2026-04", [{"field_code": "HALL_CARD", "amount": 1}]),
    )
    assert res.status_code == 403, res.text
    assert res.json()["error"]["code"] == "FORBIDDEN"


async def test_duplicate_month_is_409(client, seeded):
    headers = await _auth(client, OWNER1)
    items = await _minimal_items(client, headers)
    first = await client.post(
        "/api/v1/reports", headers=headers, json=_payload("2026-05", items)
    )
    assert first.status_code == 202, first.text

    second = await client.post(
        "/api/v1/reports", headers=headers, json=_payload("2026-05", items)
    )
    assert second.status_code == 409, second.text
    assert second.json()["error"]["code"] == "CONFLICT"


async def test_two_owners_can_submit_the_same_month(client, seeded):
    """제약은 (branch_id, report_month) 다. 점포가 다르면 같은 월도 허용된다."""
    for creds in (OWNER1, OWNER2):
        headers = await _auth(client, creds)
        res = await client.post(
            "/api/v1/reports",
            headers=headers,
            json=_payload("2026-06", await _minimal_items(client, headers)),
        )
        assert res.status_code == 202, res.text


# ------------------------------------------------------------------ DB 제약 (이슈 #7)
async def test_db_rejects_mid_month_report_month(client, seeded):
    """ck_report_month_first_day. API 를 우회해 직접 INSERT 해도 막힌다."""
    from sqlalchemy import select, text
    from sqlalchemy.exc import IntegrityError

    from app.db.session import SessionLocal
    from app.models import Branch

    async with SessionLocal() as session:
        branch_id = (await session.execute(select(Branch.id).limit(1))).scalar_one()
        with pytest.raises(IntegrityError):
            await session.execute(
                text(
                    "INSERT INTO operation_report (branch_id, report_month, status) "
                    "VALUES (:b, :m, 'DRAFT')"
                ),
                {"b": branch_id, "m": dt.date(2026, 8, 15)},
            )
        await session.rollback()


async def test_db_rejects_cross_franchise_owner(client, seeded):
    """fk_branch_owner_same_franchise. 다른 프랜차이즈 사용자를 점주로 못 붙인다."""
    from sqlalchemy import select, text
    from sqlalchemy.exc import IntegrityError

    from app.core.security import hash_password
    from app.db.session import SessionLocal
    from app.models import Branch, Franchise, UserAccount
    from app.models.enums import UserType

    async with SessionLocal() as session:
        other = Franchise(name="다른 프랜차이즈")
        session.add(other)
        await session.flush()

        outsider = UserAccount(
            franchise_id=other.id,
            email="outsider@example.com",
            password_hash=hash_password("x" * 12),
            user_type=UserType.OWNER,
            name="외부 점주",
        )
        session.add(outsider)
        await session.flush()

        branch = (await session.execute(select(Branch).limit(1))).scalar_one()
        with pytest.raises(IntegrityError):
            await session.execute(
                text("UPDATE branch SET owner_user_id = :u WHERE id = :b"),
                {"u": outsider.id, "b": branch.id},
            )
        await session.rollback()
