from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from services.siren.providers.fmp_provider import BranchSnapshot
from services.siren.providers.ideaton_provider import IdeatonProvider


@pytest.mark.asyncio
async def test_branch_location_facts_are_used_without_address_lookup() -> None:
    provider = IdeatonProvider(None)
    connection = AsyncMock()
    branch = BranchSnapshot(
        branch_id="10022",
        franchise_id="10",
        branch_name="마포 서교점",
        address="주소 형식이 건축물 원천과 달라도 무관",
        region_code="11440",
        industry_code="CS100001",
        trade_area_code="3120185",
        x_5181=201912.0,
        y_5181=445706.0,
    )

    location = await provider._resolve_location(connection, branch)

    assert location == {
        "gu_code": "11440",
        "admin_dong_code": None,
        "trade_area_code": "3120185",
        "x_5181": 201912.0,
        "y_5181": 445706.0,
    }
    connection.execute.assert_not_awaited()
