from __future__ import annotations

import datetime as dt
from unittest.mock import AsyncMock, MagicMock

import pytest

from services.siren.providers.fmp_provider import BranchSnapshot, FmpProvider
from services.siren.providers.ideaton_provider import IdeatonProvider


class _Result:
    def __init__(self, rows: list[dict]) -> None:
        self._rows = list(rows)

    def mappings(self) -> "_Result":
        return self

    def all(self) -> list[dict]:
        return list(self._rows)

    def one_or_none(self) -> dict | None:
        return self._rows[0] if self._rows else None


class _Conn:
    def __init__(self, results: list[_Result]) -> None:
        self._results = list(results)
        self.statements: list[str] = []

    async def __aenter__(self) -> "_Conn":
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False

    async def execute(self, statement: object, params: object = None) -> _Result:
        self.statements.append(str(statement))
        return self._results.pop(0)


class _Engine:
    def __init__(self, conn: _Conn) -> None:
        self._conn = conn

    def connect(self) -> _Conn:
        return self._conn


@pytest.mark.asyncio
async def test_fetch_branch_joins_siren_branch_location() -> None:
    provider = FmpProvider("postgresql://example/fmp")
    branch_row = {
        "id": 10022,
        "franchise_id": 10,
        "name": "마포 서교점",
        "address": "서울 마포구 ...",
        "region_code": "11440",
        "business_category_code": "CS100001",
        "trade_area_code": "3120185",
        "admin_dong_code": "1144058000",
        "x_5181": 201912.0,
        "y_5181": 445706.0,
    }
    conn = _Conn([_Result([branch_row]), _Result([])])
    provider._get_engine = lambda: _Engine(conn)  # type: ignore[method-assign]

    snapshot = await provider.fetch_branch("10022", dt.date(2026, 1, 31), report_id="55")

    assert any("siren_branch_location" in stmt for stmt in conn.statements)
    assert snapshot.trade_area_code == "3120185"
    assert snapshot.admin_dong_code == "1144058000"
    assert snapshot.x_5181 == 201912.0
    assert snapshot.y_5181 == 445706.0


def _branch(**overrides: object) -> BranchSnapshot:
    base = dict(
        branch_id="10022",
        franchise_id="10",
        branch_name="마포 서교점",
        address="주소 형식이 건축물 원천과 달라도 무관",
        region_code="11440",
        industry_code="CS100001",
    )
    base.update(overrides)
    return BranchSnapshot(**base)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_branch_location_facts_are_used_without_address_lookup() -> None:
    provider = IdeatonProvider(None)
    connection = AsyncMock()
    branch = _branch(
        trade_area_code="3120185",
        admin_dong_code="1144058000",
        x_5181=201912.0,
        y_5181=445706.0,
    )

    location = await provider._resolve_location(connection, branch)

    assert location == {
        "gu_code": "11440",
        "admin_dong_code": "1144058000",
        "trade_area_code": "3120185",
        "x_5181": 201912.0,
        "y_5181": 445706.0,
    }
    connection.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_gu_code_falls_back_to_region_code_without_dong() -> None:
    provider = IdeatonProvider(None)
    connection = AsyncMock()
    branch = _branch(trade_area_code="3120185", x_5181=1.0, y_5181=2.0)

    location = await provider._resolve_location(connection, branch)

    assert location["gu_code"] == "11440"
    assert location["admin_dong_code"] is None
    connection.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_address_lookup_only_fills_gaps_left_by_branch_facts() -> None:
    provider = IdeatonProvider(None)
    result = MagicMock()
    result.mappings.return_value.one_or_none.return_value = {
        "admin_dong_code": "1144058500",
        "host_area_id": "commercial_area:9999999",
        "x_5181": 1.0,
        "y_5181": 2.0,
        "sigungu_code": "11440",
    }
    connection = MagicMock()
    connection.execute = AsyncMock(return_value=result)
    # Branch has the market-critical area code but is missing coordinates, so the
    # address lookup runs and supplies only what is absent.
    branch = _branch(trade_area_code="3120185")

    location = await provider._resolve_location(connection, branch)

    connection.execute.assert_awaited_once()
    assert location["trade_area_code"] == "3120185"  # stored fact still wins
    assert location["admin_dong_code"] == "1144058500"
    assert location["x_5181"] == 1.0
    assert location["y_5181"] == 2.0
    assert location["gu_code"] == "11440"
