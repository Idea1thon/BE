"""사이렌 요청 계약과 구조 대조.

`services/siren/models.py` 의 필드명을 그대로 옮겨 놓고 우리가 만든 payload 와
집합 비교한다. 상대 모델은 `extra="forbid"` 에 블록마다 기본값이 없어서
이름이 하나 다르거나 하나 빠지면 런타임에 422 가 된다. 그 실패를 배포가 아니라
여기서 잡는다.

상대 모델을 직접 임포트하지 않는 이유: `services/siren` 은 별도 서비스이고
`middle-backend` 이미지에 들어가지 않는다. 여기 옮겨 적은 이름이 정본과
어긋나면 통합 시 422 로 드러나므로, 상대 계약이 바뀌면 이 리터럴을 갱신한다.
"""

from __future__ import annotations

import datetime as dt

from app.models.enums import InputSource
from app.services.siren_mapper import (
    LocationInputs,
    build_analyze_request,
    build_location,
    build_monthly_report,
)

# services/siren/models.py 에서 그대로 옮긴 필드명.
SIREN = {
    "sales": {
        "hall": {"credit", "cash", "simple_pay"},
        "delivery": {"baemin", "coupang_eats", "other"},
        "takeout": {"credit", "cash", "simple_pay"},
    },
    "deductions": {"customer_refund", "own_coupon_discount"},
    "cogs": {"food_ingredients", "sub_materials", "alcohol", "beverage",
             "inventory_begin", "inventory_end"},
    "labor": {"fulltime", "parttime", "four_major_insurance", "meal_welfare", "short_term"},
    "variable": {"platform_fee", "delivery_agency_fee", "supplies", "utilities", "marketing_ad"},
    "opex": {"rent_mgmt", "equipment_rental", "telecom_it", "tax_bookkeeping",
             "insurance", "card_fee"},
    "finance": {"loan_interest", "other_misc"},
}
MONTHLY_TOP = {"month", "sales", "deductions", "cogs", "labor", "variable", "opex",
               "finance", "sales_source", "cost_source"}
LOCATION = {"gu_code", "admin_dong_code", "trade_area_code", "x_5181", "y_5181"}
REQUEST_REQUIRED = {"request_id", "franchise_id", "branch_id", "as_of", "industry_code", "location"}
REQUEST_ALLOWED = REQUEST_REQUIRED | {"brand_name", "market_data", "branch_reports",
                                      "reviews", "options", "franchise_closure"}
SOURCE_TAGS = {"pos", "synthetic_pos", "self_reported", "synthetic_self_reported"}


def _monthly():
    return build_monthly_report(
        report_month=dt.date(2026, 8, 1), items={}, input_source=InputSource.MANUAL
    )


def test_monthly_report_top_level_matches_exactly():
    assert set(_monthly()) == MONTHLY_TOP


def test_every_cost_block_matches_exactly():
    payload = _monthly()
    for block, expected in SIREN.items():
        if block == "sales":
            assert set(payload["sales"]) == set(expected)
            for sub, fields in expected.items():
                assert set(payload["sales"][sub]) == fields, f"sales.{sub}"
        else:
            assert set(payload[block]) == expected, block


def test_source_tags_are_in_the_contract_literal():
    payload = _monthly()
    assert payload["sales_source"] in SOURCE_TAGS
    assert payload["cost_source"] in SOURCE_TAGS


def test_location_matches_exactly():
    loc = build_location(
        region_code="1168010100",
        inputs=LocationInputs(trade_area_code="3120185", x_5181=1.0, y_5181=2.0),
    )
    assert set(loc) == LOCATION


def test_request_keys_are_within_the_contract():
    payload = build_analyze_request(
        request_id="r", franchise_id=1, branch_id=7, as_of=dt.date(2026, 8, 31),
        industry_code="CS100001",
        location=build_location(
            region_code="11680",
            inputs=LocationInputs(trade_area_code="3120185", x_5181=1.0, y_5181=2.0),
        ),
        monthly_reports=[_monthly()],
    )
    assert REQUEST_REQUIRED <= set(payload)        # 필수 누락 없음
    assert set(payload) <= REQUEST_ALLOWED         # extra="forbid" 위반 없음
    assert payload["as_of"] == "2026-08-31"        # date 가 아니라 문자열이어야 JSON 직렬화된다
    assert payload["industry_code"] == "CS100001"  # IndustryCode enum 값
